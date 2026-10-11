#!/usr/bin/env python3
"""
Multi-being isolation tests: do beings that share one browser stay separate?

Boots the real src/index.html in headless Chromium against a local fake LLM
(Gemini-format SSE) and checks the five problems described in
README.md. Each check asserts the CORRECT (isolated) behaviour.
Issue 5's checks are known failures (not fixed; see README.md) and don't count.

    python3 src/tests/multi_being_isolation/multi_being_isolation_test.py
    CHROME=/usr/bin/chromium-browser python3 src/tests/multi_being_isolation/multi_being_isolation_test.py
    BROWSER=firefox python3 src/tests/multi_being_isolation/multi_being_isolation_test.py   # or webkit
    python3 src/tests/multi_being_isolation/multi_being_isolation_test.py --json /tmp/isolation.json

Needs: pip install playwright, and a Chromium/Chrome (Playwright's bundled one,
or set CHROME). Network: loads marked/html2canvas from jsDelivr and bip39 from
esm.sh; every other external request (hub, relay) is blocked.
Exit code 0 = no failures other than known ones, 1 = any other failure.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from playwright.sync_api import sync_playwright

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = int(os.environ.get("PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
ALLOWED_HOSTS = ("cdn.jsdelivr.net", "cdn.bootcdn.net", "esm.sh")
SLOW_REPLY_S = 6.0   # total stream time for a TASK-SLOW* prompt
FAST_REPLY_S = 0.3
SETTINGS = {"provider": "custom", "apiEndpoint": f"{BASE}/mock", "tokens": {"custom": "x"},
            "model": "gemini-3.1-pro", "relayUrl": "ws://127.0.0.1:9/ws",
            "hostDevice": "this browser", "devices": {}}


# --- Fake LLM + static server -------------------------------------------------
# Replies to the newest "TASK-<x>" marker in the request with "ANSWER-<x> ...",
# so the test can tell which prompt each saved answer belongs to.
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        if not self.path.startswith("/mock"):
            self.send_response(404); self.end_headers(); return
        marks = [(m.start(), m.group(1)) for m in re.finditer(r"TASK-([A-Za-z0-9_]+)", body)]
        mark = max(marks)[1] if marks else "NONE"
        delay = SLOW_REPLY_S if mark.startswith("SLOW") else FAST_REPLY_S
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        parts = [f"ANSWER-{mark} part1 ", "part2 ", "done.\n\n/call_for_human"]
        try:
            for p in parts:
                time.sleep(delay / len(parts))
                chunk = {"candidates": [{"content": {"parts": [{"text": p}]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b'data: {"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page aborted the stream (e.g. switching beings mid-reply)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=SRC))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- Page helpers -------------------------------------------------------------
# Every other host (hub, relay, ...) fails DNS inside Chromium. Blocking in the browser rather
# than with ctx.route() matters: the sync API only serves intercepted requests while Python is
# inside a Playwright call, so routed requests would stall the page during time.sleep().
CHROME_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, "
               + ", ".join(f"EXCLUDE {h}" for h in ALLOWED_HOSTS)]
# Playwright turns the back/forward cache off; real Chrome has it on, and a page kept there
# still holds its being's Web Lock (switching A -> B -> A in one tab used to be refused).
CHROME_KEEP = dict(ignore_default_args=["--disable-back-forward-cache"])


def wait_idle(pg, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pg.evaluate("() => !loopRunning && dispatchTimer === null"):
            return
        time.sleep(0.2)
    raise RuntimeError("loop never went idle")


def settle(pg):
    """Wait for the loop to stop, then flush the debounced save."""
    wait_idle(pg)
    pg.evaluate("() => saveBeing()")
    time.sleep(0.3)


def wait_until(pg, js, arg=None, timeout=60):
    """Poll a JS predicate, surviving page reloads (an evaluate during navigation throws).
    Not wait_for_function: the page's cancelAllAnimations() cancels every
    requestAnimationFrame, including the one Playwright's default polling uses."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pg.evaluate(js, arg):
                return
        except Exception:
            pass   # mid-navigation
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {js}")


BOOTED = "(window._bootDone !== false && !!currentBeingId)"   # code without the flag: being loaded


def wait_boot(pg):
    wait_until(pg, "() => " + BOOTED)


def switch(pg, bid, tries=3):
    """Switch through the being picker, like a user. Works whether or not switching reloads.
    A refused pick is retried, as a user would; check 8 tests that the first pick works."""
    done = "id => currentBeingId === id && " + BOOTED + " && !beingSelect.disabled"
    for _ in range(tries):
        if pg.evaluate("() => currentBeingId") == bid:
            break
        pg.select_option("#being-select", bid)
        try:
            wait_until(pg, done, bid, timeout=20)
            return
        except RuntimeError:
            pass
    wait_until(pg, done, bid)


def new_being(pg):
    """Click the New Being button and wait for the fresh being."""
    old = pg.evaluate("() => currentBeingId")
    pg.click('button[title="New Being"]')
    wait_until(pg, "id => currentBeingId !== id && " + BOOTED, old)


def stored(pg, bid):
    r = pg.evaluate("k => DB.get(k)", f"{bid}/consciousness.txt")
    return (r or {}).get("value", "")


def holders(pg, needle, ids):
    return [name for name, bid in ids.items() if needle in stored(pg, bid)]


def open_page(ctx, url):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: print("    [pageerror]", str(e)[:150]))
    pg.goto(url)
    wait_boot(pg)
    return pg


def launch(p):
    name = os.environ.get("BROWSER", "chromium")
    if name != "chromium":
        # No host blocking needed: with these settings the hub and relay point at closed local ports
        return getattr(p, name).launch(headless=True)
    exe = os.environ.get("CHROME")
    if exe:
        return p.chromium.launch(executable_path=exe, headless=True, args=CHROME_ARGS, **CHROME_KEEP)
    try:
        return p.chromium.launch(headless=True, args=CHROME_ARGS, **CHROME_KEEP)
    except Exception:
        for exe in ("/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"):
            if os.path.exists(exe):
                return p.chromium.launch(executable_path=exe, headless=True, args=CHROME_ARGS, **CHROME_KEEP)
        raise


# --- Tests --------------------------------------------------------------------
checks = []
# Issue 5 needs a per-being origin or per-being encryption (see README.md);
# its checks are expected to fail and don't affect the exit code. A pass is reported as UNEXPECTED.
KNOWN = {5}


def check(num, name, ok, detail):
    known = num in KNOWN
    checks.append({"issue": num, "check": name, "pass": bool(ok), "known": known, "detail": detail})
    label = ("UNEXPECTED PASS" if known else "PASS") if ok else ("KNOWN" if known else "FAIL")
    print(f"  [{label}] {name} — {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write results to this file")
    args = ap.parse_args()

    srv = start_server()
    try:
        with sync_playwright() as p:
            br = launch(p)
            ctx = br.new_context()
            ctx.add_init_script("if (!localStorage.getItem('genesis_settings')) "
                                f"localStorage.setItem('genesis_settings', {json.dumps(json.dumps(SETTINGS))});")

            # Setup: two fresh beings, A and B (each runs one auto-infer turn).
            pg = open_page(ctx, f"{BASE}/index.html?new")
            time.sleep(4); settle(pg)
            A = pg.evaluate("() => currentBeingId")
            new_being(pg)
            time.sleep(4); settle(pg)
            B = pg.evaluate("() => currentBeingId")
            ids = {"A": A, "B": B}
            print(f"beings: A={A[:16]}…  B={B[:16]}…")
            # Labels in the being picker before anyone has typed to either being (checked in [9])
            labels = dict(pg.evaluate("() => [...beingSelect.options].map(o => [o.value, o.textContent])"))
            labels = {"A": labels.get(A), "B": labels.get(B)}

            print("\n[1] Switching mid-loop writes A's turn into B")
            switch(pg, A); settle(pg)
            pg.evaluate("() => trigger('TASK-SLOW1', 0)")
            time.sleep(4)   # past the 3 s save debounce, mid-stream
            running = pg.evaluate("() => loopRunning")
            switch(pg, B); settle(pg)
            # Switching pauses A: its in-flight reply is cancelled, its prompt stays in A.
            reply = holders(pg, "ANSWER-SLOW1", ids)
            prompt = holders(pg, "TASK-SLOW1", ids)
            check(1, "nothing of A's turn lands in B; A keeps its prompt",
                  "B" not in reply and prompt == ["A"],
                  f"loop running at switch={running}; reply saved in {reply or 'none'}, prompt in {prompt or 'none'}")

            print("\n[2] Switching within ~3 s of a write loses it")
            switch(pg, A); settle(pg)
            pg.evaluate("() => trigger('TASK-FAST2', 0)")
            wait_idle(pg)
            pending = pg.evaluate("() => _saveBeingTimer !== null")
            switch(pg, B)
            time.sleep(4)
            switch(pg, A)
            where = holders(pg, "ANSWER-FAST2", ids)
            check(2, "A's last reply survives an immediate switch", where == ["A"],
                  f"save pending at switch={pending}; reply saved in {where or 'none'}")

            print("\n[3] A's code keeps running after the switch")
            switch(pg, A); settle(pg)
            pg.evaluate("() => (0, eval)(\"window.__leak = setTimeout(() => trigger('TASK-LEAK3', 0), 3000)\")")
            switch(pg, B)
            time.sleep(5); settle(pg)
            where = holders(pg, "TASK-LEAK3", ids)
            check(3, "A's timer does not wake B", "B" not in where,
                  f"prompt from A's timer landed in {where or 'none'}")

            print("\n[8] Switching back to a being this tab showed before")
            # Bug: the page left behind sat in the back/forward cache still holding A's lock, so the
            # first pick was refused as "open in another tab" (Chromium here runs with that cache on).
            switch(pg, A); settle(pg); switch(pg, B); settle(pg)
            pg.select_option("#being-select", A)                       # one pick, no retry
            try:
                wait_until(pg, "id => currentBeingId === id && " + BOOTED, A, timeout=15)
            except RuntimeError:
                pass
            on = "A" if pg.evaluate("() => currentBeingId") == A else "B"
            note = pg.evaluate("() => [...document.querySelectorAll('#chat-box .msg')].some(m => /open in another tab/.test(m.textContent))")
            check(8, "one pick switches back to a being this tab showed before", on == "A" and not note,
                  f"after one pick of A: on {on}; 'open in another tab' notice={note}")
            if on != "A":
                switch(pg, A)

            print("\n[4] Multiple tabs")
            switch(pg, A); settle(pg)
            pg2 = open_page(ctx, f"{BASE}/index.html?being={B}")
            time.sleep(2)
            opened = "A" if pg2.evaluate("() => currentBeingId") == A else "B"
            check("4a", "a new tab opened with ?being=B opens B", opened == "B",
                  f"new tab opened {opened} (tab 1 is on A)")
            pg2.reload(); wait_boot(pg2)
            again = "A" if pg2.evaluate("() => currentBeingId") == A else "B" if pg2.evaluate("() => currentBeingId") == B else "a new being"
            check("4a", "reloading a tab reopens the same being", again == "B",
                  f"after reload tab 2 shows {again}")
            # Back-to-back saves with different changes (no time for the storage event in between)
            pg.evaluate("() => { settings.devices = {TestDevice: {online: false}}; saveSettingsToStorage(settings); }")
            pg2.evaluate("() => { settings.vision = 'full'; saveSettingsToStorage(settings); }")
            stored_now = pg.evaluate("() => JSON.parse(localStorage.genesis_settings)")
            devices = list((stored_now.get("devices") or {}).keys())
            check("4b", "a device paired in tab 1 survives a settings save in tab 2", "TestDevice" in devices,
                  f"saved devices after tab 2's save: {devices}")
            check("4b", "both tabs' changes are kept", "TestDevice" in devices and stored_now.get("vision") == "full",
                  f"devices={devices}, vision={stored_now.get('vision')}")
            pg.evaluate("() => { delete settings.devices.TestDevice; saveSettingsToStorage(settings); }")
            pg2.evaluate("() => saveSettingsToStorage(settings)")
            devices = pg.evaluate("() => Object.keys(JSON.parse(localStorage.genesis_settings).devices || {})")
            check("4b", "a device removed in tab 1 stays removed after tab 2 saves", "TestDevice" not in devices,
                  f"saved devices: {devices}")
            # Tab 1 has A open; tab 2 (on B) tries to switch to it through the picker.
            pg2.select_option("#being-select", A)
            time.sleep(3)
            on = "A" if pg2.evaluate("() => currentBeingId") == A else "B"
            note = pg2.evaluate("() => [...document.querySelectorAll('#chat-box .msg')].some(m => /open in another tab/.test(m.textContent))")
            check("4c", "a tab can't switch to a being open in another tab", on == "B",
                  f"tab 2 is on {on} after picking A; notice shown={note}")
            pg3 = open_page(ctx, f"{BASE}/index.html?being={A}")
            got = pg3.evaluate("() => currentBeingId")
            label = "A" if got == A else "B" if got == B else "a new being"
            check("4d", "a new tab asking for a being open elsewhere opens another one", got != A,
                  f"new tab opened {label}")
            pg3.close()
            pg2.close()
            time.sleep(0.5)   # let the closed tab release B's lock (WebKit takes ~0.1 s)

            print("\n[5] No access boundary between beings")
            switch(pg, B); settle(pg)
            keys = pg.evaluate("async a => (await DB.list()).map(r => r.id).filter(k => k.startsWith(a + '/'))", A)
            check(5, "B cannot read A's records", not keys,
                  f"B read {len(keys)} of A's records, incl. identity={any(k.endswith('/identity') for k in keys)}")
            wrote = pg.evaluate("async a => { await DB.put({id: a + '/probe', value: 'by-B'});"
                                " return (await DB.get(a + '/probe'))?.value === 'by-B'; }", A)
            check(5, "B cannot write A's records", not wrote, f"B wrote {A[:8]}…/probe={wrote}")

            print("\n[9] Labels of beings that haven't named themselves")
            # Bug: the title was the first non-system paragraph of the consciousness. A fresh being's
            # auto first turn ends with '/call_for_human', so every unnamed being was labelled
            # '/call_for_human...', and the human's messages never changed it.
            check(9, "two fresh beings get different labels in the being picker", labels["A"] != labels["B"],
                  f"A={labels['A']!r}, B={labels['B']!r}")
            label_of = lambda bid: pg.evaluate("async id => { await loadBeingsList();"
                                               " return [...beingSelect.options].find(o => o.value === id)?.textContent; }", bid)
            new_being(pg); time.sleep(4); settle(pg)
            C = pg.evaluate("() => currentBeingId")
            before = label_of(C)
            pg.evaluate("() => trigger('TASK-LABEL9 hello', 0)"); time.sleep(1); settle(pg)
            after = label_of(C)
            check(9, "the human's first message becomes the label", after == "TASK-LABEL9 hel...",
                  f"before typing={before!r}, after={after!r}")
            legacy = "0123456789abcdef" * 4
            pg.evaluate("id => DB.put({id, name: '', title: '/call_for_human...', updatedAt: 1})", legacy)
            old_label = label_of(legacy)
            pg.evaluate("id => DB.del(id)", legacy)
            check(9, "an old '/call_for_human...' title shows as the short id", old_label == "b_01234567",
                  f"label={old_label!r}")
            br.close()
    finally:
        srv.shutdown()

    passed = sum(c["pass"] for c in checks)
    known = sum(c["known"] and not c["pass"] for c in checks)
    failed = sum(not c["pass"] and not c["known"] for c in checks)
    print(f"\n{passed}/{len(checks)} checks passed, {known} known failure(s), {failed} failure(s)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(checks, f, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
