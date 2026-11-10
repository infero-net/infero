"""Reload tests — default skills, skill code on load, identity, hub skills on new beings (area 8)."""
import pytest

from harness.bugs import known_bug


def skill(app, name):
    rec = app.db_get(f"{app.being_id}/skill/{name}")
    return rec and rec["value"]


@pytest.mark.case("8.02")
def test_reseed_keeps_user_fields(app):
    bid = app.being_id
    app.js("async k => { const r = await DB.get(k); r.value.enable = false; r.value.note = 'my note'; "
           "r.value.contact = 'me@here'; await DB.put(r); }", f"{bid}/skill/freedom")
    app.reload()
    v = skill(app, "freedom")
    assert v["enable"] is False and v["note"] == "my note" and v["contact"] == "me@here"


@pytest.mark.case("8.03")
def test_changed_default_skill_text_restored(app):
    bid = app.being_id
    default = app.js("() => DEFAULT_SKILLS.find(s => s.name === 'freedom').instruction")
    app.js("async k => { const r = await DB.get(k); r.value.instruction = 'edited by hand'; r.value.enable = false; "
           "await DB.put(r); }", f"{bid}/skill/freedom")
    app.reload()
    v = skill(app, "freedom")
    assert v["instruction"] == default and v["enable"] is False


@pytest.mark.case("8.04")
def test_enabled_skill_code_runs_on_load(app):
    bid = app.being_id
    app.db_put({"id": f"{bid}/skill/counter", "value": {"id": "counter", "instruction": "x", "enable": True,
                                                       "code": "window.__counter = (window.__counter || 0) + 1;"}})
    app.db_put({"id": f"{bid}/skill/off", "value": {"id": "off", "instruction": "x", "enable": False,
                                                   "code": "window.__off = true;"}})
    app.db_put({"id": f"{bid}/skill/py_only", "value": {"id": "py_only", "instruction": "x", "enable": True,
                                                       "code": {"python": "raise SystemExit"}}})
    app.reload()
    assert app.js("() => window.__off") is None
    assert app.js("() => window.__counter") >= 1
    with known_bug("BUG-06", reason="boot runs every enabled skill's code twice"):
        assert app.js("() => window.__counter") == 1


@pytest.mark.case("8.05")
def test_throwing_skill_code_does_not_stop_load(app):
    bid = app.being_id
    app.db_put({"id": f"{bid}/skill/aaa_broken", "value": {"id": "aaa_broken", "instruction": "x", "enable": True,
                                                          "code": "throw new Error('skill exploded')"}})
    app.db_put({"id": f"{bid}/skill/zzz_after", "value": {"id": "zzz_after", "instruction": "x", "enable": True,
                                                         "code": "window.__after = true;"}})
    app.reload()
    assert app.js("() => window.__after") is True
    assert any("code eval failed" in t and "aaa_broken" in t for _, t in app.console)


@pytest.mark.case("8.07")
def test_identity_kept_across_reloads(app):
    bid = app.being_id
    mnemonic = app.db_get(f"{bid}/identity")["value"]["mnemonic"]
    app.reload()
    assert app.js("() => window.identityInit().then(i => i.pubkey)") == bid
    assert app.db_get(f"{bid}/identity")["value"]["mnemonic"] == mnemonic


@pytest.mark.case("8.08")
def test_identity_export_and_import(app):
    bid = app.being_id
    app.reload()                                   # clean session identity (see BUG-04)
    words = app.db_get(f"{bid}/identity")["value"]["mnemonic"]
    app.js("() => window.identityExport()")
    overlay = app.js("() => document.querySelector('.identity-export-overlay')?.innerText || ''")
    assert all(w in overlay for w in words.split())
    app.page.click(".identity-export-overlay button")
    app.new_being()
    app.reload()
    msg = app.js("w => window.identityImport(w)", "  " + words.upper() + "  ")
    assert msg.startswith("Identity replaced")
    assert app.js("() => window._identity.pubkey") == bid
    with pytest.raises(Exception, match="checksum failed"):
        app.js("() => window.identityImport('abandon abandon abandon abandon abandon abandon abandon abandon "
               "abandon abandon abandon abandon')")


@pytest.mark.case("8.13")
def test_hub_defaults_only_for_new_beings(app, server):
    n = len(server.of_kind("hub_skill"))
    assert n >= 1
    app.reload()
    assert len(server.of_kind("hub_skill")) == n, "reloading an existing being installs nothing from the hub"
