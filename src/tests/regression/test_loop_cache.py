"""Loop tests — Gemini cached content: create, extend, rebuild, recover (checklist 4.05–4.08).

The custom provider gets its cache endpoint from settings.customCacheEndpoint via the settings panel;
tests set PROVIDERS.custom.cacheEndpoint directly."""
import pytest

from harness import Reply

CFH = "\n\n/call_for_human"


@pytest.fixture
def cached_app(app, server):
    app.js("ep => { PROVIDERS.custom.cacheEndpoint = ep; }", f"{server.base}/mock/cache")
    return app


def cache_ops(server):
    return [(r["op"], r["path"]) for r in server.of_kind("cache")]


@pytest.mark.case("4.05")
def test_cache_created_then_used(cached_app, server):
    app = cached_app
    server.reply(Reply(text="first" + CFH, usage={"prompt": 5000, "cached": 0, "output": 5}), "second" + CFH)
    app.send("one")
    app.until_py(lambda: server.of_kind("cache"), msg="cache create")
    app.until("() => cacheName === 'cachedContents/test1'")
    create = server.of_kind("cache")[0]
    assert create["op"] == "create" and create["query"].get("key") == ["test-token"]
    assert create["body"]["model"] == "models/gemini-3.1-pro-preview"
    assert create["body"]["ttl"] == "3600s"
    cached_len = app.js("() => cachedLength")
    app.send("two")
    body = server.llm_requests()[-1]["body"]
    assert body["cachedContent"] == "cachedContents/test1" and "systemInstruction" not in body
    sent = body["contents"][0]["parts"][0]["text"]
    assert "two" in sent and len(sent) <= len(app.consciousness()) - cached_len + 10, "only the delta is sent"


@pytest.mark.case("4.05")
def test_small_prompt_not_cached(cached_app, server):
    server.reply(Reply(text="x" + CFH, usage={"prompt": 4000, "cached": 0, "output": 5}))
    cached_app.send("one")
    cached_app.wait(800)
    assert cache_ops(server) == []


@pytest.mark.case("4.06")
def test_small_growth_extends_ttl(cached_app, server):
    app = cached_app
    server.reply(Reply(text="a" + CFH, usage={"prompt": 5000, "cached": 0, "output": 5}),
                 Reply(text="b" + CFH, usage={"prompt": 9000, "cached": 5000, "output": 5}))
    app.send("one")
    app.until("() => cacheName === 'cachedContents/test1'")
    app.send("two")
    app.until_py(lambda: len(server.of_kind("cache")) >= 2, msg="ttl patch")
    op, path = cache_ops(server)[1]
    assert op == "patch" and path.endswith("/cachedContents/test1")
    assert server.of_kind("cache")[1]["body"] == {"ttl": "3600s"}


@pytest.mark.case("4.07")
def test_large_growth_rebuilds_and_deletes_old(cached_app, server):
    app = cached_app
    server.reply(Reply(text="a" + CFH, usage={"prompt": 5000, "cached": 0, "output": 5}),
                 Reply(text="b" + CFH, usage={"prompt": 20000, "cached": 5000, "output": 5}))
    app.send("one")
    app.until("() => cacheName === 'cachedContents/test1'")
    app.send("two")
    app.until("() => cacheName === 'cachedContents/test2'")
    app.until_py(lambda: any(op == "delete" for op, _ in cache_ops(server)), msg="old cache deleted")
    ops = cache_ops(server)
    assert [o for o, _ in ops] == ["create", "create", "delete"]
    assert ops[2][1].endswith("/cachedContents/test1")


@pytest.mark.case("4.08")
def test_expired_cache_rebuilt_and_retried(cached_app, server):
    app = cached_app
    server.reply(Reply(text="a" + CFH, usage={"prompt": 5000, "cached": 0, "output": 5}),
                 Reply(status=400, body='{"error":{"message":"CachedContent not found (or permission denied)"}}'),
                 "after rebuild" + CFH)
    app.send("one")
    app.until("() => cacheName === 'cachedContents/test1'")
    app.send("two")
    reqs = server.llm_requests()
    assert len(reqs) == 3
    assert reqs[1]["body"]["cachedContent"] == "cachedContents/test1"
    assert reqs[2]["body"]["cachedContent"] == "cachedContents/test2"
    assert any("Cache expired, rebuilding" in t for r, t in app.chat() if r == "system")
    assert "after rebuild" in app.consciousness()
