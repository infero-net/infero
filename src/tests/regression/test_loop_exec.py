"""Loop tests — code execution and /view inside the loop, time rewind (areas 5 and 7)."""
import pytest

from harness import Reply
from harness.server import prompt_text

CFH = "\n\n/call_for_human"
# 4x4 red PNG
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEklEQVR4nGP4z8CAB+GTG8HSALfKY52fTcuYAAAAAElFTkSuQmCC")


def exec_reply(code, tail="/call_for_human"):
    return f"running it\n/exec browser\n```js\n{code}\n```\n{tail}"


@pytest.mark.case("5.03")
def test_exec_result_reaches_consciousness_and_next_request(app, server):
    server.reply(exec_reply("console.log('side'); return 6 * 7", "/self_continue"), "saw 42" + CFH)
    app.send("compute")
    app.wait_requests(2)
    app.wait_idle()
    c = app.consciousness()
    assert "System - [Browser] - Result:\n```text\n[Console Logs]\nside\n[Return Value]\n42\n```" in c
    assert any(r == "system" and "42" in t for r, t in app.chat())
    assert "[Return Value]\n42" in prompt_text(server.llm_requests()[1]["body"])


@pytest.mark.case("5.05")
def test_exec_error_reported_and_loop_still_works(app, server):
    server.reply(exec_reply("null.boom"), "fine again" + CFH)
    app.send("break it")
    assert "[Execution Error]\nTypeError" in app.consciousness()
    app.send("again")
    assert "fine again" in app.consciousness()


@pytest.mark.case("5.08")
def test_long_exec_continues_in_background(app, server):
    app.fast_timers({30000: 1500})
    server.reply(exec_reply("await new Promise(r => setTimeout(r, 4000)); return 'late result'", "/self_continue"),
                 "next cycle ran" + CFH)
    app.send("slow")
    app.wait_requests(2)
    app.wait_idle()
    c = app.consciousness()
    assert "Async task（>30s）continues in background" in c
    assert c.index("continues in background") < c.index("next cycle ran")
    app.until_py(lambda: "late result" in app.consciousness(), timeout=10, msg="late exec result")


@pytest.mark.case("5.10")
def test_exec_can_build_ui_and_draw(app, server):
    code = ("const d = document.createElement('div'); d.id = 'made-by-ai'; d.textContent = 'HELLO UI';"
            "document.getElementById('html-div').appendChild(d);"
            "const ctx = document.getElementById('main-canvas').getContext('2d'); ctx.fillStyle = '#ff0000';"
            "ctx.fillRect(0, 0, 40, 40); return 'drawn'")
    server.reply(exec_reply(code))
    app.send("draw")
    assert app.js("() => document.getElementById('made-by-ai')?.textContent") == "HELLO UI"
    px = app.js("() => [...document.getElementById('main-canvas').getContext('2d').getImageData(5, 5, 1, 1).data]")
    assert px[:3] == [255, 0, 0]


@pytest.mark.case("5.11")
def test_view_loads_image_into_next_request(app, server):
    key = f"{app.being_id}/pics/red.png"
    app.db_put({"id": key, "value": PNG})
    server.reply(f"let me look\n/view {key}\n\n/self_continue", "it is red" + CFH)
    app.send("look")
    app.wait_requests(2)
    app.wait_idle()
    c = app.consciousness()
    assert f"System - [View] loaded {key} (image/png" in c
    marker = c[c.index("<<<IMAGE:"):].split(">>>")[0][len("<<<IMAGE:"):]
    assert marker.startswith(f"{app.being_id}/vision/")
    parts = server.llm_requests()[1]["body"]["contents"][0]["parts"]
    imgs = [p["inlineData"] for p in parts if "inlineData" in p]
    assert any(i["mimeType"] == "image/png" and i["data"] == PNG for i in imgs)


@pytest.mark.case("5.12")
def test_view_skipped_without_vision(app, server):
    app.use_format("openai", model="deepseek-v4-flash")
    key = f"{app.being_id}/pics/red.png"
    app.db_put({"id": key, "value": PNG})
    server.reply(f"/view {key}" + CFH)
    app.send("look")
    assert f"System - [View] {key} → skipped: current model does not support image input" in app.consciousness()


@pytest.mark.case("5.13")
def test_view_missing_key(app, server):
    server.reply("/view nothing/here.png" + CFH)
    app.send("look")
    assert "System - [View] nothing/here.png → failed: IndexedDB key not found" in app.consciousness()


@pytest.mark.case("5.15")
def test_view_in_user_message(app, server):
    key = f"{app.being_id}/uploads/1_red.png"
    app.db_put({"id": key, "value": PNG})
    server.reply("I see it" + CFH)
    app.send(f"what is this\n/view {key}")
    parts = server.llm_requests()[0]["body"]["contents"][0]["parts"]
    assert any(p.get("inlineData", {}).get("data") == PNG for p in parts), "image goes with the same request"


@pytest.mark.case("7.11")
def test_time_rewind(app, server):
    server.reply("answer one" + CFH, "answer two" + CFH)
    app.send("question one")
    app.send("question two")
    before = app.consciousness()
    app.page.click("#chat-box .msg.user >> nth=1")
    app.until("() => document.getElementById('input').value === 'question two'")
    after = app.consciousness()
    assert "answer one" in after and "question two" not in after and "answer two" not in after
    assert len(after) < len(before)
    assert any(d[0] == "confirm" and "Rewind" in d[1] for d in app.dialogs)


@pytest.mark.case("7.11")
def test_time_rewind_refused_while_running(app, server):
    server.reply("one" + CFH, Reply(text="slow" + CFH, delay=3, chunks=6))
    app.send("first")
    app.send("second", wait=False)
    app.wait_requests(2)
    app.page.click("#chat-box .msg.user >> nth=0")
    app.wait(300)
    assert not app.dialogs, "no rewind prompt while the loop runs"
    app.wait_idle()
    assert "first" in app.consciousness()
