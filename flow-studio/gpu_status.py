"""Read current GPU and compute-process telemetry from NVIDIA's management tool."""
import csv
from datetime import datetime, timezone
import subprocess


GPU_QUERY = "--query-gpu=index,name,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu"
PROCESS_QUERY = "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory"


def _rows(query):
    try:
        proc = subprocess.run(["nvidia-smi", query, "--format=csv,noheader,nounits"],
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=3, check=False)
    except FileNotFoundError as exc:
        raise ValueError("服务器未安装 nvidia-smi。") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("nvidia-smi 查询超时。") from exc
    except OSError as exc:
        raise ValueError("无法执行服务器上的 nvidia-smi。") from exc
    if proc.returncode:
        raise ValueError("nvidia-smi 查询失败，请检查服务器驱动及读取权限。")
    return [[cell.strip() for cell in row] for row in csv.reader(proc.stdout.splitlines(), skipinitialspace=True)
            if row and any(cell.strip() for cell in row)]


def _number(value):
    return int(value) if value.isascii() and value.isdigit() else None


def gpu_snapshot():
    result = dict(available=False, updatedAt=datetime.now(timezone.utc).isoformat(),
                  error=None, processError=None, gpus=[])
    try:
        gpus = []
        for row in _rows(GPU_QUERY):
            if len(row) != 7 or _number(row[0]) is None:
                raise ValueError("nvidia-smi 返回的显卡信息不完整。")
            index, name, uuid, utilization, used, total, temperature = row
            gpus.append(dict(index=int(index), name=name, uuid=uuid, utilization=_number(utilization),
                             memoryUsedMiB=_number(used), memoryTotalMiB=_number(total),
                             temperatureC=_number(temperature), processes=[]))
        if not gpus:
            raise ValueError("服务器未检测到 NVIDIA 显卡。")
    except (ValueError, csv.Error) as exc:
        result["error"] = str(exc)
        return result

    result.update(available=True, gpus=gpus)
    by_uuid = {gpu["uuid"]: gpu for gpu in gpus}
    try:
        for row in _rows(PROCESS_QUERY):
            if row == ["No running processes found"]:
                continue
            if len(row) < 4:
                raise ValueError("nvidia-smi 返回的进程信息不完整。")
            uuid, pid, *name, memory = row
            if uuid not in by_uuid:
                raise ValueError("显卡与计算进程的快照未能对应。")
            by_uuid[uuid]["processes"].append(dict(pid=_number(pid), name=", ".join(name), memoryMiB=_number(memory)))
    except (ValueError, csv.Error) as exc:
        result["processError"] = str(exc)
        for gpu in gpus:
            gpu["processes"] = None
    return result
