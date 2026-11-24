"""Persist operator progress for the read-only run monitor."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import time
from uuid import uuid4

STATE = Path.home() / ".local/state/dataflow-qwen-gencep"
STAGES = ("qwen", "genception", "abs", "gate", "refiner")
CURRENT = ContextVar("dataflow_run_progress", default=None)


def process_start(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", maxsplit=1)[1].split()
        return fields[19] if fields[0] not in ("Z", "X") else None
    except (OSError, IndexError):
        return None


class RunProgress:
    def __init__(self, out, stages, mode, state_dir=STATE):
        runs = state_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = uuid4().hex
        self.path = runs / f"{identity}.json"
        self.operator = None
        self.data = {
            "id": identity, "name": out.name, "output": str(out), "mode": mode,
            "status": "running", "pid": os.getpid(), "process_start": process_start(os.getpid()),
            "started": time.time(), "finished": None, "rounds": [],
        }
        self.add_round(0, stages)

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        tmp.replace(self.path)

    def add_round(self, number, stages):
        present = set(stages)
        self.data["rounds"].append({"number": number, "stages": {
            name: {"status": "pending" if name in present else "skipped"} for name in STAGES
        }})
        self.save()


@contextmanager
def monitor_run(out, stages, mode, *, state_dir=STATE):
    progress = RunProgress(out, stages, mode, state_dir)
    token = CURRENT.set(progress)
    try:
        yield progress
    except KeyboardInterrupt:
        progress.data["status"] = "interrupted"
        raise
    except BaseException as error:
        progress.data.update(status="failed", error=type(error).__name__)
        raise
    else:
        progress.data["status"] = "done"
    finally:
        progress.data["finished"] = time.time()
        try:
            progress.save()
        finally:
            CURRENT.reset(token)


def new_round(number):
    progress = CURRENT.get()
    if progress is not None:
        progress.add_round(number, ("genception", "abs", "gate", "refiner"))


def operator_progress(stage):
    def decorate(run):
        @wraps(run)
        def wrapped(*args, **kwargs):
            progress = CURRENT.get()
            if progress is None:
                return run(*args, **kwargs)
            step = progress.data["rounds"][-1]["stages"][stage]
            step.update(status="running", started=time.time(), worked=False)
            progress.operator = step
            progress.save()
            try:
                result = run(*args, **kwargs)
            except KeyboardInterrupt:
                step.update(status="interrupted")
                raise
            except BaseException as error:
                step.update(status="failed", error=type(error).__name__)
                raise
            else:
                step["status"] = "done" if step["worked"] or stage == "gate" else "skipped"
                return result
            finally:
                step["finished"] = time.time()
                progress.operator = None
                progress.save()
        return wrapped
    return decorate


def report_work(*, sequence=None, detail=None, log=None):
    progress = CURRENT.get()
    if progress is None or progress.operator is None:
        return
    step = progress.operator
    step["worked"] = True
    if sequence is not None:
        step.update(sequence=str(sequence), detail=None, log=None)
    if detail is not None:
        step["detail"] = detail
    if log is not None:
        step.update(detail=log.stem, log=str(log))
    progress.save()
