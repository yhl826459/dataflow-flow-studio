"""Serve pipeline progress over a private Unix socket, via SSH forwarding."""

import fcntl
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
from socketserver import ThreadingUnixStreamServer

from run_progress import STATE, process_start


def snapshots():
    runs = []
    for path in (STATE / "runs").glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state["status"] == "running" and process_start(state["pid"]) != state["process_start"]:
            state["status"] = "interrupted"
            for step in state["rounds"][-1]["stages"].values():
                if step["status"] == "running":
                    step["status"] = "interrupted"
        runs.append(state)
    return sorted(runs, key=lambda state: state["started"], reverse=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        host = self.headers.get("Host", "").split(":", 1)[0]
        if host not in ("localhost", "127.0.0.1"):
            self.send_error(403)
            return
        if self.path == "/":
            payload = Path(__file__).with_name("watch_runs.html").read_bytes()
            media = "text/html"
        elif self.path == "/api/runs":
            payload = json.dumps(snapshots(), ensure_ascii=False).encode("utf-8")
            media = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{media}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def main():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket = STATE / "viewer.sock"
    with (STATE / "viewer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("DataFlow run monitor is already running") from None
        if socket.is_socket():
            socket.unlink()
        with ThreadingUnixStreamServer(str(socket), Handler) as server:
            socket.chmod(0o600)
            print(socket, flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
