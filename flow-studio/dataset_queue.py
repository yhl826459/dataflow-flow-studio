"""Persist ordered dataset submissions and advance them using the existing launcher's output identity."""
import hashlib
import json
import logging
import os
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ACTIVE_RUNS = {"running", "queued", "pending", "launching"}


def attempt_key(output, attempt):
    return output + "\0" + attempt if attempt else output


class QueueOrderConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _summary(job):
    graph = job["graph"]
    task = next((n.get("config", {}).get("task", "hands") for n in graph.get("nodes", [])
                 if n.get("type") == "input"), "hands")
    return {"id": job["id"], "pipelineId": graph["id"], "task": task, "status": job["status"],
            "createdAt": job["createdAt"], "updatedAt": job["updatedAt"],
            "items": [{"out": item["out"],
                       "selection": deepcopy(item["dataset"]["selection"]) if "dataset" in item else None,
                       **({"sequenceName": item["dataset"]["sequenceName"]}
                          if "dataset" in item and "sequenceName" in item["dataset"] else {}),
                       **({"clipPlan": deepcopy(item["dataset"]["clipPlan"])}
                          if "dataset" in item and "clipPlan" in item["dataset"] else {}),
                       **({"input": item["input"]} if "input" in item else {}),
                       **({"sequenceName": item["resumeSequence"], "continuation": True}
                          if "resumeSequence" in item else {}),
                       "status": item["status"], **({"error": item["error"]} if item.get("error") else {})}
                      for item in job["items"]]}


class DatasetQueue:
    """A single workbench service owns the directory. launch(graph, item) must be idempotent by out."""

    def __init__(self, base, launch, statuses, *, poll_interval=2.0):
        self.base = Path(base)
        self.launch = launch
        self.statuses = statuses
        self.poll_interval = poll_interval
        self._lock = threading.RLock()
        self._submissions = threading.Lock()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._worker = None
        self._jobs = {}
        for path in sorted(self.base.glob("*.json")):
            with path.open(encoding="utf-8") as stream:
                job = json.load(stream)
            if job["id"] != path.stem or not job["items"]:
                raise ValueError(f"队列存档无效，请检查 {path.name}")
            self._jobs[job["id"]] = job
        order_file = self.base / "pending-order"
        self._order = json.loads(order_file.read_text(encoding="utf-8")) if order_file.is_file() else []

    def _write(self, path, data):
        """Publish the in-memory transition only after its on-disk record is durable."""
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.base, delete=False) as stream:
            tmp = Path(stream.name)
            try:
                json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            os.replace(tmp, path)
            if os.name == "posix":
                fd = os.open(self.base, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            tmp.unlink(missing_ok=True)

    def _store(self, job):
        self._write(self.base / (job["id"] + ".json"), job)
        self._jobs[job["id"]] = job

    def submit(self, requestId, graph, items, *, preflight=None):
        try:
            job_id = str(UUID(requestId))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("队列请求标识必须是 UUID。") from exc
        if not isinstance(graph, dict) or not isinstance(graph.get("id"), str) or not graph["id"]:
            raise ValueError("缺少流水线快照。")
        if not isinstance(items, list) or not items:
            raise ValueError("请至少选择一个队列任务。")
        outputs = set()
        for item in items:
            dataset = item.get("dataset") if isinstance(item, dict) else None
            if (not isinstance(item, dict) or not isinstance(item.get("out"), str) or not item["out"] or
                    ("dataset" in item) == ("input" in item) or
                    ("dataset" in item and (not isinstance(dataset, dict) or not isinstance(dataset.get("selection"), dict))) or
                    ("input" in item and (not isinstance(item["input"], str) or not item["input"])) or
                    "status" in item or "error" in item):
                raise ValueError("队列任务须包含一种输入来源和输出目录。")
            if item["out"] in outputs:
                raise ValueError("队列中的任务需要各自独立的输出目录。")
            outputs.add(item["out"])
        encoded = json.dumps([graph, items], sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        signature = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._submissions:
            with self._lock:
                existing = self._jobs.get(job_id)
                if existing:
                    if existing["signature"] != signature:
                        raise ValueError("同一队列请求标识已用于不同的任务。")
                    return _summary(existing)
                conflicts = [old for job in self._jobs.values() for old in job["items"] if old["out"] in outputs]
                resumed = {item["out"] for item in items if item.get("resumeAttempt")}
                if any(old["out"] not in resumed or old["status"] not in ("failed", "interrupted") for old in conflicts):
                    raise ValueError("所选输出目录已分配给其他队列任务，或该任务已在续跑。")
            if preflight:
                preflight()
            graph, items = json.loads(encoded)
            stamp = _now()
            job = {"id": job_id, "signature": signature, "graph": graph, "items": items,
                   "createdAt": stamp, "updatedAt": stamp, "status": "queued"}
            for item in items:
                item["status"] = "queued"
            with self._lock:
                self._store(job)
                answer = _summary(job)
            self._wake.set()
            return answer

    def list(self):
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda j: (j["createdAt"], j["id"]), reverse=True)
            return [_summary(job) for job in ordered]

    def _pending(self):
        entries = {item["out"]: (job, index)
                   for job in sorted(self._jobs.values(), key=lambda j: (j["createdAt"], j["id"]))
                   if job["status"] in ("queued", "running")
                   for index, item in enumerate(job["items"]) if item["status"] == "queued"}
        ordered = dict.fromkeys(key for key in self._order if key in entries)
        ordered.update(dict.fromkeys(entries))
        return {key: entries[key] for key in ordered}

    def view(self):
        with self._lock:
            return {"queues": self.list(), "queueOrder": list(self._pending())}

    def reorder(self, body):
        if set(body) != {"expected", "order"}:
            raise ValueError("请提供原排队顺序和新的顺序。")
        with self._lock:
            current = list(self._pending())
            if body["expected"] != current:
                raise QueueOrderConflict("队列已有变化，请按刷新后的列表重新调整。")
            order = body["order"]
            if (not isinstance(order, list) or len(order) != len(current) or
                    not all(isinstance(key, str) for key in order) or set(order) != set(current)):
                raise ValueError("只能调整仍在排队的序列。")
            self._write(self.base / "pending-order", order)
            self._order = order
        self._wake.set()
        return self.view()

    def _next(self):
        # On restart, reconcile any previously dispatched output before considering fresh work.
        for job in self._jobs.values():
            for index, item in enumerate(job["items"]):
                if item["status"] in ("launching", "running"):
                    return job, index
        return next(iter(self._pending().values()), None)

    def _mark(self, job_id, index, state, error=None):
        with self._lock:
            current = self._jobs[job_id]
            if current["items"][index]["status"] == state:
                return
            job = deepcopy(current)
            item = job["items"][index]
            item["status"] = state
            if error:
                item["error"] = error
            job["updatedAt"] = _now()
            if state in ("failed", "interrupted"):
                for later in job["items"]:
                    if later["status"] == "queued":
                        later["status"] = "paused"
                job["status"] = "paused"
            elif all(entry["status"] == "completed" for entry in job["items"]):
                job["status"] = "completed"
            else:
                job["status"] = "running"
            self._store(job)

    def _step(self):
        with self._lock:
            selected = self._next()
        if not selected:
            return
        job, index = selected
        item = job["items"][index]
        out = item["out"]
        monitor_key = attempt_key(out, item.get("resumeAttempt"))
        observed = self.statuses()
        if not isinstance(observed, dict):
            raise ValueError("服务器任务状态暂不可用。")
        if monitor_key in observed:
            if item["status"] == "queued":
                self._mark(job["id"], index, "failed", "该输出目录已有其他任务，请更换目录。")
                return
            state = observed[monitor_key]
            if state in ACTIVE_RUNS:
                self._mark(job["id"], index, "running")
            elif state == "completed":
                self._mark(job["id"], index, "completed")
            elif state in ("failed", "interrupted", "cancelled", "canceled"):
                self._mark(job["id"], index, "failed" if state == "failed" else "interrupted",
                           "服务器任务失败。" if state == "failed" else "服务器任务已中断。")
            return
        if item["status"] == "running" or self._stopped.is_set():
            return
        # Let work that entered DataFlow before this queue finish as well.
        if ACTIVE_RUNS.intersection(observed.values()):
            return
        # Write intent *before* entering the launcher. A crash or lost acknowledgement can be retried by out.
        with self._lock:
            # A drag may have changed the selection during the remote status read.
            if self._next() != selected:
                return
            self._mark(job["id"], index, "launching")
        request = {k: deepcopy(v) for k, v in item.items() if k != "status"}
        try:
            assigned = self.launch(deepcopy(job["graph"]), request)
        except ValueError as exc:
            self._mark(job["id"], index, "failed", str(exc))
            return
        if not isinstance(assigned, dict) or assigned.get("output") != out:
            raise RuntimeError("启动回执尚未确认预定输出目录。")
        self._mark(job["id"], index, "running")

    def _run(self):
        last_error = None
        while not self._stopped.is_set():
            self._wake.clear()
            try:
                self._step()
                last_error = None
            except Exception as exc:
                # Fail closed on unavailable inventory or ambiguous launch acknowledgements.
                if str(exc) != last_error:
                    logging.warning("数据集队列等待服务器确认：%s", exc)
                    last_error = str(exc)
            if not self._stopped.is_set():
                self._wake.wait(self.poll_interval)

    def start(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stopped.clear()
            self._worker = threading.Thread(target=self._run, name="dataset-queue", daemon=True)
            self._worker.start()

    def stop(self):
        self._stopped.set()
        self._wake.set()
        if self._worker:
            self._worker.join()
