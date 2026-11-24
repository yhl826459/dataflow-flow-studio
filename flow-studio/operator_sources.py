"""Read and compile per-run versions of the existing video operators."""

import ast
from hashlib import sha256
from pathlib import Path
import re


NATIVE = {
    "input": ("pipeline.py", "snapshot_input"),
    "qwen": ("pipeline.py", "QwenVideoPhraseGenerator"),
    "segment": ("pipeline.py", "GenCeptionVideoSegmenter"),
    "score": ("pipeline.py", "GenCeptionAbsoluteScorer"),
    "gate": ("score_feedback.py", "MaskQualityGate"),
    "refine": ("score_feedback.py", "QwenPromptRefiner"),
    "output": ("pipeline.py", "workbench_output"),
}
RECORDS = {"input", "output", "transform", "filter", "python"}
LIMIT = 64 * 1024
OUTPUT_ADAPTER = '''def workbench_output(result):
    """result 是原流水线刚写出的 JSONL 路径，可在这里处理交付结果。"""
    print(result)
'''


def digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def implementation_shape(kind, implementation):
    if not isinstance(implementation, dict) or set(implementation) != {"kind", "code", "baseHash"}:
        raise ValueError("代码版本需要包含 kind、code 和 baseHash。")
    mode, code, base = (implementation[k] for k in ("kind", "code", "baseHash"))
    if not isinstance(kind, str) or not ((mode == "native" and kind in NATIVE) or
                                         (mode == "records" and kind in RECORDS)):
        raise ValueError("此算子与所选的代码运行方式不匹配。")
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > LIMIT:
        raise ValueError("算子源码不能为空，最大为 64 KiB。")
    if not isinstance(base, str) or not re.fullmatch("[0-9a-f]{64}", base):
        raise ValueError("缺少有效的基础源码版本，请重新打开编辑器。")


def definition(text, name):
    matches = [n for n in ast.parse(text).body
               if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name]
    if len(matches) != 1:
        raise ValueError(f"当前 DataFlow 代码中无法唯一定位 {name}。")
    return matches[0]


def start_line(node):
    return min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1


def output_call(text):
    main = definition(text, "main")
    matches = [n for n in ast.walk(main) if isinstance(n, ast.Expr)
               and ast.dump(n.value) == ast.dump(ast.parse("print(result)", mode="eval").body)]
    if len(matches) != 1:
        raise ValueError("当前流水线的输出接口已变化，请检查 DataFlow 版本。")
    return matches[0]


def native_block(kind, text):
    if kind == "output":
        call = output_call(text)
        return OUTPUT_ADAPTER, call.lineno, "工作台输出适配器（原流水线写出 JSONL 后调用）"
    node = definition(text, NATIVE[kind][1])
    start = start_line(node)
    return "".join(text.splitlines(True)[start:node.end_lineno]), start + 1, node.name


def source_for(root, kind, mode):
    if mode == "records" and kind in RECORDS:
        from operator_runtime import record_source
        code = record_source(kind)
        return dict(code=code, baseHash=digest(code), origin="工作台 · Python 记录算子", kind=mode)
    if mode != "native" or kind not in NATIVE:
        raise ValueError("没有对应的算子源码。")
    filename = NATIVE[kind][0]
    path = Path("extensions/qwen_gencep") / filename
    text = (root / path).read_text(encoding="utf-8")
    code, line, label = native_block(kind, text)
    return dict(code=code, baseHash=digest(text + code), origin=f"{path.as_posix()}:{line} · {label}", kind=mode)


def function_contract(node):
    return (ast.dump(node.args), [ast.dump(d) for d in node.decorator_list])


def check_native(code, original):
    try:
        compile(code, "<算子>", "exec")
        body = ast.parse(code).body
    except (SyntaxError, ValueError) as e:
        raise ValueError(f"Python 第 {getattr(e, 'lineno', '?')} 行：{getattr(e, 'msg', str(e))}") from e
    base = ast.parse(original).body[0]
    if len(body) != 1 or type(body[0]) is not type(base) or body[0].name != base.name:
        raise ValueError(f"保留单个 {base.name} 定义。需要的额外导入可以放在方法里。")
    updated = body[0]
    if isinstance(base, ast.FunctionDef):
        if function_contract(updated) != function_contract(base):
            raise ValueError(f"请保留 {base.name} 的参数和装饰器。")
    else:
        if any([ast.dump(n) for n in getattr(updated, key)] != [ast.dump(n) for n in getattr(base, key)]
               for key in ("bases", "keywords", "decorator_list")):
            raise ValueError("请保留 DataFlow 的基类和注册装饰器。")
        for method in base.body:
            if isinstance(method, ast.FunctionDef) and method.name in ("run", "__init__"):
                choices = [n for n in updated.body if isinstance(n, ast.FunctionDef) and n.name == method.name]
                if len(choices) != 1 or function_contract(choices[0]) != function_contract(method):
                    raise ValueError(f"请保留 {method.name} 的参数与进度装饰器。")


def validate(root, kind, implementation):
    implementation_shape(kind, implementation)
    if implementation["kind"] == "records":
        from operator_runtime import validate_record_code
        validate_record_code(implementation["code"])
    else:
        original = source_for(root, kind, "native")
        if implementation["baseHash"] != original["baseHash"]:
            raise ValueError("服务器基础源码已更新。请从当前内置算子创建新版后再应用修改。")
        check_native(implementation["code"], original["code"])


def native_snapshot(root, nodes):
    """Freeze the original Python helpers and replace only selected definitions."""
    src = root / "extensions/qwen_gencep"
    files = {p.name: p.read_text(encoding="utf-8") for p in src.glob("*.py")}
    changes = {}
    for node in nodes:
        impl = node.get("implementation")
        if impl is None:
            continue
        kind = node["type"]
        implementation_shape(kind, impl)
        if impl["kind"] != "native":
            raise ValueError("手部分割任务只能加载视频算子的 Python 版本。")
        filename, symbol = NATIVE[kind]
        text = files[filename]
        original, _, _ = native_block(kind, text)
        if impl["baseHash"] != digest(text + original):
            raise ValueError(f"{symbol} 的基础源码已更新，请重新核对本节点。")
        code = impl["code"].rstrip() + "\n"
        check_native(code, original)
        edits = changes.setdefault(filename, [])
        if kind == "output":
            call = output_call(text)
            edits.append((call.lineno - 1, call.end_lineno, " " * call.col_offset + "workbench_output(result)\n"))
            main = definition(text, "main")
            edits.append((main.lineno - 1, main.lineno - 1, code + "\n\n"))
        else:
            old = definition(text, symbol)
            edits.append((start_line(old), old.end_lineno, code))
    for filename, edits in changes.items():
        lines = files[filename].splitlines(keepends=True)
        for start, end, code in sorted(edits, reverse=True):
            lines[start:end] = [code]
        files[filename] = "".join(lines)
        compile(files[filename], filename, "exec")
    return files
