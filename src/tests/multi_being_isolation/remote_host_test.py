#!/usr/bin/env python3
"""
Remote-host test: switching beings (which reloads the page) must still hand the new being to a
paired device that runs the loop. Covers bug 6 in README.md.

Pairs a throwaway Linux "device" with a headless browser on a DEPLOYED site, makes it the loop
host, switches beings both ways, runs one turn on the device, then unpairs:

    python3 src/tests/multi_being_isolation/remote_host_test.py                  # dev2.infero.net
    SITE=https://dev.infero.net python3 src/tests/multi_being_isolation/remote_host_test.py
    AGENT_PYTHON=/path/to/venv/bin/python python3 ...   # skip creating the agent's venv

- The device is relay/agent.py as served by the site's relay (/device-relay/update), run as a
  plain subprocess with INFERO_DIR and HOME in a temp dir. The pairing script isn't run: it
  would install a systemd user service and edit your shell rc. Without AGENT_PYTHON a venv is
  created in the temp dir (pip installs cryptography, websockets, python-socks, aiohttp).
- The LLM is the fake one from multi_being_isolation_test.py on 127.0.0.1, reached by the agent
  on this machine (the browser never calls it: the device runs the loop).
- The agent gets shell access through the relay while paired. The test unpairs at the end; if
  it dies mid-run, a pairing token may stay in the relay's tokens.json.
Needs: playwright + Chromium (CHROME, or /usr/bin/chromium-browser). Exit 0 = all pass.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("PORT", "8769")
import multi_being_isolation_test as t          # noqa: E402  (fake LLM + page helpers)
from playwright.sync_api import sync_playwright  # noqa: E402

SITE = os.environ.get("SITE", "https://dev2.infero.net").rstrip("/")
BASE = SITE + "/genesis/"
SETTINGS = {"provider": "custom", "apiEndpoint": f"{t.BASE}/mock", "tokens": {"custom": "x"},
            "model": "gemini-3.1-pro"}
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def wait(cond, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def agent_python(tmp):
    if os.environ.get("AGENT_PYTHON"):
        return os.environ["AGENT_PYTHON"]
    venv = os.path.join(tmp, "venv")
    print("creating the agent's venv...", flush=True)
    subprocess.run([sys.executable, "-m", "venv", venv], check=True)
    subprocess.run([f"{venv}/bin/pip", "install", "-q", "cryptography", "websockets", "python-socks", "aiohttp"], check=True)
    return f"{venv}/bin/python"


def main():
    tmp = tempfile.mkdtemp(prefix="infero-remote-host-")
    infero, home = os.path.join(tmp, "infero"), os.path.join(tmp, "home")
    os.makedirs(infero); os.makedirs(home)
    py = agent_python(tmp)
    log_path = os.path.join(infero, "agent.log")
    agent_log = lambda: open(log_path, errors="replace").read() if os.path.exists(log_path) else ""
    srv = t.start_server()
    agent = None
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/chromium-browser"), headless=True)
            ctx = br.new_context()
            ctx.add_init_script("if (!localStorage.getItem('genesis_settings')) "
                                f"localStorage.setItem('genesis_settings', {json.dumps(json.dumps(SETTINGS))});")
            pg = ctx.new_page()
            pg.on("dialog", lambda d: d.accept())
            pg.goto(BASE + "?new"); t.wait_boot(pg)
            A = pg.evaluate("() => currentBeingId")
            t.new_being(pg); B = pg.evaluate("() => currentBeingId")
            print(f"beings: A={A[:12]}…  B={B[:12]}… (tab on B)", flush=True)

            # Pair: browser asks for a code; set the agent up the way the pairing script would
            pg.evaluate("() => startPairing()")
            wait(lambda: pg.evaluate("() => window._pairCode"), 20)
            code = pg.evaluate("() => window._pairCode")
            script = urllib.request.urlopen(f"{SITE}/device-relay/pair/{code}").read().decode()
            v = {}
            for k, val in re.findall(r'^(RELAY_WS|RELAY_HTTP|INSTANCE_ID|TOKEN|BROWSER_PUB|CLIENT_NAME)="(.*)"$', script, re.M):
                if not val.startswith("$"):
                    v.setdefault(k, val)   # skip the CLI heredoc's "$VAR" copies
            json.dump([{"instance_id": v["INSTANCE_ID"], "token": v["TOKEN"], "browser_pub": v["BROWSER_PUB"],
                        "relay_ws": v["RELAY_WS"], "relay_http": v["RELAY_HTTP"], "client_name": v["CLIENT_NAME"],
                        "first_added": "remote_host_test"}], open(os.path.join(infero, "instances.json"), "w"))
            urllib.request.urlretrieve(f"{v['RELAY_HTTP']}/update", os.path.join(infero, "agent.py"))
            agent = subprocess.Popen([py, "-u", os.path.join(infero, "agent.py")], cwd=infero,
                                     env=dict(os.environ, INFERO_DIR=infero, HOME=home),
                                     stdout=open(log_path, "a"), stderr=subprocess.STDOUT)
            paired = wait(lambda: any(d.get("keyB64") and d.get("online") for d in
                                      pg.evaluate("() => Object.values(settings.devices || {})")), 60)
            dev = next(iter(pg.evaluate("() => settings.devices") or {}), None)
            check("device pairs and comes online", paired, dev or "")
            if not paired:
                return 1

            pg.evaluate("n => setHostDevice(n)", dev)
            check("making the device host hands it the current being (B)",
                  wait(lambda: f"Saved being {B}" in agent_log(), 30))

            mark = len(agent_log())
            t.switch(pg, A)
            check("host stays the device after switching", pg.evaluate("() => settings.hostDevice") == dev)
            check("switching to A (page reload) hands A to the device",
                  wait(lambda: f"Saved being {A}" in agent_log()[mark:], 45))

            pg.evaluate("() => trigger('TASK-REMOTE1', 0)")
            check("a turn on A runs on the device and its reply shows in A",
                  wait(lambda: "ANSWER-REMOTE1" in pg.evaluate("() => consciousness"), 60))
            check("the reply isn't in B", "ANSWER-REMOTE1" not in t.stored(pg, B))

            mark = len(agent_log())
            t.switch(pg, B)
            check("switching back to B hands B to the device",
                  wait(lambda: f"Saved being {B}" in agent_log()[mark:], 45))

            pg2 = ctx.new_page(); pg2.on("dialog", lambda d: d.accept())
            pg2.goto(BASE + "?being=" + A); t.wait_boot(pg2); time.sleep(3)
            check("a second tab's relay traffic doesn't drop the paired device",
                  dev in pg.evaluate("() => Object.keys(JSON.parse(localStorage.genesis_settings).devices || {})"))
            pg2.close()

            pg.evaluate("n => removeDevice(n)", dev); time.sleep(2)
            check("unpairing removes the device", dev not in pg.evaluate("() => Object.keys(settings.devices || {})"))
            br.close()
    finally:
        if agent:
            agent.terminate(); agent.wait(10)
        srv.shutdown()
    failed = checks.count(False)
    if failed:
        print(f"\n{len(checks) - failed}/{len(checks)} checks passed (kept for debugging: {tmp})")
        return 1
    shutil.rmtree(tmp, ignore_errors=True)   # holds the (now revoked) pairing token
    print(f"\n{len(checks)}/{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
