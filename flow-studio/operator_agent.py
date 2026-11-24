"""Flow Studio adapter for the upstream DataFlow Writer, isolated from the pipeline environment."""
import argparse
import ast
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


REVISION = "8f78a389e778bd7aee8d35b8a5f1fc136cdd3451"
BASE = "dataflow/dataflowagent/"
MODULES = (
    "__init__.py", "state.py", "utils.py", "agentroles/__init__.py",
    "agentroles/base_agent.py", "agentroles/writer.py", "toolkits/__init__.py",
    "toolkits/tool_manager.py", "promptstemplates/__init__.py",
    "promptstemplates/prompt_template.py", "promptstemplates/prompts_repo.py",
)


def runtime(root):
    return Path(root) / "flow-studio" / ".local" / ("writer-" + REVISION[:12])


def install(root):
    """Extract just the upstream Writer and install its private Python dependencies."""
    destination = runtime(root)
    if destination.is_dir():
        print("Writer 已安装。")
        return
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix="writer-install-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "package"
        for path in ["LICENSE", *(BASE + name for name in MODULES)]:
            data = subprocess.check_output(["git", "-C", str(root), "show", REVISION + ":" + path])
            output = stage / "src" / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        pins = ["langchain-core==0.3.76", "langchain-openai==0.3.33", "langgraph==0.6.7",
                "langgraph-checkpoint==2.1.1", "langgraph-prebuilt==0.6.4", "langgraph-sdk==0.2.6"]
        subprocess.run([str(root / ".venv/bin/uv"), "pip", "install", "--python",
                        str(root / ".venv/bin/python"), "--target", str(stage / "deps"), *pins], check=True)
        # Verify imports before making this revision available to the HTTP service.
        subprocess.run([str(root / ".venv/bin/python"), "-IB", __file__, "--check",
                        str(root), str(stage)], check=True, cwd=stage)
        stage.rename(destination)
    print("Writer 已安装，依赖与 DataFlow 原环境隔离。")


def custom_provider():
    return any(os.environ.get("FLOW_STUDIO_AGENT_" + field, "").strip()
               for field in ("BASE_URL", "MODEL", "API_KEY"))


def configuration(root):
    custom = custom_provider()
    if custom:
        url = os.environ.get("FLOW_STUDIO_AGENT_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("FLOW_STUDIO_AGENT_MODEL", "").strip()
        if not url or not model:
            raise ValueError("自定义模型需同时配置 FLOW_STUDIO_AGENT_BASE_URL 和 FLOW_STUDIO_AGENT_MODEL。")
        key_env, timeout, parameters = "FLOW_STUDIO_AGENT_API_KEY", 120, {}
    else:
        # The hand pipeline passes its ABS_MODEL as a CLI override to local_score.py.
        # Inspect that one literal without importing the GPU pipeline or scoring code.
        try:
            pipeline = Path(root) / "extensions/qwen_gencep/pipeline.py"
            tree = ast.parse(pipeline.read_text(encoding="utf-8"))
            models = [stmt.value.value for stmt in tree.body if isinstance(stmt, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "ABS_MODEL" for t in stmt.targets)
                      and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)]
            if len(models) != 1 or not models[0].strip():
                raise ValueError("无法从手部分割流水线读取 ABS_MODEL。")
            model = models[0]
            path = Path.home() / "scoring_scripts/abs/api_config.json"
            api = json.loads(path.read_text(encoding="utf-8"))["api"]
            url = api["base_url"].strip().rstrip("/")
            key_env = api.get("key_env", "MODEL_API_KEY")
            timeout = api.get("timeout_seconds", 180)
            params = api.get("parameters", {})
            allowed = {"temperature", "top_p", "max_tokens", "max_completion_tokens",
                       "reasoning_effort", "seed"}
            parameters = {k: v for k, v in params.items() if k in allowed}
            extra = {k: v for k, v in params.get("extra_body", {}).items() if k in {"reasoning", "thinking"}}
            if extra:
                parameters["extra_body"] = extra
        except (OSError, KeyError, AttributeError, TypeError, SyntaxError, json.JSONDecodeError):
            raise ValueError("无法读取 ABS 模型连接，请检查手部分割入口和 ~/scoring_scripts/abs/api_config.json。") from None
        if (not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env)
                or type(timeout) not in (int, float) or not 0 < timeout <= 86400):
            raise ValueError("ABS 模型连接的密钥环境变量名或超时配置无效。")
    parsed = urlsplit(url)
    local = parsed.hostname in ("127.0.0.1", "::1", "localhost")
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or not (parsed.scheme == "https" or parsed.scheme == "http" and (local or not custom))):
        raise ValueError("模型服务地址格式无效。自定义远程服务须使用 HTTPS。")
    return {"url": url, "model": model, "source": "custom" if custom else "abs", "key_env": key_env,
            "needs_key": not (custom and local), "timeout": timeout, "parameters": parameters}


def status(root):
    installed = bool(root and (runtime(root) / "src" / BASE / "agentroles/writer.py").is_file()
                     and (runtime(root) / "deps/langchain_openai").is_dir())
    result = {"ready": False, "installed": installed, "model": "", "revision": REVISION,
              "source": "custom" if custom_provider() else "abs", "key_env": "",
              "detail": "服务器尚未安装 DataFlow Writer。"}
    if installed:
        try:
            connection = configuration(root)
        except ValueError as error:
            result["detail"] = str(error)
        else:
            model, key_env = connection["model"], connection["key_env"]
            ready = not connection["needs_key"] or bool(os.environ.get(key_env, "").strip())
            label = "ABS 评分连接" if connection["source"] == "abs" else "自定义连接"
            detail = f"{label} · {model}"
            if not ready:
                detail += f"。工作台服务缺少环境变量 {key_env}。"
            result.update(ready=ready, model=model, key_env=key_env, detail=detail)
    return result


def load_writer(root, package):
    """Expose only the revisioned agent subtree beside the server's installed DataFlow."""
    sys.path.insert(0, str(package / "deps"))
    sys.path.insert(1, str(root))
    import dataflow
    dataflow.__path__.append(str(package / "src/dataflow"))
    from dataflow.dataflowagent.agentroles.writer import Writer
    from dataflow.dataflowagent.state import DFRequest, DFState
    from dataflow.dataflowagent.toolkits.tool_manager import ToolManager
    return Writer, DFRequest, DFState, ToolManager


async def generate(root, payload):
    writer_type, request_type, state_type, manager_type = load_writer(root, runtime(root))
    connection = payload["connection"]
    url, model = connection["url"], connection["model"]
    key = os.environ[connection["key_env"]] if connection["needs_key"] else os.environ.get(connection["key_env"]) or "local"
    if payload["kind"] == "native":
        contract = ("只返回示例中同一个 Python 顶层定义。保留名称、装饰器、方法和函数签名。"
                    "额外导入放在定义内部，无需顶层导入。")
    else:
        contract = ("返回完整的 Python 记录算子模块，可包含导入、常量与辅助函数。"
                    "保留一个 OperatorABC 算子类，其 run(self, storage) 读取 storage.read('dict')，"
                    "通过 storage.write(rows) 写回记录。节点参数从 self.config 读取。")
    target = (f"基于示例修改工作台中的 {payload['type']} 算子。\n用户需求：{payload['prompt']}\n"
              + contract + "不要附加执行入口或测试。以工作台当前接口为准。")
    request = request_type(language="zh", chat_api_url=url, model=model, api_key=key, target=target)
    state = state_type(request=request, messages=[])
    manager = manager_type()
    manager.register_pre_tool("example", lambda: payload["implementation"], "write_the_operator")
    manager.register_pre_tool("target", lambda: target, "write_the_operator")
    # Native Writer builds the messages, runs its two pre-tools and parses the draft.
    # Inject the same Chat Completions transport as ABS, including omission of
    # sampling parameters when they are absent from its connection configuration.
    from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
    failure = None
    async with AsyncOpenAI(base_url=url, api_key=key, timeout=connection["timeout"], max_retries=2) as client:
        class Chat:
            async def ainvoke(self, messages):
                nonlocal failure
                roles = {"human": "user", "system": "system"}
                formatted = [{"role": roles[m.type], "content": m.content} for m in messages]
                endpoint = urlsplit(url).netloc
                try:
                    response = await client.chat.completions.create(model=model, messages=formatted,
                                                                    **connection["parameters"])
                except APITimeoutError:
                    failure = f"amax 请求模型 API 超时（{endpoint}）。"
                    raise
                except APIConnectionError:
                    failure = f"amax 无法连接模型 API（{endpoint}），请检查服务器到该地址的网络和端口。"
                    raise
                except APIStatusError as error:
                    failure = f"模型 API 返回 HTTP {error.status_code}（{endpoint}）。"
                    if error.status_code in (401, 403):
                        failure += "请检查服务密钥和模型访问权限。"
                    raise
                return response.choices[0].message

        class ConnectedWriter(writer_type):
            def create_llm(self, state, bind_post_tools=False):
                return Chat()

        try:
            result = await ConnectedWriter(tool_manager=manager).execute(state, use_agent=False)
        except Exception:
            if failure:
                return {"error": failure}
            raise
    if failure:
        return {"error": failure}
    if not isinstance(result.draft_operator_code, str) or not result.draft_operator_code.strip():
        raise ValueError("Writer 未生成代码，请检查接口地址、模型名称、密钥及返回格式。")
    return {"code": result.draft_operator_code}


def design(root, type, implementation, prompt):
    info = status(root)
    if not info["ready"]:
        raise ValueError(info["detail"])
    if not isinstance(implementation, dict) or implementation.get("kind") not in ("native", "records"):
        raise ValueError("请选择算子的代码运行方式。")
    code = implementation.get("code")
    if not all(isinstance(s, str) and s.strip() for s in (type, code, prompt)):
        raise ValueError("请提供算子类型、当前代码和设计需求。")
    if len(type) > 128 or len(code.encode()) > 64 * 1024 or len(prompt) > 8000:
        raise ValueError("算子代码或设计需求过长。")
    root = Path(root)
    connection = configuration(root)
    payload = json.dumps({"type": type, "kind": implementation["kind"], "implementation": code,
                          "prompt": prompt, "connection": connection})
    # Only the chosen provider is part of a design request: avoid unrelated tracing,
    # default OpenAI credentials and project environments leaking into the Writer.
    passthrough = {"PATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "SSL_CERT_FILE",
                   "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    worker_env = {k: v for k, v in os.environ.items() if k.upper() in passthrough}
    if key := os.environ.get(connection["key_env"]):
        worker_env[connection["key_env"]] = key
    if connection["source"] == "abs":
        from abs_transport import proxy_environment
        worker_env.update(proxy_environment(connection["url"]))
    for name in ("no_proxy", "NO_PROXY"):
        worker_env[name] = ",".join(filter(None, (worker_env.get(name), "localhost,127.0.0.1,::1")))
    try:
        process = subprocess.run([str(root / ".venv/bin/python"), "-IB", __file__, "--generate", str(root)],
                                 input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 cwd=runtime(root), env=worker_env, timeout=connection["timeout"] + 15, encoding="utf-8")
    except subprocess.TimeoutExpired:
        raise ConnectionError("Writer 生成超时，请稍后重试或检查模型服务。") from None
    if process.returncode:
        raise ConnectionError("Writer 未生成有效代码，请检查服务器配置、模型连接及响应格式。")
    result = json.loads(process.stdout)
    if "error" in result:
        raise ConnectionError(result["error"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--install", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--generate", action="store_true")
    parser.add_argument("root", type=Path)
    parser.add_argument("stage", type=Path, nargs="?")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.install:
        install(root)
        return
    # Upstream logs include prompts and completions. The only stdout contract is JSON.
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(sys.stderr):
        if args.check:
            load_writer(root, args.stage or runtime(root))
            return
        result = asyncio.run(generate(root, json.load(sys.stdin)))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
