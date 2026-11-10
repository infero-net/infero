"""Device tests — a paired device as the core-loop host (checklist areas 11–13)."""
import json
import os
import re

import pytest

from conftest import pair_page
from harness import Reply
from harness.bugs import known_bug
from harness.server import prompt_text

CFH = "\n\n/call_for_human"


def make_host(p):
    """Hand the core loop to the paired device (the host icon → confirm)."""
    app, agent = p.app, p.agent
    n = len(re.findall("Loop handoff received", agent.log_text()))
    app.js("n => setHostDevice(n)", agent.name)
    app.until("n => settings.hostDevice === n && document.getElementById('status').innerText.includes('Remote')",
              agent.name, timeout=30, msg="device is host")
    app.until_py(lambda: len(re.findall("Loop handoff received", agent.log_text())) > n, timeout=20, msg="agent got handoff")


def remote_send(app, text):
    app.page.fill("#input", text)
    app.page.press("#input", "Enter")


def agent_consciousness(agent, being_id):
    try:
        with open(os.path.join(agent.being_dir(being_id), "consciousness.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


@pytest.mark.case("12.01")
def test_handoff_sends_being_to_device(paired, server):
    app, agent = paired.app, paired.agent
    bid = app.being_id
    server.reply("local reply" + CFH)
    app.send("TASK-LOCAL")
    app.db_put({"id": f"{bid}/skill/shared_skill", "value": {"id": "shared_skill", "instruction": "x", "enable": True}})
    make_host(paired)
    app.until_py(lambda: "TASK-LOCAL" in agent_consciousness(agent, bid), msg="consciousness on the device")
    skill_dir = os.path.join(agent.being_dir(bid), "skill")
    assert os.path.exists(os.path.join(skill_dir, "shared_skill.json"))
    with open(os.path.join(agent.being_dir(bid), "identity.json")) as f:
        assert len(json.load(f)["mnemonic"].split()) == 12
    with open(os.path.join(agent.being_dir(bid), "state.json")) as f:
        st = json.load(f)
    assert st["llm_settings"]["endpoint"] == f"{server.base}/mock/gemini"
    assert st["llm_settings"]["format"] == "gemini"


@pytest.mark.case("12.02", "11.04")
def test_remote_cycle_streams_into_browser(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    server.reply(Reply(text="streamed from the device" + CFH, chunks=6, delay=1.5))
    remote_send(app, "TASK-REMOTE")
    app.until_py(lambda: server.llm_requests(), timeout=20, msg="device called the LLM")
    req = server.llm_requests()[-1]
    assert "TASK-REMOTE" in prompt_text(req["body"])
    assert "[core loop host]" in prompt_text(req["body"]) and agent.name in prompt_text(req["body"])
    app.until(r"() => [...document.querySelectorAll('#chat-box .msg.ai')].some(d => d.innerText.includes('streamed from the device'))",
              timeout=20, msg="reply streamed into the chat")
    app.until_py(lambda: "streamed from the device" in app.consciousness(), timeout=20, msg="synced back after idle")


@pytest.mark.case("11.04")
def test_device_host_runs_browser_exec(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    server.reply("/exec browser\n```js\nreturn 'title=' + document.title\n```\n/self_continue", "done" + CFH)
    remote_send(app, "use the browser")
    app.until_py(lambda: len(server.llm_requests()) >= 2, timeout=30, msg="second cycle on the device")
    assert "title=INFERO" in prompt_text(server.llm_requests()[1]["body"])
    assert any("title=INFERO" in t for r, t in app.chat() if r == "system")


@pytest.mark.case("12.03")
def test_stop_remote_loop(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    server.reply(Reply(text="this reply gets cut" + CFH, chunks=20, delay=8))
    remote_send(app, "long one")
    app.until_py(lambda: server.llm_requests(), timeout=20)
    app.until("() => sendBtn.textContent === 'Abort'", timeout=10)
    app.click_send()
    app.until_py(lambda: "Loop stop requested" in agent.log_text(), timeout=10, msg="loop_stop reached the device")
    app.until_py(lambda: "Infer aborted by loop_stop" in agent.log_text(), timeout=15, msg="inference aborted")
    app.wait(1500)
    assert "this reply gets cut" not in agent_consciousness(agent, app.being_id)


@pytest.mark.case("12.04")
def test_switching_being_hands_new_being_to_device(paired, server):
    app, agent = paired.app, paired.agent
    first = app.being_id
    second = app.new_being()
    app.switch(first)
    make_host(paired)
    app.switch(second)
    app.until_py(lambda: os.path.exists(agent.being_dir(second)), timeout=30, msg="second being handed to the device")
    app.until("n => settings.hostDevice === n", agent.name, timeout=10)


@pytest.mark.case("12.05")
def test_host_offline_then_take_back(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    agent.stop()
    app.until("() => document.getElementById('status').innerText.includes('Host offline')", timeout=20,
              msg="status shows the host is offline")
    app.js("() => setHostDevice('this browser')")
    app.until("() => settings.hostDevice === 'this browser'", timeout=15, msg="browser host again")
    server.reply("local again" + CFH)
    app.send("back home")
    assert "local again" in app.consciousness()


@pytest.mark.case("12.08", "13.07")
def test_python_exec_on_device_shown_at_once(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    code = "import os\nprint('py says', 6 * 7)\nresult = agent.being_id[:6]"
    server.reply(f"/exec python {agent.name}\n```python\n{code}\n```\n/call_for_human")
    remote_send(app, "python please")
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('py says 42'))",
              timeout=30, msg="python result displayed")
    sysmsg = next(t for r, t in app.chat() if r == "system" and "py says 42" in t)
    assert f"System - [Python][{agent.name}] - Result:" in sysmsg and f"[Return Value]\n{app.being_id[:6]}" in sysmsg


@pytest.mark.case("13.08")
def test_trigger_file_wakes_device_loop(paired, server):
    app, agent = paired.app, paired.agent
    make_host(paired)
    server.reply("sleeping\n\n/call_for_trigger", "woken" + CFH)
    remote_send(app, "go to sleep")
    app.until_py(lambda: "sleeping" in agent_consciousness(agent, app.being_id), timeout=30)
    with open(os.path.join(agent.being_dir(app.being_id), "trigger.txt"), "a") as f:
        f.write("timer fired\n")
    app.until_py(lambda: len(server.llm_requests()) >= 2, timeout=15, msg="trigger woke the loop")
    assert "System - [Trigger] timer fired" in prompt_text(server.llm_requests()[1]["body"])
    app.until(r"() => [...document.querySelectorAll('#chat-box .system')].some(d => d.innerText.includes('[Trigger] timer fired'))",
              timeout=10)


@pytest.mark.case("13.09")
def test_skill_code_runs_on_device_at_handoff(paired):
    app, agent = paired.app, paired.agent
    bid = app.being_id
    marker = os.path.join(agent.dir, "skill_ran.txt")
    app.db_put({"id": f"{bid}/skill/dev_setup", "value": {"id": "dev_setup", "instruction": "x", "enable": True,
               "code": {"js": "window.__devsetup = 1", "shell": f"echo shell-ran > {marker}",
                        "python": "def install(agent):\n    agent._skill_installed = 'yes'\n"}}})
    app.db_put({"id": f"{bid}/skill/broken_setup", "value": {"id": "broken_setup", "instruction": "x", "enable": True,
               "code": {"shell": "exit 7"}}})
    make_host(paired)
    app.until_py(lambda: os.path.exists(marker), timeout=15, msg="shell variant ran on the device")
    app.until_py(lambda: "skill python init ok: dev_setup" in agent.log_text(), timeout=10)
    app.until_py(lambda: "[skill:broken_setup] shell init exited 7" in agent_consciousness(agent, bid), timeout=10,
                 msg="skill errors surfaced to the being")


@pytest.mark.case("13.10")
def test_device_resumes_after_restart(paired, server):
    app, agent = paired.app, paired.agent
    bid = app.being_id
    make_host(paired)
    server.reply("remembered on disk" + CFH)
    remote_send(app, "TASK-DISK")
    app.until_py(lambda: "remembered on disk" in agent_consciousness(agent, bid), timeout=30)
    agent.stop()
    agent.start()
    app.until_py(lambda: len(re.findall(r"Connected: ", agent.log_text())) >= 2, timeout=20, msg="agent reconnected")
    app.wait(500)
    server.reply("after restart" + CFH)
    remote_send(app, "TASK-AFTER")
    app.until_py(lambda: "after restart" in agent_consciousness(agent, bid), timeout=40, msg="loop resumed on the device")
    c = agent_consciousness(agent, bid)
    assert c.index("remembered on disk") < c.index("TASK-AFTER")


@pytest.mark.case("12.02")
def test_message_sent_while_device_reconnects(paired, server):
    """The relay announces a device offline only after 8 s, so for a quick agent restart (e.g. `infero update`)
    the page still shows it online. A message sent in that gap must not vanish silently."""
    app, agent = paired.app, paired.agent
    make_host(paired)
    agent.stop()
    app.wait(500)
    server.reply("got the gap message" + CFH)
    remote_send(app, "TASK-IN-GAP")
    agent.start()
    app.wait(12000)
    delivered = any("TASK-IN-GAP" in prompt_text(r["body"]) for r in server.llm_requests())
    told = any("not delivered" in t.lower() or "offline" in t.lower() for r, t in app.chat() if r == "system")
    with known_bug("BUG-09", reason="the relay drops a message for a disconnected device; the page waits forever"):
        assert delivered or told


@pytest.mark.case("11.03")
def test_device_to_device_exec(paired, server, relay):
    app, host = paired.app, paired.agent
    peer = pair_page(app, relay)
    paired.extra_agents.append(peer)
    make_host(paired)
    server.reply(f"/exec shell {peer.name}\n```bash\necho from-peer-$((2+3))\n```\n/call_for_human")
    remote_send(app, "ask the peer")
    app.until_py(lambda: "from-peer-5" in agent_consciousness(host, app.being_id), timeout=40,
                 msg="peer's output reached the host")
    assert f"[Shell][{peer.name}]" in agent_consciousness(host, app.being_id)
    assert re.search(r"exec \(\w+\) from " + re.escape(host.name), peer.log_text())
