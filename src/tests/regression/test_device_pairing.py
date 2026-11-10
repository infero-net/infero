"""Device tests — pairing, keys, removal, relay messaging (checklist areas 10 and 11).

Each test pairs its own page with a local agent (see harness/devices.py); nothing reaches a real relay."""
import asyncio
import json
import re
import threading

import pytest

from harness.bugs import known_bug

CFH = "\n\n/call_for_human"


def shell_reply(device, cmd, tail="/call_for_human"):
    return f"/exec shell {device}\n```bash\n{cmd}\n```\n{tail}"


@pytest.mark.case("10.01", "10.06")
def test_pair_linux_device(paired):
    app, agent = paired.app, paired.agent
    dev = app.settings()["devices"][agent.name]
    assert dev["type"] == "shell" and dev["os"] == "Linux" and dev["online"] is True
    status = app.js("() => document.getElementById('pair-status').textContent")
    assert status == f"✓ {agent.name} connected!"
    msg = next(t for r, t in app.chat() if r == "system" and "Verify Words" in t)
    words = re.search(r"Verify Words: (\S+ \S+)", msg).group(1)
    agent_words = app.until_py(lambda: re.search(r"verify: (\S+ \S+)", agent.log_text()), msg="agent verify words")
    assert agent_words.group(1) == words, "browser and device show the same verify words"
    assert app.js("() => document.getElementById('pair-cmd-text').textContent").startswith(f"curl {paired.relay.base}/pair/")


@pytest.mark.case("10.02", "11.10")
def test_windows_script_and_bot_filter(app_in_context, relay):
    app = app_in_context(settings={"relayHost": relay.base})
    app.open("new")
    app.until("() => _relayConnected === true", timeout=20)
    relay.before_pairing()
    app.js("() => startPairing()")
    app.page.click(".pair-os-btn >> text=Windows")
    cmd = app.js("() => document.getElementById('pair-cmd-text').textContent")
    assert cmd.startswith(f"irm {relay.base}/pair/") and cmd.endswith("/ps1 | iex")
    code = re.search(r"/pair/([A-Z0-9]{4})/ps1", cmd).group(1)
    status, body = relay.get(f"/pair/{code}/ps1", headers={"User-Agent": "Slackbot-LinkExpanding 1.0"})
    assert status == 204, "link-preview bots must not consume the one-time code"
    status, body = relay.get(f"/pair/{code}/ps1", headers={"User-Agent": "Mozilla/5.0 PowerShell"})
    assert status == 200
    for k in ("INSTANCE_ID", "TOKEN", "BROWSER_PUB", "RELAY_WS"):
        assert re.search(rf'^\${k} = "[^"]+"', body, re.M), k
    assert "Register-ScheduledTask" in body


@pytest.mark.case("10.03")
def test_pair_code_single_use_and_unknown(app_in_context, relay):
    app = app_in_context(settings={"relayHost": relay.base})
    app.open("new")
    app.until("() => _relayConnected === true", timeout=20)
    relay.before_pairing()
    app.js("() => startPairing()")
    cmd = app.until("() => { const t = document.getElementById('pair-cmd-text').textContent; return /\\/pair\\/[A-Z0-9]{4}/.test(t) && t; }")
    code = re.search(r"/pair/([A-Z0-9]{4})", cmd).group(1)
    first = relay.get(f"/pair/{code}")[1]
    second = relay.get(f"/pair/{code}")[1]
    assert "INSTANCE_ID=" in first
    assert "pairing code not found or expired" in second
    assert "pairing code not found or expired" in relay.get("/pair/ZZZZ")[1]


@pytest.mark.case("11.01")
def test_exec_shell_on_device(paired, server):
    app, name = paired.app, paired.name
    server.reply(shell_reply(name, "echo hello from $(hostname | head -c 0)device; echo oops >&2; exit 3"))
    app.send("run it")
    c = app.consciousness()
    assert f"System - [Shell][{name}] - Result:" in c
    assert "[stdout]\nhello from device" in c and "[stderr]\noops" in c and "[exit_code] 3" in c


@pytest.mark.case("11.02")
def test_shell_command_past_timeout(paired, server):
    app, name = paired.app, paired.name
    server.reply(shell_reply(name, "echo started; sleep 40; echo never"))
    app.send("slow", timeout=60)
    # the browser's 30 s "continues in background" note and the device's 30 s timeout race; either order is fine
    c = app.until_py(lambda: f"[Shell][{name}]" in app.consciousness() and app.consciousness(), timeout=15,
                     msg="shell result after the device's 30 s timeout")
    result = c[c.index(f"System - [Shell][{name}] - Result:"):]
    # by design the device drops output of a command that outlives 30 s (the prompt says to write to a file)
    assert "never" not in result
    assert "still running after 30s" in result and "[exit_code] running" in result


@pytest.mark.case("11.05")
def test_view_file_from_device(paired, server):
    import base64
    from test_loop_exec import PNG
    app, agent = paired.app, paired.agent
    path = f"{agent.dir}/red.png"
    with open(path, "wb") as f:
        f.write(base64.b64decode(PNG))
    server.reply(f"/view {agent.name}:{path}\n\n/self_continue", "seen" + CFH)
    app.send("look")
    app.wait_requests(2)
    app.wait_idle()
    assert f"System - [View] loaded {agent.name}:{path} (image/png" in app.consciousness()
    parts = server.llm_requests()[1]["body"]["contents"][0]["parts"]
    assert any(p.get("inlineData", {}).get("data") == PNG for p in parts)


@pytest.mark.case("11.05")
def test_view_missing_file_on_device(paired, server):
    app, name = paired.app, paired.name
    server.reply(f"/view {name}:/no/such/file.png" + CFH)
    app.send("look")
    assert f"System - [View] {name}:/no/such/file.png → failed: file not found: /no/such/file.png" in app.consciousness()


@pytest.mark.case("10.08")
def test_rekey(paired, server):
    app, agent = paired.app, paired.agent
    old_key = app.settings()["devices"][agent.name]["keyB64"]
    app.js("n => startRekey(n)", agent.name)
    app.until("([n, k]) => settings.devices[n]?.keyB64 && settings.devices[n].keyB64 !== k", [agent.name, old_key],
              timeout=20, msg="new key after re-key")
    app.until_py(lambda: "re-keyed | verify:" in agent.log_text(), msg="agent re-keyed")
    assert any("Pairing complete" in t for r, t in app.chat() if r == "system")
    server.reply(shell_reply(agent.name, "echo after-rekey"))
    app.send("still works?")
    assert "after-rekey" in app.consciousness()


@pytest.mark.case("10.09")
def test_remove_device_from_browser(paired):
    app, agent, relay = paired.app, paired.agent, paired.relay
    token = agent.cfg["token"]
    app.js("n => removeDevice(n)", agent.name)
    assert agent.name not in (app.settings().get("devices") or {})
    app.until_py(lambda: token not in relay.tokens(), msg="token revoked")
    app.until_py(lambda: "Removed from" in agent.log_text(), timeout=20, msg="agent told it was removed")
    app.until_py(lambda: agent.instances() == [], msg="agent forgot the instance")
    app.wait(10000)          # the relay announces offline after an 8 s debounce
    with known_bug("BUG-08", reason="the 'deleted' note needs the device in settings, which removal already cleared"):
        assert any(f"{agent.name} deleted" in t for r, t in app.chat() if r == "system")


@pytest.mark.case("10.10")
def test_device_removes_itself(paired):
    import websockets
    app, agent, relay = paired.app, paired.agent, paired.relay
    agent.stop()

    async def goodbye():
        async with websockets.connect(relay.ws_url, open_timeout=5) as ws:
            await ws.send(json.dumps({"type": "device_hello", "instance_id": agent.cfg["instance_id"],
                                      "token": agent.cfg["token"], "device_name": agent.name}))
            await ws.send(json.dumps({"type": "device_remove_self", "device_name": agent.name}))
            await asyncio.sleep(0.5)
    t = threading.Thread(target=lambda: asyncio.run(goodbye()))
    t.start()
    app.until("n => !settings.devices?.[n]", agent.name, timeout=20, msg="device removed in the browser")
    t.join(10)
    assert any(f"{agent.name} removed itself" in x for r, x in app.chat() if r == "system")
    assert agent.cfg["token"] not in relay.tokens()


@pytest.mark.case("10.11", "11.06")
def test_relay_restart_reconnects(paired, server):
    app, agent, relay = paired.app, paired.agent, paired.relay
    relay.restart()
    app.until("() => _relayConnected === true", timeout=30, msg="page reconnected")
    app.until("n => settings.devices[n]?.online === true", agent.name, timeout=40, msg="device back online")
    server.reply(shell_reply(agent.name, "echo after-restart"))
    app.send("ping")
    assert "after-restart" in app.consciousness()


@pytest.mark.case("10.12")
def test_undecryptable_message_reported_in_plain_text(paired):
    app, agent = paired.app, paired.agent
    app.js("""n => _relayWs.send(JSON.stringify({ type: 'user_input', device_name: n,
        payload: btoa('x'.repeat(40)) }))""", agent.name)
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('Could not decrypt'))",
              timeout=15)


@pytest.mark.case("11.08")
def test_other_instance_does_not_see_device(paired, app_in_context):
    other = app_in_context(settings={"relayHost": paired.relay.base})
    other.open("new")
    other.until("() => _relayConnected === true", timeout=20)
    other.wait(1500)
    assert paired.name not in (other.settings().get("devices") or {})
    assert not other.js("() => Object.keys(window._unregisteredDevices || {})")


@pytest.mark.case("11.09")
def test_pair_create_rate_limit():
    from harness.devices import LocalRelay
    r = LocalRelay().start()
    try:
        codes = [r.post("/pair/create", {"instance_id": "i", "browser_pub": "p"})[0] for _ in range(11)]
        assert codes[:10] == [200] * 10 and codes[10] == 429
    finally:
        r.stop()


@pytest.mark.case("11.11")
def test_update_and_bip39_endpoints(relay):
    status, body = relay.get("/update")
    assert status == 200 and "class GenesisWorker" in body and "async def connect_instance" in body
    status, words = relay.get("/bip39")
    assert status == 200 and len(words.split()) == 2048


@pytest.mark.case("11.07")
def test_second_connection_replaces_first(paired, server):
    """The same device connecting twice (e.g. a restarted agent before the old socket closed) must not
    double-deliver messages or flip the device offline when the stale socket closes."""
    import websockets
    app, agent, relay = paired.app, paired.agent, paired.relay
    received = []

    async def stale():
        ws = await websockets.connect(relay.ws_url, open_timeout=5)
        await ws.send(json.dumps({"type": "device_hello", "instance_id": agent.cfg["instance_id"],
                                  "token": agent.cfg["token"], "device_name": agent.name,
                                  "device_pub": "", "device_os": "Linux"}))
        try:
            async for m in ws:
                received.append(json.loads(m).get("type"))
        except Exception:
            pass
    # A newer connection (the real agent) replaces the stale one: restart the agent so it reconnects last
    def run_stale():
        try:
            asyncio.run(asyncio.wait_for(stale(), 25))
        except asyncio.TimeoutError:
            pass   # the stale socket just stays open until the test ends
    t = threading.Thread(target=run_stale, daemon=True)
    t.start()
    app.wait(1500)
    agent.stop()
    agent.start()
    app.until_py(lambda: len(re.findall(r"Connected: ", agent.log_text())) >= 2, timeout=20, msg="agent reconnected")
    app.wait(1500)
    server.reply(shell_reply(agent.name, "echo once"))
    app.send("exec once")
    c = app.consciousness()
    assert c.count("[stdout]\nonce") == 1
    assert "exec" not in received, "the replaced socket gets no traffic"
    app.wait(9000)   # relay debounces offline by 8 s
    assert app.js("n => settings.devices[n].online", agent.name) is True


@pytest.mark.case("13.11")
def test_second_agent_on_same_dir_refuses(paired):
    import subprocess
    import sys
    import os
    agent = paired.agent
    r = subprocess.run([sys.executable, os.path.join(agent.dir, "agent.py")], env=dict(os.environ, INFERO_DIR=agent.dir),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 1 and "Another agent is already running" in r.stdout


@pytest.mark.case("10.01")
def test_pairing_survives_page_reload_mid_pairing(app_in_context, relay):
    """A reload between showing the code and the device connecting must still complete pairing."""
    from harness.devices import LocalAgent
    app = app_in_context(settings={"relayHost": relay.base})
    app.open("new")
    app.until("() => _relayConnected === true", timeout=20)
    relay.before_pairing()
    app.js("() => startPairing()")
    cmd = app.until("() => { const t = document.getElementById('pair-cmd-text').textContent; return /\\/pair\\/[A-Z0-9]{4}/.test(t) && t; }")
    code = re.search(r"/pair/([A-Z0-9]{4})", cmd).group(1)
    app.reload()
    agent = LocalAgent(relay)
    try:
        agent.pair(code)
        agent.start()
        app.until("n => settings.devices?.[n]?.keyB64", agent.name, timeout=30, msg="pairing recovered after reload")
        assert any("Pairing complete" in t for r, t in app.chat() if r == "system")
    finally:
        agent.cleanup()
