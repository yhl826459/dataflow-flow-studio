"""Open the amax DataFlow workbench through a loopback SSH tunnel."""

import argparse
import http.client
import json
import subprocess
import sys
import time
import webbrowser


HOST = "127.0.0.1"
PORT = 18766
URL = f"http://{HOST}:{PORT}/"
SOCKET = "/data/yanghonglei/DataFlow/flow-studio/.local/workbench.sock"
ABS_RELAY = "127.0.0.1:18767:hduiipl.orz2333.fun:12333"
SSH = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
       "-o", "ServerAliveCountMax=3"]


def workbench_info():
    conn = http.client.HTTPConnection(HOST, PORT, timeout=1)
    try:
        conn.request("GET", "/api/bridge")
        response = conn.getresponse()
        if response.status != 200:
            return {}
        data = json.loads(response.read(4097))
        if (isinstance(data, dict) and data.get("app") == "flow-studio"
                and data.get("connected") is True and data.get("mode") == "direct"
                and data.get("host") == "amax"):
            return data
        return {}
    except (OSError, http.client.HTTPException, ValueError):
        return {}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="使用 SSH 打开 amax 上的流水线工作台")
    parser.add_argument("--no-browser", action="store_true", help="建立隧道后手动打开网页")
    args = parser.parse_args()
    tunnels = []
    try:
        if workbench_info():
            print("已复用现有 amax 隧道，由原连接窗口维护。")
        else:
            print("正在通过 SSH 5090 连接 amax，并启动流水线工作台……", flush=True)
            # Closing this process's stdin pipe ends the remote cat and the SSH session.
            tunnel = subprocess.Popen([*SSH, "-L", f"{HOST}:{PORT}:{SOCKET}", "5090",
                "systemctl --user start flow-studio.service && exec cat >/dev/null",
            ], stdin=subprocess.PIPE)
            tunnels.append(tunnel)
            deadline = time.monotonic() + 30
            while not workbench_info():
                if tunnel.poll() is not None:
                    raise RuntimeError(f"SSH 提前退出（状态码 {tunnel.returncode}）。"
                                       f"请检查上方 SSH 提示、5090 登录权限及本机端口 {PORT} 的占用情况。")
                if time.monotonic() >= deadline:
                    raise RuntimeError("等待 amax 工作台响应超时。请检查 flow-studio.service 的运行状态。")
                time.sleep(0.4)
        if not workbench_info().get("absRelay"):
            relay = subprocess.Popen([*SSH, "-R", ABS_RELAY, "5090", "exec cat >/dev/null"],
                                     stdin=subprocess.PIPE)
            tunnels.append(relay)
            deadline = time.monotonic() + 12
            while not workbench_info().get("absRelay"):
                if relay.poll() is not None or time.monotonic() >= deadline:
                    print("API 反向转发未就绪，请检查 amax 服务或 SSH 转发设置。", file=sys.stderr)
                    break
                time.sleep(0.4)
            else:
                print("ABS 和算子 Agent 的 API 反向转发已连接。")
        if tunnels:
            print("保留此窗口以维持本次隧道。")
        print(URL, flush=True)
        if not args.no_browser:
            webbrowser.open(URL)
        while tunnels:
            if any(tunnel.poll() is not None for tunnel in tunnels):
                raise RuntimeError("SSH 转发已断开，重新运行本入口即可连接。")
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as error:
        print(f"工作台连接失败。{error}", file=sys.stderr)
        return 1
    finally:
        for tunnel in tunnels:
            tunnel.stdin.close()
            try:
                tunnel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
