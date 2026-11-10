# Bugs found by the regression suite

Tests assert the correct behaviour. Where the app still has one of these bugs, the test is reported as
an expected failure (`xfailed`) with the id below (see `harness/bugs.py`). When a bug is fixed, its tests
start failing with "fixed?": remove the `known_bug(...)` wrapper and move the entry to "Fixed" below.

Found on `dev` at `14249d5` (2026-10-16). "Also on fix branch" means `fix/multi-being-isolation` has the
same code.

## Open app bugs (`src/index.html`)

All four are in code that `fix/multi-being-isolation` rewrites, so they wait for that PR.

| Id | Summary | Tests |
|---|---|---|
| BUG-02 | Duplicate ("fork") copies only consciousness and the UI snapshot, not core memory or installed skills | `test_reload_beings::test_duplicate_being` |
| BUG-03 | Deleting a being leaves most of its records in IndexedDB, including its identity mnemonic | `test_reload_beings::test_delete_being_removes_all_its_records` |
| BUG-04 | A fresh being's session identity is a stray one: a `null/identity` record is written and hub requests can be signed with the wrong pubkey | `test_reload_beings::test_new_being_session_identity_is_its_own` |
| BUG-09 | A message sent to a remote host while its agent reconnects is dropped without notice; the page shows "Inferring..." forever | `test_device_remote::test_message_sent_while_device_reconnects` |

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
record stays. Also on fix branch. The test is marked timing-dependent: in Firefox the stray write
occasionally lands after the check.

Fix idea: match the call exactly (e.g. `/window\.identityInit\(\)\.catch\([\s\S]*\);?\s*$/`) or move the
call out of the skill code.

### BUG-09 — Message lost while the remote host reconnects

The relay waits 8 s before announcing a device offline, so during a quick agent restart (`infero update`,
`infero online`, a crash + systemd restart) the page still shows the host online. `trigger()` sends
`user_input`; the relay finds no connection for that device and drops it (`send_to_device` has no error
path). The page shows "Inferring..." until reload; the message is never processed.

## Fixed by the multi-being isolation PR

These tests expect the isolated behaviour. On `dev` they are expected failures; with
`fix/multi-being-isolation` (`5847ad9`) all six pass, with no regressions (see `README.md` → "Comparing two
versions"). Details: `src/tests/multi_being_isolation/README.md` on that branch.

| Id | Summary | Test |
|---|---|---|
| ISO-1 | Switching beings mid-reply writes the reply into the other being | `test_loop_core::test_switching_mid_loop_does_not_leak_turn` |
| ISO-4 | The same being can be open in two tabs | `test_reload_beings::test_same_being_not_open_in_two_tabs` |
| ISO-4a | Two tabs saving settings: the last one overwrites the other's changes | `test_reload_settings::test_two_tabs_change_different_settings` |
| ISO-4b | A settings save in one tab drops a device paired in another | `test_reload_settings::test_device_paired_in_one_tab_survives_save_in_other` |
| ISO-RELOAD | A reload reopens the most recently saved being, not the open one | `test_reload_beings::test_reload_keeps_the_open_being` |
| BUG-06 | Every enabled skill's code runs twice on each page load: `boot()` evaluates them after `initApp()` → `switchBeing()` already did | `test_reload_skills::test_enabled_skill_code_runs_on_load` |

## Fixed

| Id | Was | Fix |
|---|---|---|
| BUG-01 | Stop during the 1 s pause between cycles or the 15 s retry wait started the next cycle at once: Stop nulls `currentAbortController`, so `loop()`'s `currentAbortController?.signal.aborted` read `undefined` | `loop()` keeps its own reference to the controller it waits on |
| BUG-05 | Closing or reloading within ~3 s of a change lost it (saves are scheduled 3 s later) | A pending save is written on `beforeunload` / `pagehide` in one transaction on a connection opened at startup (WebKit drops a transaction started in `pagehide`, hence `beforeunload` too); a hidden tab runs the normal save |
| BUG-07 | The custom provider's cache endpoint was applied only when the settings panel closed, so it was lost on reload | `loadModels()` sets it from settings at startup |
| BUG-08 | Removing an online device never showed "deleted": the handler only announced devices still in settings | The "deleted" note is shown for a device removed from this page |
| BUG-10 | WebKit (Safari private browsing): attaching an image failed, a `File` can't be stored in IndexedDB there | The upload is stored as a data URL, which `/view` already reads |
| BUG-11 | WebKit: "Reset all data" could reload with all data still there (`deleteDatabase` is blocked by the page's open connections) | `resetAllData()` clears every store, then deletes the database and reloads once that has settled |
| DRIFT-1 | agent.py's stop words lacked `\n**Digital Being` | Same stop words as the browser |
| DRIFT-2 | agent.py sent `stop` to models marked `stop: false` | The page sends the model's `stop` flag; agent.py omits `stop` when it's false |
| DRIFT-3 | agent.py's OpenAI body had no `thinking` / `reasoning` (or DeepSeek `thinking: disabled`) | Same thinking parameters and temperature rules as the browser |
| DRIFT-4 | agent.py's stable head left out each skill's `code_readme` and the disabled-skill list | Both added |
| DRIFT-5 | A tail with both `/self_continue` and `/call_for_human`: the browser stopped, agent.py continued | A stop signal wins on both |
| DRIFT-6 | agent.py compressed at 300k unless the model had a `compress` block | The page sends `maxContext`; agent.py uses 2/3 of it, like the browser |
| DRIFT-7 | agent.py ignored `delta.reasoning` | Read like `reasoning_content` |
| DRIFT-8 | agent.py stripped only a leading model-written turn header | Same two regexes as the browser (leading and trailing) |
| DRIFT-9 | agent.py raised `IndexError` on an OpenAI chunk with `"choices": []` (the usage chunk) and dropped the reply | Empty `choices` is skipped |

The page now sends the model fields a device host needs through one function, `agentModelSettings()`
(`thinking_mode`, `compressCfg`, `maxContext`, `stop`), used by both `loop_handoff` and `settings_update`;
`test_match_agent.py` builds the agent's settings from it.
