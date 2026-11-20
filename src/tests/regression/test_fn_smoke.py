"""Harness smoke checks: the app boots offline against the fake server."""
import pytest


@pytest.mark.case("harness")
def test_boots_offline(app, cdn):
    assert app.being_id
    assert app.js("() => typeof marked !== 'undefined'"), "marked must load from the CDN cache"
    assert app.js("() => !!window._identity || window.identityInit().then(i => !!i.pubkey)")
    assert not [u for u in cdn.blocked if "127.0.0.1" not in u and ("esm.sh" in u or "jsdelivr" in u)], cdn.blocked


@pytest.mark.case("harness")
def test_one_cycle(app, server):
    server.reply("hello from the fake model\n\n/call_for_human")
    app.send("TASK-ONE")
    assert "hello from the fake model" in app.consciousness()
    assert "TASK-ONE" in app.last_prompt()
