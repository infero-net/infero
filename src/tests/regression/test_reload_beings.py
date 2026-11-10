"""Reload tests — creating, switching, duplicating, deleting, exporting and importing beings (area 6)."""
import json
import re

import pytest

from harness.bugs import known_bug

CFH = "\n\n/call_for_human"


def act_and_wait_boot(app, fn, arg=None, changed_from=None):
    """Run an app action that may reload the page (fix) or not (dev), then wait for the app."""
    app._call_may_navigate(fn, arg)
    if changed_from is not None:
        app.until("old => typeof currentBeingId !== 'undefined' && currentBeingId && currentBeingId !== old",
                  changed_from, timeout=40, msg="being changed")
    app.wait_boot()
    app.stop_auto_infer()


def chat_with(app, server, text):
    server.reply(f"reply to {text}" + CFH)
    app.send(text)
    app.settle()


# ---- 6.01 new being ---------------------------------------------------------------------
@pytest.mark.case("6.01", "8.01", "8.07", "8.13")
def test_new_being(app, server):
    bid = app.being_id
    assert re.fullmatch(r"[0-9a-f]{66}", bid), "a being's id is its identity pubkey"
    ident = app.db_get(f"{bid}/identity")
    assert ident and len(ident["value"]["mnemonic"].split()) == 12
    skills = {s["name"]: s for s in app.js("id => DB.listSkills(id)", bid)}
    defaults = app.js("() => DEFAULT_SKILLS.map(s => s.name)")
    assert len(defaults) == 9 and all(skills[n]["enable"] is True for n in defaults)
    hub_defaults = app.js("() => DEFAULT_HUB_SKILLS")
    assert all(n in skills for n in hub_defaults), "default hub skills installed on a new being"
    assert [r["name"] for r in server.of_kind("hub_skill")] == hub_defaults


@pytest.mark.case("6.01", "8.07")
def test_new_being_session_identity_is_its_own(app):
    """The identity the page signs hub requests with must be the being's own."""
    bid = app.being_id
    app.until("() => !!window._identity", msg="identity loaded")
    app.wait(1000)                # the stray init writes its record asynchronously (Firefox: after boot)
    with known_bug("BUG-04", reason="identity_seed's init runs before the being id is set", intermittent=True):
        assert app.db_get("null/identity") is None, "no stray identity record (written on every first boot)"
        assert app.js("() => window.identityInit().then(i => i.pubkey)") == bid


@pytest.mark.case("6.01")
def test_new_being_button_makes_another(app):
    first = app.being_id
    second = app.new_being()
    assert second != first and set(app.beings()) >= {first, second}


# ---- 6.02 switching (changed by the isolation fix: reload instead of in place) ------------------
@pytest.mark.case("6.02", "7.03")
def test_switch_shows_the_other_beings_chat_and_ui(app, server):
    first = app.being_id
    chat_with(app, server, "TASK-FIRST")
    app.js("() => { document.getElementById('html-div').innerHTML = '<p id=ui-first>FIRST UI</p>'; return saveBeing(); }")
    second = app.new_being()
    chat_with(app, server, "TASK-SECOND")
    app.js("() => { window.__samePage = true; }")
    app.switch(first)
    assert "TASK-FIRST" in app.chat_text() and "TASK-SECOND" not in app.chat_text()
    assert app.js("() => document.getElementById('ui-first')?.textContent") == "FIRST UI"
    reloaded = not app.js("() => !!window.__samePage")
    if app.fixed:
        assert reloaded and f"being={first}" in app.page.url, "fix: switching reloads as ?being=<id>"
    else:
        assert not reloaded, "dev: switching happens in place"
    app.switch(second)
    assert "TASK-SECOND" in app.chat_text() and "TASK-FIRST" not in app.chat_text()


@pytest.mark.case("6.03")
def test_reload_keeps_the_open_being(app, server):
    first = app.being_id
    chat_with(app, server, "TASK-FIRST")
    second = app.new_being()
    chat_with(app, server, "TASK-SECOND")   # second is now the most recently saved
    app.switch(first)
    app.reload()
    with known_bug("ISO-RELOAD", unless=app.fixed, reason="dev reopens the most recently saved being"):
        assert app.being_id == first


@pytest.mark.case("6.04")
def test_unknown_being_in_url_falls_back(app, server):
    existing = app.being_id
    app.page.goto(app.url + "?being=" + "0" * 66)
    app.wait_boot()
    assert app.being_id in app.beings()
    assert existing in app.beings()


# ---- 6.05 duplicate -------------------------------------------------------------------------
@pytest.mark.case("6.05")
def test_duplicate_being(app, server):
    src = app.being_id
    chat_with(app, server, "TASK-ORIGINAL")
    app.db_put({"id": f"{src}/core_mem.md", "value": "core memory of the original"})
    app.db_put({"id": f"{src}/skill/own_skill", "value": {"id": "own_skill", "instruction": "mine", "enable": True}})
    app.js("() => { document.getElementById('html-div').innerHTML = '<b id=dup-ui>UI</b>'; return saveBeing(); }")
    before = app.stored(src)
    act_and_wait_boot(app, "() => duplicateBeing()", changed_from=src)
    fork = app.being_id
    assert fork != src and fork in app.beings()
    c = app.stored(fork) or app.consciousness()
    assert "TASK-ORIGINAL" in c and f"Being forked from {src}" in c
    assert app.stored(src) == before, "the original is unchanged"
    assert (app.db_get(f"{fork}/snapshot") or {}).get("htmlContent", "").find("dup-ui") >= 0
    with known_bug("BUG-02", reason="duplicate copies only consciousness and the UI snapshot"):
        assert (app.db_get(f"{fork}/core_mem.md") or {}).get("value") == "core memory of the original"
        assert app.db_get(f"{fork}/skill/own_skill") is not None


# ---- 6.06 / 6.07 delete ------------------------------------------------------------------------
@pytest.mark.case("6.06")
def test_delete_being_opens_another(app, server):
    keep = app.being_id
    chat_with(app, server, "TASK-KEEP")
    gone = app.new_being()
    chat_with(app, server, "TASK-GONE")
    act_and_wait_boot(app, "() => deleteBeing()", changed_from=gone)
    assert gone not in app.beings()
    assert app.being_id == keep and "TASK-KEEP" in app.chat_text()
    assert any(d[0] == "confirm" for d in app.dialogs)


@pytest.mark.case("6.06")
def test_delete_being_removes_all_its_records(app, server):
    keep = app.being_id
    gone = app.new_being()
    chat_with(app, server, "TASK-GONE")
    app.db_put({"id": f"{gone}/core_mem.md", "value": "x"})
    act_and_wait_boot(app, "() => deleteBeing()", changed_from=gone)
    with known_bug("BUG-03", reason="delete removes only the metadata and consciousness records"):
        assert app.db_keys(gone) == []
    assert app.being_id == keep


@pytest.mark.case("6.06")
def test_delete_cancelled_keeps_being(app, server):
    bid = app.being_id
    app.dialog_answer = False
    app.js("() => deleteBeing()")
    assert app.being_id == bid and bid in app.beings()


@pytest.mark.case("6.07")
def test_delete_last_being_creates_fresh(app, server):
    only = app.being_id
    assert app.beings() == [only]
    act_and_wait_boot(app, "() => deleteBeing()", changed_from=only)
    assert app.being_id != only and len(app.beings()) == 1


# ---- 6.08 – 6.10 export / import ---------------------------------------------------------------
def export(app, work_dir):
    import pathlib
    return pathlib.Path(app.download('button[title="Export Being"]', work_dir))


def import_file(app, path_or_text, name="being.json"):
    import pathlib
    data = pathlib.Path(path_or_text).read_bytes() if not isinstance(path_or_text, str) else path_or_text
    app.upload("#import-file", name, data, "application/json")


@pytest.mark.case("6.08")
def test_export_being(app, server, work_dir):
    bid = app.being_id
    chat_with(app, server, "TASK-EXPORT")
    app.db_put({"id": f"{bid}/core_mem.md", "value": "exported memory"})
    path = export(app, work_dir)
    assert path.name == f"{bid}.json"
    data = json.loads(path.read_text())
    assert data["sourceId"] == bid and data["version"]
    ids = {r["id"] for r in data["records"]}
    assert {bid, f"{bid}/consciousness.txt", f"{bid}/core_mem.md", f"{bid}/identity", f"{bid}/snapshot"} <= ids
    assert all(i == bid or i.startswith(bid + "/") for i in ids), "only this being's records"


@pytest.mark.case("6.09")
def test_import_being_round_trip(app, server, work_dir):
    other = app.being_id
    chat_with(app, server, "TASK-OTHER")
    src = app.new_being()
    chat_with(app, server, "TASK-IMPORTED")
    path = export(app, work_dir)
    act_and_wait_boot(app, "() => deleteBeing()", changed_from=src)
    assert src not in app.beings()
    import_file(app, path)
    app.until("id => currentBeingId === id", src, timeout=40, msg="imported being opened")
    app.wait_boot()
    assert "TASK-IMPORTED" in app.consciousness()
    assert other in app.beings() and "TASK-OTHER" in app.stored(other)


@pytest.mark.case("6.09")
def test_import_over_existing_needs_confirm(app, server, work_dir):
    bid = app.being_id
    chat_with(app, server, "TASK-V1")
    path = export(app, work_dir)
    chat_with(app, server, "TASK-V2")
    app.dialog_answer = False
    import_file(app, path)
    app.until_py(lambda: any("already exists" in m for _, m in app.dialogs), msg="overwrite prompt")
    app.wait(500)
    assert "TASK-V2" in app.stored(bid), "declined: nothing overwritten"


@pytest.mark.case("6.10")
def test_import_malformed_file(app, work_dir):
    before = sorted(app.db_keys())
    import_file(app, '{"hello": "not a being"}', "bad.json")
    app.until_py(lambda: any(t == "alert" for t, _ in app.dialogs), msg="error alert")
    assert any("Failed to import being: not a valid being export" in m for _, m in app.dialogs)
    assert sorted(app.db_keys()) == before


# ---- 6.12 / 6.13 URL entry points -----------------------------------------------------------------
@pytest.mark.case("6.12")
def test_first_visit_opens_receptionist(new_app, server):
    a = new_app()
    a.open("", auto_infer=False)
    assert [r["name"] for r in server.of_kind("hub_skill")][-1] == "infero_receptionist"
    card = a.js("() => document.getElementById('html-div').innerText")
    assert "Hub experience" in card and "infero_receptionist" in card
    assert "skills=" not in a.page.url


@pytest.mark.case("6.13")
def test_skills_url_installs_into_fresh_being(app, server):
    old = app.being_id
    app.page.goto(app.url + "?skills=echo_demo,no_such_skill")
    app.wait_boot()
    app.stop_auto_infer()
    assert app.being_id != old
    assert app.db_get(f"{app.being_id}/skill/echo_demo") is not None
    card = app.js("() => document.getElementById('html-div').innerText")
    assert "installed: echo_demo" in card and "failed: no_such_skill" in card
    assert app.js("() => window.echo_demo_loaded") >= 1


# ---- 6.14 one tab per being (isolation fix) ------------------------------------------------------
@pytest.mark.case("6.14")
def test_same_being_not_open_in_two_tabs(app, new_app, server):
    first = app.being_id
    chat_with(app, server, "TASK-TAB")
    other = new_app()
    other.page.goto(other.url + (f"?being={first}" if app.fixed else ""))
    other.wait_boot()
    other.stop_auto_infer()
    with known_bug("ISO-4", unless=app.fixed, reason="two tabs can run the same being"):
        assert other.being_id != first
