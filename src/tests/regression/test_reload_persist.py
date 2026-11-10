"""Reload tests — saving and restoring a being's state (checklist area 7)."""
import pytest


CFH = "\n\n/call_for_human"


@pytest.mark.case("7.01")
def test_debounced_save_then_restore(app, server):
    bid = app.being_id
    server.reply("remember this reply" + CFH)
    app.send("TASK-SAVE")
    app.until_py(lambda: "remember this reply" in app.stored(bid), timeout=8, msg="debounced save (3 s)")
    app.reload()
    assert app.being_id == bid
    assert "remember this reply" in app.consciousness()
    assert "remember this reply" in app.chat_text()


@pytest.mark.case("7.02")
def test_reload_right_after_a_change_keeps_it(app, server):
    bid = app.being_id
    server.reply("fresh words" + CFH)
    app.send("TASK-QUICK")
    app.page.reload()             # well inside the 3 s save delay
    app.wait_boot()
    app.stop_auto_infer()
    assert "fresh words" in app.stored(bid)


@pytest.mark.case("7.03", "7.04")
def test_html_div_and_form_values_restored(app, server):
    app.send("build me a form")          # a being with no history has nothing to restore
    app.js("""() => {
        document.getElementById('html-div').innerHTML = `
          <div id="panel"><h3>My UI</h3>
            <input id="f-text" value=""><textarea id="f-area"></textarea>
            <select id="f-sel"><option>a</option><option>b</option><option>c</option></select>
            <input id="f-check" type="checkbox"><input id="f-radio1" type="radio" name="r"><input id="f-radio2" type="radio" name="r">
          </div>`;
    }""")
    app.page.fill("#f-text", "typed text")
    app.page.fill("#f-area", "multi\nline")
    app.page.select_option("#f-sel", "c")
    app.page.check("#f-check")
    app.page.check("#f-radio2")
    app.js("() => saveBeing()")
    app.reload()
    got = app.js("""() => ({
        h3: document.querySelector('#panel h3')?.textContent,
        text: document.getElementById('f-text').value, area: document.getElementById('f-area').value,
        sel: document.getElementById('f-sel').value, check: document.getElementById('f-check').checked,
        r1: document.getElementById('f-radio1').checked, r2: document.getElementById('f-radio2').checked })""")
    assert got == {"h3": "My UI", "text": "typed text", "area": "multi\nline", "sel": "c", "check": True,
                   "r1": False, "r2": True}


@pytest.mark.case("7.05")
def test_typing_in_living_ui_schedules_save(app):
    bid = app.being_id
    app.js("() => { document.getElementById('html-div').innerHTML = '<input id=live>'; }")
    app.page.click("#live")
    app.page.keyboard.type("abc")
    app.until_py(lambda: 'value="abc"' in ((app.db_get(f"{bid}/snapshot") or {}).get("htmlContent") or ""),
                 timeout=8, msg="snapshot saved after typing")


@pytest.mark.case("7.06")
def test_cache_and_token_state_restored(app):
    bid = app.being_id
    app.js("() => { cacheName = 'cachedContents/keepme'; cachedLength = 123; _lastPromptTokens = 4567; "
           "_lastCachedTokens = 89; return saveBeing(); }")
    app.reload()
    assert app.being_id == bid
    assert app.js("() => [cacheName, cachedLength, _lastPromptTokens, _lastCachedTokens]") == \
        ["cachedContents/keepme", 123, 4567, 89]


@pytest.mark.case("7.07")
def test_old_text_record_migrated(app):
    bid = app.being_id
    app.db_put({"id": f"{bid}/consciousness.txt", "text": "[System Environment]\nTime: x\nDay: Mon\n\nold format\n\n"})
    app.js("async id => { const r = await DB.get(id); await DB.put({...r, updatedAt: Date.now() + 1e9}); }", bid)
    app.reload()
    assert app.being_id == bid
    assert "old format" in app.consciousness()
    rec = app.db_get(f"{bid}/consciousness.txt")
    assert "old format" in rec.get("value", "") and "text" not in rec


@pytest.mark.case("7.08")
def test_history_rendered_in_order(app, server):
    server.reply("/exec browser\n```js\nreturn 'R1'\n```\n/self_continue", "final words" + CFH)
    app.send("first question")
    app.wait_requests(2)
    app.wait_idle()
    live = [(r, t.strip()[:40]) for r, t in app.chat() if r in ("user", "ai", "system")]
    app.js("() => saveBeing()")
    app.reload()
    restored = [(r, t.strip()[:40]) for r, t in app.chat() if r in ("user", "ai", "system")]
    roles = [r for r, _ in restored]
    assert roles == ["user", "ai", "system", "ai"], restored
    assert restored[0][1] == "first question" and "final words" in restored[-1][1]
    assert [r for r, _ in live][-4:] == roles


@pytest.mark.case("7.09")
def test_long_history_renders_only_tail(app):
    bid = app.being_id
    early = "[System Environment]\nTime: t\nDay: Mon\n\nEARLY-MARKER\n\n"
    filler = "".join(f"**Digital Being - [2026-01-01 10:{i % 60:02d}]**\nfiller line {i}\n\n" for i in range(4000))
    late = "[System Environment]\nTime: t\nDay: Mon\n\nLATE-MARKER\n\n"
    app.db_put({"id": f"{bid}/consciousness.txt", "value": early + filler + late})
    app.reload()
    text = app.chat_text()
    assert "LATE-MARKER" in text and "EARLY-MARKER" not in text
    assert "EARLY-MARKER" in app.consciousness(), "only rendering is limited, not the memory"


@pytest.mark.case("7.10")
def test_empty_being_renders_placeholder_and_clears_ui(app):
    app.js("() => { document.getElementById('html-div').innerHTML = '<p>left over</p>'; }")
    app.js("() => { consciousness = ''; return renderHistory(); }")
    assert app.chat() == [["ai", "System online. Initializing."]]
    assert app.js("() => document.getElementById('html-div').innerHTML") == ""


@pytest.mark.case("7.12")
def test_save_updates_being_menu(app):
    bid = app.being_id
    app.js("async id => { const r = await DB.get(id); await DB.put({...r, name: 'Menu Name'}); return saveBeing(); }", bid)
    opts = app.js("() => [...beingSelect.options].map(o => [o.value, o.textContent])")
    assert [bid, "Menu Name"] in opts
    assert app.js("() => beingSelect.value") == bid


@pytest.mark.case("5.16")
def test_image_markers_render_as_thumbnails_after_reload(app):
    from test_loop_exec import PNG
    bid = app.being_id
    key = f"{bid}/vision/1.png"
    app.db_put({"id": key, "value": PNG, "media_type": "image/png"})
    app.db_put({"id": f"{bid}/consciousness.txt", "value":
                "[System Environment]\nTime: t\nDay: Mon\n\nlook\n\n"
                f"System - [View] loaded {key} (image/png, 1KB) into visual context:\n<<<IMAGE:{key}>>>\n\n"
                "System - [View] gone → <<<IMAGE:" + bid + "/vision/missing.png>>>\n\n"})
    app.reload()
    app.until("() => [...document.querySelectorAll('#chat-box img.view-thumb')].some(i => i.src.startsWith('data:image/png;base64,'))",
              timeout=10, msg="thumbnail rendered")
    assert f"<<<IMAGE:{bid}/vision/missing.png>>>" in app.chat_text(), "a missing image stays as its marker text"
