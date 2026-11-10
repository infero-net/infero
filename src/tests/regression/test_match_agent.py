"""JS–Python match tests: src/index.html and relay/agent.py implement the same loop, payloads,
compression, exec parsing and encryption (CLAUDE.md: "Symmetric Logic"). Each test gives both
the same input and compares what they produce.

A difference that exists today is recorded as DRIFT-n in BUGS.md and the test is an expected
failure until the two sides agree again."""
import asyncio
import base64
import json
import os
import re
import sys
import tempfile
import threading

import pytest

from harness import FORMAT_MODELS, Reply

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "relay", "tests")))
from agent_loader import load_agent  # noqa: E402

CFH = "\n\n/call_for_human"
PREFIX_RE = re.compile(r"\*\*Digital Being - \[[^\]]*\]\*\*\n$")
T1 = "**Digital Being - [2026-10-16 10:00:00 UTC+0]**\n"
T2 = "**Digital Being - [2026-10-16 10:05:00 UTC+0]**\n"
CONV = ("[System Environment]\nTime: t1\nDay: Fri\n\nfirst question\n\n" + T1 + "first answer\n\n"
        "System - [Browser] - Result:\n```text\n1\n```\n\n"
        "[System Environment]\nTime: t2\nDay: Fri\n\nsecond question\n\n" + T2 + "second answer\n\n")


@pytest.fixture(scope="module")
def agent():
    return load_agent(tempfile.mkdtemp(prefix="infero-match-"))


def run_async(coro_fn):
    """Run an agent coroutine in its own thread/event loop (Playwright's sync API owns this thread)."""
    box = {}

    def target():
        try:
            box["value"] = asyncio.run(coro_fn())
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
    t = threading.Thread(target=target)
    t.start()
    t.join(60)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def worker(agent, consciousness="", core_mem="", skills=None, realtime="", llm=None, metadata=None, infero_dir=None):
    W = agent.GenesisWorker
    w = W.__new__(W)
    w.being_id = "match_being"
    w.consciousness = consciousness
    w.metadata = metadata or {}
    w._last_realtime = realtime
    w._last_prompt_tokens = 0
    w.llm_settings = llm or {}
    w.running = True
    w.pending_user_input = None
    w.devices = {}
    w.hidden_devices = set()
    w._read_core_mem = lambda: core_mem
    w._read_skills = lambda: skills or []
    w._log = lambda *a, **k: None

    async def no_relay(msg):
        return None
    w.send_relay = no_relay
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    w.cipher = AESGCM(os.urandom(32))
    return w


def agent_llm(app, **extra):
    """The model settings the page sends a device host (loop_handoff / settings_update)."""
    return {**app.js("() => agentModelSettings()"), **extra}


def js_payload(app, consciousness, **settings):
    app.set_settings(**settings)
    return app.js("""async c => { consciousness = c; const p = await buildPayload();
        return { p, sys: SYSTEM_INSTRUCTION, head: await loadStableHead() }; }""", consciousness)


def split_tail(text):
    """'\\n\\n' + realtime + '\\n\\n' + prefix  ->  realtime (prefix timestamps differ by design)."""
    t = text[2:] if text.startswith("\n\n") else text
    t = PREFIX_RE.sub("", t)
    return t[:-2] if t.endswith("\n\n") else t


# ---- 13.01 request bodies ------------------------------------------------------------------
@pytest.mark.case("13.01")
def test_anthropic_layout_matches(fnapp, agent):
    js = js_payload(fnapp, CONV, model=FORMAT_MODELS["anthropic"])
    jc = js["p"]["messages"][0]["content"]
    realtime = split_tail([b for b in jc if b["type"] == "text"][-1]["text"])
    w = worker(agent, CONV, core_mem="cm", skills=[{"name": "s", "instruction": "i"}], realtime=realtime)
    py = w._build_payload("anthropic", js["p"]["model"], js["sys"], False)
    pc = py["messages"][0]["content"]
    jt = [b for b in jc if b["type"] == "text"]
    pt = [b for b in pc if b["type"] == "text"]
    assert [("cache_control" in b) for b in jt] == [("cache_control" in b) for b in pt], "breakpoint positions"
    assert [b["text"] for b in jt[1:-1]] == [b["text"] for b in pt[1:-1]], "history / latest split"
    assert split_tail(pt[-1]["text"]) == realtime
    for k in ("model", "system", "max_tokens", "temperature", "stream"):
        assert py.get(k) == js["p"].get(k), k


@pytest.mark.case("13.01")
@pytest.mark.parametrize("fmt", ["gemini", "openai", "anthropic"])
def test_stop_words_match(fnapp, agent, fmt):
    js = js_payload(fnapp, CONV, model=FORMAT_MODELS[fmt])["p"]
    py = worker(agent, CONV)._build_payload(fmt, "m", "sys", False)
    get = {"gemini": lambda p: p["generationConfig"]["stopSequences"], "openai": lambda p: p.get("stop"),
           "anthropic": lambda p: p["stop_sequences"]}[fmt]
    assert get(py) == get(js)


@pytest.mark.case("13.01")
def test_openai_model_without_stop_words(fnapp, agent):
    js = js_payload(fnapp, CONV, model="gpt-5.4")["p"]
    py = worker(agent, CONV, llm=agent_llm(fnapp))._build_payload("openai", "gpt-5.4", "sys", False)
    assert "stop" not in js
    assert "stop" not in py


@pytest.mark.case("13.01")
@pytest.mark.parametrize("case", ["glm-thinking", "deepseek-off", "adaptive-on"])
def test_openai_thinking_params_match(fnapp, agent, case):
    model, provider, thinking = {
        "glm-thinking": ("glm-5.3", "custom", True),
        "deepseek-off": ("deepseek-v4-flash", "deepseek", False),
        "adaptive-on": ("gpt-6.1-sol", "custom", True),
    }[case]
    js = js_payload(fnapp, CONV, model=model, provider=provider, thinking=thinking)["p"]
    llm = agent_llm(fnapp, provider=provider)
    py = worker(agent, CONV, llm=llm)._build_payload("openai", js["model"], "sys", thinking)
    keys = ("thinking", "reasoning", "temperature")
    assert {k: py.get(k) for k in keys} == {k: js.get(k) for k in keys}


@pytest.mark.case("13.01")
@pytest.mark.parametrize("thinking", [False, True])
def test_anthropic_thinking_params_match(fnapp, agent, thinking):
    for model, mode in (("claude-sonnet-4.6", None), ("claude-opus-5.5", "adaptive")):
        js = js_payload(fnapp, CONV, model=model, thinking=thinking)["p"]
        py = worker(agent, CONV, llm={"thinking_mode": mode})._build_payload("anthropic", js["model"], "sys", thinking)
        for k in ("thinking", "output_config", "temperature", "max_tokens", "fallbacks"):
            assert py.get(k) == js.get(k), f"{model} thinking={thinking}: {k}"


@pytest.mark.case("13.01")
def test_gemini_body_matches(fnapp, agent):
    js = js_payload(fnapp, CONV, model=FORMAT_MODELS["gemini"])["p"]
    realtime = split_tail(js["contents"][0]["parts"][1]["text"])
    py = worker(agent, CONV, core_mem="cm", realtime=realtime)._build_payload("gemini", js["model"], "sys", False)
    assert py["contents"][0]["parts"][0]["text"].endswith(CONV)
    assert split_tail(py["contents"][0]["parts"][1]["text"]) == realtime
    jg, pg = dict(js["generationConfig"]), dict(py["generationConfig"])
    jg.pop("stopSequences"), pg.pop("stopSequences")          # compared in test_stop_words_match
    assert pg == jg
    assert ("systemInstruction" in py) == ("systemInstruction" in js)


@pytest.mark.case("13.01")
def test_gemini_cached_delta_matches(fnapp, agent):
    js = fnapp.js("""async c => { consciousness = c; const head = await loadStableHead();
        cacheName = 'cachedContents/x'; cachedLength = 40; _cachedStableHead = head;
        const p = await buildPayload(); cacheName = null; cachedLength = 0; _cachedStableHead = null; return p; }""", CONV)
    py = worker(agent, CONV, metadata={"cacheName": "cachedContents/x", "cachedLength": 40}) \
        ._build_payload("gemini", "m", "sys", False)
    assert py["cachedContent"] == js["cachedContent"]
    assert py["contents"][0]["parts"][0]["text"] == js["contents"][0]["parts"][0]["text"] == CONV[40:]


@pytest.mark.case("13.01")
def test_stable_head_skill_details_match(fnapp, agent):
    """The browser tells the model each enabled skill's code_readme and lists disabled skills; the agent should too."""
    bid = fnapp.being_id
    fnapp.db_put({"id": f"{bid}/skill/zz_tool", "value": {"id": "zz_tool", "instruction": "TOOL", "enable": True,
                                                          "code": {"js": "1"}, "code_readme": "README-ZZ"}})
    js_head = fnapp.js("() => loadStableHead()")
    fnapp.js("k => DB.del(k)", f"{bid}/skill/zz_tool")
    assert "README-ZZ" in js_head
    d = tempfile.mkdtemp(prefix="infero-match-skills-")
    ag = load_agent(d)
    skill_dir = os.path.join(d, "beings", "match_being", "skill")
    os.makedirs(skill_dir)
    with open(os.path.join(skill_dir, "zz_tool.json"), "w") as f:
        json.dump({"id": "zz_tool", "instruction": "TOOL", "enable": True, "code": {"js": "1"}, "code_readme": "README-ZZ"}, f)
    W = ag.GenesisWorker
    w = W.__new__(W)
    w.being_id, w.consciousness, w.metadata, w.llm_settings = "match_being", "", {}, {}
    w._last_realtime, w._log = "", (lambda *a, **k: None)
    py = w._build_payload("openai", "m", "sys", False)
    assert "README-ZZ" in py["messages"][1]["content"][0]["text"]


# ---- 13.02 exec block parsing ---------------------------------------------------------------
EXEC_CORPUS = [
    "/exec browser\n```js\nwindow.__found.push('a')\n```\n",
    "/browser exec\n```javascript\nwindow.__found.push('b')\n```\n",
    "/exec browser\n```\nwindow.__found.push('c')\n```",
    "  /exec browser\n```js\nwindow.__found.push('d')\n```\n",
    "/exec browser\n```python\nwindow.__found.push('e')\n```\n",
    "/exec browser\n\n```js\nwindow.__found.push('f')\n```\n",
    "text\n/exec browser\n```js\nwindow.__found.push('g')\n```\nmore\n/exec browser\n```js\nwindow.__found.push('h')\n```\n",
]


@pytest.mark.case("13.02")
@pytest.mark.parametrize("text", EXEC_CORPUS, ids=[f"corpus{i}" for i in range(len(EXEC_CORPUS))])
def test_exec_blocks_found_match(fnapp, agent, text):
    js = fnapp.js("async t => { window.__found = []; await _execCode(t); return window.__found; }", text)
    found = []
    w = worker(agent)

    async def fake_browser_exec(code):
        found.extend(re.findall(r"__found\.push\('(\w)'\)", code))
        return ""
    w._exec_browser = fake_browser_exec
    run_async(lambda: w.act(text))
    assert found == js


# ---- 13.03 stop / continue decision -----------------------------------------------------------
STOP_CORPUS = {
    "cfh": ("done\n\n/call_for_human", False),
    "cft": ("sleep\n\n/call_for_trigger", False),
    "sc": ("more\n\n/self_continue", True),
    "none": ("no signal at all", True),
    "fenced": ("```\n/call_for_human\n```\nand on", True),
    "both": ("/self_continue? no: /call_for_human", None),     # the drift case
}


def py_continues(agent, reply):
    w = worker(agent)
    replies = [reply, "stop" + CFH]
    calls = []

    async def perceive():
        return None

    async def infer():
        calls.append(1)
        return replies[len(calls) - 1]

    async def act(B):
        return None
    w.perceive, w.infer, w.act = perceive, infer, act
    w._maybe_compress_consciousness = lambda: None
    w.save_to_disk = lambda: None
    run_async(w.loop)
    return len(calls) == 2


@pytest.mark.case("13.03")
@pytest.mark.parametrize("name", list(STOP_CORPUS))
def test_stop_continue_decision_matches(app, server, agent, name):
    reply, expected = STOP_CORPUS[name]
    server.reply(reply, "stop" + CFH)
    app.send("go")
    app.wait(1600)
    app.wait_idle()
    js_cont = len(server.llm_requests()) == 2
    py_cont = py_continues(agent, reply)
    if expected is not None:
        assert js_cont == expected
    assert py_cont == js_cont


# ---- 13.04 compression ---------------------------------------------------------------------------
def numbered(n):
    return "".join(f"<{i:07d}>" for i in range(n // 9 + 1))[:n]


def norm_gaps(s):
    return re.sub(r"\[gap \d+:", "[gap N:", s)


def py_compress(agent, consciousness, tokens, llm):
    d = tempfile.mkdtemp(prefix="infero-match-compress-")
    w = worker(load_agent(d), consciousness, llm=llm)
    w._last_prompt_tokens = tokens
    w._maybe_compress_consciousness()
    return w.consciousness


def js_compress(app, consciousness, tokens, model):
    app.set_settings(model=model)
    return app.js("""async ([c, t]) => {
        const id = currentBeingId + '/context_log_';
        for (const r of await DB.list()) if (typeof r.id === 'string' && r.id.startsWith(id)) await DB.del(r.id);
        consciousness = c; _lastPromptTokens = t; await maybeCompressConsciousness(); return consciousness; }""",
                  [consciousness, tokens])


@pytest.mark.case("13.04")
def test_compression_matches_with_model_config(fnapp, agent):
    c = numbered(200_000)
    js = js_compress(fnapp, c, 150_000, "claude-sonnet-5.5")
    assert agent_llm(fnapp)["compressCfg"], "this case needs a model with a compress block"
    py = py_compress(agent, c, 150_000, agent_llm(fnapp))
    assert js != c
    assert norm_gaps(py) == norm_gaps(js)


@pytest.mark.case("13.04")
def test_compression_limit_without_model_config(fnapp, agent):
    """deepseek-v4-flash: maxContext 1M, no compress block. Both sides compress at 2/3 of maxContext."""
    c = numbered(500_000)
    js = js_compress(fnapp, c, 400_000, "deepseek-v4-flash")
    py = py_compress(agent, c, 400_000, agent_llm(fnapp))
    assert js == c, "browser: 400k < 666k, no compression"
    assert py == c


# ---- 13.05 overload chain --------------------------------------------------------------------------
@pytest.mark.case("13.05")
def test_overload_chain_matches(fnapp, agent):
    js = fnapp.js("() => [OVERLOAD_CHAIN, ...['claude-fable-5', 'claude-opus-5', 'claude-opus-4-8', 'x'].map(nextOverloadModel)]")
    W = agent.GenesisWorker
    w = W.__new__(W)
    py = [W._OVERLOAD_CHAIN] + [w._next_overload_model(m) for m in ("claude-fable-5", "claude-opus-5", "claude-opus-4-8", "x")]
    assert py == js


# ---- 13.06 SSE parsing -------------------------------------------------------------------------------
def py_infer(agent, server, fmt, reply):
    server.reply(reply)
    w = worker(agent, llm={"format": fmt, "model": "m", "endpoint": f"{server.base}/mock/{fmt}", "token": "t",
                           "system_prompt": "sys", "thinking": False})
    text = run_async(w.infer)
    return {"text": text, "turn": w.consciousness, "prompt": w.metadata.get("promptTokens"),
            "cached": w.metadata.get("cachedTokens")}


def js_infer(app, server, fmt, reply):
    app.use_format(fmt)
    server.reply(reply)
    app.send("go")
    c = app.consciousness()
    turn = c[c.rindex("**Digital Being - ["):]
    think = app.js("() => [...document.querySelectorAll('#chat-box .msg.ai')].pop().querySelector('.msg-thinking')?.innerText || ''")
    return {"turn": turn.split("\n", 1)[1], "think": think,
            "prompt": app.js("() => _lastPromptTokens"), "cached": app.js("() => _lastCachedTokens")}


@pytest.mark.case("13.06")
@pytest.mark.parametrize("fmt", ["gemini", "openai", "anthropic"])
def test_sse_parsing_matches(app, server, agent, fmt):
    r = Reply(text="answer text" + CFH, thinking="", chunks=4, usage={"prompt": 3000, "cached": 700, "output": 9},
              usage_choices="delta")
    js = js_infer(app, server, fmt, r)
    py = py_infer(agent, server, fmt, r)
    assert py["turn"].split("\n", 1)[1] == js["turn"]
    assert py["prompt"] == js["prompt"]
    assert (py["cached"] or 0) == js["cached"]


@pytest.mark.case("13.06")
def test_openai_usage_chunk_with_empty_choices(app, server, agent):
    """OpenAI's final usage chunk (stream_options.include_usage) has "choices": []."""
    r = Reply(text="answer text" + CFH, chunks=3, usage_choices="empty")
    js = js_infer(app, server, "openai", r)
    assert js["turn"].startswith("answer text")
    py = py_infer(agent, server, "openai", r)
    assert py["text"] and py["text"].startswith("answer text")


@pytest.mark.case("13.06")
def test_openai_reasoning_field_matches(app, server, agent):
    """Some OpenAI-compatible providers stream thinking as delta.reasoning, not reasoning_content."""
    r = Reply(text="final" + CFH, thinking="THOUGHTS", reasoning_key="reasoning", usage_choices="delta")
    js = js_infer(app, server, "openai", r)
    assert "THOUGHTS" in js["think"]
    seen = {}
    server.reply(r)
    w = worker(agent, llm={"format": "openai", "model": "m", "endpoint": f"{server.base}/mock/openai", "token": "t",
                           "system_prompt": "s"})
    orig = w.send_relay

    async def capture(msg):
        if msg.get("type") == "stream_token":
            seen.setdefault("payloads", []).append(agent.decrypt(w.cipher, msg["payload"]))
        return await orig(msg)
    w.send_relay = capture
    run_async(w.infer)
    done = [p for p in seen.get("payloads", []) if p.get("done")][0]
    assert done["thinking"] == "THOUGHTS"


@pytest.mark.case("13.06")
def test_trailing_header_stripped_both_sides(app, server, agent):
    r = Reply(text="the answer\n**Digital Being - [2026-01-01 10:01]**", chunks=2)
    js = js_infer(app, server, "gemini", r)
    app.wait_idle()
    py = py_infer(agent, server, "gemini", r)
    assert "2026-01-01" not in js["turn"]
    assert "2026-01-01" not in py["turn"]


# ---- 10.04 – 10.06 pairing crypto -------------------------------------------------------------------
@pytest.mark.case("10.04")
def test_ecdh_hkdf_same_key(fnapp, agent):
    kp = fnapp.js("""async () => {
        const k = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
        window.__pairPriv = k.privateKey;
        return btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey('raw', k.publicKey))));
    }""")
    aes_key, device_pub = agent.ecdh_derive_key(kp)
    js_key = fnapp.js("pub => deriveSharedKey(window.__pairPriv, pub)", device_pub)
    assert base64.b64decode(js_key) == aes_key


@pytest.mark.case("10.05")
def test_aes_gcm_both_directions(fnapp, agent):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = os.urandom(32)
    kb64 = base64.urlsafe_b64encode(key).decode().rstrip("=")
    cipher = AESGCM(key)
    msg = {"cmd": "echo hi", "unicode": "✓ ümlaut", "n": [1, 2]}
    assert fnapp.js("async ([k, ct]) => decryptPayload(await importDeviceKey(k), ct)", [kb64, agent.encrypt(cipher, msg)]) == msg
    ct = fnapp.js("async ([k, m]) => encryptPayload(await importDeviceKey(k), m)", [kb64, msg])
    assert agent.decrypt(cipher, ct) == msg


@pytest.mark.case("10.06")
def test_verify_words_match(fnapp, agent, src_dir):
    words_path = os.path.join(os.path.dirname(src_dir), "relay", "bip39.txt")
    if not os.path.exists(words_path):
        words_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "relay", "bip39.txt")
    words = open(words_path).read().split()
    key = os.urandom(32)
    agent._BIP39[:] = words
    py = agent.pair_verify_words(key)
    js = fnapp.js("""async ([w, k]) => { _bip39 = w; return pairVerifyWords(k); }""", [words, base64.b64encode(key).decode()])
    assert py == js and len(py.split()) == 2
