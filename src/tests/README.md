# Tests

## Deployment regression (e2e, hits live prod+dev)

```bash
python3 src/tests/regression_e2e.py --env both          # HTTP only (no deps)
python3 src/tests/regression_e2e.py --env both --cdp --ssh
```
- HTTP: genesis static, API-relay auth gate, hub (list / open read / server-rendered
  `/hub/<name>` / gated write / health), device-relay bot-UA filter.
- `--cdp`: boots genesis in a headless Chrome over CDP, asserts MODELS + IndexedDB +
  survives client disconnect. Needs local Google Chrome + `websockets`.
- `--ssh`: checks the 6 systemd units are active. Needs `~/.ssh/ec2_tokyo_2023.pem`.
- Exit non-zero on any failure. **Run after any deploy / nginx / service change.**

## Model smoke (Playwright — drives the real Being loop, looks at output)

Verifies a configured model actually infers → streams → renders `/exec browser`.
Runs as `provider=google` so it needs no relay auth.

```bash
# needs playwright installed somewhere node can resolve it, + local Google Chrome
cd /tmp && npm i playwright            # one-time
GKEY=<google_api_key> node <repo>/src/tests/genesis_smoke.mjs            # gemini-3.5-flash, dev
GKEY=... MODEL=gemini-3.1-pro ENV=prod node .../genesis_smoke.mjs
HEADED=1 GKEY=... node .../genesis_smoke.mjs                              # watch it
```
Asserts: selected model matches, ≥1 `streamGenerateContent` request, response
> 400 chars. Saves a screenshot to `/tmp/genesis_smoke_<env>_<model>.png`.
Use this when adding/bumping a model in `models.js`.

## Unit tests

```bash
python3 -m pytest relay/tests/ -v          # _build_payload, _compress
python3 src/tests/cache_merged_block_test.py
python3 src/tests/test_shell_timeout.py
```

`TEST_PLAN.md` holds the manual UI checks (self-loop, theme switch).

## Multi-being isolation (Playwright — local, no network keys)

Checks that beings sharing one browser stay separate: switching mid-loop, switching right after a
write, leftover skill code, multiple tabs, and IndexedDB access between beings. Uses a local fake LLM,
so it needs no API key. Issue 5 (no storage boundary between beings) is a
known failure; everything else should pass. `remote_host_test.py` (needs a deployed site, pairs a
throwaway device) covers handoff to a remote host after a switch. See `multi_being_isolation/README.md`
for the issues, bug reports, fixes, repro steps and results.

```bash
pip install playwright        # + Playwright's Chromium, or CHROME=/path/to/chromium
python3 src/tests/multi_being_isolation/multi_being_isolation_test.py [--json results.json]
```
