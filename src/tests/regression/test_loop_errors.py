"""Loop tests — errors, retries and the overload fallback chain (checklist area 2).

The app waits 15 s before retrying after an error and 90 s for response headers; tests shorten
exactly those delays with App.fast_timers so the logic runs unchanged, just quicker."""
import pytest

from harness import Reply

CFH = "\n\n/call_for_human"


def system_msgs(app):
    return [t for r, t in app.chat() if r == "system"]


@pytest.mark.case("2.01")
def test_http_500_reported_then_retried(app, server):
    app.fast_timers({15000: 1500})
    server.reply(Reply(status=500, body='{"error":{"message":"server exploded"}}'), "recovered" + CFH)
    app.send("go", wait=False)
    app.wait_requests(2, timeout=20)
    app.wait_idle()
    msgs = system_msgs(app)
    assert any(m.startswith("System - [HTTP 500] HTTP 500:") and "server exploded" in m for m in msgs), msgs
    c = app.consciousness()
    assert "System - [HTTP 500]" in c and "recovered" in c


@pytest.mark.case("2.02")
def test_stop_during_retry_wait(app, server):
    app.fast_timers({15000: 3000})
    server.reply(Reply(status=500), "should not run" + CFH)
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('HTTP 500'))")
    app.click_send()
    app.wait(4500)
    assert len(server.llm_requests()) == 1


@pytest.mark.case("2.03")
def test_unreachable_server(app, server):
    app.fast_timers({15000: 60000})
    app.set_settings(apiEndpoint="http://127.0.0.1:9/mock/gemini")
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('System - ['))")
    assert any("[Network Unreachable]" in m for m in system_msgs(app)), system_msgs(app)
    app.click_send()


@pytest.mark.case("2.04")
def test_connection_reset_mid_stream(app, server):
    app.fast_timers({15000: 60000})
    server.reply(Reply(text="partial words here that get cut" + CFH, chunks=6, delay=1.2, drop_after=3))
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('System - ['))")
    msgs = system_msgs(app)
    assert any("[Connection Reset]" in m for m in msgs), msgs
    assert "**Digital Being - [" not in app.consciousness(), "a cut-off reply is not saved as a turn"
    app.click_send()


@pytest.mark.case("2.05")
def test_no_response_headers_times_out(app, server):
    app.fast_timers({90000: 1500, 15000: 60000})
    server.reply(Reply(head_delay=4))
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('System - ['))",
              timeout=15)
    msgs = system_msgs(app)
    assert any("[Timeout] No response in 90s" in m for m in msgs), msgs
    app.click_send()


@pytest.mark.case("2.06")
def test_error_inside_stream(app, server):
    app.fast_timers({15000: 60000})
    server.reply(Reply(text="starts fine" + CFH, chunks=3, sse_error={"code": 400, "message": "bad things"}))
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('System - ['))")
    msgs = system_msgs(app)
    assert any(m.startswith("System - [Error]") and "bad things" in m for m in msgs), msgs
    app.click_send()


@pytest.mark.case("2.07")
def test_overload_fallback_chain(app, server):
    app.use_format("anthropic", model="claude-fable-5")
    server.reply(Reply(status=529, body='{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'),
                 Reply(status=529, body='{"error":{"message":"overloaded"}}'),
                 "served by the last model" + CFH)
    app.send("go")
    models = [r["body"]["model"] for r in server.llm_requests()]
    assert models == ["claude-fable-5", "claude-opus-5", "claude-opus-4-8"]
    msgs = system_msgs(app)
    assert any("Model overloaded, falling back to claude-opus-5" in m for m in msgs)
    assert any("Model overloaded, falling back to claude-opus-4-8" in m for m in msgs)
    assert "served by the last model" in app.consciousness()


@pytest.mark.case("2.08")
def test_overload_outside_chain_is_a_normal_error(app, server):
    app.fast_timers({15000: 60000})
    app.use_format("anthropic")      # claude-sonnet-4.6: not in the chain
    server.reply(Reply(status=529, body='{"error":{"message":"overloaded"}}'))
    app.send("go", wait=False)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('System - ['))")
    msgs = system_msgs(app)
    assert any("[HTTP 529]" in m for m in msgs)
    assert not any("falling back" in m for m in msgs)
    assert len(server.llm_requests()) == 1
    app.click_send()


@pytest.mark.case("2.09")
def test_next_cycle_starts_from_configured_model(app, server):
    app.use_format("anthropic", model="claude-fable-5")
    server.reply(Reply(status=529, body="overloaded"), "ok" + CFH, "ok again" + CFH)
    app.send("one")
    app.send("two")
    models = [r["body"]["model"] for r in server.llm_requests()]
    assert models == ["claude-fable-5", "claude-opus-5", "claude-fable-5"]


@pytest.mark.case("2.10")
def test_unhandled_rejection_reported(app, server):
    server.reply("/exec browser\n```js\nPromise.reject(new Error('nobody catches me')); return 1\n```\n/call_for_human")
    app.send("go")
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('Unhandled Promise Rejection'))")
    assert any("nobody catches me" in m for m in system_msgs(app))
