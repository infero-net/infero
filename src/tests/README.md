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

## Regression suite (offline — run before merging)

Tests the features the app has today against a local fake LLM, a stub hub and a local relay + agent:
no API keys, no network. Five test types (function, loop, reload, JS–Python match, device), 292 tests, ~10 min per browser.
See `regression/README.md`; the case list is `regression/CHECKLIST.md`, bugs found are in
`regression/BUGS.md`.

```bash
pip install -r src/tests/regression/requirements.txt
python3 src/tests/regression/run.py                    # all, Chromium
python3 src/tests/regression/run.py --browser all      # + Firefox, WebKit
python3 src/tests/regression/run.py --src ../other/src --json other.json   # test another copy
python3 src/tests/regression/compare.py dev.json other.json                # what changed
```

## Unit tests

```bash
python3 -m pytest relay/tests/ -v          # agent.py: _build_payload, _maybe_compress_consciousness
```
`relay/tests/agent_loader.py` loads `agent.py` without starting it (needs `websockets`, `aiohttp`).

`experiments/cache_merged_block_test.py` is a caching experiment against the live Anthropic API (needs a
key), not a test.
