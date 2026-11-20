"""Function tests — request bodies, endpoints, model and format choice (checklist area 3)."""
import pytest

from harness import FORMAT_MODELS

T1 = "**Digital Being - [2026-10-16 10:00:00 UTC+0]**\n"
T2 = "**Digital Being - [2026-10-16 10:05:00 UTC+0]**\n"
CONV = ("[System Environment]\nTime: 10/16/2026, 10:00:00 AM (UTC+0)\nDay: Fri\n\nfirst question\n\n"
        + T1 + "first answer\n\n"
        "System - [Browser] - Result:\n```text\n[Return Value]\n1\n```\n\n"
        "[System Environment]\nTime: 10/16/2026, 10:05:00 AM (UTC+0)\nDay: Fri\n\nsecond question\n\n"
        + T2 + "second answer\n\n")


def build(app, consciousness, **settings):
    """buildPayload() for this consciousness and settings (restores neither: fnapp resets settings)."""
    if settings:
        app.set_settings(**settings)
    return app.js("async c => { consciousness = c; return await buildPayload(); }", consciousness)


def consts(app):
    return app.js("() => ({ sys: SYSTEM_INSTRUCTION, stop: ['\\nSystem - [', '\\n[System Environment]', '\\n**Digital Being'] })")


def stable_head(app):
    return app.js("() => loadStableHead()")


def text_blocks(content):
    return [b["text"] for b in content if b.get("type") == "text"]


# ---- 3.01 Gemini -------------------------------------------------------------
@pytest.mark.case("3.01")
def test_gemini_body(fnapp):
    c = consts(fnapp)
    head = stable_head(fnapp)
    p = build(fnapp, CONV)
    assert p["model"] == "gemini-3.1-pro-preview"
    assert p["systemInstruction"]["parts"][0]["text"] == c["sys"]
    assert "cachedContent" not in p
    parts = p["contents"][0]["parts"]
    assert p["contents"][0]["role"] == "user"
    assert parts[0]["text"] == head + CONV, "first part = stable head + consciousness"
    tail = parts[1]["text"]
    assert tail.startswith("\n\n[Realtime]")
    assert "**Digital Being - [" in tail.splitlines()[-1], "tail ends with the being prefix for this turn"
    gc = p["generationConfig"]
    assert gc["temperature"] == 0.7
    assert gc["thinkingConfig"] == {"includeThoughts": True}
    assert gc["stopSequences"] == c["stop"]


# ---- 3.02 OpenAI -------------------------------------------------------------
@pytest.mark.case("3.02")
def test_openai_body(fnapp):
    c = consts(fnapp)
    head = stable_head(fnapp)
    p = build(fnapp, CONV, model=FORMAT_MODELS["openai"])
    assert p["model"] == "glm-5.3"
    assert p["stream"] is True
    assert p["messages"][0] == {"role": "system", "content": c["sys"]}
    content = p["messages"][1]["content"]
    assert p["messages"][1]["role"] == "user"
    assert content[0] == {"type": "text", "text": head + CONV}
    assert content[1]["type"] == "text" and content[1]["text"].startswith("\n\n[Realtime]")
    assert p["temperature"] == 0.7
    assert p["stop"] == c["stop"]
    assert "thinking" not in p


@pytest.mark.case("3.02")
def test_openai_body_no_stop_for_models_without_stop(fnapp):
    p = build(fnapp, CONV, model="gpt-5.4")
    assert "stop" not in p, "models.js marks gpt-5.4 with stop:false"


# ---- 3.03 – 3.06 Anthropic -----------------------------------------------------
@pytest.mark.case("3.03")
def test_anthropic_body(fnapp):
    c = consts(fnapp)
    head = stable_head(fnapp)
    p = build(fnapp, CONV, model=FORMAT_MODELS["anthropic"])
    assert p["model"] == "claude-sonnet-4-6"
    assert p["system"] == c["sys"]
    assert p["stream"] is True and p["max_tokens"] == 16384
    assert p["stop_sequences"] == c["stop"]
    assert p["temperature"] == 0.7 and "thinking" not in p and "fallbacks" not in p
    content = p["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}}
    texts = [b for b in content if b["type"] == "text"]
    assert texts[-1]["text"].startswith("\n\n[Realtime]") and "cache_control" not in texts[-1]
    # history (no breakpoint), then the latest turn (breakpoint)
    assert texts[-2]["text"] == T2 + "second answer\n\n" and texts[-2]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in texts[1]
    assert content[-1]["type"] == "image", "canvas image last (vision model)"


@pytest.mark.case("3.04")
@pytest.mark.parametrize("conv", ["", "no header at all\n\n", T1 + "only turn\n\n", CONV, CONV + CONV])
def test_anthropic_history_concatenates_to_consciousness(fnapp, conv):
    p = build(fnapp, conv, model=FORMAT_MODELS["anthropic"])
    texts = text_blocks(p["messages"][0]["content"])
    assert "".join(texts[1:-1]) == conv, "history + latest must be byte-for-byte the consciousness"


@pytest.mark.case("3.05")
@pytest.mark.parametrize("conv,expected", [("", 1), (T1 + "only turn\n\n", 2), (CONV, 2), (CONV * 5, 2)])
def test_anthropic_cache_breakpoints(fnapp, conv, expected):
    p = build(fnapp, conv, model=FORMAT_MODELS["anthropic"])
    n = sum(1 for b in p["messages"][0]["content"] if "cache_control" in b)
    assert n == expected


@pytest.mark.case("3.06")
def test_anthropic_header_mid_line_does_not_split(fnapp):
    conv = T1 + "answer mentions " + T2 + "and goes on\n\n"   # header text mid-line, followed by \n
    p = build(fnapp, conv, model=FORMAT_MODELS["anthropic"])
    texts = text_blocks(p["messages"][0]["content"])
    assert texts[1:-1] == [conv], "one turn: the inline header is not at a line start"


# ---- 3.07 – 3.10 thinking, adaptive, deepseek, fallbacks -----------------------------
@pytest.mark.case("3.07")
def test_thinking_non_adaptive_anthropic(fnapp):
    p = build(fnapp, CONV, model=FORMAT_MODELS["anthropic"], thinking=True)
    assert p["thinking"] == {"type": "enabled", "budget_tokens": 10000}
    assert p["temperature"] == 1 and p["max_tokens"] == 16000


@pytest.mark.case("3.07")
def test_thinking_non_adaptive_openai(fnapp):
    p = build(fnapp, CONV, model=FORMAT_MODELS["openai"], thinking=True)
    assert p["thinking"] == {"type": "enabled", "budget_tokens": 10000}
    assert p["reasoning"] == {"max_tokens": 8000}
    assert p["temperature"] == 1


@pytest.mark.case("3.08")
def test_thinking_adaptive(fnapp):
    p = build(fnapp, CONV, model="claude-opus-5.5", thinking=True)
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": "high"}
    assert "temperature" not in p


@pytest.mark.case("3.09")
def test_thinking_off_adaptive_has_no_temperature(fnapp):
    p = build(fnapp, CONV, model="claude-opus-5.5", thinking=False)
    assert "temperature" not in p and "thinking" not in p


@pytest.mark.case("3.09")
def test_deepseek_thinking_disabled(fnapp):
    p = build(fnapp, CONV, provider="deepseek", model="deepseek-v4-flash", thinking=False)
    assert p["thinking"] == {"type": "disabled"}


@pytest.mark.case("3.10")
@pytest.mark.parametrize("model,has_fallback", [("claude-opus-5", True), ("claude-fable-5", True),
                                                ("claude-opus-4.8", False), ("claude-sonnet-4.6", False)])
def test_fallbacks_for_fable_and_opus5(fnapp, model, has_fallback):
    p = build(fnapp, CONV, provider="anthropic", model=model)
    assert (p.get("fallbacks") == [{"model": "claude-opus-4-8"}]) == has_fallback


# ---- 3.11 endpoints, 3.13 model id, 3.14 format ------------------------------------
@pytest.mark.case("3.11")
@pytest.mark.parametrize("provider,model,endpoint", [
    ("custom", "gemini-3.1-pro", "{base}/mock/gemini"),
    ("infero", "gemini-3.1-pro", "https://infero.net/api/relay"),
    ("infero", "claude-opus-5.5", "https://infero.net/api/v1/messages"),
    ("google", "gemini-3.1-pro",
     "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?alt=sse"),
    ("openai", "gpt-5.4", "https://api.openai.com/v1/chat/completions"),
    ("anthropic", "claude-sonnet-4.6", "https://api.anthropic.com/v1/messages"),
    ("deepseek", "deepseek-v4-flash", "https://api.deepseek.com/v1/chat/completions"),
    ("no_such_provider", "gemini-3.1-pro", "https://infero.net/api/relay"),
])
def test_endpoint_per_provider(fnapp, server, provider, model, endpoint):
    fnapp.set_settings(provider=provider, model=model)
    assert fnapp.js("() => getActiveEndpoint()") == endpoint.format(base=server.base)


@pytest.mark.case("3.13")
@pytest.mark.parametrize("provider,model,custom,expected", [
    ("openrouter", "claude-sonnet-4.6", "", "anthropic/claude-sonnet-4.6"),
    ("anthropic", "claude-sonnet-4.6", "", "claude-sonnet-4-6"),
    ("custom", "claude-sonnet-4.6", "", "claude-sonnet-4-6"),        # no custom entry: first `from`
    ("custom", "custom", "my-own-model", "my-own-model"),
    ("custom", "not-in-models-js", "", "not-in-models-js"),
])
def test_active_model_id(fnapp, provider, model, custom, expected):
    fnapp.set_settings(provider=provider, model=model, customModel=custom)
    assert fnapp.js("() => getActiveModel()") == expected


@pytest.mark.case("3.13")
def test_overload_model_overrides(fnapp):
    fnapp.set_settings(provider="anthropic", model="claude-fable-5")
    assert fnapp.js("() => { window._overloadModel = 'claude-opus-5'; const m = getActiveModel(); "
                    "window._overloadModel = null; return m; }") == "claude-opus-5"


@pytest.mark.case("3.14")
@pytest.mark.parametrize("provider,model,fmt", [
    ("deepseek", "gemini-3.1-pro", "openai"),       # provider format wins
    ("custom", "gemini-3.1-pro", "gemini"),          # else the model's
    ("custom", "claude-sonnet-4.6", "anthropic"),
    ("custom", "not-in-models-js", "openai"),        # else openai
    ("infero", "claude-opus-5.5", "anthropic"),      # infero has no forced format
])
def test_active_format(fnapp, provider, model, fmt):
    fnapp.set_settings(provider=provider, model=model)
    assert fnapp.js("() => getActiveFormat()") == fmt


# ---- 3.20 / 3.21 canvas image ---------------------------------------------------------
@pytest.mark.case("3.20")
def test_no_image_for_model_without_vision(fnapp):
    p = build(fnapp, CONV, model="deepseek-v4-flash")
    assert not [b for b in p["messages"][1]["content"] if b["type"] != "text"]


@pytest.mark.case("3.21")
def test_canvas_image_shape_per_format(fnapp):
    g = build(fnapp, CONV, model=FORMAT_MODELS["gemini"])
    img = g["contents"][0]["parts"][-1]["inlineData"]
    assert img["mimeType"] == "image/jpeg" and img["data"].startswith("/9j/")
    o = build(fnapp, CONV, model=FORMAT_MODELS["openai"])
    last = o["messages"][1]["content"][-1]
    assert last["type"] == "image_url" and last["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")
    a = build(fnapp, CONV, model=FORMAT_MODELS["anthropic"])
    src = a["messages"][0]["content"][-1]["source"]
    assert src["type"] == "base64" and src["media_type"] == "image/jpeg" and src["data"].startswith("/9j/")
