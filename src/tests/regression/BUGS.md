# Bugs found by the regression suite

Tests assert the correct behaviour. Where the app still has one of these bugs, the test is reported as
an expected failure (`xfailed`) with the id below (see `harness/bugs.py`). When a bug is fixed, its tests
start failing with "fixed?": remove the `known_bug(...)` wrapper and the entry here.

Found on `dev` at `14249d5` (2026-10-16). "Also on fix branch" means `fix/multi-being-isolation` has the
same code.

## App bugs (`src/index.html`)

| Id | Summary | Tests |
|---|---|---|
| BUG-01 | Stop doesn't stop the loop during the 1 s pause between cycles or the 15 s retry wait after an error: it skips the wait and starts the next cycle at once | `test_loop_core::test_stop_in_gap_between_cycles`, `test_loop_errors::test_stop_during_retry_wait` |
| BUG-02 | Duplicate ("fork") copies only consciousness and the UI snapshot, not core memory or installed skills | `test_reload_beings::test_duplicate_being` |
| BUG-03 | Deleting a being leaves most of its records in IndexedDB, including its identity mnemonic | `test_reload_beings::test_delete_being_removes_all_its_records` |
| BUG-04 | A fresh being's session identity is a stray one: a `null/identity` record is written and hub requests can be signed with the wrong pubkey | `test_reload_beings::test_new_being_session_identity_is_its_own` |
| BUG-05 | Closing or reloading the page within ~3 s of a change loses it (the latest reply) | `test_reload_persist::test_reload_right_after_a_change_keeps_it` |
| BUG-06 | Every enabled skill's code runs twice on each page load | `test_reload_skills::test_enabled_skill_code_runs_on_load` |
| BUG-07 | The custom provider's cache endpoint is lost on reload (Gemini caching silently off) | `test_reload_settings::test_custom_cache_endpoint_survives_reload` |
| BUG-08 | Removing an online device never shows the "deleted" message | `test_device_pairing::test_remove_device_from_browser` |
| BUG-09 | A message sent to a remote host while its agent reconnects is dropped without notice; the page shows "Inferring..." forever | `test_device_remote::test_message_sent_while_device_reconnects` |
| BUG-10 | WebKit (Safari private browsing, Playwright's WebKit): attaching an image fails — a `File` can't be stored in IndexedDB there | `test_reload_settings::test_attach_image` (WebKit only) |
| BUG-11 | WebKit: "Reset all data" sometimes reloads with all data still there (timing-dependent) | `test_reload_settings::test_reset_all_data` (WebKit only) |

### BUG-01 — Stop during the pause between cycles starts the next cycle

`loop()` waits 1 s before the next cycle (15 s after an error) and then checks
`if (currentAbortController?.signal.aborted) return;`. The Stop button (`sendBtn.onclick`) and
`sendMessage()` call `currentAbortController.abort()` **and then set `currentAbortController = null`**.
The check reads `null?.signal.aborted`, which is `undefined`, so `loop()` continues: pressing Stop skips
the wait and starts the next cycle immediately. Also on fix branch.

Repro: a reply ending in `/self_continue`; press Stop right after it finishes. The next request goes out
at once. Same after an HTTP error: Stop during "retry in 15 s" retries immediately.

Fix idea: keep the controller in a local (`const ctl = currentAbortController = new AbortController()`) and
check `ctl.signal.aborted`.

### BUG-02 — Duplicate drops core memory and skills

`duplicateBeing()` switches `currentBeingId` to `being_fork_<ts>`, appends a fork note and calls
`saveBeing()`, which writes only the metadata, consciousness and snapshot records, then seeds default
skills. `core_mem.md`, skills installed from the hub or written by the being, uploads and the identity are
not copied, so the fork starts without its long-term notebook. Also on fix branch.

### BUG-03 — Delete leaves orphaned records

`deleteBeing()` deletes `<id>` and `<id>/consciousness.txt` only. `<id>/snapshot`, `<id>/core_mem.md`,
`<id>/skill/*`, `<id>/identity` (the 12-word mnemonic), `<id>/context_log_*`, `<id>/uploads/*` and
`<id>/vision/*` stay in IndexedDB with no way to reach them from the UI. Also on fix branch.

### BUG-04 — Stray identity on a fresh being

`newBeing()` (`createBeing()` on the fix branch) evaluates the `identity_seed` skill code after trying to
strip its last line, `window.identityInit().catch(e => console.error('[identity] init failed', e));`, with
`/window\.identityInit\(\)\.catch\([^)]*\);?\s*$/`. The handler contains `)`, so the regex never matches
and `identityInit()` runs while `currentBeingId` is still the previous being (or `null` on a first visit).
On a first visit it writes a new mnemonic to the key `null/identity`. Whichever `identityInit()` finishes
last sets `window._identity`, so for the rest of that session `hubHeaders()` may sign hub requests with
the stray identity (or the previous being's), not the new being's. A reload fixes the session; the stray
record stays. Also on fix branch.

Fix idea: match the call exactly (e.g. `/window\.identityInit\(\)\.catch\([\s\S]*\);?\s*$/`) or move the
call out of the skill code.

### BUG-05 — Last few seconds lost on reload / close

Changes are saved by `scheduleSaveBeing()` 3 s after the last change; nothing saves on `pagehide` /
`beforeunload`. A reply that finished within 3 s of closing or reloading the tab is lost. Also on fix branch:
it saves at once when *switching* beings, but not on reload or close.

### BUG-06 — Skill code runs twice per load

`boot()` calls `initApp()` → `switchBeing()`, which evals every enabled skill's code, and then evals them
all again itself. The `?new` path does the same (`newBeing()` evals, then `boot()` again). Skill code that
adds listeners, timers or UI does so twice. Fixed on the fix branch (confirmed by `compare.py`): its `boot()` no
longer evaluates skills a second time.

### BUG-07 — Custom cache endpoint lost on reload

`PROVIDERS.custom.cacheEndpoint` is set from `settings.customCacheEndpoint` only in `applySettings()`
(closing the settings panel). After a reload it is `null`, so `maybeRefreshCache()` never caches for the
custom provider until the user opens and closes the settings panel again. Also on fix branch.

### BUG-08 — No "deleted" note for an online device

`removeDevice()` deletes the device from `settings.devices` and waits for the relay's offline
`device_status` to show "<name> deleted". That handler only announces devices still in
`settings.devices`, so the note never appears (the pending flag is consumed silently).

### BUG-09 — Message lost while the remote host reconnects

The relay waits 8 s before announcing a device offline, so during a quick agent restart (`infero update`,
`infero online`, a crash + systemd restart) the page still shows the host online. `trigger()` sends
`user_input`; the relay finds no connection for that device and drops it (`send_to_device` has no error
path). The page shows "Inferring..." until reload; the message is never processed.

### BUG-10 — Image attach fails in WebKit private storage

`storeUpload()` stores the picked `File` itself in IndexedDB. WebKit refuses that in ephemeral / private
storage with "Error preparing Blob/File data to be stored in object store" (the same message Safari Private
Browsing gives), so the app shows "Attach failed". Storing a base64 string or an `ArrayBuffer` (as `/view`
already accepts) would work everywhere. Seen in Playwright's WebKit; not checked in a real Safari.

### BUG-11 — "Reset all data" doesn't delete anything in WebKit

The handler calls `indexedDB.deleteDatabase('GenesisDB')`, `localStorage.clear()` and then
`location.reload()` without waiting for the delete. The page's own connections are still open (`DB.open()`
opens a new one per call and never closes them), so the delete is blocked; Chromium and Firefox finish it
across the reload, WebKit doesn't, and the old beings come back. Waiting for `onsuccess` (closing connections
first) before reloading would fix it. Seen in Playwright's WebKit; not checked in a real Safari.

## JS–Python drift (`src/index.html` ↔ `relay/agent.py`)

CLAUDE.md asks for both to implement the same logic. These differ today
(`test_match_agent.py`):

| Id | Difference | Effect |
|---|---|---|
| DRIFT-1 | agent.py's stop words lack `\n**Digital Being` | a model that starts writing the next turn header isn't stopped on a device host |
| DRIFT-2 | agent.py sends `stop` for models `models.js` marks `stop: false` (gpt-5.4, gpt-6.1-sol, …) | those models get a parameter the browser deliberately leaves out |
| DRIFT-3 | agent.py's OpenAI-format body has no `thinking` / `reasoning` parameters (and no DeepSeek `thinking: disabled`) | thinking settings are ignored on a device host |
| DRIFT-4 | agent.py's stable head lists skills without their `code_readme` (and no disabled-skill list) | the model on a device host doesn't learn how to call cached skill code |
| DRIFT-5 | A reply tail with both `/self_continue` and `/call_for_human`: the browser stops, agent.py continues | different behaviour on the two hosts |
| DRIFT-6 | agent.py compresses at 300k tokens unless the model has a `compress` block; the browser uses 2/3 of `maxContext` | on a device host, 1M-context models compress early and 128k models (e.g. deepseek-v3.2) only at 300k, past their context limit |
| DRIFT-7 | agent.py ignores `delta.reasoning` (OpenAI-compatible providers such as OpenRouter) | thinking text is lost on a device host |
| DRIFT-8 | agent.py strips only a leading model-written turn header, not a trailing one | stray headers saved in consciousness on a device host |
| DRIFT-9 | agent.py raises `IndexError` on an OpenAI stream chunk with `"choices": []` (OpenAI's usage chunk with `stream_options.include_usage`; some providers send one) | the whole reply is dropped and replaced by `System - [Error] list index out of range` |

## Fixed by the multi-being isolation PR

These tests expect the isolated behaviour. On `dev` they are expected failures; with
`fix/multi-being-isolation` (`5847ad9`) all five pass, as does BUG-06, with no regressions (see `README.md` → "Comparing two versions"). Details:
`src/tests/multi_being_isolation/README.md` on that branch.

| Id | Summary | Test |
|---|---|---|
| ISO-1 | Switching beings mid-reply writes the reply into the other being | `test_loop_core::test_switching_mid_loop_does_not_leak_turn` |
| ISO-4 | The same being can be open in two tabs | `test_reload_beings::test_same_being_not_open_in_two_tabs` |
| ISO-4a | Two tabs saving settings: the last one overwrites the other's changes | `test_reload_settings::test_two_tabs_change_different_settings` |
| ISO-4b | A settings save in one tab drops a device paired in another | `test_reload_settings::test_device_paired_in_one_tab_survives_save_in_other` |
| ISO-RELOAD | A reload reopens the most recently saved being, not the open one | `test_reload_beings::test_reload_keeps_the_open_being` |
