"""Function tests — stable head, Gemini cache decisions, context compression (checklist area 4)."""
import re

import pytest

BRIDGE_START = "=== bridge:start ==="
BRIDGE_END = "=== bridge:end ==="


def put_skill(app, name, enable=True, instruction=None, code=None, code_readme=None):
    app.db_put({"id": f"{app.being_id}/skill/{name}", "value": {
        "id": name, "instruction": instruction if instruction is not None else f"instruction of {name}",
        "enable": enable, "code": code, "code_readme": code_readme}})


def stable_head(app):
    return app.js("() => loadStableHead()")


# ---- stable head ----------------------------------------------------------------------
@pytest.mark.case("4.01")
def test_stable_head_has_core_mem(app):
    app.db_put({"id": f"{app.being_id}/core_mem.md", "value": "remember: the sky is green"})
    head = stable_head(app)
    assert "=== CORE MEMORY(" in head and f'"{app.being_id}/core_mem.md"' in head
    assert "remember: the sky is green" in head
    assert "[Architecture Note]" in head


@pytest.mark.case("4.02")
def test_core_mem_truncated_at_20000(app):
    app.db_put({"id": f"{app.being_id}/core_mem.md", "value": "a" * 19995 + "KEPT" + "b" + "TAIL_MARK" + "b" * 4991})
    head = stable_head(app)
    assert "KEPT" in head and "TAIL_MARK" not in head
    assert "core_mem truncated to 20000 chars. Full long_mem: 25000 chars" in head
    short = "x" * 20000
    app.db_put({"id": f"{app.being_id}/core_mem.md", "value": short})
    assert "core_mem truncated" not in stable_head(app)


@pytest.mark.case("4.03")
def test_stable_head_lists_enabled_skills_and_names_disabled(app):
    put_skill(app, "zz_enabled", instruction="ENABLED-INSTRUCTION", code="window.x=1", code_readme="README-TEXT")
    put_skill(app, "zz_no_code", instruction="NO-CODE-INSTRUCTION", code_readme="README-WITHOUT-CODE")
    put_skill(app, "zz_disabled", enable=False, instruction="DISABLED-INSTRUCTION")
    head = stable_head(app)
    assert "### Skill: zz_enabled\nENABLED-INSTRUCTION" in head
    assert "_call_ (code_readme):\nREADME-TEXT" in head
    assert "NO-CODE-INSTRUCTION" in head and "README-WITHOUT-CODE" not in head, "readme only when there is code"
    assert "DISABLED-INSTRUCTION" not in head
    assert re.search(r"\[Disabled \(local, not loaded\): [^\]]*zz_disabled", head)


@pytest.mark.case("4.04")
def test_disabled_skill_list_capped_at_50(app):
    app.js("""id => Promise.all([...Array(55).keys()].map(i => DB.put({id: id + '/skill/off_' + String(i).padStart(2, '0'),
        value: {id: 'off_' + i, instruction: 'x', enable: false}})))""", app.being_id)
    head = stable_head(app)
    line = next(l for l in head.splitlines() if l.startswith("[Disabled"))
    assert "(+5 more" in line
    assert line.count("off_") == 50


# ---- Gemini cache decisions (no network) --------------------------------------------
@pytest.mark.case("4.09")
def test_cache_dropped_when_stable_head_changed(fnapp):
    r = fnapp.js("""async () => {
        consciousness = 'abc'.repeat(10);
        const head = await loadStableHead();
        cacheName = 'cachedContents/old'; cachedLength = 9; _cachedStableHead = head + 'CHANGED';
        const p = await buildPayload();
        return { p, cacheName };
    }""")
    assert r["cacheName"] is None
    assert "cachedContent" not in r["p"] and "systemInstruction" in r["p"]


@pytest.mark.case("4.09")
def test_cache_kept_sends_delta_only(fnapp):
    r = fnapp.js("""async () => {
        consciousness = 'OLDPART'.repeat(3) + 'NEWPART';
        const head = await loadStableHead();
        cacheName = 'cachedContents/keep'; cachedLength = 21; _cachedStableHead = head;
        const p = await buildPayload();
        cacheName = null; cachedLength = 0; _cachedStableHead = null;
        return p;
    }""")
    assert r["cachedContent"] == "cachedContents/keep"
    assert "systemInstruction" not in r
    assert r["contents"][0]["parts"][0]["text"] == "NEWPART"


@pytest.mark.case("4.10")
@pytest.mark.parametrize("model,provider_cache", [("claude-sonnet-4.6", "{base}/mock/cache"), ("gemini-3.1-pro", None)])
def test_no_cache_calls_without_gemini_cache_endpoint(fnapp, server, model, provider_cache):
    fnapp.set_settings(model=model)
    fnapp.js("ep => { PROVIDERS.custom.cacheEndpoint = ep; }", provider_cache and provider_cache.format(base=server.base))
    fnapp.js("() => maybeRefreshCache(true, 99999, 0)")
    fnapp.wait(300)
    fnapp.js("() => { PROVIDERS.custom.cacheEndpoint = null; }")
    assert server.of_kind("cache") == []


# ---- compression -----------------------------------------------------------------------
def compress(app, consciousness, prompt_tokens, model="gemini-3.1-pro"):
    app.set_settings(model=model)
    return app.js("""async ([c, t]) => {
        consciousness = c; _lastPromptTokens = t; cacheName = 'cachedContents/x'; cachedLength = 5;
        await maybeCompressConsciousness();
        return { c: consciousness, tokens: _lastPromptTokens, cacheName, cachedLength };
    }""", [consciousness, prompt_tokens])


def logs(app):
    return app.js("""id => DB.list().then(a => a.filter(r => typeof r.id === 'string' && r.id.startsWith(id + '/context_log_'))
        .sort((x, y) => x.id.localeCompare(y.id)))""", app.being_id)


def numbered(n):
    """n chars of text whose every 10-char slot is unique, so cut points can be located."""
    s = "".join(f"<{i:07d}>" for i in range(n // 9 + 1))
    return s[:n]


@pytest.mark.case("4.11")
def test_no_compression_below_limit(app):
    c = numbered(400_000)
    r = compress(app, c, 299_999)
    assert r["c"] == c and r["cacheName"] == "cachedContents/x"
    assert logs(app) == []


@pytest.mark.case("4.12", "4.17")
def test_first_compression_cuts_middle(app):
    c = numbered(400_000)
    r = compress(app, c, 400_000)          # 1 char per token
    out = r["c"]
    head = c[:30_000]                      # LIMIT 300k * head 0.1
    assert out.startswith(head)
    assert BRIDGE_START in out and BRIDGE_END in out
    tail = out.split(BRIDGE_END + "\n\n", 1)[1]
    assert c.endswith(tail) and len(tail) >= 90_000, "tail kept (at least MIN_TAIL_FRAC of the limit)"
    saved = logs(app)
    assert len(saved) == 1
    assert c == head + saved[0]["text"] + tail, "head + saved middle + tail = original"
    gap = re.findall(r"\[gap \d+: .* \.\.\. .*\]", out)
    assert len(gap) == 1
    # 4.17: compression clears the Gemini cache state and the token count
    assert r["tokens"] == 0 and r["cacheName"] is None and r["cachedLength"] == 0


@pytest.mark.case("4.13")
def test_second_compression_rebuilds_one_bridge(app):
    out1 = compress(app, numbered(400_000), 400_000)["c"]
    grown = out1 + numbered(300_000)
    app.wait(5)  # different context_log timestamp
    out2 = compress(app, grown, len(grown))["c"]
    assert out2.count(BRIDGE_START) == 1 and out2.count(BRIDGE_END) == 1
    assert len(re.findall(r"^\[gap \d+:", out2, re.M)) == 2
    assert len(logs(app)) == 2


@pytest.mark.case("4.14")
def test_bridge_keeps_at_most_50_entries(app):
    app.js("""id => Promise.all([...Array(55).keys()].map(i => DB.put({ id: id + '/context_log_' + (1000000000000 + i) + '.txt',
        text: 'old gap ' + i })))""", app.being_id)
    out = compress(app, numbered(400_000), 400_000)["c"]
    assert len(re.findall(r"^\[gap \d+:", out, re.M)) == 50
    assert len(logs(app)) == 56, "all logs stay in IndexedDB; only the bridge is capped"


@pytest.mark.case("4.15")
def test_incidental_bridge_markers_are_not_removed(app):
    fake = f"talking about {BRIDGE_START}\nno gap lines here\n{BRIDGE_END}\n"
    c = fake + numbered(400_000)
    out = compress(app, c, 400_000)["c"]
    assert out.startswith(fake), "delimiters without [gap ...] lines are ordinary text"


@pytest.mark.case("4.16")
def test_per_model_compress_settings(app):
    # claude-sonnet-5.5: compress { at: 128000, head: 0.1, tail: 0.6 }
    c = numbered(200_000)
    assert compress(app, c, 127_999, model="claude-sonnet-5.5")["c"] == c
    out = compress(app, c, 200_000, model="claude-sonnet-5.5")["c"]
    assert out.startswith(c[:12_800]) and not out.startswith(c[:12_801] + "x")
    assert out[12_800:].lstrip("\n").startswith(BRIDGE_START)
