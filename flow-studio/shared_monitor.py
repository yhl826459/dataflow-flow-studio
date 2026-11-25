"""Serve a read-only view of the owner workbench to one Linux SSH account."""
import argparse
import fcntl
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import socket
from socketserver import ThreadingUnixStreamServer
import stat
import struct
import subprocess
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
OWNER_SOCKET = ROOT / ".local/workbench.sock"
ASSETS = {"/": ("shared-monitor.html", "text/html; charset=utf-8"),
          **{f"/{name}": (name, "text/javascript; charset=utf-8")
             for name in ("shared-monitor.js", "canvas.js", "model.js", "operator-summary.js", "records.js","abs-monitor.js")},
          **{f"/{name}": (name, "text/css; charset=utf-8") for name in ("studio.css", "canvas.css")}}


def pick(row, *fields):
    return {key: row[key] for key in fields if key in row}


def reference(output):
    return sha256(output.encode()).hexdigest()[:24]


def graph_view(graph):
    return {
        **pick(graph, "id", "name", "version"),
        "nodes": [pick(n, "id", "type", "name", "x", "y") for n in graph["nodes"]],
        "edges": [pick(e, "id", "from", "to", "condition", "label", "targetPort") for e in graph["edges"]],
    }


def pipelines_view(library):
    workflows = []
    for graph in (library["data"] or {}).get("graphs", []):
        shared = graph_view(graph)
        shared.update(pick(graph, "description", "updatedAt"))
        for node, original in zip(shared["nodes"], graph["nodes"], strict=True):
            node["notes"] = pick(original.get("notes", {}), "input", "logic", "model", "output")
            config = original.get("config", {})
            node["config"] = {k: v for k, v in pick(config, "sample_fps", "every", "threshold", "maxRetries").items()
                              if type(v) in (int, float)}
            for field, options in (("task", ("hands", "objects")), ("operation", ("trim", "uppercase", "lowercase", "copy")),
                                   ("rule", ("nonempty", "equals", "contains"))):
                if config.get(field) in options:
                    node["config"][field] = config[field]
            if kind := (original.get("implementation") or {}).get("kind"):
                if kind in ("native", "records"):
                    node["implementation"] = {"kind": kind}
        workflows.append(shared)
    return {"pipelines": workflows}


def monitoring_view(state):
    runs = []
    for original in state["runs"]:
        run = pick(original, "id", "name", "task", "status", "startedAt", "endedAt", "current",
                   "nodeStates", "activeEdges", "enabledTypes", "clips", "events", "absScores")
        run["ref"] = reference(original["output"])
        run["activity"] = original.get("currentActivity", {}).get("detail")
        if graph := original.get("graph"):
            run["graph"] = graph_view(graph)
        if fusion := original.get("fusion"):
            run["fusion"] = pick(fusion, "tracks", "reviewSeams", "reviewNotes", "emptyFrameRanges")
        runs.append(run)
    items = {}
    for group in reversed(state["queues"]):
        for item in group["items"]:
            key = reference(item["out"])
            items[key] = dict(pick(item, "status", "clipPlan", "continuation"),
                              ref=key, task=group["task"],
                              name=item.get("sequenceName") or PurePosixPath(item["out"]).name)
    return {"runs": runs, "queueItems": list(items.values()),
            "queueOrder": [reference(out) for out in state["queueOrder"]]}


class OwnerConnection(HTTPConnection):
    def connect(self):
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(self.timeout)
        try:
            stream.connect(str(OWNER_SOCKET))
        except OSError:
            stream.close()
            raise
        self.sock = stream


class MonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_bytes(self, value, mime="application/json; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(value)

    def do_GET(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if (not re.fullmatch(r"(127\.0\.0\.1|localhost)(?::[1-9][0-9]{0,4})?", host)
                or (origin and origin != "http://" + host)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            return self.send_bytes(b"{}", status=403)
        path = urlsplit(self.path).path
        if path in ASSETS:
            filename, mime = ASSETS[path]
            return self.send_bytes((ROOT / "dist" / filename).read_bytes(), mime)
        if path not in ("/api/live", "/api/pipelines"):
            return self.send_bytes(b"{}", status=404)
        pipelines = path == "/api/pipelines"
        connection = OwnerConnection("localhost", timeout=30)
        try:
            connection.request("GET", "/api/library" if pipelines else "/api/live")
            response = connection.getresponse()
            if response.status != 200:
                return self.send_bytes(b"{}", status=503)
            data = json.load(response)
            view = pipelines_view(data) if pipelines else monitoring_view(data)
        except (OSError, HTTPException, KeyError, TypeError, ValueError):
            return self.send_bytes(b"{}", status=503)
        finally:
            connection.close()
        self.send_bytes(json.dumps(view, ensure_ascii=False).encode())


class MonitorServer(ThreadingUnixStreamServer):
    daemon_threads = True

    def verify_request(self, request, _address):
        credentials = request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", credentials)[1] in self.allowed_uids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    uid = pwd.getpwnam(args.viewer).pw_uid
    path = args.socket.absolute()
    directory = path.parent
    if directory.parent != Path("/tmp"):
        raise ValueError("The shared socket directory must be directly under /tmp.")
    directory.mkdir(mode=0o700, exist_ok=True)
    lock = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(lock).st_uid != os.getuid():
            raise PermissionError("Invalid shared socket directory owner.")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(lock, 0o700)
        subprocess.run(["setfacl", "-b", "--", str(directory)], check=True)
        try:
            old = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.getuid():
                raise ValueError("The monitor socket path is occupied.")
            path.unlink()
        with MonitorServer(str(path), MonitorHandler) as server:
            try:
                server.allowed_uids = {os.getuid(), uid}
                path.chmod(0o600)
                subprocess.run(["setfacl", "-m", f"u:{uid}:rw-", "--", str(path)], check=True)
                subprocess.run(["setfacl", "-m", f"u:{uid}:--x", "--", str(directory)], check=True)
                server.serve_forever()
            finally:
                path.unlink(missing_ok=True)
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
