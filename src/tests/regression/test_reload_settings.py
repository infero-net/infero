"""Reload tests — settings panel, settings across tabs, uploads, input box, canvas (area 9, 4.18)."""
import json

import pytest

from harness.bugs import known_bug
from test_loop_exec import PNG

CFH = "\n\n/call_for_human"


def open_panel(app):
    app.page.click('button[title="Settings"]')
    app.until("() => document.getElementById('settings-panel').style.display === 'block'")


def close_panel(app):
    app.page.click('button[title="Settings"]')    # closing applies the settings
    app.until("() => document.getElementById('settings-panel').style.display === 'none'")


def stored_settings(app):
    return app.js("() => JSON.parse(localStorage.getItem('genesis_settings'))")


@pytest.mark.case("9.01", "9.09")
def test_settings_panel_saves_and_restores(app):
    open_panel(app)
    app.page.select_option("#set-vision", "full")
    app.page.check("#set-preview")
    app.page.check("#set-thinking")
    app.page.fill("#set-companion-name", "Sam")
    app.page.fill("#set-token", "tok-xyz")
    close_panel(app)
    s = stored_settings(app)
    assert s["vision"] == "full" and s["preview"] is True and s["thinking"] is True
    assert s["companion_name"] == "Sam" and s["tokens"]["custom"] == "tok-xyz"
    assert s["thinkingPerModel"][s["model"]] is True
    app.reload()
    open_panel(app)
    assert app.js("() => [document.getElementById('set-vision').value, document.getElementById('set-preview').checked,"
                  " document.getElementById('set-companion-name').value]") == ["full", True, "Sam"]


@pytest.mark.case("9.02")
def test_unknown_saved_model_reset_on_boot(app_in_context):
    a = app_in_context(settings={"model": "model-that-was-removed", "provider": "openrouter"})
    a.open("new")
    s = a.settings()
    assert s["model"] == "gemini-3.1-pro" and s["provider"] == "infero"


@pytest.mark.case("9.06")
@pytest.mark.parametrize("provider,endpoint_rows", [("custom", "flex"), ("openai", "none")])
def test_provider_fields(app, provider, endpoint_rows):
    open_panel(app)
    app.page.select_option("#set-model", "custom")
    app.page.select_option("#set-provider", provider)
    rows = app.js("() => ['row-endpoint', 'row-cache-endpoint', 'row-token'].map(i => document.getElementById(i).style.display)")
    assert rows == [endpoint_rows, endpoint_rows, "flex"]


@pytest.mark.case("9.07")
def test_model_change_filters_providers_and_thinking(app):
    open_panel(app)
    app.page.select_option("#set-model", "gpt-5.4")
    hidden = app.js("() => Object.fromEntries([...document.querySelectorAll('#set-provider option')].map(o => [o.value, o.hidden]))")
    assert hidden["openai"] is False and hidden["custom"] is False and hidden["google"] is True
    app.page.check("#set-thinking")
    close_panel(app)
    open_panel(app)
    app.page.select_option("#set-model", "claude-sonnet-4.6")
    app.page.uncheck("#set-thinking")
    close_panel(app)
    open_panel(app)
    thinking = "() => document.getElementById('set-thinking').checked"
    app.page.select_option("#set-model", "gpt-5.4")
    assert app.js(thinking) is True, "thinking is remembered per model"
    app.page.select_option("#set-model", "claude-sonnet-4.6")
    assert app.js(thinking) is False
    app.page.select_option("#set-model", "glm-5.3")
    assert app.js(thinking) is False, "a model with no remembered choice follows the last saved one"


@pytest.mark.case("4.18")
def test_custom_cache_endpoint_survives_reload(app, server):
    open_panel(app)
    app.page.select_option("#set-provider", "custom")
    app.page.fill("#set-cache-endpoint", f"{server.base}/mock/cache")
    close_panel(app)
    assert app.js("() => PROVIDERS.custom.cacheEndpoint") == f"{server.base}/mock/cache"
    app.reload()
    with known_bug("BUG-07", reason="custom cache endpoint is only applied when the settings panel closes"):
        assert app.js("() => PROVIDERS.custom.cacheEndpoint") == f"{server.base}/mock/cache"


# ---- settings across tabs (isolation fix) -----------------------------------------------------
@pytest.mark.case("9.03")
def test_two_tabs_change_different_settings(app, new_app):
    tab2 = new_app()
    tab2.open("new")
    app.js("() => { settings.vision = 'full'; saveSettingsToStorage(settings); }")
    tab2.js("() => { settings.companion_name = 'Tab Two'; saveSettingsToStorage(settings); }")
    s = stored_settings(app)
    with known_bug("ISO-4a", unless=app.fixed, reason="the last tab to save overwrites the other's settings"):
        assert s["vision"] == "full" and s.get("companion_name") == "Tab Two"


@pytest.mark.case("9.04")
def test_device_paired_in_one_tab_survives_save_in_other(app, new_app):
    tab2 = new_app()
    tab2.open("new")
    app.js("() => { settings.devices = { TestDevice: { online: false, keyB64: 'k' } }; saveSettingsToStorage(settings); }")
    tab2.js("() => { settings.vision = 'full'; saveSettingsToStorage(settings); }")
    with known_bug("ISO-4b", unless=app.fixed, reason="a settings save in another tab drops the paired device"):
        assert "TestDevice" in (stored_settings(app).get("devices") or {})
    app.js("() => { delete settings.devices.TestDevice; saveSettingsToStorage(settings); }")
    tab2.js("() => saveSettingsToStorage(settings)")
    assert "TestDevice" not in (stored_settings(app).get("devices") or {}), "a removed device stays removed"


# ---- 9.10 reset all data -------------------------------------------------------------------------
@pytest.mark.case("9.10")
def test_reset_all_data(app, server):
    old = app.being_id
    app.send("something")
    app.settle()
    app.js("() => localStorage.setItem('extra_key', '1')")
    app._call_may_navigate("() => [...document.querySelectorAll('#settings-panel span')]"
                           ".find(s => s.textContent.includes('Reset all data')).click()")
    webkit = app.page.context.browser.browser_type.name == "webkit"
    with known_bug("BUG-11", unless=not webkit, reason="WebKit: the data survives (deleteDatabase is blocked, page reloads first)",
                   intermittent=True):
        app.until("old => typeof currentBeingId !== 'undefined' && currentBeingId && currentBeingId !== old", old,
                  timeout=20 if webkit else 40, msg="page reloaded with a fresh being")
    app.wait_boot()
    app.stop_auto_infer()
    assert app.being_id != old and app.beings() == [app.being_id]
    assert app.js("() => localStorage.getItem('extra_key')") is None
    assert any("Delete all data" in m for _, m in app.dialogs)


# ---- 9.14 / 9.15 attachments -----------------------------------------------------------------------
@pytest.mark.case("9.14")
def test_attach_image(app):
    import base64
    app.upload("#file-input", "red dot.png", base64.b64decode(PNG), "image/png")
    with known_bug("BUG-10", unless=app.page.context.browser.browser_type.name != "webkit",
                   reason="WebKit (private/ephemeral storage) can't store a File in IndexedDB"):
        app.until("() => document.getElementById('input').value.includes('/view ')", timeout=5)
    line = app.js("() => document.getElementById('input').value").strip()
    key = line.split(" ", 1)[1]
    assert key.startswith(f"{app.being_id}/uploads/") and key.endswith("_red_dot.png")
    assert app.js("k => DB.get(k).then(r => r.value instanceof Blob)", key)
    assert app.js("() => sendBtn.textContent") == "Send"


@pytest.mark.case("9.15")
def test_attach_non_image_ignored(app):
    app.upload("#file-input", "notes.txt", b"hello", "text/plain")
    app.wait(500)
    assert app.js("() => document.getElementById('input').value") == ""
    assert app.db_keys(f"{app.being_id}/uploads/") == []


# ---- 9.16 / 9.17 send button and keys ---------------------------------------------------------------
@pytest.mark.case("9.16")
def test_send_button_states(app, server):
    from harness import Reply
    btn = "() => sendBtn.textContent"
    assert app.js(btn) in ("Go", "Wake")
    app.page.fill("#input", "x")
    app.page.dispatch_event("#input", "input")
    assert app.js(btn) == "Send"
    server.reply(Reply(text="slow" + CFH, delay=2, chunks=4))
    app.page.press("#input", "Enter")
    app.until(f"() => ({btn})() === 'Abort'".replace("(() => sendBtn.textContent)()", "sendBtn.textContent"))
    app.wait_idle()
    assert app.js(btn) == "Go"


@pytest.mark.case("9.17")
def test_enter_and_shift_enter(app, server):
    app.page.click("#input")
    app.page.keyboard.type("line one")
    app.page.keyboard.press("Shift+Enter")
    app.page.keyboard.type("line two")
    assert app.js("() => document.getElementById('input').value") == "line one\nline two"
    app.wait(300)
    assert server.llm_requests() == []
    app.page.keyboard.press("Enter")
    app.wait_requests(1)
    app.wait_idle()
    assert "line one\nline two" in app.consciousness()


# ---- 9.19 canvas scale --------------------------------------------------------------------------------
@pytest.mark.case("9.19")
def test_canvas_backing_store_uses_device_pixel_ratio(app_in_context):
    a = app_in_context(device_scale_factor=2)
    a.open("new")
    r = a.js("""() => { const rect = document.getElementById('canvas-container').getBoundingClientRect();
        const c = document.getElementById('main-canvas'); const t = c.getContext('2d').getTransform();
        return { w: c.width, h: c.height, cw: Math.floor(rect.width * 2), ch: Math.floor(rect.height * 2), a: t.a, d: t.d }; }""")
    assert (r["w"], r["h"]) == (r["cw"], r["ch"]) and r["a"] == 2 and r["d"] == 2
    a.page.set_viewport_size({"width": 1000, "height": 700})
    a.until("""() => { const rect = document.getElementById('canvas-container').getBoundingClientRect();
        return document.getElementById('main-canvas').width === Math.floor(rect.width * 2); }""", timeout=5)
