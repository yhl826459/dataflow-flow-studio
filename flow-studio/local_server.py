"""Serve the workbench on loopback and access DataFlow directly or through SSH."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent
LIBRARY = ROOT / ".local/library.json"
DOCS = ROOT / ".local/pipeline-docs"
TASK_PRESETS = ROOT / ".local/task-presets.json"
LIBRARY_LIMIT = 4 * 1024 * 1024
REMOTE = "/data/yanghonglei/DataFlow"
STAGES = {"qwen": "qwen", "genception": "segment", "abs": "score", "gate": "gate", "refiner": "refine",
          "fusion": "fusion"}
LABELS = {"input": "输入清单", "qwen": "Qwen 提词", "segment": "视频分割", "score": "ABS 评分",
          "gate": "质量判断", "refine": "提示词改写", "fusion": "全视频掩码融合", "output": "运行结束"}
EDGE_IDS = {("input", "qwen", "next"): "in-qwen", ("qwen", "segment", "next"): "qwen-segment",
            ("segment", "score", "next"): "segment-score", ("score", "gate", "next"): "score-gate",
            ("gate", "output", "pass"): "gate-pass", ("gate", "output", "review"): "gate-review",
            ("gate", "refine", "retry"): "gate-retry", ("refine", "segment", "next"): "refine-segment"}


def graph_task(graph):
    return next(node for node in graph["nodes"] if node["type"] == "input").get("config", {}).get("task", "hands")


def checked_output(path, root, task):
    target = Path(path).resolve()
    if Path.home().resolve() not in target.parents:
        raise ValueError("新输出目录必须位于服务器个人目录内。")
    output = (root / "dataflow_output").resolve()
    expected = output / task
    if target == output or (output in target.parents and expected not in target.parents):
        raise ValueError(f"本次目标为 {task}，请在 {expected} 下指定新子目录。")
    return target


def compile_graph(graph, reviewed):
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError("流水线格式无效。")
    if not isinstance(graph.get("id"), str) or not 0 < len(graph["id"]) <= 128:
        raise ValueError("流水线标识无效。")
    if type(graph.get("version")) is not int or graph["version"] < 1:
        raise ValueError("启动前请先保存带版本号的流水线。")
    nodes = {}
    type_by_id = {}
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError("节点格式无效。")
        key = node.get("type")
        if not isinstance(key, str) or key not in LABELS or key in nodes or node["id"] in type_by_id:
            raise ValueError("服务器仅支持原分割流水线的算子组合，每种算子须唯一。自定义图可使用演练。")
        if "implementation" in node:
            from operator_sources import implementation_shape
            implementation_shape(key, node["implementation"])
            if node["implementation"]["kind"] != "native":
                raise ValueError("记录算子应通过 JSONL 流水线运行。")
            if reviewed is not True:
                raise ValueError("请在启动窗口确认已审阅当前节点的自定义 Python 代码。")
        type_by_id[node["id"]] = key
        config = node.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("算子参数格式无效。")
        nodes[key] = config
    config_keys = {"input": {"task"}, "qwen": {"sample_fps"}, "gate": {"threshold", "maxRetries"},
                   "score": {"every"}}
    for name, config in nodes.items():
        if set(config) - config_keys.get(name, set()):
            raise ValueError(f"{LABELS[name]} 包含当前服务器入口不支持的参数。")
    feedback = "gate" in nodes or "refine" in nodes
    scoring = "score" in nodes
    required = {"input", "qwen", "segment", "output"}
    if scoring:
        required.add("score")
    if feedback:
        required.update(("score", "gate", "refine"))
    fusion = "fusion" in nodes
    if fusion:
        required.add("fusion")
    if set(nodes) != required:
        raise ValueError("质量反馈需要分割、ABS、质量判断和改写算子。")
    connections = {("input", "qwen", "next"), ("qwen", "segment", "next")}
    if feedback:
        connections = set(EDGE_IDS)
    elif scoring:
        connections.update({("segment", "score", "next"), ("score", "output", "next")})
    else:
        connections.add(("segment", "output", "next"))
    if fusion:
        connections = {(a, "fusion" if b == "output" else b, c) for a, b, c in connections}
        connections.add(("fusion", "output", "next"))
    seen, seen_ids = set(), set()
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or not all(isinstance(edge.get(field), str) for field in ("from", "to", "id")):
            raise ValueError("连线格式无效。")
        condition = edge.get("condition", "next")
        if not isinstance(condition, str):
            raise ValueError("连线条件无效。")
        connection = (type_by_id.get(edge["from"]), type_by_id.get(edge["to"]), condition)
        if connection in seen or not edge["id"] or edge["id"] in seen_ids:
            raise ValueError("重复连线。")
        seen.add(connection)
        seen_ids.add(edge["id"])
    if seen != connections:
        raise ValueError("这张图的连线与服务器视频分割入口不匹配，可先运行演练。")
    if nodes["input"].get("task", "hands") not in ("hands", "objects"):
        raise ValueError("目标任务须选择找手（hands）或找物（objects）。")
    if nodes["qwen"].get("sample_fps", 5) != 5:
        raise ValueError("服务器 Qwen 固定按 5 fps 采样。")
    args = []
    if scoring:
        every = nodes["score"].get("every", 6)
        if type(every) is not int or every < 1:
            raise ValueError("ABS 采样间隔必须是正整数。")
        args.extend(["--abs", "--abs-every", str(every)])
    if feedback:
        gate = nodes["gate"]
        threshold = gate.get("threshold", 7)
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 10:
            raise ValueError("通过阈值应在 0 至 10 之间。")
        if gate.get("maxRetries", 2) != 2:
            raise ValueError("服务器原实现的每实例重试上限固定为 2。")
        args.extend(["--refine", "--refine-threshold", str(threshold)])
    if fusion:
        args.append("--workbench-fuse")
    return args


def compile_request(body):
    if (not isinstance(body, dict) or set(body) - {"graph", "input", "dataset", "out", "allowNativeCode"}
            or ("input" in body) == ("dataset" in body)):
        raise ValueError("请选择一条服务器序列或一个 JSONL 清单，并指定新输出目录。")
    graph = body.get("graph")
    args = compile_graph(graph, body.get("allowNativeCode"))
    if "input" in body and "--workbench-fuse" in args:
        raise ValueError("全视频掩码融合需要片段帧号，请从工作台选择服务器数据集。")
    for field in (("out", "input") if "input" in body else ("out",)):
        value = body.get(field)
        if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
            raise ValueError("请输入服务器绝对路径。")
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or (field == "input" and path.suffix.lower() != ".jsonl"):
            raise ValueError("请输入服务器 JSONL 清单和新输出目录的绝对路径。")
    payload = {"graph": graph, "out": body["out"], "flags": args}
    if "dataset" in body:
        choice = body["dataset"]
        if (not isinstance(choice, dict) or set(choice) - {"selection", "fingerprint", "sequenceName", "clipPlan"}
                or not isinstance(choice.get("selection"), dict) or not isinstance(choice.get("fingerprint"), str)
                or not re.fullmatch("[0-9a-f]{64}", choice["fingerprint"])):
            raise ValueError("请重新检查所选序列后再确认启动。")
        if "sequenceName" in choice:
            name = choice["sequenceName"]
            if (not isinstance(name, str) or not re.fullmatch(r"[\w][\w.-]*", name)
                    or name.endswith((".", "-")) or len(name.encode("utf-8")) > 150):
                raise ValueError("序列输出名称无效，请重新检查所选序列。")
        if "clipPlan" in choice:
            from datasets_catalog import FUSION_OVERLAP
            plan = choice["clipPlan"]
            keys = {"startFrame", "frameCount", "parts"}
            overlap = FUSION_OVERLAP if "--workbench-fuse" in args else 0
            if overlap:
                keys.add("overlap")
            if (not isinstance(plan, dict) or set(plan) != keys or
                    any(type(plan[key]) is not int for key in keys) or plan["startFrame"] < 0 or
                    plan["frameCount"] < 1 or plan["parts"] < 1 or plan.get("overlap", 0) != overlap or
                    plan["parts"] != 1 + max(0, (plan["frameCount"] - 81 + 80 - overlap) // (81 - overlap))):
                raise ValueError("片段计划无效，请重新检查所选序列。")
        payload["dataset"] = choice
    else:
        payload["input"] = body["input"]
    return payload


def dataset_runtime_issues(root, flags, task):
    home = Path.home()
    script = "experiment_hands.py" if task == "hands" else "experiment.py"
    needed = [root / ".venv/bin/python", home / "pipe_14b_v2.pkl",
              home / "umt5-xxl-encoder-bf16-dataflow/config.json",
              home / "qwen38-fp8/.venv/bin/python", home / "qwen38-fp8" / script,
              home / ".venv-genception14b/bin/python"]
    if "--abs" in flags:
        needed.extend([home / "scoring_scripts/abs/local_score.py", home / "scoring_scripts/abs/api_config.json"])
    issues = [f"缺少服务器运行文件 {p}" for p in needed if not p.is_file()]
    if not shutil.which("ffprobe"):
        issues.append("服务器缺少 ffprobe，无法读取视频规格。")
    config = home / "scoring_scripts/abs/api_config.json"
    if "--abs" in flags and config.is_file():
        try:
            env_name = json.loads(config.read_text(encoding="utf-8"))["api"]["key_env"]
            if not isinstance(env_name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", env_name):
                raise ValueError()
            if not os.environ.get(env_name):
                issues.append(f"当前图包含 ABS 评分，请为工作台服务配置环境变量 {env_name}。")
        except (OSError, KeyError, ValueError, TypeError):
            issues.append("请检查 ABS 的 api_config.json 和 api.key_env 配置。")
    return issues


def inspect_dataset(root, selection, graph, reserved=()):
    from datasets_catalog import FUSION_OVERLAP, inspect_selection, registered_root
    if not isinstance(selection, dict) or not isinstance(selection.get("id"), str) or not selection["id"]:
        raise ValueError("请选择服务器目录和序列。")
    flags = compile_graph(graph, reviewed=True)
    task = graph_task(graph)
    overlap = FUSION_OVERLAP if "--workbench-fuse" in flags else 0
    result = inspect_selection(registered_root(root, selection.get("sourceRoot")), selection, task=task, overlap=overlap)
    issues = dataset_runtime_issues(root, flags, task)
    if result["needsPreparation"] and not shutil.which("ffmpeg"):
        issues.append("服务器缺少 ffmpeg，无法为这条序列准备片段。")
    result["runtimeIssues"] = issues
    if result["ready"]:
        parent = root / "dataflow_output" / task
        result["suggestedOut"] = str(next_sequence_output(parent, result["sequenceName"], reserved))
    return result


def next_sequence_output(parent, name, reserved=()):
    receipts = Path.home() / ".local/state/dataflow-qwen-gencep/workbench"
    for index in range(1, 10000):
        leaf = name if index == 1 else f"{name}-{index:02}"
        target = parent / leaf
        identity = sha256(str(target.resolve()).encode()).hexdigest()[:32]
        if str(target) not in reserved and not target.exists() and not target.is_symlink() and not (receipts / identity).exists():
            return target
    raise ValueError("此序列的同名输出过多，请手动填写新的输出路径。")


def prepare_dataset_launch(root, payload):
    from datasets_catalog import FUSION_OVERLAP, materialize, registered_root
    task = graph_task(payload["graph"])
    checked_output(payload["out"], root, task)
    issues = dataset_runtime_issues(root, payload["flags"], task)
    if issues:
        raise ValueError(" ".join(issues))
    choice = payload["dataset"]
    selection = choice["selection"]
    manifest = materialize(registered_root(root, selection.get("sourceRoot")), selection,
                           ROOT / ".local/dataset-inputs", choice["fingerprint"], task=task,
                           overlap=FUSION_OVERLAP if "--workbench-fuse" in payload["flags"] else 0)
    return dict(payload, input=str(manifest))


def check_queue_dataset(server, graph, choice, label):
    result = inspect_dataset(server.dataflow_root, choice["selection"], graph)
    issues = [*result["issues"], *result["runtimeIssues"]]
    if not result["ready"] or result["fingerprint"] != choice["fingerprint"] or issues:
        detail = " ".join(issues) or "素材已变化，请重新检查这条序列。"
        raise ValueError(f"{label}：{detail}")
    if "sequenceName" in choice and choice["sequenceName"] != result["sequenceName"]:
        raise ValueError(f"{label}的序列名已变化，请重新检查。")
    if "clipPlan" in choice and choice["clipPlan"] != result["clipPlan"]:
        raise ValueError(f"{label}的片段计划已变化，请重新检查。")


def submit_single_launch(server, body):
    payload = compile_request({key: value for key, value in body.items() if key != "requestId"})
    graph = payload.pop("graph")
    target = checked_output(payload["out"], server.dataflow_root, graph_task(graph))
    payload["out"] = str(target)

    def preflight():
        if target.exists():
            raise ValueError("输出目录已存在，请使用新目录。")
        if "dataset" in payload:
            check_queue_dataset(server, graph, payload["dataset"], "所选序列")

    # Older clients may retry the same POST without an explicit request ID.
    signature = json.dumps([graph, payload], sort_keys=True, ensure_ascii=False)
    request_id = body.get("requestId", str(UUID(sha256(signature.encode()).hexdigest()[:32])))
    queue = server.queue.submit(request_id, graph, [payload], preflight=preflight)
    return {"ok": True, "queue": queue, "output": str(target)}


def submit_dataset_queue(server, body):
    if server.queue is None:
        raise ValueError("请从 amax 工作台提交序列队列。")
    if set(body) - {"requestId", "graph", "datasets", "out", "allowNativeCode"}:
        raise ValueError("队列提交格式有误。")
    choices = body.get("datasets")
    if not isinstance(choices, list) or not 1 <= len(choices) <= 200:
        raise ValueError("请选择 1～200 条序列。")
    base = body.get("out")
    if not isinstance(base, str) or not Path(base).is_absolute() or ".." in Path(base).parts:
        raise ValueError("请填写有效的新队列输出目录。")
    graph = body.get("graph")
    items = []
    used_names = set()
    for idx, choice in enumerate(choices, 1):
        payload = compile_request(dict(graph=graph, allowNativeCode=body.get("allowNativeCode"),
                                       dataset=choice, out=base))
        if idx == 1:
            parent = checked_output(base, server.dataflow_root, graph_task(graph))
        selection = choice["selection"]
        if not isinstance(selection.get("id"), str) or not selection["id"]:
            raise ValueError(f"队列第 {idx} 项缺少序列。")
        if "sequenceName" in choice:
            name = leaf = choice["sequenceName"]
            version = 1
            while leaf in used_names:
                version += 1
                leaf = f"{name}-{version:02}"
        else:
            name = re.sub(r"[^A-Za-z0-9_.-]+", "-", selection["id"].split("/")[-1]).strip(".-")[:70] or "sequence"
            leaf = f"{idx:03d}-{name}"
        used_names.add(leaf)
        items.append(dict(dataset=choice, flags=payload["flags"], out=str(parent / leaf)))

    def preflight():
        if parent.exists():
            raise ValueError("输出目录已存在，请使用新目录。")
        for idx, item in enumerate(items, 1):
            choice = item["dataset"]
            check_queue_dataset(server, graph, choice, f"队列第 {idx} 项 {choice['selection']['id']}")
    return server.queue.submit(body.get("requestId"), graph, items, preflight=preflight)


def submit_resume(server, body):
    if set(body) != {"id", "requestId"} or not isinstance(body["id"], str) or not re.fullmatch(r"launch-[0-9a-f]{32}", body["id"]):
        raise ValueError("请选择一条可续跑的服务器任务。")
    try:
        attempt = UUID(body["requestId"])
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("续跑请求标识无效。") from error
    for queue in server.queue.list():
        if queue["id"] == str(attempt):
            item, = queue["items"]
            if body["id"] == "launch-" + sha256(item["out"].encode()).hexdigest()[:32]:
                return queue
    inventory = remote("snapshots", dataflow_root=server.dataflow_root)
    state = next((item for item in inventory["items"]
                  if item.get("receipt", {}).get("answer", {}).get("id") == body["id"]), None)
    if not state or not state.get("resumeCandidate"):
        raise ValueError("任务当前没有可用的 ABS 续跑检查点，或已经在继续执行。")
    record = state["receipt"]
    graph = record["graph"]
    flags = compile_graph(graph, reviewed=True)
    if "--abs" not in flags:
        raise ValueError("原任务未启用 ABS 评分。")
    target = checked_output(state["output"], server.dataflow_root, graph_task(graph))
    candidate = state["resumeCandidate"]
    folder = target / candidate["group"] if candidate["group"] else target
    checkpoint = folder / "dataflow_step2.jsonl"
    item = dict(out=str(target), input=str(checkpoint), resumeAttempt=attempt.hex, flags=flags,
                checkpoint=dict(candidate, sha256=sha256(checkpoint.read_bytes()).hexdigest()))
    if "dataset" in record:
        # The original dataset choice stays attached to progress via its launch receipt.
        item["resumeSequence"] = record["dataset"].get("sequenceName") or target.name
    return server.queue.submit(str(attempt), graph, [item])


# These functions run in the DataFlow environment alongside its progress monitor.
def remote_launch(payload):
    import hashlib
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    from run_progress import STATE, process_start

    source = Path(payload["input"]).resolve(strict=True)
    task = graph_task(payload["graph"])
    target = checked_output(payload["out"], Path.cwd(), task)
    if not source.is_file() or source.suffix.lower() != ".jsonl":
        raise ValueError("输入必须是现有 JSONL 清单。")
    with source.open(encoding="utf-8") as f:
        count = 0
        for line_no, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("task") != task:
                    raise ValueError(f"输入清单第 {line_no} 行目标不匹配。所有记录均须声明 task={task}。")
                count += 1
        if not count:
            raise ValueError("输入清单为空。")
    flags = payload["flags"]
    if "--abs" in flags:
        config = Path.home() / "scoring_scripts/abs/api_config.json"
        key_name = json.loads(config.read_text(encoding="utf-8"))["api"]["key_env"]
        if not os.environ.get(key_name):
            raise ValueError("当前服务器进程环境未配置评分所需的密钥。请在服务器环境配置后重新启动。")
    identity = hashlib.sha256(str(target).encode()).hexdigest()[:32]
    work = STATE / "workbench" / identity
    clean = dict(payload, input=str(source), out=str(target))
    signature = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    receipt = work / "launch.json"
    if receipt.exists():
        old = json.loads(receipt.read_text(encoding="utf-8"))
        if old["signature"] == signature:
            if old.get("spawn_failed"):
                raise ValueError("此前启动已失败。请查看服务器启动日志或选择新输出目录。")
            return old["answer"]
        raise ValueError("该输出路径已经提交过其他参数。请选择新目录。")
    if target.exists():
        raise ValueError("输出目录已经存在。请选择新的输出目录。")
    snapshot = None
    dataset_runtime = "dataset" in payload
    entry = "extensions/qwen_gencep/workbench_batch.py"
    if not dataset_runtime:
        flags = [*flags, "--workbench-manifest"]
    if any("implementation" in node for node in payload["graph"]["nodes"]):
        sys.path.insert(0, str(Path.cwd() / "flow-studio"))
        from operator_sources import native_snapshot
        snapshot = native_snapshot(Path.cwd(), payload["graph"]["nodes"])
    work.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    work.mkdir(mode=0o700)
    log = work / "launcher.log"
    answer = {"ok": True, "id": "launch-" + identity, "requestId": identity,
              "output": str(target), "log": str(log)}
    record = {"signature": signature, "graph": payload["graph"], "answer": answer, "started": time.time()}
    if "dataset" in payload:
        record["dataset"] = payload["dataset"]
    def save():
        draft = receipt.with_suffix(".tmp")
        draft.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        draft.replace(receipt)
    save()
    try:
        if snapshot is not None:
            implementation_dir = work / "source"
            implementation_dir.mkdir(mode=0o700)
            for filename, text in snapshot.items():
                (implementation_dir / filename).write_text(text, encoding="utf-8")
            entry = str(implementation_dir / Path(entry).name)
        with log.open("xb") as stream:
            command = [sys.executable, "-B", entry,
                       "--input", str(source), "--out", str(target), *flags]
            env = dict(os.environ, FLOW_STUDIO_NATIVE_OVERRIDES=",".join(
                node["type"] for node in payload["graph"]["nodes"] if "implementation" in node))
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=subprocess.STDOUT, start_new_session=True, env=env)
        record.update(pid=proc.pid, process_start=process_start(proc.pid))
    except OSError:
        record["spawn_failed"] = True
        raise
    finally:
        save()
    return answer


def failure_reason(log, folder, fallback, *, check_abs_retry=False):
    import re
    from pathlib import Path

    if not log or not folder:
        return fallback
    try:
        path = Path(log).resolve(strict=True)
        if not path.is_relative_to(Path(folder).resolve()) or not path.is_file():
            return fallback
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            text = stream.read(65536).decode("utf-8", "replace")
    except (OSError, ValueError):
        return fallback
    if re.search(r"CUDA out of memory|torch\.OutOfMemoryError|not enough GPU memory|insufficient GPU memory", text, re.I):
        return "GPU 显存不足，模型未能加载或完成推理。"
    if re.search(r"No space left on device", text):
        return "服务器磁盘空间不足。"
    if re.search(r"PermissionError|Permission denied", text):
        return "读取输入或写入结果时权限不足。"
    if re.search(r"AuthenticationError|Error code:\s*(401|403)\b", text):
        return "模型 API 鉴权失败，请检查服务器配置和访问权限。"
    if re.search(r"RateLimitError|Error code:\s*429\b", text):
        return True if check_abs_retry else "模型 API 请求受限（HTTP 429）。"
    if re.search(r"InternalServerError|Error code:\s*5\d\d\b", text):
        return True if check_abs_retry else "模型 API 暂时不可用。"
    if re.search(r"Connection refused|ECONNREFUSED", text, re.I):
        return True if check_abs_retry else "amax 连接模型 API 端口时被拒绝，请检查服务器网络或接口地址。"
    if re.search(r"APITimeoutError|APIConnectionError|Connection error\b|timed out\b", text, re.I):
        return True if check_abs_retry else "amax 连接模型 API 失败或请求超时，请检查服务器到接口的网络。"
    if re.search(r"Invalid data found when processing input|Error while decoding", text):
        return "视频读取或解码失败。"
    for line in reversed(text.splitlines()):
        match = re.fullmatch(r"([A-Za-z_][\w.]{1,100}(?:Error|Exception))(?::.*)?", line.strip())
        if match and match[1] not in ("RuntimeError", "subprocess.CalledProcessError"):
            return match[1]
    return fallback


def prepared_clips(output, record=None):
    import json
    from pathlib import Path
    import re

    choice = (record or {}).get("dataset", {})
    fingerprint = choice.get("fingerprint", "")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        return []
    folder = Path("flow-studio/.local/dataset-inputs") / fingerprint
    try:
        plan = json.loads((folder / "preparation.json").read_text(encoding="utf-8"))["plan"]
        with (folder / "input.jsonl").open(encoding="utf-8") as stream:
            names = [json.loads(line)["sequence_id"] for line in stream if line.strip()]
        if len(names) != plan["parts"]:
            return []
        stride = 81 - plan.get("overlap", 0)
        return [{"id": name, "index": i+1, "firstFrame": plan["first"]+stride*i,
                 "frames": min(81, plan["count"]-stride*i)} for i, name in enumerate(names)]
    except (OSError, KeyError, TypeError, ValueError):
        return []


def resume_candidate(state, record):
    from pathlib import Path

    if not record or state["status"] != "failed" or not state["rounds"]:
        return None
    if any("implementation" in node for node in record["graph"]["nodes"]):
        return None
    rnd = state["rounds"][-1]
    step = rnd.get("stages", {}).get("abs", {})
    if rnd["number"] != 0 or step.get("status") != "failed":
        return None
    root = Path(record["answer"]["output"]).resolve()
    group = rnd.get("group")
    if group not in (None, "first-part", "remaining-parts"):
        return None
    folder = root / group if group else root
    sequence = step.get("sequence")
    if not isinstance(sequence, str) or not sequence or Path(sequence).name != sequence or sequence in (".", ".."):
        return None
    clip = folder / sequence
    required = (folder / "dataflow_step2.jsonl", clip / "abs_inputs/job.json",
                clip / "abs_inputs/config.json", clip / "Absolute Score/_run/manifest.json")
    if (folder / "refinement").exists() or any(not f.is_file() or not f.resolve().is_relative_to(root) for f in required):
        return None
    if failure_reason(step.get("log"), root, None, check_abs_retry=True) is not True:
        return None
    return {"stateId": state["id"], "group": group, "sequence": sequence}


def remote_resume(payload):
    import hashlib
    import json
    import os
    from pathlib import Path
    import re
    import subprocess
    import sys
    import time
    from run_progress import STATE, process_start

    out = Path(payload["out"]).resolve(strict=True)
    identity = hashlib.sha256(str(out).encode()).hexdigest()[:32]
    directory = STATE / "workbench" / identity
    receipt = directory / "launch.json"
    record = json.loads(receipt.read_text(encoding="utf-8"))
    attempt = payload["resumeAttempt"]
    if record.get("resumeAttempt") == attempt:
        if record.get("spawn_failed"):
            raise ValueError("本次续跑进程未能启动。")
        return record["answer"]
    if record.get("pid") and record.get("process_start") and process_start(record["pid"]) == record["process_start"]:
        raise ValueError("原任务的进程仍在退出，请稍后继续。")
    candidate = payload["checkpoint"]
    state_id = candidate["stateId"]
    if not re.fullmatch(r"[0-9a-f]{32}", state_id):
        raise ValueError("原任务进度标识无效。")
    state = json.loads((STATE / "runs" / (state_id + ".json")).read_text(encoding="utf-8"))
    expected = {key: candidate[key] for key in ("stateId", "group", "sequence")}
    if state.get("output") != str(out) or resume_candidate(state, record) != expected:
        raise ValueError("此任务已不符合 ABS 检查点续跑条件。")
    group = out / candidate["group"] if candidate["group"] else out
    checkpoint = group / "dataflow_step2.jsonl"
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != candidate["sha256"]:
        raise ValueError("分割检查点已改变，保留原任务。")
    plan = directory / ("resume-" + attempt + ".json")
    log = directory / ("resume-" + attempt + ".log")
    plan.write_text(json.dumps(dict(payload, prior=state, dataset=record.get("dataset")), ensure_ascii=False), encoding="utf-8")
    record.update(resumeAttempt=attempt, resumeStarted=time.time(), pid=None, process_start=None, spawn_failed=False)
    record["answer"]["log"] = str(log)

    def save():
        draft = receipt.with_suffix(".tmp")
        draft.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        draft.replace(receipt)

    save()
    try:
        with log.open("xb") as stream:
            process = subprocess.Popen([sys.executable, "-B", "extensions/qwen_gencep/abs_resume.py", str(plan)],
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        record.update(pid=process.pid, process_start=process_start(process.pid))
    except OSError:
        record["spawn_failed"] = True
        raise
    finally:
        save()
    return record["answer"]


def remote_snapshots():
    import json
    import time
    from pathlib import Path
    from run_progress import STATE, process_start
    from watch_runs import snapshots

    receipts = {}
    for filename in (STATE / "workbench").glob("*/launch.json"):
        try:
            record = json.loads(filename.read_text(encoding="utf-8"))
            receipts[record["answer"]["output"]] = record
        except (OSError, KeyError, ValueError):
            continue
    items = []
    seen = set()
    hidden = 0
    def rows(path):
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    home = Path.home().resolve()
    for state in snapshots():
        output = Path(state["output"]).resolve()
        if home not in output.parents:
            hidden += 1
            continue
        if str(output) in seen:
            continue
        record = receipts.get(str(output))
        if record and record.get("resumeAttempt") and record["resumeAttempt"] != state.get("resumeAttempt"):
            continue
        receipts.pop(str(output), None)
        seen.add(str(output))
        try:
            source = next((output / n for n in ("input.jsonl", "abs_step1.jsonl", "dataflow_step2.jsonl")
                           if (output / n).is_file()), None)
            if not record:
                tasks = {r.get("task") for r in rows(source)} if source else set()
                if tasks not in ({"hands"}, {"objects"}):
                    hidden += 1
                    continue
                state["task"] = next(iter(tasks))
        except (ValueError, OSError, AttributeError):
            hidden += 1
            continue
        state["has_input"] = any((folder / "input.jsonl").is_file()
                                 for folder in (output, output / "first-part", output / "remaining-parts"))
        if record:
            state["receipt"] = record
            state["clips"] = prepared_clips(output, record)
            state["resumeCandidate"] = resume_candidate(state, record)
        for rnd in state["rounds"]:
            for step in rnd["stages"].values():
                if step["status"] == "failed":
                    step["reason"] = failure_reason(step.get("log"), output, step.get("error"))
                    for part in step.get("parts", {}).values():
                        if part["status"] == "failed":
                            part["reason"] = failure_reason(part.get("log"), output, step["reason"])
            base = output / rnd["group"] if rnd.get("group") in ("first-part", "remaining-parts") else output
            segmented = base / "dataflow_step2.jsonl"
            if rnd["number"] == 0 and segmented.is_file():
                try:
                    results = {row["sequence_id"]: row["segmentation_status"] for row in rows(segmented)}
                    for name, step in rnd["stages"].items():
                        if name == "genception" and step["status"] in ("done", "skipped") and not step.get("parts"):
                            step["parts"] = {
                                sequence: {"status": "skipped", "detail": "Qwen 未识别到目标，跳过分割"}
                                if result == "no_targets" else {"status": "done", "detail": "掩码已生成"}
                                for sequence, result in results.items() if result in ("no_targets", "segmented")
                            }
                        if name == "qwen" and step["status"] == "done" and not step.get("parts"):
                            step["parts"] = {sequence: {"status": "done", "detail": "提词和复核完成"}
                                             for sequence in results}
                except (OSError, KeyError, ValueError, TypeError):
                    pass
            feedback = base / "refinement" / f"round_{rnd['number']:02d}"
            evidence = set()
            file = next((feedback / n for n in ("feedback_step2.jsonl", "feedback_step1.jsonl")
                         if (feedback / n).is_file()), None)
            if file is not None:
                try:
                    for row in rows(file):
                        if row.get("revisions"):
                            evidence.add("retry")
                        for obj in row.get("refinement", []):
                            history = obj.get("history") or []
                            if not history or history[-1].get("round") != rnd["number"]:
                                continue
                            if obj.get("status") == "passed":
                                evidence.add("pass")
                            if obj.get("status") in ("needs_review", "below_threshold"):
                                evidence.add("review")
                except (OSError, ValueError, AttributeError):
                    pass
            rnd["branches"] = sorted(evidence)
        items.append(state)
    for record in receipts.values():
        alive = record.get("pid") and record.get("process_start") and process_start(record["pid"]) == record["process_start"]
        started = record.get("resumeStarted", record["started"])
        pending = not record.get("spawn_failed") and (alive or (not record.get("pid") and time.time() - started < 15))
        reason = None
        if not pending:
            log = record["answer"].get("log")
            reason = failure_reason(log, STATE / "workbench", None)
        items.append({"id": "", "output": record["answer"]["output"], "started": started,
                      "status": "running" if pending else "failed", "rounds": [], "receipt": record,
                      "clips": prepared_clips(record["answer"]["output"], record), "reason": reason,
                      "resumeAttempt": record.get("resumeAttempt")})
    return {"items": items, "otherRuns": hidden}


def remote(operation, payload=None, *, dataflow_root=None):
    func = {"snapshots": remote_snapshots, "launch": remote_launch, "resume": remote_resume}[operation]
    # Only the fixed bootstrap is sent through the remote shell. User data is
    # encoded as a Python string literal on stdin, never as shell commands.
    call = "remote_snapshots()" if operation == "snapshots" else f"{func.__name__}(json.loads({json.dumps(payload)!r}))"
    source = ("import json, sys\nfrom pathlib import Path\nsys.path.insert(0, 'extensions/qwen_gencep')\n" +
              inspect.getsource(graph_task) + "\n" + inspect.getsource(checked_output) + "\n" +
              inspect.getsource(failure_reason) + "\n" +
              inspect.getsource(prepared_clips) + "\n" +
              inspect.getsource(resume_candidate) + "\n" +
              inspect.getsource(func) + "\ntry:\n print(json.dumps({'data': " + call +
              "}, ensure_ascii=True))\nexcept (OSError, ValueError, KeyError) as e:\n"
              " print(json.dumps({'error': str(e)}, ensure_ascii=True))\n")
    if dataflow_root is None:
        command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "5090",
                   f"cd {REMOTE} && .venv/bin/python -B -"]
        backend = "SSH 5090"
    else:
        command = [str(dataflow_root / ".venv/bin/python"), "-B", "-"]
        backend = "服务器 DataFlow"
    try:
        result = subprocess.run(command, cwd=dataflow_root, input=source,
                                text=True, encoding="utf-8", errors="replace",
                                capture_output=True, timeout=24)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ConnectionError(f"{backend} 不可用或请求超时，请检查连接后刷新。") from e
    if result.returncode:
        detail = "读取任务进度失败" if operation == "snapshots" else "启动请求未获确认"
        raise ConnectionError(f"{backend} {detail}。")
    reply = json.loads(result.stdout)
    if "error" in reply:
        raise ValueError(reply["error"])
    return reply["data"]


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def amax_queue_request(path, body=None):
    method = "GET" if body is None else "POST"
    # Path and method come from the fixed workbench routes. JSON travels on stdin.
    command = (f"curl -sS --max-time 30 --unix-socket {REMOTE}/flow-studio/.local/workbench.sock "
               f"-X {method} -H 'Origin: http://localhost' -w '\\n%{{http_code}}' ")
    if method == "POST":
        command += "-H 'Content-Type: application/json' --data-binary @- "
    command += f"http://localhost{path}"
    try:
        result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "5090", command],
                                input=json.dumps(body) if body is not None else "",
                                text=True, encoding="utf-8", capture_output=True, timeout=36, check=True)
        payload, code = result.stdout.rsplit("\n", 1)
        return int(code), json.loads(payload)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ConnectionError("amax 工作台队列暂不可用，请打开服务器工作台查看提交状态。") from error


def clip_progress(state):
    groups = state.get("workbenchGroups", {})
    rounds = state["rounds"]
    clips = state.get("clips", [])
    indexes = {clip["id"]: clip["index"] for clip in clips}
    for clip in clips:
        group = "first-part" if clip["index"] == 1 else "remaining-parts"
        name = clip["id"]
        steps = []
        for rnd in rounds:
            if rnd.get("group") and rnd["group"] != group:
                continue
            for key, step in rnd["stages"].items():
                if key not in STAGES:
                    continue
                detail = step.get("parts", {}).get(name)
                if not detail and step.get("sequence") == name:
                    detail = step
                if (not detail and not rnd.get("group") and not step.get("parts") and
                        step.get("status") == "done" and rnd["number"] == 0 and key == "qwen"):
                    detail = {"status": "done"}
                if (not detail and not rnd.get("group") and not step.get("parts") and key == "qwen" and
                        step.get("status") == "running" and indexes.get(step.get("sequence"), 0) > clip["index"]):
                    detail = {"status": "done"}
                if detail:
                    status = detail["status"]
                    if status == "running" and step.get("sequence") == name and step["status"] in ("failed", "interrupted"):
                        status = step["status"]
                    if step["status"] in ("done", "skipped") and status == "waiting":
                        status = "skipped"
                    steps.append({"stage": LABELS[STAGES[key]], "round": rnd["number"] + 1, "status": status,
                                  "detail": detail.get("reason") or (step.get("reason") if status == "failed" else None)
                                  or detail.get("detail")})
        status = state["status"]
        if groups.get(group) == "done" or status == "done":
            clip["status"] = "done"
        elif any(step["status"] in ("failed", "interrupted") for step in steps):
            clip["status"] = next(step["status"] for step in steps if step["status"] in ("failed", "interrupted"))
        elif any(step["detail"] in ("Qwen 未识别到目标，跳过分割", "无待分割目标") for step in steps):
            clip["status"] = "skipped"
        elif status in ("failed", "interrupted"):
            clip["status"] = "interrupted" if any(step["status"] == "running" for step in steps) else "paused"
        else:
            clip["status"] = "running" if any(step["status"] == "running" for step in steps) else "waiting"
        clip["steps"] = steps
    return clips


def convert(state):
    record = state.get("receipt", {})
    graph = record.get("graph")
    if graph:
        node_ids = {n["type"]: n["id"] for n in graph["nodes"]}
        types_by_id = {n["id"]: n["type"] for n in graph["nodes"]}
        edges = {(types_by_id[e["from"]], types_by_id[e["to"]], e.get("condition", "next")): e["id"]
                 for e in graph["edges"]}
    else:
        edges = EDGE_IDS
    active, events, states = [], [], {}
    enabled = {"output"}
    location = {}
    if state.get("has_input"):
        enabled.add("input")
    previous = {}
    group = None
    def mark(a, b, outcome="next"):
        token = edges.get((a, b, outcome))
        if token and token not in active:
            active.append(token)
    for rnd in state["rounds"]:
        current_group = rnd.get("group")
        if current_group != group:
            group = current_group
            states.clear()
            active.clear()
            previous.clear()
            location.clear()
        group_label = {"first-part": "首段 · ", "remaining-parts": "其余片段 · "}.get(group, "")
        num = rnd["number"]
        stages = {STAGES[k]: v for k, v in rnd["stages"].items() if k in STAGES}
        for key, val in stages.items():
            if val.get("started") or val["status"] != "skipped":
                enabled.add(key)
            status = {"pending": "waiting", "interrupted": "failed"}.get(val["status"], val["status"])
            if not (num and key == "qwen"):
                states[key] = status
            if status == "running":
                location = {"sequence": val.get("sequence"), "detail": val.get("detail"), "log": val.get("log")}
            stage_label = ("整条序列 · " if key == "fusion" else f"{group_label}第 {num + 1} 轮 · ") + LABELS[key]
            if val.get("started"):
                events.append({"time": stamp(val["started"]), "nodeId": key, "message": f"{stage_label} 开始"})
            if val.get("finished"):
                desc = {"done": "完成", "failed": "失败", "interrupted": "中断", "skipped": "跳过"}.get(val["status"], val["status"])
                reason = val.get("reason") or val.get("error")
                message = f"{stage_label}{desc}"
                if val["status"] == "failed" and reason:
                    message += f" · {reason}"
                events.append({"time": stamp(val["finished"]), "nodeId": key, "message": message})
        if state.get("has_input") and stages.get("qwen", {}).get("started"):
            states["input"] = "done"
            mark("input", "qwen")
        if num and previous.get("refine", {}).get("worked") and stages.get("segment", {}).get("started"):
            mark("refine", "segment")
        for a, b in (("qwen", "segment"), ("segment", "score"), ("score", "gate")):
            if stages.get(a, {}).get("status") == "done" and stages.get(b, {}).get("started"):
                mark(a, b)
        for branch in rnd.get("branches", []):
            mark("gate", "refine" if branch == "retry" else "fusion" if ("fusion", "output", "next") in edges
                 else "output", branch)
        if stages.get("refine", {}).get("worked"):
            mark("gate", "refine", "retry")
        previous = stages
    status = {"done": "completed", "running": "running", "interrupted": "interrupted"}.get(state["status"], "failed")
    if status == "running" and record and not state.get("has_input") and "running" not in states.values():
        states["input"] = "running"
        selection = record.get("dataset", {})
        location = {"sequence": selection.get("sequenceName"), "detail": "正在核验输入视频，等待提词开始"}
    start, end = stamp(state["started"]), stamp(state.get("finished"))
    if status == "completed":
        states["output"] = "done"
        for key in ("fusion", "score", "segment"):
            if states.get(key) == "done":
                mark(key, "output")
    if "fusion" in states and "gate" not in states:
        for key in ("score", "segment"):
            mark(key, "fusion")
    events.insert(0, {"time": start, "nodeId": "input", "message": "服务器已接收启动请求" if record else "服务器记录开始"})
    if end:
        desc = {"completed": "完成", "failed": "失败", "interrupted": "中断"}.get(status, "结束")
        events.append({"time": end, "nodeId": "output", "message": "服务器任务" + desc})
    elif status == "failed" and not state["rounds"]:
        reason = state.get("reason")
        events.append({"time": start, "nodeId": "input", "message": f"启动失败 · {reason}" if reason else
                       "启动进程已退出，未生成算子记录。请查看服务器启动日志。"})
    if graph:
        states = {node_ids[key]: value for key, value in states.items() if key in node_ids}
        for event in events:
            event["nodeId"] = node_ids.get(event["nodeId"])
    result = {"id": record.get("answer", {}).get("id", state["id"]), "pipelineId": graph["id"] if graph else "hand-seg",
              "mode": "live", "name": state.get("name") or PurePosixPath(state["output"]).name,
              "status": status, "startedAt": start, "endedAt": end, "current": next((k for k, v in states.items() if v == "running"), None),
              "nodeStates": states, "activeEdges": active, "events": sorted(events, key=lambda e: e["time"]),
              "enabledTypes": sorted(enabled), "output": state["output"],
              "currentActivity": location, "clips": clip_progress(state),
              "round": state["rounds"][-1]["number"] if state["rounds"] else None}
    if "fusion" in state:
        result["fusion"] = state["fusion"]
    if graph:
        result.update(graph=graph, version=graph.get("version"), log=record["answer"]["log"], task=graph_task(graph))
        if "dataset" in record:
            result["dataset"] = record["dataset"]
        if state.get("resumeCandidate"):
            result["canResume"] = True
    elif state.get("task"):
        result["task"] = state["task"]
    return result


def validate_library(data):
    if (not isinstance(data, dict) or set(data) != {"graphs", "drafts", "custom"}
            or not isinstance(data["graphs"], list) or not isinstance(data["drafts"], dict)
            or not isinstance(data["custom"], list)):
        raise ValueError("目录只接受流水线、草稿和算子模板。")
    def fields(obj, allowed):
        if not isinstance(obj, dict) or set(obj) - allowed:
            raise ValueError("目录含有不支持的字段，请只保存编排定义。")
    def settings(config):
        fields(config, {"task", "sample_fps", "every", "threshold", "maxRetries", "sourceKey",
                        "targetKey", "operation", "key", "rule", "value"})
        if any(v is not None and type(v) not in (str, int, float, bool) for v in config.values()):
            raise ValueError("算子参数应为简单值。")
    def operator_metadata(item):
        if "notes" in item:
            notes = item["notes"]
            fields(notes, {"input", "logic", "model", "output"})
            if any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in notes.values()):
                raise ValueError("算子说明每项需要 1～2000 个字符。")
        value = item.get("savedAt")
        if value is None:
            return
        if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
            raise ValueError("算子的保存时间必须是 UTC 时间。")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    for graph in [*data["graphs"], *data["drafts"].values()]:
        fields(graph, {"id", "name", "version", "updatedAt", "description", "nodes", "edges"})
        if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
            raise ValueError("流水线必须包含节点和连线列表。")
        for node in graph["nodes"]:
            fields(node, {"id", "type", "name", "x", "y", "config", "implementation", "savedAt", "notes"})
            operator_metadata(node)
            settings(node.get("config", {}))
            if "implementation" in node:
                from operator_sources import implementation_shape
                implementation_shape(node.get("type"), node["implementation"])
        for edge in graph["edges"]:
            fields(edge, {"id", "from", "to", "condition", "label"})
    for item in data["custom"]:
        fields(item, {"id", "name", "type", "config", "implementation", "savedAt", "notes"})
        operator_metadata(item)
        settings(item.get("config", {}))
        if "implementation" in item:
            from operator_sources import implementation_shape
            implementation_shape(item.get("type"), item["implementation"])


def read_library(path):
    try:
        with path.open(encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 1:
            raise ValueError()
        validate_library(value["data"])
        return value
    except FileNotFoundError:
        return {"revision": 0, "data": None}
    except (KeyError, ValueError, RecursionError) as e:
        raise OSError("本机目录文件格式异常，原文件已保留。") from e


@contextmanager
def library_write_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        # Closing the handle also releases this process' filesystem lock.
        yield


def save_library(path, body):
    if not isinstance(body, dict) or set(body) != {"expectedRevision", "data"}:
        raise ValueError("目录存储请求格式无效。")
    expected = body["expectedRevision"]
    if type(expected) is not int or expected < 0:
        raise ValueError("目录修订号无效。")
    data = body["data"]
    validate_library(data)
    with library_write_lock(path):
        revision = read_library(path)["revision"]
        if expected != revision:
            return 409, {"error": "其他窗口已更新本机目录，请重新载入后合并改动。", "revision": revision}
        content = json.dumps({"revision": revision + 1, "data": data}, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
        fd, name = tempfile.mkstemp(dir=path.parent, prefix="library-", suffix=".tmp")
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return 200, {"ok": True, "revision": revision + 1}


def read_task_presets():
    try:
        items = json.loads(TASK_PRESETS.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("Expected a list")
        return items
    except FileNotFoundError:
        return []
    except ValueError as error:
        raise OSError("待办文件无法读取。") from error


def task_preset_summary(item):
    graph = item["graph"]
    current = latest_preset_graph(graph)
    effective = {node["type"]: node.get("config", {}) for node in preset_overrides(current, graph)["nodes"]}
    return dict(id=item["id"], name=item["name"], pipelineId=graph["id"], task=graph_task(graph),
                createdAt=item["createdAt"], requiresReview=any("implementation" in n for n in current["nodes"]),
                version=current["version"],
                absEvery=effective["score"].get("every", 6) if "score" in effective else None,
                threshold=effective["gate"].get("threshold", 7) if "gate" in effective else None,
                sequences=[choice.get("sequenceName") or choice["selection"]["id"] for choice in item["datasets"]])


def save_task_preset(body):
    if set(body) != {"name", "graph", "datasets"}:
        raise ValueError("请提供待办名称、流水线和数据集序列。")
    name, graph, choices = body["name"], body["graph"], body["datasets"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("请输入 1～80 个字符的待办名称。")
    if not isinstance(choices, list) or not 1 <= len(choices) <= 200:
        raise ValueError("请先检查并选定至少一条序列。")
    for choice in choices:
        compile_request(dict(graph=graph, dataset=choice, out="/preview", allowNativeCode=True))
        if not isinstance(choice["selection"].get("id"), str) or not choice["selection"]["id"]:
            raise ValueError("待办里缺少序列标识。")
    item = dict(id=str(uuid4()), name=name.strip(), graph=graph, datasets=choices,
                createdAt=datetime.now(timezone.utc).isoformat())
    with library_write_lock(TASK_PRESETS):
        items = read_task_presets()
        if any(p["graph"]["id"] == graph["id"] and p["name"].casefold() == item["name"].casefold() for p in items):
            raise ValueError("这条流水线已有同名待办，请换个名称。")
        items.append(item)
        fd, filename = tempfile.mkstemp(dir=TASK_PRESETS.parent, prefix=".task-presets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(items, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, TASK_PRESETS)
        finally:
            Path(filename).unlink(missing_ok=True)
    return task_preset_summary(item)


def latest_preset_graph(fallback, browser=None):
    saved = read_library(LIBRARY)["data"]
    current = next((graph for graph in saved["graphs"] if graph["id"] == fallback["id"]), None) if saved else None
    if browser is not None:
        if (not isinstance(browser, dict) or browser.get("id") != fallback["id"] or
                type(browser.get("version")) is not int):
            raise ValueError("选择的预设与当前流水线不一致。")
        if current is None or browser["version"] > current["version"]:
            current = browser
    return current or fallback


def preset_overrides(current, preset):
    configs = {node["type"]: node.get("config", {}) for node in preset["nodes"]}
    keys = {"input": "task", "score": "every", "gate": "threshold"}
    nodes = []
    for node in current["nodes"]:
        key = keys.get(node["type"])
        original = configs.get(node["type"], {})
        if key in original:
            node = dict(node, config={**node.get("config", {}), key: original[key]})
        nodes.append(node)
    return dict(current, nodes=nodes)


def start_task_preset(server, body):
    if set(body) - {"id", "requestId", "allowNativeCode", "graph"}:
        raise ValueError("待办启动格式有误。")
    preset = next((p for p in read_task_presets() if p["id"] == body.get("id")), None)
    if preset is None:
        raise ValueError("找不到这个待办。")
    try:
        request_id = UUID(body.get("requestId")).hex
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("待办启动请求标识无效。") from error
    graph = preset_overrides(latest_preset_graph(preset["graph"], body.get("graph")), preset["graph"])
    choices = preset["datasets"]
    old_flags = compile_graph(preset["graph"], reviewed=True)
    new_flags = compile_graph(graph, reviewed=True)
    if ("--workbench-fuse" in old_flags) != ("--workbench-fuse" in new_flags):
        refreshed = []
        for choice in choices:
            label = choice.get("sequenceName", "预设序列")
            prior = inspect_dataset(server.dataflow_root, choice["selection"], preset["graph"])
            if prior["fingerprint"] != choice["fingerprint"]:
                raise ValueError(f"{label}：原输入材料有变化，请重新检查并保存预设。")
            result = inspect_dataset(server.dataflow_root, choice["selection"], graph)
            if not result["ready"]:
                raise ValueError(f"{label}：" + " ".join(result["issues"]))
            refreshed.append(dict(choice, **{key: result[key] for key in ("fingerprint", "clipPlan", "sequenceName")}))
        choices = refreshed
    parent = server.dataflow_root / "dataflow_output" / graph_task(graph)
    args = dict(graph=graph, requestId=body["requestId"], allowNativeCode=body.get("allowNativeCode"))
    if len(choices) == 1:
        name = choices[0].get("sequenceName", "sequence")
        return submit_single_launch(server, dict(args, dataset=choices[0], out=str(parent / f"{name}-{request_id[:12]}")))
    queue = submit_dataset_queue(server, dict(args, datasets=choices, out=str(parent / f"batch-{request_id[:12]}")))
    return {"ok": True, "queue": queue}


class Handler(SimpleHTTPRequestHandler):
    def address_string(self):
        return super().address_string() if self.server.address_family == socket.AF_INET else "local"

    def end_headers(self):
        path = urlsplit(self.path).path
        if not path.startswith("/api/") and (path == "/" or path.endswith((".html", ".js", ".css"))):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def local_request(self, write=False):
        # SSH port forwarding can use a different loopback port in the browser.
        host = self.headers.get("Host", "")
        match = re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::([1-9][0-9]{0,4}))?", host)
        port = int(match.group(1) or "80") if match else 0
        if not 0 < port <= 65535 or (self.server.address_family == socket.AF_INET
                                     and self.server.dataflow_root is None
                                     and port != self.server.server_address[1]):
            self.send_json(403, {"error": "只接受本机地址。"})
            return False
        origin = self.headers.get("Origin")
        if ((write or origin) and origin != "http://" + host) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_json(403, {"error": "请从本机同源工作台访问。"})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        url = urlsplit(self.path)
        path = url.path
        if path == "/api/bridge":
            root = self.server.dataflow_root
            direct = root is not None
            from abs_transport import relay_listening
            self.send_json(200, {"connected": True, "app": "flow-studio",
                                 "mode": "direct" if direct else "ssh",
                                 "host": socket.gethostname() if direct else "5090",
                                 "absRelay": direct and relay_listening(),
                                 "outputRoot": str((root or PurePosixPath(REMOTE)) / "dataflow_output")})
        elif path == "/api/library":
            try:
                self.send_json(200, read_library(LIBRARY))
            except OSError as e:
                self.send_json(503, {"error": f"本机目录读取失败：{e}"})
        elif path in ("/api/pipeline-doc", "/api/pipeline-doc/image"):
            from pipeline_docs import read_doc, read_image
            query = parse_qs(url.query)
            pipeline_id = query.get("id", [""])[0]
            try:
                if path == "/api/pipeline-doc":
                    return self.send_json(200, read_doc(DOCS, pipeline_id))
                raw, mime = read_image(DOCS, pipeline_id, query.get("file", [""])[0])
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except FileNotFoundError:
                self.send_json(404, {"error": "此文档的图片不存在。"})
            except ValueError as e:
                self.send_json(400, {"error": str(e)})
            except OSError as e:
                self.send_json(503, {"error": f"说明文档读取失败：{e}"})
        elif path == "/api/gpu":
            if self.server.dataflow_root is None:
                return self.send_json(409, {"error": "请从 amax 工作台查看服务器 GPU。"})
            try:
                from gpu_status import gpu_snapshot
                self.send_json(200, gpu_snapshot())
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                self.send_json(503, {"error": str(error)})
        elif path == "/api/queues":
            try:
                answer = (200, self.server.queue.view()) if self.server.queue else amax_queue_request("/api/queues")
                self.send_json(*answer)
            except ConnectionError as e:
                self.send_json(503, {"error": str(e), "queues": []})
        elif path == "/api/task-presets":
            try:
                if self.server.queue is None:
                    return self.send_json(*amax_queue_request("/api/task-presets"))
                self.send_json(200, {"presets": [task_preset_summary(p) for p in read_task_presets()]})
            except OSError as e:
                self.send_json(503, {"error": str(e)})
        elif path == "/api/datasets":
            root = self.server.dataflow_root
            if root is None:
                return self.send_json(409, {"error": "请在 amax 工作台中浏览服务器数据集。"})
            from datasets_catalog import catalog, registered_root, registered_roots
            keys = parse_qs(url.query, keep_blank_values=True).get("root", ["yanghonglei"])
            sources = registered_roots(root)
            try:
                if len(keys) != 1 or not keys[0]:
                    raise ValueError("请选择一个已登记的数据目录。")
                listing = catalog(registered_root(root, keys[0]))
                self.send_json(200, dict(listing, sourceRoot=keys[0], roots=sources))
            except (ValueError, OSError) as e:
                self.send_json(400, {"error": f"数据集目录不可用：{e}", "roots": sources})
        elif path in ("/api/operators/agent", "/api/operators/source"):
            root = self.server.dataflow_root
            if root is None:
                return self.send_json(409, {"error": "请通过 amax 工作台访问代码编辑器。"})
            try:
                if path.endswith("/agent"):
                    from operator_agent import status
                    result = status(root)
                else:
                    from operator_sources import source_for
                    q = parse_qs(url.query)
                    result = source_for(root, q.get("type", [""])[0], q.get("kind", [""])[0])
                self.send_json(200, result)
            except ValueError as e:
                self.send_json(400, {"error": str(e)})
            except (ImportError, OSError) as e:
                self.send_json(503, {"error": f"服务器算子组件不可用：{e}"})
        elif path == "/api/live":
            try:
                if self.server.dataflow_root is None:
                    return self.send_json(*amax_queue_request("/api/live"))
                with self.server.cache_lock:
                    if not self.server.cached or time.monotonic() - self.server.cached[0] > 3:
                        data = remote("snapshots", dataflow_root=self.server.dataflow_root)
                        runs = sorted((convert(s) for s in data["items"]), key=lambda r: r["startedAt"], reverse=True)
                        self.server.cached = time.monotonic(), {"runs": runs, "otherRuns": data["otherRuns"]}
                    answer = self.server.cached[1]
                self.send_json(200, dict(answer, **self.server.queue.view()))
            except (ConnectionError, ValueError, KeyError, OSError, TypeError) as e:
                self.send_json(503, {"error": str(e), "runs": []})
        elif path.startswith("/api/"):
            self.send_json(404, {"error": "接口不存在。"})
        else:
            super().do_GET()

    def do_POST(self):
        if not self.local_request(write=True):
            return
        operator = self.path in ("/api/operators/design", "/api/operators/validate", "/api/operators/run")
        url = urlsplit(self.path)
        doc = url.path in ("/api/pipeline-doc", "/api/pipeline-doc/image")
        queue_routes = ("/api/queues", "/api/queues/order", "/api/task-presets", "/api/task-presets/start", "/api/runs/resume")
        if not operator and not doc and self.path not in ("/api/launch", "/api/library", "/api/datasets/inspect", *queue_routes):
            return self.send_json(404, {"error": "接口不存在。"})
        if operator and self.server.dataflow_root is None:
            return self.send_json(409, {"error": "请通过 amax 工作台操作 Python 算子。"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = LIBRARY_LIMIT if self.path in ("/api/library", "/api/launch", *queue_routes) else 2 * 1024 * 1024
            if doc:
                from pipeline_docs import MAX_IMAGE_BYTES, RevisionConflict, save_doc, save_image
                pipeline_id = parse_qs(url.query).get("id", [""])[0]
                if url.path.endswith("/image"):
                    limit = MAX_IMAGE_BYTES
            if length > limit:
                self.send_json(413, {"error": f"请求超过 {limit // 1024} KiB。"})
                # Windows may reset the connection and discard the 413 while
                # a browser still transmits its body. Wind down a bounded tail.
                self.connection.shutdown(socket.SHUT_WR)
                self.connection.settimeout(1)
                remaining = min(length, limit * 2)
                try:
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 65536))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except OSError:
                    pass
                return
            if doc and url.path.endswith("/image"):
                if length <= 0:
                    raise ValueError("请选择图片文件。")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("图片传输未完成。")
                return self.send_json(201, save_image(DOCS, pipeline_id, raw))
            if length <= 0 or self.headers.get_content_type() != "application/json":
                raise ValueError("请提交 JSON 对象。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请提交 JSON 对象。")
            if doc:
                try:
                    return self.send_json(200, save_doc(DOCS, pipeline_id, body, library_write_lock))
                except RevisionConflict as error:
                    return self.send_json(409, {"error": str(error), "revision": error.revision})
            if self.path == "/api/library":
                self.send_json(*save_library(LIBRARY, body))
                return
            if self.path in queue_routes:
                if self.server.dataflow_root is None:
                    return self.send_json(*amax_queue_request(self.path, body))
                if self.path == "/api/queues":
                    return self.send_json(202, {"ok": True, "queue": submit_dataset_queue(self.server, body)})
                if self.path == "/api/runs/resume":
                    return self.send_json(202, {"ok": True, "queue": submit_resume(self.server, body)})
                if self.path == "/api/task-presets":
                    return self.send_json(201, {"preset": save_task_preset(body)})
                if self.path == "/api/task-presets/start":
                    return self.send_json(202, start_task_preset(self.server, body))
                from dataset_queue import QueueOrderConflict
                try:
                    return self.send_json(200, self.server.queue.reorder(body))
                except QueueOrderConflict as error:
                    return self.send_json(409, {"error": str(error), **self.server.queue.view()})
            if self.path == "/api/datasets/inspect":
                if self.server.dataflow_root is None:
                    return self.send_json(409, {"error": "请在 amax 工作台中检查服务器序列。"})
                if not isinstance(body.get("selection"), dict) or not isinstance(body.get("graph"), dict):
                    raise ValueError("请提供所选序列和当前流水线。")
                reserved = {item["out"] for batch in self.server.queue.list() for item in batch["items"]}
                result = inspect_dataset(self.server.dataflow_root, body["selection"], body["graph"], reserved)
                return self.send_json(200, result)
            if operator:
                root = self.server.dataflow_root
                if self.path.endswith("/run"):
                    from operator_runtime import run_records
                    result = {"records": run_records(root, body["code"], body["records"], body.get("config", {}))}
                elif self.path.endswith("/design"):
                    from operator_agent import design
                    from operator_sources import implementation_shape
                    implementation_shape(body["type"], body["implementation"])
                    result = design(root, body["type"], body["implementation"], body["prompt"])
                else:
                    from operator_sources import validate
                    validate(root, body["type"], body["implementation"])
                    result = {"ok": True}
                self.send_json(200, result)
                return
            if self.server.dataflow_root is None:
                return self.send_json(*amax_queue_request("/api/launch", body))
            self.send_json(202, submit_single_launch(self.server, body))
        except (ValueError, TypeError, KeyError, RecursionError) as e:
            self.send_json(400, {"error": str(e)})
        except ConnectionError as e:
            self.send_json(503, {"error": str(e)})
        except (ImportError, RuntimeError) as e:
            self.send_json(503, {"error": str(e)})
        except OSError as e:
            self.send_json(503, {"error": f"本机存储不可用：{e}"})

    def list_directory(self, path):
        self.send_error(404)


@contextmanager
def private_listener(path, handler):
    """Only the DataFlow account and its SSH forwards can reach this socket."""
    import fcntl

    private_dir = ROOT / ".local"
    path = path.absolute()
    if path.parent != private_dir:
        raise ValueError(f"套接字必须直接位于 {private_dir} 内。")
    private_dir.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(private_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(directory).st_uid != os.getuid():
            raise ValueError("套接字目录属于其他用户。")
        os.fchmod(directory, 0o700)
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
                raise ValueError("套接字路径被其他文件占用。")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise ValueError("套接字已在使用中。")

        class UnixHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_UNIX

            def server_bind(self):
                self.socket.bind(self.server_address)
                self.server_name, self.server_port = "localhost", 0

        with UnixHTTPServer(str(path), handler) as server:
            bound = path.lstat()
            try:
                path.chmod(0o600)
                yield server
            finally:
                try:
                    if os.path.samestat(bound, path.lstat()):
                        path.unlink()
                except FileNotFoundError:
                    pass
    finally:
        os.close(directory)


def serve(server, dataflow_root):
    server.dataflow_root = dataflow_root
    server.cached = None
    server.cache_lock = threading.Lock()
    server.queue = None
    if dataflow_root is not None:
        from dataset_queue import DatasetQueue, attempt_key
        def launch(graph, item):
            payload = dict(graph=graph, **item)
            if item.get("resumeAttempt"):
                return remote("resume", payload, dataflow_root=dataflow_root)
            if "dataset" in item:
                try:
                    payload = prepare_dataset_launch(dataflow_root, payload)
                except OSError as error:
                    raise ValueError(f"准备序列失败：{error}") from error
            return remote("launch", payload, dataflow_root=dataflow_root)
        def statuses():
            data = remote("snapshots", dataflow_root=dataflow_root)
            observed = {}
            for s in data["items"]:
                status = {"done": "completed"}.get(s["status"], s["status"])
                observed[s["output"]] = status
                if s.get("resumeAttempt"):
                    observed[attempt_key(s["output"], s["resumeAttempt"])] = status
            return observed
        server.queue = DatasetQueue(ROOT / ".local/dataset-queues", launch, statuses)
        server.queue.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server.queue:
            server.queue.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    listener = parser.add_mutually_exclusive_group()
    listener.add_argument("--port", type=int, default=8766)
    listener.add_argument("--unix-socket", type=Path, metavar="PATH",
                          help="Listen on a private Unix socket for an SSH local forward")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--dataflow-root", type=Path, metavar="DIR",
                        help="Use the server's DataFlow checkout and virtualenv directly")
    args = parser.parse_args()
    if args.dataflow_root is not None:
        args.dataflow_root = args.dataflow_root.resolve()
        required = (".venv/bin/python", "extensions/qwen_gencep/watch_runs.py",
                    "extensions/qwen_gencep/run_progress.py", "extensions/qwen_gencep/pipeline.py")
        for relative in required:
            if not (args.dataflow_root / relative).is_file():
                parser.error(f"DataFlow 运行环境缺少 {args.dataflow_root / relative}")
    handler = partial(Handler, directory=str(ROOT / "dist"))
    if args.unix_socket:
        if os.name != "posix" or args.open:
            parser.error("--unix-socket 需在服务器启动，再通过 SSH 转发访问。")
        try:
            with private_listener(args.unix_socket, handler) as server:
                print(server.server_address, flush=True)
                serve(server, args.dataflow_root)
        except (OSError, ValueError) as e:
            parser.exit(1, f"无法运行私有工作台：{e}\n")
        return
    address = f"http://127.0.0.1:{args.port}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError:
        try:
            with urllib.request.urlopen(address + "/api/bridge", timeout=2) as response:
                existing = json.load(response)
            if existing.get("app") != "flow-studio" or (args.dataflow_root is not None) != (existing.get("mode") == "direct"):
                raise ValueError()
        except (OSError, ValueError):
            raise SystemExit("端口被其他服务占用，请使用 --port 指定端口。")
        if args.open:
            webbrowser.open(address)
        return
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(address, flush=True)
    if args.open:
        webbrowser.open(address)
    with server:
        serve(server, args.dataflow_root)


if __name__ == "__main__":
    main()
