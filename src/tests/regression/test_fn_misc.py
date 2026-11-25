"""Function tests — realtime context, labels, skills helper, settings, models, i18n, status, sync chunks."""
import json
import os
import re

import pytest


# ---- 1.16 realtime context ----------------------------------------------------------------
@pytest.mark.case("1.16")
def test_realtime_context_for_browser_host(fnapp):
    ctx = fnapp.js("() => getRealtimeContext()")
    assert ctx.startswith("[Realtime]\nReminder: end with /self_continue or /call_for_trigger")
    assert "[core loop host]" in ctx
    assert f"Being ID: {fnapp.being_id}" in ctx
    assert "/exec browser\n```javascript" in ctx
    assert "Vision: canvas.toDataURL" in ctx


@pytest.mark.case("1.16")
def test_realtime_context_lists_online_visible_shell_devices(fnapp):
    fnapp.set_settings(devices={"box1": {"online": True, "type": "shell"},
                                "box2": {"online": False, "type": "shell"},
                                "box3": {"online": True, "type": "shell", "hidden": True}})
    ctx = fnapp.js("() => getRealtimeContext()")
    fnapp.set_settings(devices={})
    assert "/exec shell box1" in ctx
    assert "box2" not in ctx and "box3" not in ctx


@pytest.mark.case("1.15", "1.16")
def test_turn_note_depends_on_user_input(fnapp):
    a = fnapp.js("() => { window._cycleHadUserInput = true; return getRealtimeContext(); }")
    b = fnapp.js("() => { window._cycleHadUserInput = false; return getRealtimeContext(); }")
    assert "New external input arrived this cycle." in a
    assert "No new user input since your last message" in b


@pytest.mark.case("1.15")
def test_persistent_context_has_time_and_day(fnapp):
    ctx = fnapp.js("() => getPersistentContext()")
    assert re.match(r"^\[System Environment\]\nTime: .+ \(UTC[+-]\d+(\.\d+)?\)\nDay: (Sun|Mon|Tue|Wed|Thu|Fri|Sat)$", ctx)


# ---- 6.11 being labels (changed by the isolation fix) ----------------------------------------
def label(app, bid):
    return app.js("""async id => { await loadBeingsList();
        return [...beingSelect.options].find(o => o.value === id)?.textContent; }""", bid)


@pytest.mark.case("6.11")
def test_named_being_label_is_its_name(app):
    bid = app.being_id
    app.js("async id => { const r = await DB.get(id); await DB.put({...r, name: 'Ada'}); }", bid)
    assert label(app, bid) == "Ada"


@pytest.mark.case("6.11")
def test_unnamed_being_label(app, server):
    bid = app.being_id
    server.reply("fine\n\n/call_for_human")
    app.send("hello there my friend")
    app.settle()
    got = label(app, bid)
    if app.fixed:
        assert got == "b_" + bid[:8], "fix: unnamed beings are labelled by short id"
    else:
        assert got == "hello there my ...", "dev: unnamed beings are labelled by the first user message"


# ---- 8.06 skillJsCode -----------------------------------------------------------------
@pytest.mark.case("8.06")
def test_skill_js_code(fnapp):
    r = fnapp.js("""() => [skillJsCode('a()'), skillJsCode({js: 'b()', python: 'x'}), skillJsCode({python: 'x'}),
                          skillJsCode(null), skillJsCode({js: 5})]""")
    assert r == ["a()", "b()", None, None, None]


# ---- 9.02 settings defaults ------------------------------------------------------------------
@pytest.mark.case("9.02")
@pytest.mark.parametrize("stored,check", [
    ('{"model": "", "provider": "", "vision": ""}', {"model": "gemini-3.1-pro", "provider": "infero", "vision": "half"}),
    ('{"provider": "openai"}', {"provider": "openai", "thinking": True, "hostDevice": "this browser", "devices": {}}),
    ("not json", {"model": "gemini-3.1-pro", "provider": "infero"}),
])
def test_load_settings_fills_defaults(fnapp, stored, check):
    got = fnapp.js("""s => { const keep = localStorage.getItem('genesis_settings');
        localStorage.setItem('genesis_settings', s); const r = loadSettings();
        localStorage.setItem('genesis_settings', keep); return r; }""", stored)
    for k, v in check.items():
        assert got[k] == v, k


# ---- 9.05 models.js ------------------------------------------------------------------------
@pytest.mark.case("9.05")
def test_models_js_fills_pickers(fnapp, src_dir):
    with open(os.path.join(src_dir, "models.js")) as f:
        raw = f.read()
    data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    providers = fnapp.js("() => [...document.querySelectorAll('#set-provider option')].map(o => o.value)")
    models = fnapp.js("() => [...document.querySelectorAll('#set-model option')].map(o => o.value)")
    assert providers == list(data["providers"]) + ["custom"]
    assert models == ["custom"] + [m["name"] for m in data["models"]]
    ml = fnapp.js("() => MODEL_LIST")
    assert ml["deepseek-v4-flash"]["vision"] is False and ml["gpt-5.4"]["stop"] is False
    assert ml["claude-opus-5.5"]["thinkingMode"] == "adaptive"


@pytest.mark.case("9.05")
def test_models_js_missing_falls_back(new_app):
    a = new_app()
    a.page.route("**/models.js", lambda r: r.abort())
    a.open("new")
    assert a.js("() => Object.keys(MODEL_LIST)") == ["gemini-3.1-pro"]
    assert any("models.js" in t for _, t in a.console)


# ---- 9.08 vision capture modes -----------------------------------------------------------------
@pytest.mark.case("9.08")
@pytest.mark.parametrize("mode", ["canvas", "half", "full"])
def test_canvas_image_modes(fnapp, mode):
    fnapp.set_settings(vision=mode)
    img = fnapp.js("() => getCanvasImage()")
    assert img and img.startswith("/9j/"), f"{mode}: expected a JPEG"


# ---- 9.12 i18n.js, 9.13 no Chinese in index.html ---------------------------------------------------
@pytest.mark.case("9.12")
def test_i18n_languages_have_same_keys(fnapp):
    i18n = fnapp.js("() => window.I18N")
    assert set(i18n) >= {"en", "zh"}
    keys = {lang: set(v) for lang, v in i18n.items()}
    assert all(k == keys["en"] for k in keys.values()), keys
    assert len({len(v["options"]) for v in i18n.values()}) == 1


@pytest.mark.case("9.13")
def test_index_html_has_no_chinese(src_dir):
    with open(os.path.join(src_dir, "index.html"), encoding="utf-8") as f:
        bad = [(n, l.strip()[:80]) for n, l in enumerate(f, 1) if re.search(r"[一-鿿㐀-䶿]", l)]
    assert not bad, f"Chinese text in index.html (CLAUDE.md rule): {bad[:5]}"


# ---- 9.18 status line ----------------------------------------------------------------------------
@pytest.mark.case("9.18")
@pytest.mark.parametrize("state", ["Ready", "Inferring...", "Executing...", "Caching...", "Remote"])
def test_status_line(fnapp, state):
    fnapp.js("s => setStatus(s)", state)
    assert re.match(r"^(Mem|Cache): \d+k tok \| " + re.escape(state) + "$", fnapp.status())
    fnapp.js("() => setStatus('Ready')")


# ---- 10.07 sync chunks -----------------------------------------------------------------------------
@pytest.mark.case("10.07")
def test_sync_chunks_reassembled(fnapp):
    r = fnapp.js("""async () => {
        const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
        const raw = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey('raw', key))));
        const k = await importDeviceKey(raw.replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, ''));
        const obj = { consciousness: 'x'.repeat(5000) + 'END', metadata: { cachedLength: 3 } };
        const cs = new CompressionStream('gzip'); const w = cs.writable.getWriter();
        w.write(new TextEncoder().encode(JSON.stringify(obj))); w.close();
        const gz = new Uint8Array(await new Response(cs.readable).arrayBuffer());
        const half = Math.floor(gz.length / 2);
        const b64 = u => btoa(String.fromCharCode(...u));
        const c1 = await encryptPayload(k, { gz: b64(gz.slice(0, half)), i: 0, n: 2 });
        const c2 = await encryptPayload(k, { gz: b64(gz.slice(half)), i: 1, n: 2 });
        const first = await handleSyncChunk({ payload: c2, being_id: 'b1' }, k);   // out of order
        const second = await handleSyncChunk({ payload: c1, being_id: 'b1' }, k);
        const legacy = await handleSyncChunk({ payload: await encryptPayload(k, { consciousness: 'plain' }) }, k);
        return { first, end: second && second.consciousness.slice(-3), meta: second && second.metadata, legacy };
    }""")
    assert r["first"] is None
    assert r["end"] == "END" and r["meta"] == {"cachedLength": 3}
    assert r["legacy"] == {"consciousness": "plain"}


@pytest.mark.case("10.05")
def test_encrypt_decrypt_roundtrip(fnapp):
    r = fnapp.js("""async () => {
        const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
        const raw = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey('raw', key))));
        const k = await importDeviceKey(raw);
        const ct = await encryptPayload(k, { hello: 'world', n: [1, 2] });
        return { back: await decryptPayload(k, ct), len: atob(ct).length };
    }""")
    assert r["back"] == {"hello": "world", "n": [1, 2]}
    assert r["len"] > 12 + 16, "12-byte IV + ciphertext + 16-byte tag"
