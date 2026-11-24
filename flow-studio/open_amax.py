"""Open the amax DataFlow workbench through a loopback SSH tunnel."""

import argparse
import http.client
import json
import re
import subprocess
import sys
import time
import webbrowser


HOST = "127.0.0.1"
PORT = 18766
ABS_RELAY = "127.0.0.1:18767:hduiipl.orz2333.fun:12333"
SSH = ["ssh", "-T", "-o", "ConnectTimeout=10",
       "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
       "-o", "ServerAliveCountMax=3"]


def ssh_profile(alias):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
        raise ValueError("SSH 别名格式无效。")
    command = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=10)
    if command.returncode:
        raise ValueError(f"无法读取 SSH 别名 {alias} 的配置。")
    return dict(line.split(" ", 1) for line in command.stdout.splitlines() if " " in line)


def workbench_info(port, root):
    conn = http.client.HTTPConnection(HOST, port, timeout=1)
    try:
        conn.request("GET", "/api/bridge")
        response = conn.getresponse()
        if response.status != 200:
            return {}
        data = json.loads(response.read(4097))
        if (isinstance(data, dict) and data.get("app") == "flow-studio"
                and data.get("connected") is True and data.get("mode") == "direct"
                and data.get("outputRoot") == f"{root}/dataflow_output"):
            return data
        return {}
    except (OSError, http.client.HTTPException, ValueError):
        return {}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="使用 SSH 打开 amax 上的流水线工作台")
    parser.add_argument("--no-browser", action="store_true", help="建立隧道后手动打开网页")
    parser.add_argument("--ssh-alias", help="已配置的个人 SSH 别名")
    parser.add_argument("--server-root", help="个人 DataFlow 目录的服务器绝对路径")
    parser.add_argument("--port", type=int, default=PORT, help="工作台本机端口")
    parser.add_argument("--abs-relay", help="可选的个人 ABS 反向转发，格式为 127.0.0.1:远端端口:API主机:API端口")
    args = parser.parse_args()
    tunnels = []
    try:
        alias = args.ssh_alias or "amax-workbench"
        profile = ssh_profile(alias)
        if not args.ssh_alias and profile.get("hostname") == alias:
            alias = "5090"
            profile = ssh_profile(alias)
        account = profile.get("user", "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", account):
            raise ValueError("请在 SSH 别名中配置有效的 amax 账号。")
        legacy_owner = alias == "5090" and account == "yanghonglei"
        root = (args.server_root or ("/data/yanghonglei/DataFlow" if account == "yanghonglei"
                                     else f"/home/{account}/DataFlow")).rstrip("/")
        if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", root)
                or any(part in ("", ".", "..") for part in root[1:].split("/"))):
            raise ValueError("请提供无空格的服务器 DataFlow 绝对路径。")
        if not 1 <= args.port <= 65535:
            raise ValueError("本机端口超出有效范围。")
        relay_address = args.abs_relay or (ABS_RELAY if legacy_owner else "")
        if relay_address and not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}:[A-Za-z0-9.-]+:[0-9]{1,5}", relay_address):
            raise ValueError("ABS 反向转发必须监听服务器的 127.0.0.1。")
        if relay_address and not all(1 <= int(port) <= 65535 for port in relay_address.split(":")[1::2]):
            raise ValueError("ABS 反向转发端口超出有效范围。")
        url = f"http://{HOST}:{args.port}/"
        if workbench_info(args.port, root):
            print("已复用该账号的 amax 隧道，由原连接窗口维护。")
        else:
            print(f"正在通过 SSH {alias} 启动 {account} 的工作台……", flush=True)
            tunnel = subprocess.Popen([*SSH, "-L", f"{HOST}:{args.port}:{root}/flow-studio/.local/workbench.sock", alias,
                "systemctl --user start flow-studio.service && exec cat >/dev/null",
            ])
            tunnels.append(tunnel)
            deadline = time.monotonic() + 90
            while not workbench_info(args.port, root):
                if tunnel.poll() is not None:
                    raise RuntimeError(f"SSH 提前退出（状态码 {tunnel.returncode}）。"
                                       f"请检查 {alias} 的登录、个人服务及本机端口 {args.port}。")
                if time.monotonic() >= deadline:
                    raise RuntimeError("未连接到该账号的工作台。请检查个人 DataFlow 路径和 flow-studio.service。")
                time.sleep(0.4)
        if relay_address and not workbench_info(args.port, root).get("absRelay"):
            relay = subprocess.Popen([*SSH, "-N", "-R", relay_address, alias])
            tunnels.append(relay)
            deadline = time.monotonic() + 12
            while not workbench_info(args.port, root).get("absRelay"):
                if relay.poll() is not None or time.monotonic() >= deadline:
                    print("API 反向转发未就绪，请检查 amax 服务或 SSH 转发设置。", file=sys.stderr)
                    break
                time.sleep(0.4)
            else:
                print("ABS 和算子 Agent 的 API 反向转发已连接。")
        if tunnels:
            print("保留此窗口以维持本次隧道。")
        print(url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while tunnels:
            if any(tunnel.poll() is not None for tunnel in tunnels):
                raise RuntimeError("SSH 转发已断开，重新运行本入口即可连接。")
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"工作台连接失败。{error}", file=sys.stderr)
        return 1
    finally:
        for tunnel in tunnels:
            if tunnel.poll() is None:
                tunnel.terminate()
            try:
                tunnel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
