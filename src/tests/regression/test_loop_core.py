"""Loop tests — the perceive / infer / act cycle, stop and continue signals (checklist area 1)."""
import pytest

from harness import Reply
from harness.bugs import known_bug
from harness.server import prompt_text

CFH = "\n\n/call_for_human"


def n_llm(server):
    return len(server.llm_requests())


@pytest.mark.case("1.01", "1.15")
def test_one_cycle_renders_and_saves(app, server):
    server.reply("Hello **human**." + CFH)
    app.send("hi there")
    c = app.consciousness()
    env = c.index("[System Environment]\nTime:")
    assert c.index("hi there", env) < c.index("**Digital Being - [") < c.index("Hello **human**.")
    ai = [t for role, t in app.chat() if role == "ai"]
    assert ai and "Hello human." in ai[-1], "reply rendered as markdown"
    app.settle()
    assert "Hello **human**." in app.stored()


@pytest.mark.case("1.02")
def test_call_for_human_stops(app, server):
    server.reply("done" + CFH)
    app.send("go")
    app.wait(2500)
    assert n_llm(server) == 1
    assert app.status().endswith("| Ready")


@pytest.mark.case("1.03")
def test_call_for_trigger_stops(app, server):
    server.reply("sleeping\n\n/call_for_trigger")
    app.send("go")
    app.wait(2500)
    assert n_llm(server) == 1


@pytest.mark.case("1.04")
@pytest.mark.parametrize("first", ["step one\n\n/self_continue", "step one, no signal at all"])
def test_self_continue_runs_another_cycle(app, server, first):
    server.reply(first, "step two" + CFH)
    app.send("go")
    app.wait_requests(2)
    app.wait_idle()
    app.wait(1500)
    assert n_llm(server) == 2
    assert "step two" in app.consciousness()


@pytest.mark.case("1.05")
def test_stop_signal_inside_code_block_is_ignored(app, server):
    server.reply("example:\n```\n/call_for_human\n```\nand I keep going", "now stop" + CFH)
    app.send("go")
    app.wait_requests(2)
    app.wait_idle()
    assert n_llm(server) == 2


@pytest.mark.case("1.06")
def test_message_during_exec_forces_another_cycle(app, server):
    slow = "/exec browser\n```js\nawait new Promise(r => setTimeout(r, 2500)); return 'slept'\n```\n/call_for_human"
    server.reply(slow, "answering the late message" + CFH)
    app.send("start", wait=False)
    app.until("() => document.getElementById('status').innerText.includes('Executing')", msg="exec running")
    app.send("late message", wait=False)
    app.wait_requests(2)
    app.wait_idle()
    assert "late message" in str(server.llm_requests()[1]["body"])
    assert "answering the late message" in app.consciousness()


@pytest.mark.case("1.06")
def test_message_while_streaming_cancels_and_answers_new_message(app, server):
    server.reply(Reply(text="a long slow answer" + CFH, delay=4, chunks=8), "answer to second" + CFH)
    app.send("first", wait=False)
    app.wait_requests(1)
    app.wait(800)
    app.send("second", wait=False)
    app.wait_requests(2)
    app.wait_idle()
    body = str(server.llm_requests()[1]["body"])
    assert "first" in body and "second" in body, "the cancelled prompt is kept"
    assert "a long slow answer" not in app.consciousness()
    assert "answer to second" in app.consciousness()


@pytest.mark.case("1.07")
def test_messages_during_exec_are_merged(app, server):
    slow = "/exec browser\n```js\nawait new Promise(r => setTimeout(r, 2500)); return 1\n```\n/call_for_human"
    server.reply(slow, "ok" + CFH)
    app.send("start", wait=False)
    app.until("() => document.getElementById('status').innerText.includes('Executing')")
    app.send("msg A", wait=False)
    app.send("msg B", wait=False)
    app.wait_requests(2)
    app.wait_idle()
    assert n_llm(server) == 2
    assert "msg A\n\nmsg B" in prompt_text(server.llm_requests()[1]["body"])


@pytest.mark.case("1.08")
def test_empty_reply_stops(app, server):
    server.reply(Reply(text="", chunks=1))
    app.send("go")
    app.wait(2000)
    assert n_llm(server) == 1


@pytest.mark.case("1.09")
def test_stop_button_aborts_stream(app, server):
    server.reply(Reply(text="partial reply that never finishes" + CFH, delay=6, chunks=12))
    app.send("go", wait=False)
    app.wait_requests(1)
    app.wait(1000)
    assert app.js("() => sendBtn.textContent") == "Abort"
    app.click_send()
    app.wait_idle(10)
    app.wait(2000)
    assert n_llm(server) == 1
    assert "partial reply" not in app.consciousness()
    assert not [t for r, t in app.chat() if r == "ai" and "partial" in t], "the half-streamed reply is removed"


@pytest.mark.case("1.10")
def test_stop_in_gap_between_cycles(app, server):
    server.reply("again\n\n/self_continue", "should not run" + CFH)
    app.send("go", wait=False)
    app.wait_requests(1)
    app.until_py(lambda: "again" in app.consciousness(), msg="first reply saved")
    app.wait(300)                 # act() has nothing to run; now in the 1 s pause before the next cycle
    app.click_send()
    app.wait(2500)
    with known_bug("BUG-01", reason="Stop in the pause between cycles starts the next cycle at once"):
        assert n_llm(server) == 1


@pytest.mark.case("1.11")
def test_go_with_empty_input_runs_a_cycle(app, server):
    server.reply("woke" + CFH)
    app.go()
    c = app.consciousness()
    env = c.rindex("[System Environment]")
    between = c[env:c.index("**Digital Being", env)]
    assert between.strip().count("\n\n") == 0, "no user text in the cycle"


@pytest.mark.case("1.12")
def test_trigger_from_page_js(app, server):
    server.reply("got it" + CFH, "got json" + CFH)
    app.js("() => trigger('from page code', 100)")
    app.wait_requests(1)
    app.wait_idle()
    assert "from page code" in str(server.llm_requests()[0]["body"])
    app.js("() => trigger({ event: 'click', n: 2 }, 100)")
    app.wait_requests(2)
    app.wait_idle()
    assert '"event": "click"' in str(server.llm_requests()[1]["body"])


@pytest.mark.case("1.13")
def test_repeated_trigger_runs_once(app, server):
    server.reply("once" + CFH)
    app.js("() => { trigger('a', 600); trigger('b', 600); trigger('c', 600); }")
    app.wait_requests(1)
    app.wait_idle()
    app.wait(1500)
    assert n_llm(server) == 1
    assert "a\n\nb\n\nc" in prompt_text(server.llm_requests()[0]["body"])


@pytest.mark.case("1.14")
def test_fresh_being_starts_on_its_own(new_app, server):
    a = new_app()
    server.reply("I am awake" + CFH)
    a.open("new", auto_infer=True)
    a.wait_requests(1, timeout=15)
    a.wait_idle()
    assert "I am awake" in a.consciousness()


@pytest.mark.case("1.17")
def test_timestamp_rows(app, server):
    server.reply("first\n\n/self_continue", "second" + CFH)
    app.send("hello")
    app.wait_requests(2)
    app.wait_idle()
    roles = [r for r, _ in app.chat()]
    i_user = roles.index("user")
    assert roles[i_user - 1] == "timestamp", "user message has its time"
    ai = [i for i, r in enumerate(roles) if r == "ai"]
    assert roles[ai[-1] - 1] == "timestamp", "self cycle gets a timestamp row"


@pytest.mark.case("1.18")
def test_switching_mid_loop_does_not_leak_turn(new_app, server):
    """Issue 1 of the multi-being isolation work: a reply streaming for A must not land in B."""
    a = new_app()
    a.open("new")
    first = a.being_id
    second = a.new_being()
    a.switch(first)
    server.reply(Reply(text="ANSWER-FOR-FIRST" + CFH, delay=4, chunks=8))
    a.send("TASK-FIRST", wait=False)
    a.wait_requests(1)
    a.wait(1000)
    a.switch(second)
    a.wait(5000)
    a.js("() => saveBeing()")
    with known_bug("ISO-1", unless=a.fixed):
        assert "ANSWER-FOR-FIRST" not in a.stored(second)
        assert "TASK-FIRST" not in a.stored(second)
