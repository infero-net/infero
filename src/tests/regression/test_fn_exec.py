"""Function tests — exec block parsing, result formatting, image handling (checklist area 5)."""
import pytest


def exec_code(app, text):
    """Run _execCode on a reply text; returns the system message or None (no block found)."""
    return app.js("t => _execCode(t)", text)


@pytest.mark.case("5.01")
@pytest.mark.parametrize("reply", [
    "/browser exec\n```js\nreturn 41 + 1\n```\n",
    "/exec browser\n```javascript\nreturn 41 + 1\n```\n",
    "/exec browser\n```\nreturn 41 + 1\n```\n",
    "text before\n/exec browser\n```js\nreturn 41 + 1\n```\ntext after\n",
])
def test_exec_block_found(fnapp, reply):
    out = exec_code(fnapp, reply)
    assert out and out.startswith("System - [Browser] - Result:")
    assert "[Return Value]\n42" in out


@pytest.mark.case("5.01")
@pytest.mark.parametrize("reply", [
    "```js\nreturn 1\n```\n",                          # no command line
    "/exec  browser\n```js\nreturn 1\n```\n",          # two spaces
    "exec browser\n```js\nreturn 1\n```\n",            # no slash
    "/exec browser\n```python\nreturn 1\n```\n",       # other language tag
    "  /exec browser\n```js\nreturn 1\n```\n",         # indented
    "/exec browser\n\n```js\nreturn 1\n```\n",         # blank line before the fence
])
def test_exec_block_not_found(fnapp, reply):
    assert exec_code(fnapp, reply) is None


@pytest.mark.case("5.02")
def test_block_at_end_without_newline(fnapp):
    out = exec_code(fnapp, "/exec browser\n```js\nreturn 'end'\n```")
    assert out and "[Return Value]\nend" in out


@pytest.mark.case("5.04")
@pytest.mark.parametrize("code,expected", [
    ("return {a: 1, b: [2, 3]}", '[Return Value]\n{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}'),
    ("let x = 1;", "[Return Value]\nundefined"),
    ("return await new Promise(r => setTimeout(() => r('later'), 50))", "[Return Value]\nlater"),
    ("console.log('hello', {k: 1}); return 5", "[Console Logs]\nhello {\"k\":1}\n[Return Value]\n5"),
])
def test_return_value_formatting(fnapp, code, expected):
    out = fnapp.js("c => _runJS(c)", code)
    assert out == expected


@pytest.mark.case("5.04", "5.05")
def test_thrown_error_formatting(fnapp):
    out = fnapp.js("c => _runJS(c)", "throw new TypeError('boom')")
    assert out.startswith("[Execution Error]\nTypeError: boom")


@pytest.mark.case("5.09")
def test_console_log_restored_after_error(fnapp):
    same = fnapp.js("""async () => { const before = console.log; await _runJS("throw new Error('x')");
        await _runJS("return 1"); return console.log === before; }""")
    assert same


@pytest.mark.case("5.07")
def test_long_result_truncated(fnapp):
    out = exec_code(fnapp, "/exec browser\n```js\nreturn 'x'.repeat(30000)\n```\n")
    assert "[Truncated: 30015 chars total, showing first 20000." in out
    assert out.count("x") < 20100


@pytest.mark.case("5.06")
def test_multiple_blocks_labelled(fnapp):
    out = exec_code(fnapp, "/exec browser\n```js\nwindow._order = ['a']; return 'A'\n```\n"
                           "middle\n/exec browser\n```js\nwindow._order.push('b'); return 'B'\n```\n")
    assert "[Block 1]\n[Return Value]\nA" in out and "[Block 2]\n[Return Value]\nB" in out
    assert fnapp.js("() => window._order") == ["a", "b"]


@pytest.mark.case("5.14")
def test_image_sniff(fnapp):
    r = fnapp.js("() => ['/9j/4AAQ', 'iVBORw0K', 'R0lGODlh', 'UklGRiQA', 'AAAA'].map(_viewSniff)")
    assert r == ["image/jpeg", "image/png", "image/gif", "image/webp", None]


@pytest.mark.case("5.14")
def test_large_image_resized_to_1536(fnapp):
    r = fnapp.js("""async () => {
        const c = document.createElement('canvas'); c.width = 3000; c.height = 600;
        c.getContext('2d').fillRect(0, 0, 10, 10);
        const b64 = c.toDataURL('image/png').split(',')[1];
        const out = await _viewNormalize('image/png', b64);
        const img = new Image();
        await new Promise(res => { img.onload = res; img.src = 'data:' + out.media_type + ';base64,' + out.data; });
        const small = await _viewNormalize('image/png', b64.length ? (() => {
            const s = document.createElement('canvas'); s.width = 100; s.height = 50; return s.toDataURL('image/png').split(',')[1]; })() : '');
        return { type: out.media_type, w: img.naturalWidth, h: img.naturalHeight, smallType: small.media_type };
    }""")
    assert r["type"] == "image/jpeg" and r["w"] == 1536 and r["h"] == 307
    assert r["smallType"] == "image/png", "small accepted images are passed through unchanged"
