# Regression suite

Offline tests for the features the project has today. They boot the real, unmodified `src/index.html`
in a browser against a local fake LLM, a stub Skill Hub, and (for device tests) a local copy of
`relay/relay.py` with real `relay/agent.py` processes. No API keys, no network, and nothing touches a
deployed relay or `~/.infero`.

What is covered, case by case: [`CHECKLIST.md`](CHECKLIST.md). Bugs the suite found:
[`BUGS.md`](BUGS.md).

## Run

```bash
pip install -r src/tests/regression/requirements.txt
python3 -m playwright install firefox webkit        # Chromium: Playwright's or the system one

python3 src/tests/regression/run.py                 # everything, Chromium (~10 min)
python3 src/tests/regression/run.py --type fn       # one test type (fn, loop, reload, match, device)
python3 src/tests/regression/run.py --browser all   # Chromium, Firefox, WebKit in turn
python3 src/tests/regression/run.py -- -k compress -x   # anything after -- goes to pytest
```

Plain `pytest` works too: `cd src/tests/regression && python3 -m pytest -m "fn or loop"`.

Exit code is non-zero only for real failures. A test hitting a bug listed in `BUGS.md` is reported as
`xfailed` (expected failure) and doesn't fail the run.

## Test types

| Type | Files | What runs |
|---|---|---|
| Function | `test_fn_*.py` | Page loaded once per file; single functions called directly (`buildPayload`, `maybeCompressConsciousness`, `_execCode`, …) |
| Loop | `test_loop_*.py` | The real loop against scripted fake-LLM replies: stop/continue, errors and retries, streaming formats, exec, `/view`, Gemini cache, hub |
| Reload | `test_reload_*.py` | Loop plus page reloads / second tabs: beings, saving and restoring, skills on load, settings, uploads |
| JS–Python match | `test_match_agent.py` | The same input to `src/index.html` and `relay/agent.py`; their outputs must match |
| Device | `test_device_*.py` | Local relay + agent processes, paired through the page's own pairing flow: exec, `/view`, remote host, handoff, re-key, removal |

`relay/tests/` (17 unit tests for `agent.py`) runs separately: `python3 -m pytest relay/tests`.

## Comparing two versions (e.g. a fix branch)

The suite can run against any copy of `src/` with `--src`, so a branch can be checked without
merging the tests into it:

```bash
git worktree add ../tp-fix fix/multi-being-isolation
python3 src/tests/regression/run.py --src src            --json dev.json
python3 src/tests/regression/run.py --src ../tp-fix/src  --json fix.json
python3 src/tests/regression/compare.py dev.json fix.json
git worktree remove ../tp-fix
```

`compare.py` prints only tests whose outcome changed:
- **REGRESSION**: passed (or was a known bug) before, fails now. Exit code 1.
- **FIXED**: failed or was a known bug before, passes now.
- **CHANGED / NEW / GONE**: anything else.

Device tests use the relay and agent from this checkout (`relay/`), not from `--src`.

Behaviour that a change alters on purpose is handled in the test: e.g. `test_switch_shows_the_other_beings_chat_and_ui`
expects an in-place switch on `dev` and a reload as `?being=<id>` with the isolation fix (`App.fixed`).

## How it works

- `harness/server.py` — one local HTTP server: serves `--src`, plays the LLM at `/mock/<format>`
  (Gemini, OpenAI, Anthropic SSE), stubs the hub (`/hub/…`) and the Gemini cache API (`/mock/cache`), and
  records every request. Tests script replies: `server.reply("text\n\n/call_for_human")`,
  `server.reply(Reply(status=529))`, `Reply(delay=4, chunks=8)`, `Reply(drop_after=2)`, …
- `harness/cdn.py` — the page's third-party scripts (`marked`, `html2canvas`, the esm.sh identity
  modules) are answered from `cdn_cache/`. A missing entry is fetched once and stored; set
  `REGRESSION_OFFLINE=1` to forbid that. Every other external host is blocked.
- `harness/app.py` — `App` drives the page: `open()`, `send()`, `wait_idle()`, `reload()`, `new_being()`,
  `switch()`, `consciousness()`, `stored()`, `set_settings()`, `use_format()`, `fast_timers()`.
- `harness/devices.py` — `LocalRelay` (a temp copy of `relay.py` on free 127.0.0.1 ports, behind a
  same-origin proxy that plays nginx) and `LocalAgent` (a temp copy of `agent.py` with its own
  `INFERO_DIR`).
- `harness/bugs.py` — `with known_bug("BUG-03"):` marks assertions that fail because of a listed bug.

Rules that keep tests reliable:
- Never `time.sleep()` in a test: wait with `app.wait(ms)` / `app.until(...)` / `app.until_py(...)`, so
  Playwright keeps serving the page's routed requests meanwhile.
- Don't use `page.wait_for_function()`: the app's `cancelAllAnimations()` cancels the
  `requestAnimationFrame` Playwright polls with.
- Long app timers (15 s retry, 30 s exec, 90 s headers) are shortened with `app.fast_timers({15000: 1500})`,
  which maps those exact delays only.
- Files the browser reads or writes go through `app.upload()` (in-memory) and the `work_dir` fixture
  (`_work/`, not `/tmp`): a snap-packaged Chromium can't access `/tmp`.

## Adding a test

1. Find or add the case in `CHECKLIST.md`.
2. Put it in the file for its test type and mark it: `@pytest.mark.case("5.03")`.
3. Assert the correct behaviour. If it fails because of a real bug, add the bug to `BUGS.md` and wrap
   the failing assertions in `with known_bug("BUG-nn", reason=...)`.
