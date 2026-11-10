# Regression test checklist

Every feature the project has today, as test cases. A ticked box has a test that passes on `dev`
(Chromium, Firefox, WebKit), or fails only because of a bug recorded in `BUGS.md` (named after the case).
Find a case's tests with `grep -rn 'case("5.03"' src/tests/regression`.

**Test types**

| Type | What runs |
|---|---|
| `Fn` | **Function test**: page loaded, one function called directly (`page.evaluate`) |
| `Loop` | **Loop test**: real loop against the scripted fake LLM |
| `Reload` | **Reload test**: a loop test plus a page reload or a second tab |
| `Match` | **JS–Python match test**: same input to `src/index.html` and `relay/agent.py`, same output |
| `Device` | **Device test**: local `relay.py` + local `agent.py`, paired with the page |

**Marks**

- **P1** = run first (core path, or recent bugs here). **P2** = everything else.
- **★** = code the multi-being isolation fix (`fix/multi-being-isolation`) changed. These are the
  cases most likely to catch a regression from that fix.
- **Δ** = behaviour the isolation fix changes on purpose. On `dev` the test asserts the old
  behaviour; with the fix it asserts the new one (see "Running the fix against this suite" in
  `README.md`).

Already covered elsewhere is noted as *(isolation N)* = `multi_being_isolation_test.py` check N,
*(remote-host)* = `remote_host_test.py`, *(agent unit)* = `relay/tests/`.

---

## 1. Core loop (`loop`, `perceive`, `trigger`)

- [x] **1.01** `Loop` P1 ★ — Send a message → one perceive/infer/act cycle; reply rendered and written to consciousness.
- [x] **1.02** `Loop` P1 ★ — Reply ending in `/call_for_human` stops the loop; status returns to Ready.
- [x] **1.03** `Loop` P1 — Reply ending in `/call_for_trigger` stops the loop.
- [x] **1.04** `Loop` P1 ★ — Reply ending in `/self_continue` (or no stop signal) runs another cycle after ~1 s.
- [x] **1.05** `Loop` P1 — Stop signal inside a code block, followed by more text, is ignored (only the tail after the last fence counts).
- [x] **1.06** `Loop` P1 — User message sent while the loop runs forces another cycle even if the reply said `/call_for_human`.
- [x] **1.07** `Loop` P1 — Messages sent while the loop runs are merged into one input for the next cycle.
- [x] **1.08** `Loop` P2 — Empty reply stops the loop.
- [x] **1.09** `Loop` P1 ★ — Stop button (empty input while running) aborts the stream; no further cycle. *(isolation 1, partly)*
- [x] **1.10** `Loop` P2 — Stop during the 1 s gap between cycles prevents the next cycle. — known bug on dev: BUG-01
- [x] **1.11** `Loop` P2 — Send button with empty input while idle starts a cycle with no user text ("Go").
- [x] **1.12** `Loop` P2 — `trigger(msg)` from page JS starts a cycle after its delay; `trigger(obj)` sends JSON.
- [x] **1.13** `Loop` P2 — Repeated `trigger()` calls inside the delay start only one cycle.
- [x] **1.14** `Loop` P2 ★ — New being with empty consciousness starts a cycle on its own after ~3 s (`scheduleAutoInfer`).
- [x] **1.15** `Loop` P1 — Each cycle writes a `[System Environment]` block (time, day) before the reply.
- [x] **1.16** `Fn` P2 — `[Realtime]` context lists host, devices and exec formats correctly for this browser.
- [x] **1.17** `Loop` P2 — Timestamp row shown for self cycles but not for user turns.
- [x] **1.18** `Loop` P1 ★ Δ — Switching beings while the loop runs: on `dev` the loop keeps running (and may write into the other being); with the fix the loop stops and the prompt is kept. *(isolation 1)* — known bug on dev: ISO-1

## 2. Errors and retries

- [x] **2.01** `Loop` P1 — HTTP 500 → `System - [HTTP 500]` in chat and consciousness; retry after 15 s.
- [x] **2.02** `Loop` P1 — Stop during the 15 s retry wait cancels the retry. — known bug on dev: BUG-01
- [x] **2.03** `Loop` P2 — Server unreachable → `Network Unreachable` label.
- [x] **2.04** `Loop` P2 — Connection dropped mid-stream → `Connection Reset` label; partial reply not written as a turn.
- [x] **2.05** `Loop` P2 — No response headers in 90 s → `Timeout` label (run with a shortened timeout hook or skip by default).
- [x] **2.06** `Loop` P1 — `{"error": ...}` inside the SSE stream is raised as an error.
- [x] **2.07** `Loop` P1 — HTTP 529 / "overloaded" on `claude-fable-5` retries on `claude-opus-5`, then `claude-opus-4-8`; system note shown each hop.
- [x] **2.08** `Loop` P2 — Overload on a model outside the chain is a normal error (no fallback).
- [x] **2.09** `Loop` P2 — Next cycle after a fallback starts from the configured model again.
- [x] **2.10** `Loop` P2 — Unhandled promise rejection in page JS is reported as a system message.

## 3. LLM providers and request bodies (`buildPayload`, `streamLLM`)

- [x] **3.01** `Fn` P1 — Gemini body: `systemInstruction`, one user content with stable head + consciousness, volatile tail, `stopSequences`, `thinkingConfig.includeThoughts`.
- [x] **3.02** `Fn` P1 — OpenAI body: system + user messages, content parts, `stop` (omitted when model has `stop: false`), temperature 0.7.
- [x] **3.03** `Fn` P1 — Anthropic body: `system`, stable head with `cache_control`, history, latest turn with `cache_control`, volatile tail last, `stop_sequences`, `max_tokens`.
- [x] **3.04** `Fn` P1 — Anthropic: history + latest concatenate back to the exact consciousness (byte for byte). *(agent unit has the Python version)*
- [x] **3.05** `Fn` P1 — Anthropic: at most the expected number of `cache_control` breakpoints, for 0, 1 and many turns.
- [x] **3.06** `Fn` P2 — Anthropic: turn split ignores a header-like line that is not at a line start.
- [x] **3.07** `Fn` P2 — Thinking on, non-adaptive model: `thinking.enabled` with budget, temperature 1 (Anthropic and OpenAI); `reasoning` for OpenAI.
- [x] **3.08** `Fn` P2 — Thinking on, adaptive model: `thinking.adaptive` + `output_config.effort`, no temperature.
- [x] **3.09** `Fn` P2 — Thinking off: DeepSeek gets `thinking: disabled`; adaptive Anthropic model gets no temperature.
- [x] **3.10** `Fn` P2 — `fable-5` / `opus-5` models get `fallbacks: [opus-4-8]` and the `anthropic-beta` header.
- [x] **3.11** `Fn` P1 — Endpoint per provider: custom uses `apiEndpoint`; infero picks endpoint by format; google builds `models/<id>:streamGenerateContent?alt=sse`; others use `models.js` endpoint.
- [x] **3.12** `Fn` P1 — Auth headers per provider: `x-api-key` (anthropic), `x-goog-api-key` (google), Bearer (others), `X-Client-ID` (infero).
- [x] **3.13** `Fn` P2 — `getActiveModel()` maps the model name through `from[provider]`; custom model name used when set.
- [x] **3.14** `Fn` P2 — Format: provider `format` wins, else the model's, else `openai`.
- [x] **3.15** `Loop` P1 — Gemini SSE: text parts streamed and rendered; `thought` parts go to the thinking box; `usageMetadata` read.
- [x] **3.16** `Loop` P1 — OpenAI SSE: `delta.content`, `reasoning_content` / `reasoning`, `[DONE]`, usage.
- [x] **3.17** `Loop` P1 — Anthropic SSE: `text_delta`, `thinking_delta`, usage from `message_start` (incl. cache read/create) and `message_delta`.
- [x] **3.18** `Loop` P2 — SSE line split across two network chunks is parsed correctly.
- [x] **3.19** `Loop` P2 — Model-written `**Digital Being - [...]**` header at the start, or a trailing one, is stripped before saving.
- [x] **3.20** `Fn` P2 — Model without vision: no canvas image in the body.
- [x] **3.21** `Fn` P2 — Vision on: canvas image appended in the right shape for each format.

## 4. Context: stable head, caching, compression

- [x] **4.01** `Fn` P1 — Stable head contains `core_mem.md` value and the architecture note.
- [x] **4.02** `Fn` P2 — `core_mem.md` over 20,000 chars is truncated with the warning line.
- [x] **4.03** `Fn` P1 — Stable head lists enabled skills' instructions and `code_readme`; disabled skills only by name.
- [x] **4.04** `Fn` P2 — More than 50 disabled skills → list truncated with the overflow note.
- [x] **4.05** `Loop` P2 — Gemini cache: prompt ≥ 4096 tokens on a provider with a cache endpoint → cache created; next request sends only the delta with `cachedContent`.
- [x] **4.06** `Loop` P2 — Gemini cache: small growth → TTL extended (PATCH), not rebuilt.
- [x] **4.07** `Loop` P2 — Gemini cache: growth ≥ 10,000 tokens → new cache created, old one deleted.
- [x] **4.08** `Loop` P2 — Gemini cache: "CachedContent" error → cache rebuilt and request retried once.
- [x] **4.09** `Fn` P2 — Stable head changed since cache creation → cache dropped before the request.
- [x] **4.10** `Fn` P2 — Non-Gemini format, or provider without cache endpoint → no cache calls.
- [x] **4.11** `Fn` P1 — Compression does nothing below the limit (2/3 of `maxContext`, or `compress.at`, or 300k).
- [x] **4.12** `Fn` P1 — Above the limit: head and tail kept, middle saved to `<being>/context_log_<ts>.txt`, bridge block inserted.
- [x] **4.13** `Fn` P1 — Second compression: old bridge removed and rebuilt with both gap entries.
- [x] **4.14** `Fn` P2 — Bridge keeps at most 50 entries.
- [x] **4.15** `Fn` P2 — Text that merely contains the bridge delimiters (no gap lines) is not removed.
- [x] **4.16** `Fn` P2 — Per-model `compress.head` / `compress.tail` settings are used.
- [x] **4.17** `Fn` P2 — Compression clears the Gemini cache state and the prompt-token count.
- [x] **4.18** `Reload` P2 — The custom provider's cache endpoint (settings panel) still applies after a reload. — known bug on dev: BUG-07

## 5. Code execution and `/view`

- [x] **5.01** `Fn` P1 — `/browser exec` and `/exec browser` blocks with ` ```js `, ` ```javascript ` or bare fence are found; other formats are not.
- [x] **5.02** `Fn` P1 — Block at the very end of the reply (no trailing newline) is still found.
- [x] **5.03** `Loop` P1 — Exec result (`[Console Logs]` + `[Return Value]`) shown as a system message and written to consciousness.
- [x] **5.04** `Fn` P2 — Return value: object → pretty JSON, undefined → `undefined`, async code awaited.
- [x] **5.05** `Loop` P1 — Thrown error → `[Execution Error]` with message and stack lines; loop continues.
- [x] **5.06** `Loop` P2 — Several blocks in one reply run in order; results labelled `[Block N]`.
- [x] **5.07** `Fn` P2 — Result over 20,000 chars truncated with the note.
- [x] **5.08** `Loop` P2 — Code still running after 30 s → background note; loop goes on.
- [x] **5.09** `Loop` P2 — `console.log` is restored after exec (including after an error).
- [x] **5.10** `Loop` P2 — Exec code can draw on the canvas and add elements to `#html-div`.
- [x] **5.11** `Loop` P2 — `/view <db key>` loads an image → `<<<IMAGE:key>>>` marker in consciousness and the image in the next request body.
- [x] **5.12** `Loop` P2 — `/view` on a model without vision → "skipped" message.
- [x] **5.13** `Loop` P2 — `/view` of a missing key → "failed" message.
- [x] **5.14** `Fn` P2 — Large image resized to at most 1536 px; type sniffed from bytes.
- [x] **5.15** `Loop` P2 — `/view` in the user's own message is handled too.
- [x] **5.16** `Reload` P2 — Image markers in history render as thumbnails after reload.

## 6. Beings

- [x] **6.01** `Reload` P1 ★ — New being (`?new`): fresh id, identity created, default skills seeded, auto-start. *(isolation, partly)* — known bug on dev: BUG-04
- [x] **6.02** `Reload` P1 ★ Δ — Switch being from the menu: on `dev` swaps in place; with the fix reloads as `?being=<id>`. Either way, the other being's chat, canvas and `#html-div` are shown. *(isolation 3)*
- [x] **6.03** `Reload` P1 ★ — Reload keeps the same being. — known bug on dev: ISO-RELOAD
- [x] **6.04** `Reload` P2 ★ — `?being=<unknown id>` falls back to a sensible being (no crash).
- [x] **6.05** `Reload` P1 ★ — Duplicate being: new id, same consciousness, snapshot and skills; original unchanged. — known bug on dev: BUG-02
- [x] **6.06** `Reload` P1 ★ — Delete being: its records (consciousness, snapshot, skills, logs) removed; another being opened. — known bug on dev: BUG-03
- [x] **6.07** `Reload` P2 ★ — Delete the last being → a fresh being is created.
- [x] **6.08** `Reload` P1 — Export being → JSON file containing all its records.
- [x] **6.09** `Reload` P1 ★ — Import that file → new being with the same content; existing beings untouched.
- [x] **6.10** `Reload` P2 ★ — Import a malformed file → error message, nothing written.
- [x] **6.11** `Fn` P2 ★ Δ — Being menu label: named being → its name; unnamed → on `dev` a text excerpt, with the fix `b_<id8>`.
- [x] **6.12** `Reload` P2 ★ — First visit with no beings → `?skills=infero_receptionist` flow (hub stub).
- [x] **6.13** `Reload` P1 ★ — `?skills=a,b` creates a fresh being, installs both from the hub (stub), shows the result card.
- [x] **6.14** `Reload` P2 ★ Δ — Same being in two tabs: on `dev` both open it; with the fix the second tab gets a different being and a notice. *(isolation 4)* — known bug on dev: ISO-4

## 7. Saving and restoring

- [x] **7.01** `Reload` P1 ★ — Consciousness saved ~3 s after a change and restored on reload.
- [x] **7.02** `Reload` P1 ★ — Leaving the page within 3 s of a change still saves it. *(isolation 2 covers the switch case)* — known bug on dev: BUG-05
- [x] **7.03** `Reload` P1 ★ — `#html-div` content restored after reload.
- [x] **7.04** `Reload` P1 ★ — Form values in `#html-div` (text input, textarea, select, checkbox, radio) restored.
- [x] **7.05** `Reload` P2 — Click or keypress inside `#html-div` schedules a save.
- [x] **7.06** `Reload` P2 ★ — Cache name, cached length and token counts restored with the being.
- [x] **7.07** `Reload` P2 ★ — Old record with `text` instead of `value` is migrated on load.
- [x] **7.08** `Reload` P2 ★ — History render: user, AI, system, timestamp rows in the right order; thinking box shown.
- [x] **7.09** `Reload` P2 ★ — History over 100,000 chars renders only the tail.
- [x] **7.10** `Reload` P2 — Empty being renders "System online" and clears canvas/`#html-div`.
- [x] **7.11** `Loop` P2 — Time rewind on a user message: consciousness cut before that turn, text put back in the input box; refused while the loop runs.
- [x] **7.12** `Reload` P2 ★ — Saving updates the being menu (name, order).

## 8. Skills

- [x] **8.01** `Reload` P1 ★ — All 9 default skills seeded on a new being, enabled.
- [x] **8.02** `Reload` P2 ★ — Seeding again leaves an unchanged skill alone and keeps the user's `enable`, `note`, `contact`.
- [x] **8.03** `Reload` P2 ★ — Changed default skill text is updated on load, keeping `enable`.
- [x] **8.04** `Reload` P1 ★ — Enabled skill with `code` is eval'd on load; disabled skill is not. *(isolation 3)* — known bug on dev: BUG-06
- [x] **8.05** `Reload` P2 ★ — Skill code that throws is logged and does not stop loading.
- [x] **8.06** `Fn` P2 — `skillJsCode`: string → itself; `{js}` → js; `{python}` only → null.
- [x] **8.07** `Reload` P2 — `identity_seed`: identity created once, kept across reloads. — known bug on dev: BUG-04
- [x] **8.08** `Reload` P2 — Identity export → import round trip gives the same pubkey.
- [x] **8.09** `Loop` P2 — `hubSearch` against the hub stub returns and renders results.
- [x] **8.10** `Loop` P1 — `hubInstall(name)` writes `<being>/skill/<name>` and evals its code.
- [x] **8.11** `Loop` P2 — `hubInstall` of an unknown name returns false, writes nothing.
- [x] **8.12** `Loop` P2 — `hubUpload` sends the skill with signed headers (`hubHeaders`).
- [x] **8.13** `Reload` P2 ★ — New being installs `DEFAULT_HUB_SKILLS`; existing beings don't.

## 9. Settings and UI

- [x] **9.01** `Reload` P1 ★ — Settings saved to `localStorage.genesis_settings` and restored after reload.
- [x] **9.02** `Fn` P2 ★ — Missing keys filled from defaults; unknown provider/model handled.
- [x] **9.03** `Reload` P1 ★ Δ — Two tabs change different settings: on `dev` the last save wins; with the fix both changes kept. *(isolation 4a)* — known bug on dev: ISO-4a
- [x] **9.04** `Reload` P1 ★ Δ — Device paired in tab 1 survives a save in tab 2; a device removed in one tab stays removed. *(isolation 4b/4c)* — known bug on dev: ISO-4b
- [x] **9.05** `Fn` P1 — `models.js` providers and models fill the pickers; missing `models.js` falls back to `gemini-3.1-pro`.
- [x] **9.06** `Reload` P2 — Changing provider shows the right token/endpoint fields; custom provider shows endpoint and cache endpoint.
- [x] **9.07** `Reload` P2 — Changing model updates thinking/vision availability.
- [x] **9.08** `Fn` P2 — Vision mode (off / half / full) changes the canvas image sent.
- [x] **9.09** `Reload` P2 — Preview toggle, companion name saved.
- [x] **9.10** `Reload` P2 — "Delete all data" clears IndexedDB and settings (keeps devices as the code intends).
- [x] **9.11** — ~~Language switch~~ Not a feature: `index.html` loads `i18n.js` but never uses `window.I18N`, and has no language switch. Nothing to test.
- [x] **9.12** `Fn` P2 — `i18n.js` parses and every language (zh, ja, en) has the same keys.
- [x] **9.13** `Fn` P1 — `src/index.html` contains no Chinese characters (CLAUDE.md rule).
- [x] **9.14** `Reload` P2 — Attach button / paste an image → stored under `<being>/uploads/`, `/view <key>` added to the input.
- [x] **9.15** `Reload` P2 — Non-image file ignored.
- [x] **9.16** `Reload` P2 ★ — Send button state: Send / Stop / Go matches idle, running and empty input.
- [x] **9.17** `Reload` P2 — Enter sends, Shift+Enter adds a line, IME composition doesn't send.
- [x] **9.18** `Fn` P2 — Status line shows Ready / Inferring / Executing / Caching / Remote.
- [x] **9.19** `Reload` P2 — Canvas resize keeps the drawing scale right (DPR); `canvas.width` not set by app code paths.

## 10. Pairing and encryption

- [x] **10.01** `Device` P1 — Pair a Linux device: `/pair/create` → code → script from `/pair/<code>` → agent connects → device listed online.
- [x] **10.02** `Device` P2 — Windows script from `/pair/<code>/ps1` is well formed.
- [x] **10.03** `Device` P2 — Pairing code is single use and expires.
- [x] **10.04** `Match` P1 — ECDH + HKDF in the browser and in `agent.py` derive the same AES key.
- [x] **10.05** `Match` P1 — AES-GCM: browser encrypts → agent decrypts, and back.
- [x] **10.06** `Match` P2 — Verification words (BIP39) are the same on both sides.
- [x] **10.07** `Fn` P2 — gzip'd sync chunks are reassembled and unzipped (`handleSyncChunk`, `gunzip`).
- [x] **10.08** `Device` P2 — Rekey a device: new key works, old key rejected.
- [x] **10.09** `Device` P2 — Remove a device from the browser: agent told, device gone from the list. — known bug on dev: BUG-08
- [x] **10.10** `Device` P2 — Device removes itself → browser updates.
- [x] **10.11** `Device` P2 — Relay restart keeps paired devices (`tokens.json`).
- [x] **10.12** `Device` P2 — Decrypt failure on the agent is reported to the browser in plain text.

## 11. Relay messaging

- [x] **11.01** `Device` P1 — `/exec shell <device>` block: command runs on the agent, output comes back as a system message.
- [x] **11.02** `Device` P2 — Shell command over the timeout is stopped and reported.
- [x] **11.03** `Device` P2 — Device → device exec is routed to the right peer and the result back to the caller.
- [x] **11.04** `Device` P2 — Agent asks the browser to run JS (`browser_exec_request`) and gets the result.
- [x] **11.05** `Device` P2 — `/view <device>:<path>` reads a file from the device (`file_read`).
- [x] **11.06** `Device` P2 — Ping/pong keeps the connection; missed pong → reconnect with backoff.
- [x] **11.07** `Device` P2 — Second connection with the same identity replaces the first (no duplicate delivery).
- [x] **11.08** `Device` P2 — Messages for another instance id are not delivered.
- [x] **11.09** `Device` P2 — Rate limit on pairing endpoints.
- [x] **11.10** `Device` P2 — Bot user agents rejected. *(regression_e2e covers it on the live site)*
- [x] **11.11** `Device` P2 — `/update` serves the current `agent.py`; `/bip39` serves the word list.

## 12. Remote host and handoff

- [x] **12.01** `Device` P1 ★ — Set host to a device: consciousness handed off, browser status shows Remote. *(remote-host)*
- [x] **12.02** `Device` P1 ★ — User message in remote mode goes to the agent; reply streams into the browser chat. — known bug on dev: BUG-09
- [x] **12.03** `Device` P1 ★ — Stop button in remote mode sends `loop_stop`; the agent stops mid-inference.
- [x] **12.04** `Device` P1 ★ Δ — Switch being while a device is host: with the fix the new being is handed to the device. *(remote-host, isolation 6)*
- [x] **12.05** `Device` P2 — Host goes offline → browser shows it; taking back the host works.
- [x] **12.06** `Device` P2 — Both sides changed consciousness → fork choice offered; each choice keeps the right text.
- [x] **12.07** `Device` P2 — Preferred host remembered; manual switch doesn't overwrite it.
- [x] **12.08** `Device` P2 — `exec_display` image shown in the browser immediately.
- [x] **12.09** `Device` P2 ★ — `stream_token` for a being that is not on screen is ignored.

## 13. `agent.py` loop (and matching the browser)

- [x] **13.01** `Match` P1 — Same consciousness, core_mem and skills → same request body from `_build_payload` and `buildPayload`, for each of the 3 formats. — known bug on dev: DRIFT-1, DRIFT-2, DRIFT-3, DRIFT-4
- [x] **13.02** `Match` P1 — Same exec-block text → same blocks found.
- [x] **13.03** `Match` P1 — Same stop/continue decision for the same reply tail. — known bug on dev: DRIFT-5
- [x] **13.04** `Match` P1 — Same compression result (cut points, bridge block, saved middle). — known bug on dev: DRIFT-6
- [x] **13.05** `Match` P2 — Same overload chain.
- [x] **13.06** `Match` P2 — Same SSE parsing result for the same stream, per format. — known bug on dev: DRIFT-7, DRIFT-8, DRIFT-9
- [x] **13.07** `Device` P2 — `/exec python` runs; hung code stopped after 30 s.
- [x] **13.08** `Device` P2 — `trigger.txt` and `wake_me_up_when(sec)` wake the agent loop.
- [x] **13.09** `Device` P2 — Skill code: shell and Python variants run on agent start.
- [x] **13.10** `Device` P2 — Agent saves to disk and resumes the same being after restart.
- [x] **13.11** `Device` P2 — Second agent instance on the same machine refuses to start.
- [x] **13.12** — Fix `relay/tests` (17 tests): fail loudly when an import is missing; add `websockets`/`aiohttp` to a dev requirements file. *(agent unit)* Done: `relay/tests/agent_loader.py`, `requirements.txt`; all 17 pass.

---

## Totals

| Area | Cases | P1 | ★ |
|---|---|---|---|
| 1 Core loop | 18 | 10 | 6 |
| 2 Errors and retries | 10 | 4 | 0 |
| 3 Providers and bodies | 21 | 10 | 0 |
| 4 Context | 18 | 5 | 0 |
| 5 Exec and `/view` | 16 | 4 | 0 |
| 6 Beings | 14 | 8 | 13 |
| 7 Saving and restoring | 12 | 4 | 9 |
| 8 Skills | 13 | 3 | 6 |
| 9 Settings and UI | 19 | 5 | 5 |
| 10 Pairing and encryption | 12 | 3 | 0 |
| 11 Relay messaging | 11 | 1 | 0 |
| 12 Remote host | 9 | 4 | 5 |
| 13 `agent.py` | 12 | 4 | 0 |
| **Total** | **185** | **65** | **44** |
