"""Route only requests to the ABS API origin through the configured SSH relay."""
from functools import wraps
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


def relay_address():
    try:
        proxy = urlsplit(os.environ.get("FLOW_STUDIO_ABS_HTTP_PROXY", ""))
        port = proxy.port
    except ValueError:
        return None
    if (proxy.scheme == "http" and proxy.hostname == "127.0.0.1" and port
            and proxy.username is None and proxy.password is None and not (proxy.path or proxy.query or proxy.fragment)):
        return proxy.hostname, port
    return None


def relay_listening():
    address = relay_address()
    if address is None:
        return False
    try:
        with socket.create_connection(address, timeout=0.5):
            return True
    except OSError:
        return False


def proxy_environment(base_url):
    endpoint = urlsplit(base_url)
    origin = urlsplit(os.environ.get("FLOW_STUDIO_ABS_PROXY_ORIGIN", ""))
    if relay_address() and endpoint.scheme == origin.scheme == "http" and endpoint.netloc == origin.netloc:
        proxy = os.environ["FLOW_STUDIO_ABS_HTTP_PROXY"]
        return {"HTTP_PROXY": proxy, "http_proxy": proxy, "NO_PROXY": "", "no_proxy": ""}
    return {}


def route_scorer(run_logged, scorer):
    @wraps(run_logged)
    def run(command, log, *, env=None, cwd=None):
        if (len(command) > 2 and Path(command[2]) == scorer
                and "--config" in command and "--prepare-only" not in command):
            path = Path(command[command.index("--config") + 1])
            config = json.loads(path.read_text(encoding="utf-8"))
            proxy = proxy_environment(config["api"]["base_url"])
            if proxy:
                env = {**(os.environ if env is None else env), **proxy}
        return run_logged(command, log, env=env, cwd=cwd)

    return run
