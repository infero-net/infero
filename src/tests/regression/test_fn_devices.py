"""Function tests — merging consciousness with a device, fork choice, preferred host, stream filtering
(checklist 12.06, 12.07, 12.09). No relay needed: the page's functions are called directly."""
import pytest

BASE = "[System Environment]\nTime: t\nDay: Mon\n\nshared start\n\n"


def merge(app, local, remote, choose=None):
    """Run mergeConsciousness(runner, remote) with `local` as the page's consciousness.
    choose: None (no fork expected), 'remote' or 'local' (click that button in the fork dialog)."""
    app.js("([l, r]) => { consciousness = l; window.__merge = mergeConsciousness('dev1', r); }", [local, remote])
    if choose:
        app.until("() => !!document.getElementById('fork-remote')", timeout=5, msg="fork dialog")
        app.page.click(f"#fork-{choose}")
    return app.js("async () => ({ changed: await window.__merge, c: consciousness })")


@pytest.mark.case("12.06")
def test_merge_identical_or_empty(fnapp):
    assert merge(fnapp, BASE, BASE) == {"changed": False, "c": BASE}
    assert merge(fnapp, BASE, None) == {"changed": False, "c": BASE}


@pytest.mark.case("12.06")
def test_merge_device_ahead_takes_device(fnapp):
    remote = BASE + "device wrote more\n\n"
    assert merge(fnapp, BASE, remote) == {"changed": True, "c": remote}


@pytest.mark.case("12.06")
def test_merge_browser_ahead_keeps_browser(fnapp):
    local = BASE + "browser wrote more\n\n"
    assert merge(fnapp, local, BASE) == {"changed": False, "c": local}


@pytest.mark.case("12.06")
@pytest.mark.parametrize("choose", ["remote", "local"])
def test_fork_asks_and_archives_the_other(fnapp, choose):
    local, remote = BASE + "LOCAL branch\n\n", BASE + "REMOTE branch\n\n"
    before = set(fnapp.db_keys(f"{fnapp.being_id}/context_log_"))
    r = merge(fnapp, local, remote, choose=choose)
    kept, lost = (remote, local) if choose == "remote" else (local, remote)
    assert r["c"] == kept and r["changed"] is True
    new = set(fnapp.db_keys(f"{fnapp.being_id}/context_log_")) - before
    assert len(new) == 1
    fnapp.until("k => DB.get(k).then(r => !!r)", next(iter(new)), timeout=5)
    assert fnapp.db_get(next(iter(new)))["value"] == lost, "the other timeline is archived, not deleted"
    assert any(f"Consciousness fork: took {choose}" in t for role, t in fnapp.chat() if role == "system")


@pytest.mark.case("12.06")
def test_merge_compressed_rules(fnapp):
    flag = "[System - Context compressed. earlier part archived]\n\n"
    # one side compressed: take the compressed one
    assert merge(fnapp, BASE + "x" * 50, flag + "short")["c"] == flag + "short"
    assert merge(fnapp, flag + "short", BASE + "x" * 50)["c"] == flag + "short"
    # both compressed: take the longer one
    assert merge(fnapp, flag + "a", flag + "bbbbbb")["c"] == flag + "bbbbbb"


@pytest.mark.case("12.07")
def test_preferred_host_toggle_and_manual_switch(fnapp):
    fnapp.set_settings(devices={"devA": {"online": False}, "devB": {"online": False}}, preferredHost="")
    fnapp.js("() => setPreferredHost('devA')")
    assert fnapp.settings()["preferredHost"] == "devA"
    fnapp.js("() => setHostDevice('devB')")
    s = fnapp.settings()
    assert s["hostDevice"] == "devB" and s["preferredHost"] == "devA", "a manual host switch keeps the preferred host"
    fnapp.js("() => setPreferredHost('devA')")
    assert fnapp.settings()["preferredHost"] == "", "clicking again clears it"
    fnapp.set_settings(hostDevice="this browser", devices={}, preferredHost="")


@pytest.mark.case("12.09")
def test_stream_token_for_other_being_ignored(fnapp):
    r = fnapp.js("""async () => {
        const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
        const keyB64 = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey('raw', key))));
        settings.devices = { dev1: { online: true, keyB64 } }; settings.hostDevice = 'dev1';
        const k = await importDeviceKey(keyB64);
        window._remoteAiDiv = null;
        await handleRelayMessage({ type: 'stream_token', sender: 'dev1',
            payload: await encryptPayload(k, { text_delta: 'FOREIGN', being_id: 'some_other_being' }) });
        const foreign = !!window._remoteAiDiv;
        await handleRelayMessage({ type: 'stream_token', sender: 'dev1',
            payload: await encryptPayload(k, { text_delta: 'MINE', being_id: currentBeingId }) });
        await handleRelayMessage({ type: 'stream_token', sender: 'dev1',
            payload: await encryptPayload(k, { text: 'MINE done', done: true, being_id: currentBeingId }) });
        const shown = [...document.querySelectorAll('#chat-box .msg.ai')].map(d => d.innerText);
        settings.devices = {}; settings.hostDevice = 'this browser'; saveSettingsToStorage(settings);
        return { foreign, shown };
    }""")
    assert r["foreign"] is False
    assert any("MINE done" in t for t in r["shown"]) and not any("FOREIGN" in t for t in r["shown"])
