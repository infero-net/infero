"""Local device relay + device agent for device tests.

Nothing touches a real relay or the user's ~/.infero:
  * LocalRelay copies relay/relay.py, agent.py and bip39.txt into a temp folder (relay.py writes
    tokens.json next to itself), binds 127.0.0.1 only, on free ports.
  * A small same-origin proxy plays nginx: http://127.0.0.1:<proxy>/ws -> the relay's WebSocket
    port, everything else -> its HTTP port. That is the layout the page expects for a relay URL.
  * LocalAgent runs a copy of agent.py with its own temp INFERO_DIR and a fixed device_id, paired
    the way the install script would (instances.json), without venv / systemd / PATH changes.

relay.py rate-limits pairing per IP (10 codes / 10 min); every test comes from 127.0.0.1, so the
relay is restarted (tokens persist in its tokens.json) before the budget runs out.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from .server import _free_port

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RELAY_SRC = os.path.join(REPO, "relay")


def _wait_port(port, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {port} never opened")


class _Proxy:
    """Same-origin HTTP + WebSocket proxy (nginx stand-in), in its own thread and event loop."""

    def __init__(self, port, http_port, ws_port):
        self.port, self.http_port, self.ws_port = port, http_port, ws_port
        self._loop = None
        self._runner = None

    def start(self):
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(10)
        _wait_port(self.port)

    def _run(self, ready):
        from aiohttp import ClientSession, WSMsgType, web
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def handler(request):
            if request.path.rstrip("/") == "/ws" and request.headers.get("Upgrade", "").lower() == "websocket":
                down = web.WebSocketResponse(max_msg_size=0)
                await down.prepare(request)
                async with ClientSession() as s:
                    try:
                        up = await s.ws_connect(f"ws://127.0.0.1:{self.ws_port}/", max_msg_size=0)
                    except Exception:
                        await down.close()
                        return down

                    async def pump(src, dst):
                        async for m in src:
                            if m.type == WSMsgType.TEXT:
                                await dst.send_str(m.data)
                            elif m.type == WSMsgType.BINARY:
                                await dst.send_bytes(m.data)
                            else:
                                break
                        # pass the close code through (e.g. the relay's 4002 "device removed")
                        if not dst.closed:
                            await dst.close(code=src.close_code or 1000)
                    await asyncio.gather(pump(down, up), pump(up, down), return_exceptions=True)
                return down
            body = await request.read()
            headers = {k: v for k, v in request.headers.items()
                       if k.lower() in ("content-type", "user-agent", "accept", "origin")
                       or k.lower().startswith("access-control-request-")}
            async with ClientSession() as s:
                async with s.request(request.method, f"http://127.0.0.1:{self.http_port}{request.path_qs}",
                                     headers=headers, data=body or None, allow_redirects=False) as r:
                    data = await r.read()
                    out = {k: v for k, v in r.headers.items()
                           if k.lower() in ("content-type", "content-disposition") or k.lower().startswith("access-control-")}
                    out.setdefault("Access-Control-Allow-Origin", "*")
                    return web.Response(status=r.status, body=data, headers=out)

        async def main():
            app = web.Application(client_max_size=64 * 1024 * 1024)
            app.router.add_route("*", "/{tail:.*}", handler)
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
            ready.set()
        self._loop.run_until_complete(main())
        self._loop.run_forever()

    def stop(self):
        if self._loop:
            async def down():
                await self._runner.cleanup()
            fut = asyncio.run_coroutine_threadsafe(down(), self._loop)
            try:
                fut.result(5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)


class LocalRelay:
    PAIR_BUDGET = 8   # relay allows 10 /pair/create per 10 min per IP

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="infero-test-relay-")
        for f in ("relay.py", "agent.py", "bip39.txt"):
            shutil.copy(os.path.join(RELAY_SRC, f), self.dir)
        path = os.path.join(self.dir, "relay.py")
        with open(path) as f:
            src = f.read()
        with open(path, "w") as f:
            f.write(src.replace("'0.0.0.0'", "'127.0.0.1'"))
        self.http_port, self.ws_port, self.proxy_port = _free_port(), _free_port(), _free_port()
        self.base = f"http://127.0.0.1:{self.proxy_port}"
        self.ws_url = f"ws://127.0.0.1:{self.proxy_port}/ws"
        self.proc = None
        self.proxy = None
        self.pairs_since_start = 0
        self.log_path = os.path.join(self.dir, "relay.log")

    def start(self):
        env = dict(os.environ, HTTP_PORT=str(self.http_port), WS_PORT=str(self.ws_port), RELAY_WS_URL=self.ws_url)
        self._log = open(self.log_path, "a")
        self.proc = subprocess.Popen([sys.executable, "-u", "relay.py"], cwd=self.dir, env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT)
        _wait_port(self.http_port)
        _wait_port(self.ws_port)
        if not self.proxy:
            self.proxy = _Proxy(self.proxy_port, self.http_port, self.ws_port)
            self.proxy.start()
        self.pairs_since_start = 0
        return self

    def stop_process(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def restart(self):
        self.stop_process()
        self.start()

    def stop(self):
        self.stop_process()
        if self.proxy:
            self.proxy.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def before_pairing(self):
        """Restart the relay (resets its in-memory rate limit) when the pairing budget is used up."""
        if self.pairs_since_start >= self.PAIR_BUDGET:
            self.restart()
        self.pairs_since_start += 1

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def post(self, path, obj):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def tokens(self):
        try:
            with open(os.path.join(self.dir, "tokens.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def log_text(self):
        try:
            with open(self.log_path) as f:
                return f.read()
        except OSError:
            return ""


def parse_pair_script(script):
    out = {}
    for k in ("RELAY_WS", "RELAY_HTTP", "INSTANCE_ID", "TOKEN", "BROWSER_PUB", "CLIENT_NAME"):
        m = re.search(rf'^{k}="([^"]*)"', script, re.M)
        if not m:
            raise ValueError(f"pair script has no {k}: {script[:200]}")
        out[k] = m.group(1)
    return out


class LocalAgent:
    def __init__(self, relay, suffix=None):
        self.relay = relay
        self.dir = tempfile.mkdtemp(prefix="infero-test-agent-")
        self.suffix = suffix or ("t" + secrets.token_hex(2))
        with open(os.path.join(self.dir, "device_id"), "w") as f:
            f.write(self.suffix)
        shutil.copy(os.path.join(relay.dir, "agent.py"), self.dir)
        self.name = socket.gethostname().removesuffix(".local") + "-" + self.suffix
        self.proc = None
        self.log_path = os.path.join(self.dir, "agent.out")
        self.cfg = None

    def pair(self, code):
        """Do what the install script does with a pairing code: fetch it and save the instance."""
        status, script = self.relay.get(f"/pair/{code}")
        if status != 200 or "INSTANCE_ID=" not in script:
            raise RuntimeError(f"pair script fetch failed ({status}): {script[:200]}")
        v = parse_pair_script(script)
        self.cfg = {"instance_id": v["INSTANCE_ID"], "token": v["TOKEN"], "browser_pub": v["BROWSER_PUB"],
                    "relay_ws": v["RELAY_WS"], "relay_http": v["RELAY_HTTP"], "client_name": v["CLIENT_NAME"]}
        with open(os.path.join(self.dir, "instances.json"), "w") as f:
            json.dump([self.cfg], f, indent=2)
        return v

    def start(self):
        env = dict(os.environ, INFERO_DIR=self.dir, PYTHONUNBUFFERED="1")
        env.pop("HTTPS_PROXY", None), env.pop("HTTP_PROXY", None), env.pop("ALL_PROXY", None)
        self._log = open(self.log_path, "a")
        self.proc = subprocess.Popen([sys.executable, "-u", os.path.join(self.dir, "agent.py")], cwd=self.dir,
                                     env=env, stdout=self._log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def log_text(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def wait_log(self, pattern, timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = re.search(pattern, self.log_text())
            if m:
                return m
            time.sleep(0.2)
        raise AssertionError(f"agent log never matched {pattern!r}:\n{self.log_text()[-2000:]}")

    def being_dir(self, being_id):
        return os.path.join(self.dir, "beings", being_id)

    def instances(self):
        try:
            with open(os.path.join(self.dir, "instances.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []
