"""Run a DataFlow record operator against JSON rows in a disposable Linux namespace."""

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from textwrap import indent


MAX_CODE = 64 * 1024
MAX_DATA = 1024 * 1024
SLOTS = threading.BoundedSemaphore(2)


def validate_record_code(code):
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE:
        raise ValueError("请输入不超过 64 KiB 的 Python 源码。")
    try:
        tree = ast.parse(code, filename="<operator>")
        compile(tree, "<operator>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 第 {exc.lineno} 行：{exc.msg}") from exc
    except RecursionError as exc:
        raise ValueError("Python 表达式嵌套过深。") from exc
    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and
        any((isinstance(base, ast.Name) and base.id == "OperatorABC") or
            (isinstance(base, ast.Attribute) and base.attr == "OperatorABC") for base in node.bases)
    ]
    if len(classes) != 1:
        raise ValueError("记录算子须定义一个继承 OperatorABC 的类。")
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(methods) != 1:
        raise ValueError("算子类需要定义同步方法 run(self, storage)。")
    args = methods[0].args
    positional = args.posonlyargs + args.args
    if ([arg.arg for arg in positional[:2]] != ["self", "storage"] or
            len(positional) - len(args.defaults) > 2 or any(arg is None for arg in args.kw_defaults)):
        raise ValueError("run(self, storage) 的其他参数需要有默认值。")
    return classes[0].name


def record_source(kind):
    """Built-in, editable Python equivalents, plus the starter for a new operator."""
    names = {"input": "ReadRecords", "output": "WriteRecords", "transform": "TransformField",
             "filter": "FilterRecords", "python": "CleanText"}
    if kind not in names:
        raise ValueError("该算子没有记录模式源码。")
    body = "rows = storage.read('dict')\nstorage.write(rows)"
    if kind == "python":
        body = """rows = storage.read('dict')
for row in rows:
    row['clean_text'] = row['text'].strip()
storage.write(rows)"""
    elif kind == "transform":
        body = """settings = {k: v for k, v in getattr(self, 'config', {}).items() if v is not None}
source = settings.get('sourceKey', 'text')
target = settings.get('targetKey', 'text')
op = settings.get('operation', 'trim')
if not source or not target or target in {'__proto__', 'constructor', 'prototype'}:
    raise ValueError('目标字段无效')
if op not in {'trim', 'uppercase', 'lowercase', 'copy'}:
    raise ValueError('无效的转换操作')
rows = storage.read('dict')
for row in rows:
    value = row[source]
    if op == 'trim':
        value = value.strip(JS_WHITESPACE)
    elif op == 'uppercase':
        value = value.upper()
    elif op == 'lowercase':
        value = value.lower()
    row[target] = value
storage.write(rows)"""
    elif kind == "filter":
        body = """settings = {k: v for k, v in getattr(self, 'config', {}).items() if v is not None}
key = settings.get('key', 'text')
rule = settings.get('rule', 'nonempty')
if rule not in {'nonempty', 'equals', 'contains'}:
    raise ValueError('无效的过滤条件')
expected = record_text(settings.get('value', ''))
kept = []
for row in storage.read('dict'):
    if row.get(key) is None:
        continue
    value = record_text(row[key])
    if rule == 'nonempty':
        keep = bool(value.strip(JS_WHITESPACE))
    elif rule == 'equals':
        keep = value == expected
    else:
        keep = expected in value
    if keep:
        kept.append(row)
storage.write(kept)"""
    header = "from dataflow.core import OperatorABC\nfrom dataflow.utils.registry import OPERATOR_REGISTRY\n"
    if kind in ("transform", "filter"):
        # Match the browser's String.trim(), including the byte-order mark.
        whitespace = "\t\n\v\f\r \xa0\u1680" + "".join(chr(i) for i in range(0x2000, 0x200b)) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
        header += "\nJS_WHITESPACE = " + ascii(whitespace) + "\n"
    if kind == "filter":
        header += '''
def record_text(value):
    """Match the text used by browser comparisons on JSON values."""
    from decimal import Decimal
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return ','.join('' if item is None else record_text(item) for item in value)
    if isinstance(value, dict):
        return '[object Object]'
    number = float(value)
    if number == 0:
        return '0'
    decimal = Decimal(str(number)).normalize()
    if 1e-6 <= abs(number) < 1e21:
        return format(decimal, 'f')
    return format(decimal, 'e').replace('e-0', 'e-')
'''
    return header + f"\n\n@OPERATOR_REGISTRY.register()\nclass {names[kind]}(OperatorABC):\n    def run(self, storage):\n" + indent(body, "        ") + "\n"


def checked_rows(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("记录必须是 JSON 对象列表。")
    return rows


def run_records(dataflow_root, code, records, config):
    name = validate_record_code(code)
    checked_rows(records)
    if not isinstance(config, dict):
        raise ValueError("算子参数需要是对象。")
    payload = json.dumps({"name": name, "code": code, "records": records, "config": config},
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_DATA + MAX_CODE:
        raise ValueError("输入记录和参数过大，单次最多约 1 MiB。")
    if not dataflow_root or sys.platform != "linux" or not Path("/usr/bin/bwrap").is_file():
        raise ValueError("执行 Python 记录算子需要 amax 上已安装的隔离运行环境。")
    root = Path(dataflow_root)
    venv = root / ".venv"
    interpreter = (venv / "bin/python").resolve(strict=True)
    packages = list((venv / "lib").glob("python*/site-packages"))
    if len(packages) != 1:
        raise ValueError("无法定位 DataFlow 虚拟环境的依赖目录。")
    # No home, checkout data, credentials, writable host directory or host network is mounted.
    cmd = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
           "--clearenv", "--setenv", "OPENBLAS_NUM_THREADS", "1", "--setenv", "OMP_NUM_THREADS", "1",
           "--ro-bind", "/usr/lib", "/usr/lib", "--ro-bind", "/usr/lib64", "/usr/lib64",
           "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
           "--ro-bind", "/dev/null", "/dev/null", "--ro-bind", "/dev/urandom", "/dev/urandom",
           "--ro-bind", str(interpreter), "/python", "--ro-bind", str(packages[0]), "/packages",
           "--ro-bind", str(root / "dataflow"), "/code/dataflow",
           "--ro-bind", str(Path(__file__).resolve()), "/worker.py", "--dir", "/tmp",
           "--remount-ro", "/", "--chdir", "/", "/python", "-BIS", "/worker.py"]
    if not SLOTS.acquire(blocking=False):
        raise ValueError("其他记录算子正在运行，请稍后重试。")
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run(cmd, input=payload, stdout=out, stderr=err, timeout=14, close_fds=True)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("算子超过 14 秒运行时限。") from exc
            if result.returncode:
                err.seek(0, 2)
                err.seek(max(0, err.tell() - 1500))
                detail = err.read().decode("utf-8", "replace").strip()
                raise ValueError("隔离执行失败：" + (detail or "进程异常退出或超过资源限额。"))
            out.seek(0)
            raw = out.read(MAX_DATA + 1)
            if len(raw) > MAX_DATA:
                raise ValueError("算子输出超过 1 MiB。")
            try:
                return checked_rows(json.loads(raw))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("算子没有写出有效的 JSON 记录列表。") from exc
    finally:
        SLOTS.release()


def worker():
    """Entry point inside bubblewrap only."""
    import resource
    for limit, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                         (resource.RLIMIT_NPROC, 0), (resource.RLIMIT_FSIZE, MAX_DATA + 1),
                         (resource.RLIMIT_AS, 1024 ** 3), (resource.RLIMIT_CPU, 8)):
        resource.setrlimit(limit, (value, value))
    sys.path[:0] = ["/code", "/packages"]
    reply, sys.stdout = sys.stdout, sys.stderr
    from dataflow.core import OperatorABC
    from dataflow.utils.storage import DataFlowStorage
    import pandas as pd
    import copy
    data = json.load(sys.stdin)

    class MemoryStorage(DataFlowStorage):
        written = False

        def read(self, output_type="dataframe"):
            if output_type == "dict":
                return copy.deepcopy(self.rows)
            if output_type == "dataframe":
                return pd.DataFrame(self.rows)
            raise ValueError("storage.read 支持 'dict' 或 'dataframe'。")

        def get_keys_from_dataframe(self):
            return list(dict.fromkeys(key for row in self.rows for key in row))

        def write(self, rows):
            if isinstance(rows, pd.DataFrame):
                rows = json.loads(rows.to_json(orient="records", force_ascii=False, date_format="iso"))
            self.rows = checked_rows(rows)
            self.written = True
            return "memory://output"

    storage = MemoryStorage()
    storage.rows = data["records"]
    namespace = {"__name__": "workbench_operator"}
    exec(compile(data["code"], "<operator>", "exec"), namespace)
    operator = namespace[data["name"]]
    if not issubclass(operator, OperatorABC):
        raise ValueError("算子需要继承 DataFlow 的 OperatorABC。")
    instance = operator()
    instance.config = data["config"]
    instance.run(storage)
    if not storage.written:
        raise ValueError("run 需要调用 storage.write(输出记录)。")
    reply.write(json.dumps(storage.rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    worker()
