"""Loop tests — streaming in each wire format, provider headers (checklist area 3, loop cases)."""
import json

import pytest

from harness import Reply
from harness.server import ENCODERS

CFH = "\n\n/call_for_human"


def sse_body(fmt, reply):
    events, tail = ENCODERS[fmt](reply)
    return "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events + tail)


@pytest.mark.case("3.15", "3.16", "3.17")
@pytest.mark.parametrize("fmt", ["gemini", "openai", "anthropic"])
def test_stream_text_thinking_usage(app, server, fmt):
    app.use_format(fmt)
    server.reply(Reply(text="visible *answer*" + CFH, thinking="hidden reasoning", chunks=5,
                       usage={"prompt": 5000, "cached": 1200, "output": 42}))
    app.send("go")
    ai = app.js("() => { const d = [...document.querySelectorAll('#chat-box .msg.ai')].pop(); "
                "return { think: d.querySelector('.msg-thinking')?.innerText || '', html: d.innerHTML }; }")
    assert "hidden reasoning" in ai["think"]
    assert "<em>answer</em>" in ai["html"]
    c = app.consciousness()
    assert "visible *answer*" in c and "hidden reasoning" not in c, "thinking is shown, not saved"
    tokens = app.js("() => [_lastPromptTokens, _lastCachedTokens]")
    expected_cached = 0 if fmt == "openai" else 1200    # OpenAI usage has no cached count
    assert tokens == [5000, expected_cached]


@pytest.mark.case("3.18")
@pytest.mark.parametrize("fmt", ["gemini", "openai", "anthropic"])
def test_sse_line_split_across_writes(app, server, fmt):
    app.use_format(fmt)
    server.reply(Reply(text="split-safe text with ümlauts ✓" + CFH, chunks=4, split_line=True))
    app.send("go")
    assert "split-safe text with ümlauts ✓" in app.consciousness()


@pytest.mark.case("3.19")
def test_model_written_headers_stripped(app, server):
    server.reply("**Digital Being - [2026-01-01 10:00:00]**\nreal content\n**Digital Being - [2026-01-01 10:01]**")
    app.send("go")
    app.wait_requests(2)      # no stop signal in that reply, so the default reply ends the loop
    app.wait_idle()
    c = app.consciousness()
    assert "2026-01-01 10:0" not in c, "model-written headers are removed"
    i = c.index("real content")
    assert c.rindex("**Digital Being - [", 0, i) > c.rindex("[System Environment]", 0, i)
    assert c[c.rindex("**Digital Being - [", 0, i):i].count("\n") == 1, "exactly one header before the text"


# ---- 3.12 provider headers (requests to real provider hosts are intercepted) ----------------
PROVIDER_CASES = [
    # provider, model, host pattern, format, expected headers
    ("anthropic", "claude-opus-5", "https://api.anthropic.com/**", "anthropic",
     {"x-api-key": "tok-anthropic", "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true", "anthropic-beta": "server-side-fallback-2026-06-01"}),
    ("anthropic", "claude-sonnet-4.6", "https://api.anthropic.com/**", "anthropic",
     {"x-api-key": "tok-anthropic", "anthropic-beta": None}),
    ("google", "gemini-3.1-pro", "https://generativelanguage.googleapis.com/**", "gemini",
     {"x-goog-api-key": "tok-google", "authorization": None}),
    ("openai", "gpt-5.4", "https://api.openai.com/**", "openai", {"authorization": "Bearer tok-openai"}),
    ("infero", "gemini-3.1-pro", "https://infero.net/api/**", "gemini",
     {"authorization": "Bearer {infero_key}", "x-client-id": "{client_id}"}),
]


@pytest.mark.case("3.12")
@pytest.mark.parametrize("provider,model,pattern,fmt,expected", PROVIDER_CASES,
                         ids=[f"{c[0]}-{c[1]}" for c in PROVIDER_CASES])
def test_provider_headers(app, provider, model, pattern, fmt, expected):
    seen = []

    def handle(route):
        seen.append(dict(route.request.headers))
        route.fulfill(status=200, headers={"content-type": "text/event-stream", "access-control-allow-origin": "*"},
                      body=sse_body(fmt, Reply(text="hi" + CFH)))
    app.page.route(pattern, handle)
    tokens = {provider: f"tok-{provider}"} if provider != "infero" else {}
    app.set_settings(provider=provider, model=model, tokens=tokens)
    keys = app.js("() => ({ infero_key: INFERO_KEY, client_id: CLIENT_ID })")
    app.go(wait=False)
    app.until_py(lambda: seen, timeout=20, msg="request to the provider")
    app.wait_idle()
    h = seen[0]
    for k, v in expected.items():
        if v is None:
            assert k not in h, f"{k} should not be sent"
        else:
            assert h.get(k) == v.format(**keys), f"{k}: {h.get(k)!r}"
