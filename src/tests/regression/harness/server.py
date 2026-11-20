"""Local server for the regression suite.

One ThreadingHTTPServer on 127.0.0.1 does three jobs:
  * serves the app under test (`src/`, or any copy given with --src) as static files;
  * plays the LLM at /mock/<format> (gemini, openai, anthropic), streaming SSE in that format;
  * stubs the Skill Hub at /hub/... and the Gemini cache API at /mock/cache.

Every POST/PATCH/DELETE is recorded so tests can check what the page sent.

Replies are scripted: tests queue Reply objects (`server.reply(...)`); with an empty queue the
server answers `ok` + `/call_for_human`, so a stray cycle (e.g. auto-start) always ends.
"""
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DEFAULT_TEXT = "ok\n\n/call_for_human"


@dataclass
class Reply:
    """One scripted LLM response.

    text      : the visible reply text
    thinking  : reasoning text (sent as thought / reasoning_content / thinking_delta)
    chunks    : how many SSE events the text is split into
    delay     : seconds spread over the stream (0 = as fast as possible)
    head_delay: seconds to wait before sending the response headers
    status    : HTTP status; non-200 sends `body` (or a JSON error) instead of a stream
    body      : raw response body for non-200 replies
    sse_error : send `data: {"error": ...}` after the first chunk
    drop_after: close the connection after this many chunks (connection reset mid-stream)
    usage     : dict(prompt=, cached=, output=) token counts reported at the end
    split_line: split one SSE line across two TCP writes (parser robustness)
    """
    text: str = DEFAULT_TEXT
    thinking: str = ""
    chunks: int = 3
    delay: float = 0.0
    head_delay: float = 0.0
    status: int = 200
    body: str = ""
    sse_error: object = None
    drop_after: int = -1
    usage: dict = field(default_factory=lambda: {"prompt": 100, "cached": 0, "output": 10})
    split_line: bool = False
    reasoning_key: str = "reasoning_content"   # OpenAI-format field for thinking text ("reasoning" on some providers)
    usage_choices: str = "empty"   # OpenAI usage chunk: "empty" = choices: [] (OpenAI include_usage), "delta" = [{delta: {}}]


def _split(text, n):
    if n <= 1 or len(text) <= 1:
        return [text]
    size = max(1, -(-len(text) // n))
    return [text[i:i + size] for i in range(0, len(text), size)]


# --- SSE encoders, one per wire format ---------------------------------------
def _gemini_events(r):
    ev = []
    if r.thinking:
        ev.append({"candidates": [{"content": {"parts": [{"text": r.thinking, "thought": True}]}}]})
    for part in _split(r.text, r.chunks):
        ev.append({"candidates": [{"content": {"parts": [{"text": part}]}}]})
    u = r.usage
    tail = [{"usageMetadata": {"promptTokenCount": u.get("prompt", 0), "cachedContentTokenCount": u.get("cached", 0),
                               "candidatesTokenCount": u.get("output", 0)}}]
    return ev, tail


def _openai_events(r):
    ev = []
    if r.thinking:
        ev.append({"choices": [{"delta": {r.reasoning_key: r.thinking}}]})
    for part in _split(r.text, r.chunks):
        ev.append({"choices": [{"delta": {"content": part}}]})
    u = r.usage
    choices = [] if r.usage_choices == "empty" else [{"delta": {}, "finish_reason": "stop"}]
    tail = [{"choices": choices, "usage": {"prompt_tokens": u.get("prompt", 0), "completion_tokens": u.get("output", 0)}},
            "[DONE]"]
    return ev, tail


def _anthropic_events(r):
    u = r.usage
    ev = [{"type": "message_start", "message": {"usage": {
        "input_tokens": u.get("prompt", 0) - u.get("cached", 0) - u.get("created", 0),
        "cache_read_input_tokens": u.get("cached", 0), "cache_creation_input_tokens": u.get("created", 0)}}}]
    if r.thinking:
        ev.append({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": r.thinking}})
    for part in _split(r.text, r.chunks):
        ev.append({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": part}})
    tail = [{"type": "message_delta", "usage": {"output_tokens": u.get("output", 0)}}, {"type": "message_stop"}]
    return ev, tail


ENCODERS = {"gemini": _gemini_events, "openai": _openai_events, "anthropic": _anthropic_events}


# --- Hub fixtures --------------------------------------------------------------
def default_hub_skills():
    """Skills the stub hub serves. `web_search_proxy` is the app's DEFAULT_HUB_SKILLS entry and
    `infero_receptionist` the first-visit skill, so both must exist for a clean boot."""
    def skill(name, code=None, instruction=None):
        return {"name": name, "instruction": instruction or f"# {name}\nTest skill served by the stub hub.",
                "code": code, "code_readme": f"window.{name}_loaded", "contact": "", "note": "",
                "tags": ["test"], "severity": "safe", "score": 7, "installs": 1, "being_name": "tester",
                "author_hash_short": "abcd1234", "review": "fine", "created_at": 0}
    return {
        "web_search_proxy": skill("web_search_proxy"),
        "infero_receptionist": skill("infero_receptionist"),
        "echo_demo": skill("echo_demo", code="window.echo_demo_loaded = (window.echo_demo_loaded || 0) + 1;"),
        "other_demo": skill("other_demo", code={"js": "window.other_demo_loaded = true;", "python": "x = 1"}),
    }


class FakeServer:
    def __init__(self, src_dir, port=0):
        self.src_dir = os.path.abspath(src_dir)
        self.port = port or _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._lock = threading.Lock()
        self.requests = []          # every recorded non-GET request + hub GETs
        self._queue = []            # scripted replies
        self.responder = None       # optional fn(request_dict) -> Reply, used before the queue
        self.hub_skills = default_hub_skills()
        self.hub_submit_response = {"decision": "approved", "severity": "safe", "score": 8, "review": "ok"}
        self.cache_fail = None      # e.g. {"create": 500}
        self._cache_n = 0
        self._srv = None

    # ---- lifecycle ----
    def start(self):
        handler = partial(_Handler, self, directory=self.src_dir)
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def reset(self):
        with self._lock:
            self.requests.clear()
            self._queue.clear()
        self.responder = None
        self.hub_skills = default_hub_skills()
        self.hub_submit_response = {"decision": "approved", "severity": "safe", "score": 8, "review": "ok"}
        self.cache_fail = None
        self._cache_n = 0

    # ---- scripting ----
    def reply(self, *replies, **kw):
        """Queue replies. `server.reply("text")`, `server.reply(Reply(...), Reply(...))`,
        or `server.reply(text="...", delay=2)`."""
        with self._lock:
            if kw:
                self._queue.append(Reply(**kw))
            for r in replies:
                self._queue.append(r if isinstance(r, Reply) else Reply(text=r))

    def queued(self):
        with self._lock:
            return len(self._queue)

    def _next_reply(self, req):
        if self.responder:
            r = self.responder(req)
            if r is not None:
                return r if isinstance(r, Reply) else Reply(text=r)
        with self._lock:
            return self._queue.pop(0) if self._queue else Reply()

    # ---- inspection ----
    def record(self, entry):
        with self._lock:
            self.requests.append(entry)

    def llm_requests(self, fmt=None):
        with self._lock:
            return [r for r in self.requests if r["kind"] == "llm" and (fmt is None or r["format"] == fmt)]

    def of_kind(self, kind):
        with self._lock:
            return [r for r in self.requests if r["kind"] == kind]

    def last_llm(self):
        reqs = self.llm_requests()
        return reqs[-1] if reqs else None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def prompt_text(body):
    """All text the page sent in a request body, whatever the format (for 'did X reach the LLM')."""
    out = []

    def walk(o):
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("data",) and isinstance(v, str) and len(v) > 200:
                    continue  # base64 image payloads
                if k == "url" and isinstance(v, str) and v.startswith("data:"):
                    continue
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(body)
    return "\n".join(out)


class _Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, fake, *a, **kw):
        self.fake = fake
        super().__init__(*a, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # Never let the browser cache the app under test (two --src runs share a port range)
        if self.command == "GET":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ---- helpers ----
    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8", "replace")) if raw else None
        except ValueError:
            return raw.decode("utf-8", "replace")

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _rec(self, kind, body=None, **extra):
        u = urlparse(self.path)
        entry = {"kind": kind, "method": self.command, "path": u.path, "query": parse_qs(u.query),
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "t": time.time()}
        entry.update(extra)
        self.fake.record(entry)
        return entry

    # ---- routes ----
    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/hub/"):
            return self._hub_get(u)
        if u.path == "/":
            self.path = "/index.html" + (("?" + u.query) if u.query else "")
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path.startswith("/mock/cache"):
            return self._cache("create", body)
        m = re.match(r"^/mock/(gemini|openai|anthropic)\b", u.path)
        if m:
            return self._llm(m.group(1), body)
        if u.path == "/hub/submit":
            self._rec("hub_submit", body)
            return self._json(200, self.fake.hub_submit_response)
        self._rec("other", body)
        self._json(404, {"error": "not found"})

    def do_PATCH(self):
        body = self._body()
        if urlparse(self.path).path.startswith("/mock/cache"):
            return self._cache("patch", body)
        self._json(404, {})

    def do_DELETE(self):
        if urlparse(self.path).path.startswith("/mock/cache"):
            return self._cache("delete", None)
        self._rec("other")
        self._json(404, {})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _hub_get(self, u):
        if u.path == "/hub/list":
            self._rec("hub_list")
            q = (parse_qs(u.query).get("q") or [""])[0].lower()
            skills = [s for s in self.fake.hub_skills.values() if not q or q in s["name"].lower()]
            return self._json(200, {"skills": skills})
        m = re.match(r"^/hub/skill/([^/]+)$", u.path)
        if m:
            from urllib.parse import unquote
            name = unquote(m.group(1))
            self._rec("hub_skill", name=name)
            s = self.fake.hub_skills.get(name)
            return self._json(200, s) if s else self._json(404, {"error": "no such skill"})
        self._json(404, {})

    def _cache(self, op, body):
        self._rec("cache", body, op=op)
        fail = (self.fake.cache_fail or {}).get(op)
        if fail:
            return self._json(fail, {"error": {"message": "cache failure"}})
        if op == "create":
            self.fake._cache_n += 1
            return self._json(200, {"name": f"cachedContents/test{self.fake._cache_n}"})
        return self._json(200, {})

    def _llm(self, fmt, body):
        req = self._rec("llm", body, format=fmt)
        r = self.fake._next_reply(req)
        req["reply"] = r
        if r.head_delay:
            time.sleep(r.head_delay)
        if r.status != 200:
            data = (r.body or json.dumps({"error": {"message": f"fake error {r.status}"}})).encode()
            self.send_response(r.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        events, tail = ENCODERS[fmt](r)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        step = r.delay / max(1, len(events))
        try:
            for i, ev in enumerate(events):
                if r.drop_after >= 0 and i >= r.drop_after:
                    # Abrupt close: RST instead of a clean FIN so fetch() sees a network error
                    import struct
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.connection.close()
                    return
                if step:
                    time.sleep(step)
                line = f"data: {json.dumps(ev)}\n\n".encode()
                if r.split_line and len(line) > 8:
                    self.wfile.write(line[:len(line) // 2]); self.wfile.flush()
                    time.sleep(0.05)
                    self.wfile.write(line[len(line) // 2:])
                else:
                    self.wfile.write(line)
                self.wfile.flush()
                if i == 0 and r.sse_error is not None:
                    self.wfile.write(f"data: {json.dumps({'error': r.sse_error})}\n\n".encode())
                    self.wfile.flush()
                    return
            for ev in tail:
                data = ev if isinstance(ev, str) else json.dumps(ev)
                self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            req["aborted"] = True  # the page aborted the stream
