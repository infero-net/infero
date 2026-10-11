# Multi-being isolation in one browser

Beings that share one Infero browser weren't isolated. The loop, the save path and skill code acted
on whichever being was **on screen**, not the being that started the work. Switching beings, or
opening a second tab, could write one being's work into another, lose it, or wake the other being up.

Issues 1–4 are fixed on branch `fix/multi-being-isolation`. Issue 5 needs a design decision and is
left open. Testing the fixes on dev2.infero.net turned up four more bugs (6–9): three in the fixes
themselves, now fixed, and one older labelling bug, still open. `multi_being_isolation_test.py` and
`remote_host_test.py` in this folder cover all nine; on `dev` at `a1fad55` the main test fails every
fixable check, and with the fixes only the known failures (5, 9) remain.

| # | Problem | Severity | Status |
|---|---|---|---|
| 1 | Switching mid-loop wrote A's turn into B | High | **Fixed** (commit 1) |
| 2 | Switching within ~3 s of a write lost it | Medium | **Fixed** (commit 1) |
| 3 | A's skill code kept running after the switch | Medium | **Fixed** (commit 2) |
| 4 | Multiple tabs: settings overwritten, no way to choose a being, same being clobbered | Medium | **Fixed** (commits 2, 3) |
| 5 | No access boundary between beings | By design today | Open: needs a per-being origin or encryption |
| 6 | After a switch, a paired device host never got the new being | High | **Fixed** (commit 4); regression from commit 2 |
| 7 | Two tabs saving settings at once could still drop a paired device | Medium | **Fixed** (commits 5, 6); gap in commit 3 |
| 8 | Switching back to a being in the same tab refused as "open in another tab" | Medium | **Fixed** (commit 6); regression from commit 3 |
| 9 | Beings that haven't named themselves get the same label in the picker | Low | Open; predates this branch (`72114fe`) |

Line numbers below refer to `src/index.html` at `a1fad55` and are approximate.

## Issues

### 1. Switching mid-loop writes A's turn into B

- `switchBeing` (~2368) and `newBeing` (~2411) replaced the globals `currentBeingId` and
  `consciousness`. They didn't stop a running `loop()`, and the being picker (~1365) was never disabled.
- The loop writes through `appendConsciousness()` (~656) to the global `consciousness`; `saveBeing()`
  (~2266) saves it under the current `currentBeingId`. The rest of A's cycle (reply, exec results,
  `/view` markers) was appended to B's history and saved under B, and the loop carried on as B.
- Not reset on switch: `currentUserInput` (input typed to A was answered as B), `dispatchTimer`,
  `_pendingHandoff` (~41), `window._overloadModel`.

**Manual repro:** host = this browser. Give A a slow task, switch to B while it's inferring.
Before the fix, B's chat ends with A's reply and A is missing that turn. After: B is untouched and A
keeps the prompt; the cancelled turn reruns when A is woken.

### 2. Switching within ~3 s of a write loses it

- Saves are debounced 3 s (`scheduleSaveBeing`, ~2258) and `switchBeing` didn't flush a pending save,
  so A's last writes, held only in memory, were dropped. When the timer fired it saved B's (already
  loaded) consciousness under B, so nothing of A's survived.
- Race (not tested): `switchBeing` set `currentBeingId = B` first, then awaited several IndexedDB
  reads before swapping `consciousness` and `#html-div`. A save in that gap stored A's consciousness
  or UI snapshot (`${id}/snapshot`) under B. Gone now: the switch saves first, then reloads.

**Manual repro:** let A finish a reply, switch to B within 1 s, switch back. Before the fix, A's last
reply is gone.

### 3. A's skill code keeps running after the switch

- Skill JS and `/exec browser` code run in the shared page via `(0, eval)`. `switchBeing` evaluated
  B's enabled skills but never unloaded A's.
- Cleaned on switch: `requestAnimationFrame` loops (`_rafIds`, ~2188); the canvas was cleared and
  `#html-div` emptied.
- Not cleaned: `window.*` globals, `setInterval` / `setTimeout`, `MutationObserver`s, `window` /
  `document` listeners, sockets, prototype patches. Any leftover callback calling `trigger(value)`
  (~4307) started the loop as B.
- Seen in practice with the hub skill `infero_agentic_ui_zh`: its `MutationObserver` on `#html-div`
  calls `window.trigger(...)` on English text and woke B with a translation task.

**Manual repro:** in A, run `setTimeout(() => trigger('hello from A'), 5000)` (or enable
`infero_agentic_ui_zh`), switch to B. Before the fix, B receives the prompt and answers it.

### 4. Multiple tabs

- **Settings:** each tab read `localStorage.genesis_settings` once and later wrote back its whole copy
  (`saveSettingsToStorage`, ~1563, ~20 callers, including every `device_status` message). A device
  paired in tab 1 was erased by tab 2's next save; model, key or host changes reverted the same way.
- **New tabs:** a new tab always opened the most recently updated being (`initApp`, ~2507), usually the
  one another tab was running. There was no URL parameter to choose a being, and boot evaluated the
  being's skills twice.
- **Same being in two tabs:** each tab saved its own in-memory consciousness, so the last save won and
  the other tab's turns were lost.

**Manual repro:** open two tabs. Pair a device in tab 1, then change the model in tab 2: before the
fix, the device is gone after reload. Or chat with the same being in both tabs: only one tab's turns
remain.

### 5. No access boundary between beings (open)

IndexedDB keys are prefixed `<beingId>/` by convention only. Any being's code can list, read and
overwrite other beings' records, identity keys included.

Why it isn't fixed here: the browser has no boundary inside one origin. Every being's code runs with
the page's full privileges, and IndexedDB belongs to the origin (`infero.net`), not to a being. A
`ctx.db` wrapper that enforces the prefix is easy, but skill code can call
`indexedDB.open('GenesisDB')` directly and bypass it; same-origin iframes share the same storage.
A real boundary needs either:

- **an origin per being** (for example `<being>.beings.infero.net`), which changes hosting, nginx,
  TLS and how beings are switched; or
- **per-being encryption** of stored records with a key other beings' code can't reach, which is hard
  while all code shares one page.

The fixes above remove the *accidental* case the original write-up described (A's leftover code
writing into B after a switch): only one being's code runs per page now. Deliberate cross-being
access remains possible.

## Bugs found after the first fix

Found on 2026-10-09/11 while testing the branch on dev2.infero.net, by hand and with the tests below.

### 6. After a switch, a paired device host never got the new being (fixed)

- **Symptom:** with a paired device set as the loop host, switch from B to A. The device keeps
  running B; A never reaches it, and the browser sits in Remote mode.
- **Repro:** pair a device, make it the host while on B, switch to A, then check the device's
  `agent.log` for `Saved being <A's id>` (or run `remote_host_test.py`).
- **Cause:** commit 2 made switching reload the page. Boot's `loadBeing` handoff runs before the relay
  is connected and gives up ("Relay not connected"). When the relay connects, `device_status` asks the
  host to sync A first; the host never had A, replies with 0 chars, and the empty-sync branch only set
  status "Remote" without handing anything over. Before commit 2, `switchBeing` called `doHandoff()`
  while the relay was still connected.
- **Fix (commit 4):** when the host's sync comes back empty and its loop isn't running, hand the current
  being over. `doHandoff()` syncs first, and the agent keeps its own copy if it's longer.
- **Covered by:** `remote_host_test.py` (needs a deployed site; pairs a throwaway device).

### 7. Two tabs saving settings at once could still drop a paired device (fixed)

- **Symptom:** pair a device in tab 1; it's gone from saved settings after tab 2 saves. Seen in WebKit
  (Safari's engine), intermittently.
- **Repro:** tab 1 adds a device and saves settings; tab 2 saves immediately after (check 4b does this
  back to back).
- **Cause:** commit 3 relied on the `storage` event to bring tab 2 up to date before its next save, but
  every connected tab handles the same `device_status` messages and saves at nearly the same moment.
  A first fix (commit 5, a 3-way merge onto the stored copy) still failed in WebKit, which syncs
  `localStorage` between tabs asynchronously: tab 2 can *read* a stale copy, so it merged onto settings
  that didn't have the device yet and wrote it away; tab 1 then adopted that save and lost it too.
- **Fix (commits 5, 6):** saves write only what this tab changed since it last read storage. Devices
  merge as a union and are dropped only through a removal marker (`removedDevices`, keyed by the
  device's key, recorded when this tab's copy loses a device it last read; pruned after 30 days), never
  because they're missing from a stale copy. A tab that sees another tab's save missing one of its
  devices keeps it and saves again. Re-pairing creates a new key, so an old marker doesn't block it.
- **Covered by:** check 4b (three checks: device survives, both tabs' changes kept, a removed device
  stays removed); `remote_host_test.py` (a second tab's real `device_status` traffic).

### 8. Switching back to a being in the same tab refused as "open in another tab" (fixed)

- **Symptom:** in one tab, switch A → B, then pick A. The notice says A "is open in another tab" and
  the tab stays on B; picking A again usually works.
- **Repro:** in Chrome (back/forward cache on, the default), switch A → B → A in the being picker.
- **Cause:** commit 3 locks the shown being for the life of the page and checks `navigator.locks.query()`
  before switching. When the tab navigated A → B, the browser kept the A page in the back/forward cache,
  still holding A's lock, so the query reported A as held. By the second pick the browser had usually
  evicted the cached page. Playwright turns the back/forward cache off by default, so the earlier
  test runs (which also switch A → B → A) didn't see it.
- **Fix (commit 6):** the tab releases its being's lock explicitly just before navigating away. A page
  restored from the back/forward cache (the Back button) reloads, so it takes its lock again.
- **Covered by:** check 8 (one pick, no retry, Chromium with the back/forward cache on). On `ef4e432`
  (before the fix) it fails: "on B; 'open in another tab' notice=True".

### 9. Beings that haven't named themselves get the same label (open)

- **Symptom:** two beings show the same label in the being picker, for example `/call_for_human...`.
- **Repro:** create two beings and don't type to either; let each finish its first turn.
- **Cause:** a being without a `name` is labelled with its `title`, which `saveBeing()` recomputes on
  every save as the first paragraph of the consciousness that doesn't start with a system marker
  (`[System`, `**Digital Being`, `System -`). For a being that spoke first, that paragraph is part of
  its own reply, often just `/call_for_human` or a stock opening line, so labels collide. Unchanged
  since `72114fe` (2026-03-29); switching only made it visible sooner, since a switch saves at once.
- **Proposed fix (not done):** build the title only from the human's first message, parsed the way
  `renderHistory()` finds user input; until there is one, fall back to the short id (`b_xxxxxxxx`).
- **Covered by:** check 9, marked as a known failure until fixed.

### Observation (not a bug): two beings picking the same name

Reported on dev2: A named itself, B was created during A's conversation, and after switching back and
forth (with bug 8) B showed the same name as A with no input to B. Tracing both beings' histories
showed each chose the name on its own, so the match was a coincidence, not a leak between beings.
Replaying the sequence with the fake LLM (A writes its name; B created mid-turn; switches including
the refused picks; back/forward cache on, on `ef4e432` and on the fix) also left B unnamed, and nothing
in switching, saving, the tab locks or device sync copies a `name` between records. Not tracked as a
bug; if it recurs, search B's consciousness for the name (`consciousness.match(/.{0,80}<name>.{0,80}/g)`)
and look for a `DB.put` that wrote it.

## The fix

Switching **pauses** a being: its loop stops, and it resumes when it's woken again. Different beings
in different tabs still run at the same time, since each tab is a separate page.

| Commit | Change | Fixes |
|---|---|---|
| 1 `fix: stop and save the current being before switching` | `leaveBeing()` disables the picker, cancels pending dispatch and auto-infer, aborts the in-flight request, waits for `loop()` to end (the loop now checks `_stopRequested` between steps, since abort alone didn't stop it after `act()`), resets pending input, handoff and overload state, and saves immediately. User actions (`switchBeing`, `newBeing`, duplicate, import, delete) leave first; boot uses the split-out `loadBeing` / `createBeing`. | 1, 2 |
| 2 `fix: reload the page when switching beings; open a being by ?being=<id>` | After leaving, switching reloads the page as `?being=<id>` (`?new` for a new being), so nothing from the old being's code survives. Boot reads `?being=` and keeps it in the URL; skills are evaluated once at boot. (This broke handoff to a remote host, bug 6, fixed in commit 4.) | 3, 4 (choose a being) |
| 3 `fix: keep settings in sync across tabs; one tab per being` | A `storage` listener adopts settings saved by other tabs. Each tab holds a Web Lock (`navigator.locks`) on its being for the life of the page. Picking a being open in another tab is refused with a notice; a tab asking for one opens the next free being, or a new one, and says so. | 4 |
| 4 `fix: hand the being to the remote host after a switch reload` | An empty sync from an idle host hands the current being over. | 6 |
| 5 `fix: merge concurrent settings saves across tabs` | Saves are a 3-way merge: write only what this tab changed since it last read storage onto the current stored copy. | 7 (partly) |
| 6 `fix: same-tab switch refused as "open in another tab"; stale-read-safe device merge` | Release the being's lock before navigating; reload a page restored from the back/forward cache. Devices merge as a union with removal markers, and a tab re-publishes a device another tab's stale save dropped. | 7, 8 |

Trade-offs:

- With a single being, opening a second tab creates a new being (every being is already open), and
  does so on every extra tab. Worth revisiting if that clutters the being list: the alternative is a
  read-only view, or a prompt instead of auto-creating.

- A switch costs a page load (about a second) and a relay reconnect.
- Switching mid-reply cancels that reply (the prompt is kept). Switching while the being is running
  exec code waits for it to finish (up to the 15 s exec timeout); the status shows "Stopping...".
- An ordinary reload (F5) still drops a save that's pending in the 3 s debounce; that's unchanged.

Not done (from the original proposal): a `Being` object per being so beings keep running in the
background, per-being skill resource tracking, and an iframe per being. Pausing makes them
unnecessary for now; they'd matter if background running is wanted later.

## The automated test

```bash
pip install playwright            # plus Playwright's Chromium, or set CHROME=/path/to/chromium
python3 src/tests/multi_being_isolation/multi_being_isolation_test.py [--json results.json]
```

How it works:

- Serves `src/` on `127.0.0.1:8765` (`PORT` to change) and runs it in headless Chromium in a fresh
  profile, with `provider=custom` pointing at a fake LLM on the same server.
- The fake LLM answers the newest `TASK-<x>` marker in the request with `ANSWER-<x> …/call_for_human`
  in Gemini SSE format, so every saved answer can be traced to its prompt. `TASK-SLOW*` prompts stream
  over 6 s; others over 0.3 s.
- Chromium resolves only `127.0.0.1`, jsDelivr, bootcdn and esm.sh (page libraries); every other host
  fails DNS. The device relay URL points at a dead port, so no relay traffic.
- Setup creates two fresh beings, A and B, via `?new` and the New Being button. Switching goes through
  the being picker, like a user, and works whether or not the switch reloads the page, so the same
  test runs on the old code too.
- Chromium runs with the back/forward cache **on** (Playwright turns it off by default; real Chrome
  has it on, and bug 8 only shows with it). `BROWSER=firefox` or `BROWSER=webkit` runs Playwright's
  other engines.
- A refused pick in the being picker is retried, as a user would, so the other checks still run on
  code with bug 8; check 8 alone tests that the first pick works.
- Each check asserts the **correct** behaviour. Checks for issues 5 and 9 are marked known failures.
  Exit code 0 = no failures other than known ones, 1 = any other failure.

Harness notes: the test blocks hosts with Chromium's `--host-resolver-rules` rather than
`ctx.route()`, because Playwright's sync API only serves intercepted requests while Python is inside a
Playwright call, so routed requests stall during `time.sleep()`. It polls on a timer rather than with
`wait_for_function`'s default, because the page's `cancelAllAnimations()` cancels every
`requestAnimationFrame`, including Playwright's.

| Check | Steps | Pass condition |
|---|---|---|
| 1 | On A, send `TASK-SLOW1`; after 4 s (mid-stream, past the save debounce) switch to B; wait for the loop to end | `ANSWER-SLOW1` not in B, and `TASK-SLOW1` stored in A only |
| 2 | On A, send `TASK-FAST2`; as soon as the loop ends switch to B; wait 4 s; switch back | `ANSWER-FAST2` stored in A |
| 3 | On A, eval `setTimeout(() => trigger('TASK-LEAK3', 0), 3000)`; switch to B; wait 5 s | `TASK-LEAK3` not in B |
| 4a | With tab 1 on A, open tab 2 at `index.html?being=<B>` | tab 2 shows B |
| 4a | Reload tab 2 | tab 2 still shows B |
| 4b | Tab 1 adds `devices.TestDevice` and saves; tab 2 changes `vision` and saves right after | `TestDevice` and tab 2's `vision` both stored |
| 4b | Tab 1 removes `TestDevice` and saves; tab 2 saves right after | `TestDevice` stays removed |
| 4c | Tab 2 (on B) picks A in the being picker while tab 1 has A open | tab 2 stays on B |
| 4d | Open tab 3 at `index.html?being=<A>` while tab 1 has A open | tab 3 opens another being |
| 5 | From B, list keys under `<A>/`; write `<A>/probe` | B can neither read nor write A's records (known failure) |
| 8 | In one tab switch A → B, then pick A once (no retry) | tab is on A, no "open in another tab" notice |
| 9 | Right after setup, before any input, read the picker labels of A and B | labels differ (known failure) |

### Remote-host test (bug 6)

```bash
python3 src/tests/multi_being_isolation/remote_host_test.py            # SITE=https://dev2.infero.net by default
AGENT_PYTHON=/path/to/venv/bin/python python3 src/tests/multi_being_isolation/remote_host_test.py
```

Needs a **deployed** site with a device relay (the browser and agent talk through it). It opens the
site in headless Chromium, pairs a throwaway Linux device (`relay/agent.py` downloaded from the site's
relay, run as a subprocess with `INFERO_DIR`/`HOME` in a temp dir; the pairing script itself isn't
run, since it would install a systemd user service and edit your shell rc), makes it the loop host,
switches beings both ways, runs one turn on the device (against the fake LLM on 127.0.0.1), checks a
second tab's relay traffic doesn't drop the device, and unpairs. Without `AGENT_PYTHON` it creates a
venv for the agent in the temp dir. While paired, the agent has shell access to this machine through
the relay; if the test dies mid-run, a pairing token may stay in the relay's `tokens.json`.

Check 4b calls the save directly; a real `device_status` save across tabs is covered by
`remote_host_test.py`. Check 3 uses a one-line timer instead of a real hub skill; the mechanism is the
same. Duplicate, delete, first-visit boot and the Abort button were checked separately with a one-off
script (see Further testing).

## Results

### Latest (2026-10-11, after commit 6)

| Run | Result |
|---|---|
| Main test, Chromium 154 (back/forward cache on) | 11/14 passed, 3 known (5 read, 5 write, 9), 0 failures, exit 0 |
| Main test, Firefox 140 and WebKit 26 | 11/14 each, 3 known, exit 0 (check 8 is only shown to catch bug 8 in Chromium; whether Playwright's Firefox/WebKit keep a back/forward cache wasn't checked) |
| Main test on `ef4e432` (before commit 6) | 10/14: check 8 FAIL ("on B; 'open in another tab' notice=True"), 3 known |
| `remote_host_test.py` on dev2.infero.net | 9/9 passed; relay left with no test tokens |

### First run (2026-10-09, after commit 3)

Chromium 154, Linux, 10 checks (4b was a single check then).

| Check | `dev` at `a1fad55` (before) | `fix/multi-being-isolation` (after) |
|---|---|---|
| 1 | FAIL: loop still running at switch; A's reply saved in **B** | PASS: reply cancelled, saved nowhere; prompt kept in A |
| 2 | FAIL: save pending at switch; A's reply saved **nowhere** | PASS: reply saved in A |
| 3 | FAIL: A's timer prompt landed in **B**, and B answered it | PASS: landed nowhere |
| 4a `?being=` | FAIL: tab 2 opened **A** | PASS: tab 2 opened B |
| 4a reload | FAIL: tab 2 reopened **A** | PASS: tab 2 reopened B |
| 4b | FAIL: saved devices after tab 2's save: `[]` | PASS: `['TestDevice']` |
| 4c | FAIL: tab 2 switched to A, no notice | PASS: tab 2 stayed on B, notice shown |
| 4d | FAIL: tab 3 opened **A** | PASS: tab 3 opened a new being (A and B both open) |
| 5 read | KNOWN: B read all 12 of A's records, incl. `identity` | KNOWN: same |
| 5 write | KNOWN: B wrote `<A>/probe` | KNOWN: same |
| **Total** | 0/10 passed, 8 failures, exit 1 | 8/10 passed, 2 known, exit 0 |

The "after" result was the same in two separate runs.

### Further testing (2026-10-09, after commits 4 and 5)

| Test | How | Result |
|---|---|---|
| Suite in other engines | `BROWSER=firefox` (Firefox 140) and `BROWSER=webkit` (WebKit 26) | 10/12, 2 known, in Chromium, Firefox and WebKit; on `a1fad55`: 1/12 |
| Remote host after a switch | A throwaway Linux device (`relay/agent.py` from dev2, isolated `INFERO_DIR`/`HOME`, fake LLM) paired with a headless browser on dev2.infero.net and set as host; switch beings both ways; run a turn; unpair | Before commit 4 the switched-to being never reached the device. After: each switch hands the new being over, the remote turn's reply lands in that being only, the device stays paired while a second tab's relay traffic saves settings, unpair works |
| Real hub skill `infero_agentic_ui_zh` (issue 3) | Installed from dev's hub into A, English text shown in A, then a new being B | Fires in A. On `a1fad55` its `window.__uiTranslator` observer was still live in B's page; with the fix it's gone and B isn't woken. (B wasn't woken on `a1fad55` either in this run: the skill's own text filter didn't fire on the sentence used) |
| Switch during an 8 s exec block | Fake reply with a `/browser exec` that awaits 8 s; switch 3 s in | Switch waits ~6 s for the exec, then leaves; reply and exec result saved in A only |
| Switch during the 15 s error retry | Fake LLM returns HTTP 500; switch 3 s later | Switch in 0.3 s; one failed request, no retry after leaving; prompt kept in A |
| Three switches fired at once | `switchBeing(B); switchBeing(A); switchBeing(B)` | Ends on B, picker enabled, URL matches |
| Closing a tab frees its being | Tab 1 on A, tab 2 on B, close tab 1, tab 2 picks A | Tab 2 opens A |
| Export, delete, import | Export A (Blob read in page), delete A, import | A deleted, re-imported, consciousness identical |
| Delete every being | Delete until none are left | Boots a fresh being (`?being=…`) |
| `file://` mode | `file:///…/src/index.html?new`, switch, second tab on the same being | Web Locks available, `?being=` kept, switch works, second tab gets another being |
| Phone width (390×844) | Second tab on an open being | Notice readable; picker usable |
| One real LLM turn | dev2.infero.net, default provider (infero) and model (gemini-3.1-pro) | Being answered; no errors |
| Deployment regression | `regression_e2e.py --env both` | 24/24 |

Not run: `relay/tests` (they need `cryptography`/`websockets` in the system Python to load
`agent.py`; unchanged by this branch), Safari on iOS, a real Windows/macOS device.
