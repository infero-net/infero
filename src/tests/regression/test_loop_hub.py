"""Loop tests — Skill Hub search, install and upload against the stub hub (checklist 8.09–8.12)."""
import pytest


@pytest.mark.case("8.09")
def test_hub_search_renders_results(app, server):
    skills = app.js("() => window.hubSearch('demo', 'new', { limit: 7 })")
    assert sorted(s["name"] for s in skills) == ["echo_demo", "other_demo"]
    q = server.of_kind("hub_list")[-1]["query"]
    assert q["q"] == ["demo"] and q["sort"] == ["new"] and q["limit"] == ["7"]
    overlay = app.js("() => document.getElementById('hub-overlay-search')?.innerText || ''")
    assert "Skill Hub — 2 results" in overlay and "echo_demo" in overlay


@pytest.mark.case("8.10")
def test_hub_install_writes_and_evals(app, server):
    assert app.js("() => window.hubInstall('echo_demo')") is True
    rec = app.db_get(f"{app.being_id}/skill/echo_demo")
    assert rec["value"]["enable"] is True and rec["value"]["instruction"].startswith("# echo_demo")
    assert app.js("() => window.echo_demo_loaded") == 1
    # runtime fields the skill wrote are kept on reinstall; unchanged code is not re-run
    app.js("async k => { const r = await DB.get(k); r.value.cognition_note = 'mine'; await DB.put(r); }",
           f"{app.being_id}/skill/echo_demo")
    assert app.js("() => window.hubInstall('echo_demo')") is True
    assert app.js("() => window.echo_demo_loaded") == 1
    assert app.db_get(f"{app.being_id}/skill/echo_demo")["value"]["cognition_note"] == "mine"


@pytest.mark.case("8.10")
def test_hub_install_runs_js_variant(app):
    assert app.js("() => window.hubInstall('other_demo')") is True
    assert app.js("() => window.other_demo_loaded") is True


@pytest.mark.case("8.11")
def test_hub_install_unknown(app):
    assert app.js("() => window.hubInstall('no_such_skill')") is False
    assert app.db_get(f"{app.being_id}/skill/no_such_skill") is None


@pytest.mark.case("8.12")
def test_hub_upload_signed(app, server):
    bid = app.being_id
    app.db_put({"id": f"{bid}/skill/my_tool", "value": {"id": "my_tool", "instruction": "does things", "enable": True,
                                                        "code": {"js": "1"}, "code_readme": "call it", "contact": "me@x"}})
    app.js("async id => { const r = await DB.get(id); await DB.put({...r, name: 'Ada'}); }", bid)
    app.set_settings(companion_name="Sam")
    data = app.js("() => window.hubUpload('my_tool', { tags: ['util'] })")
    assert data["decision"] == "approved"
    sub = server.of_kind("hub_submit")[-1]
    b = sub["body"]
    assert b["name"] == "my_tool" and b["instruction"] == "does things" and b["tags"] == ["util"]
    assert b["being_name"] == "Ada" and b["companion_name"] == "Sam" and b["contact"] == "me@x"
    h = sub["headers"]
    pub = app.js("() => window.identityInit().then(i => i.pubkey)")
    assert h["x-infero-pubkey"] == pub and len(pub) == 66
    assert len(h["x-infero-sig"]) >= 128 and h["x-infero-ts"].isdigit()
    assert "✓ Approved" in app.js("() => document.getElementById('hub-overlay-upload').innerText")


@pytest.mark.case("8.12")
def test_hub_upload_missing_local_skill(app, server):
    r = app.js("() => window.hubUpload('not_here')")
    assert r["error"] == "local skill not found: not_here"
    assert not server.of_kind("hub_submit")
