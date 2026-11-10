"""Page helpers: boot the real src/index.html against the fake server and drive it.

Rules that keep tests reliable:
  * Never time.sleep() in a test. Waiting goes through page.wait_for_timeout(), so Playwright keeps
    serving routed requests (the CDN cache) while we wait.
  * Never use page.wait_for_function(): the page's cancelAllAnimations() cancels every
    requestAnimationFrame, including the one Playwright polls with. `App.until()` polls instead.
  * Work on both the current code and the multi-being isolation fix: switching / new being may
    happen in place (dev) or by reloading the page (fix). Helpers wait for either.
"""
import json
import time

from .server import prompt_text

# Model per wire format. All go through the custom provider to /mock/<format>.
FORMAT_MODELS = {
    "gemini": "gemini-3.1-pro",      # gemini format, vision
    "openai": "glm-5.3",             # openai format, vision, stop words on, no adaptive thinking
    "anthropic": "claude-sonnet-4.6",  # anthropic format, not adaptive, no compress config
}

BASE_SETTINGS = {
    "provider": "custom",
    "model": FORMAT_MODELS["gemini"],
    "tokens": {"custom": "test-token"},
    "vision": "canvas",        # fastest capture; html2canvas only in vision tests
    "thinking": False,
    "preview": False,
    "relayUrl": "ws://127.0.0.1:9/ws",   # closed port: no device relay
    "hostDevice": "this browser",
    "devices": {},
}

# Boot finished: initRelay() runs near the end of boot() on every version of the app;
# the isolation fix also sets window._bootDone.
BOOTED = ("(typeof _relayGeneration !== 'undefined' && _relayGeneration > 0 "
          "&& window._bootDone !== false && !!currentBeingId)")
IDLE = "(!loopRunning && dispatchTimer === null)"


class PageGone(Exception):
    pass


class App:
    def __init__(self, page, server, context=None):
        self.page = page
        self.server = server
        self.context = context or page.context
        self.dialogs = []          # (type, message) of every dialog shown
        self.dialog_answer = True  # accept confirm() by default
        self.prompt_answer = None
        self.console = []
        self.page_errors = []
        self._hook(page)

    def _hook(self, page):
        page.on("dialog", self._on_dialog)
        page.on("console", lambda m: self.console.append((m.type, m.text)))
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))

    def _on_dialog(self, d):
        self.dialogs.append((d.type, d.message))
        try:
            if d.type == "prompt" and self.prompt_answer is not None:
                d.accept(self.prompt_answer)
            elif self.dialog_answer:
                d.accept()
            else:
                d.dismiss()
        except Exception:
            pass

    # ---- basics ----
    @property
    def url(self):
        return f"{self.server.base}/index.html"

    def js(self, expr, arg=None):
        """Evaluate a JS expression or function in the page."""
        return self.page.evaluate(expr, arg)

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def until(self, js, arg=None, timeout=30, interval=100, msg=None):
        """Poll a JS predicate until truthy; survives page navigation."""
        t0 = time.time()
        last_err = None
        while time.time() - t0 < timeout:
            try:
                v = self.page.evaluate(js, arg)
                if v:
                    return v
            except Exception as e:  # mid-navigation
                last_err = e
            self.page.wait_for_timeout(interval)
        raise AssertionError(msg or f"timed out after {timeout}s waiting for: {js}" + (f" ({last_err})" if last_err else ""))

    def until_py(self, fn, timeout=30, interval=100, msg="condition"):
        """Poll a Python predicate (e.g. on server state) while letting the page run."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = fn()
            if v:
                return v
            self.page.wait_for_timeout(interval)
        raise AssertionError(f"timed out after {timeout}s waiting for {msg}")

    # ---- boot / navigation ----
    def open(self, query="new", auto_infer=False):
        """Load the app. query: 'new' (fresh being), 'being=<id>', 'skills=a,b', '' (plain)."""
        self.page.goto(self.url + (("?" + query) if query else ""))
        self.wait_boot()
        if not auto_infer:
            self.stop_auto_infer()
        return self

    def wait_boot(self, timeout=40):
        self.until("() => " + BOOTED, timeout=timeout, msg="app boot")
        # newBeing() on dev runs async work after currentBeingId is set; wait for skills
        self.until("() => typeof window.hubInstall === 'function' && typeof window.identityInit === 'function'",
                   timeout=timeout, msg="default skills loaded")

    def reload(self, auto_infer=False):
        self.page.reload()
        self.wait_boot()
        if not auto_infer:
            self.stop_auto_infer()

    def stop_auto_infer(self):
        """Cancel the 3 s auto-start a fresh (empty) being schedules on boot."""
        self.js("() => { try { clearTimeout(autoInferTimer); } catch (e) {} }")

    @property
    def fixed(self):
        """True when the app under test has the multi-being isolation fix (one being per page)."""
        return bool(self.js("() => typeof leaveBeing === 'function'"))

    @property
    def being_id(self):
        return self.js("() => currentBeingId")

    def new_being(self, auto_infer=False):
        """New being via the app's own function; in place (dev) or by reload (fix)."""
        old = self.being_id
        self._call_may_navigate("() => newBeing()")
        self.until("old => typeof currentBeingId !== 'undefined' && currentBeingId && currentBeingId !== old", old,
                   timeout=40, msg="new being")
        self.wait_boot()
        if not auto_infer:
            self.stop_auto_infer()
        return self.being_id

    def switch(self, bid, auto_infer=False):
        """Switch being like the picker does (switchBeing), in place or by reload."""
        self._call_may_navigate("id => switchBeing(id)", bid)
        self.until("id => currentBeingId === id", bid, timeout=40, msg=f"switch to {bid}")
        self.wait_boot()
        self.until("() => !beingSelect.disabled", timeout=20)
        if not auto_infer:
            self.stop_auto_infer()

    def _call_may_navigate(self, fn, arg=None):
        try:
            self.page.evaluate(fn, arg)
        except Exception as e:
            s = str(e)
            if not any(k in s for k in ("Execution context was destroyed", "navigation", "Target closed",
                                         "context was destroyed", "frame was detached")):
                raise

    # ---- loop ----
    def idle(self):
        return self.js("() => " + IDLE)

    def wait_idle(self, timeout=40):
        self.until("() => " + IDLE, timeout=timeout, msg="loop idle")

    def send(self, text, wait=True, timeout=40):
        """Type a message and press Enter (sendMessage). With wait=True, wait for the loop to start
        and go idle again."""
        n = len(self.server.llm_requests())
        self.page.fill("#input", text)
        self.page.press("#input", "Enter")
        if wait:
            self.until_py(lambda: len(self.server.llm_requests()) > n, timeout=timeout, msg="LLM request")
            self.wait_idle(timeout)
        return self

    def go(self, wait=True):
        """Press the send button with an empty input (starts a cycle with no user text)."""
        n = len(self.server.llm_requests())
        self.page.fill("#input", "")
        self.page.click("#send-btn")
        if wait:
            self.until_py(lambda: len(self.server.llm_requests()) > n, msg="LLM request")
            self.wait_idle()

    def wait_requests(self, n, timeout=40):
        return self.until_py(lambda: len(self.server.llm_requests()) >= n, timeout=timeout, msg=f"{n} LLM requests")

    def settle(self):
        """Loop idle, then flush the debounced save."""
        self.wait_idle()
        self.js("() => saveBeing()")

    def fast_timers(self, mapping):
        """Shorten specific setTimeout delays in the page, e.g. {15000: 1500} for the error retry.
        Only exact delay values are mapped, so the app's logic is unchanged — just quicker."""
        self.js("""m => {
            if (!window.__origSetTimeout) window.__origSetTimeout = window.setTimeout.bind(window);
            window.__timerMap = Object.assign(window.__timerMap || {}, m);
            window.setTimeout = (fn, ms, ...a) => window.__origSetTimeout(fn, (window.__timerMap[ms] ?? ms), ...a);
        }""", {str(k): v for k, v in mapping.items()})

    def upload(self, selector, name, data, mime="application/octet-stream"):
        """Set a file input from in-memory bytes (works with sandboxed browsers that can't read /tmp)."""
        if isinstance(data, str):
            data = data.encode()
        self.page.set_input_files(selector, {"name": name, "mimeType": mime, "buffer": data})

    def download(self, click_selector, dest_dir):
        """Click something that downloads a file; returns the saved path."""
        import os
        with self.page.expect_download() as dl:
            self.page.click(click_selector)
        path = os.path.join(dest_dir, dl.value.suggested_filename)
        dl.value.save_as(path)
        return path

    def click_send(self):
        self.page.click("#send-btn")

    # ---- state ----
    def consciousness(self):
        return self.js("() => consciousness")

    def stored(self, bid=None):
        bid = bid or self.being_id
        rec = self.js("k => DB.get(k)", f"{bid}/consciousness.txt")
        return (rec or {}).get("value", "")

    def db_get(self, key):
        return self.js("k => DB.get(k)", key)

    def db_put(self, rec):
        return self.js("r => DB.put(r)", rec)

    def db_keys(self, prefix=""):
        return self.js("p => DB.list().then(a => a.map(r => r.id).filter(id => typeof id === 'string' && id.startsWith(p)))",
                       prefix)

    def beings(self):
        return self.js("() => DB.list().then(a => a.filter(r => typeof r.id === 'string' && !r.id.includes('/')).map(r => r.id))")

    def chat(self):
        """[(role, text)] of rendered chat rows."""
        return self.js("""() => [...document.querySelectorAll('#chat-box > div')].map(d => [
            d.classList.contains('msg-timestamp') ? 'timestamp' :
            (['user','ai','system'].find(c => d.classList.contains(c)) || d.className), d.innerText])""")

    def chat_text(self):
        return "\n".join(t for _, t in self.chat())

    def status(self):
        return self.js("() => document.getElementById('status').innerText")

    # ---- settings ----
    def settings(self):
        return self.js("() => settings")

    def set_settings(self, **kw):
        """Change the in-memory settings and persist them, like the settings panel does."""
        self.js("kw => { Object.assign(settings, kw); saveSettingsToStorage(settings); }", kw)

    def use_format(self, fmt, model=None, **extra):
        self.set_settings(provider="custom", model=model or FORMAT_MODELS[fmt],
                          apiEndpoint=f"{self.server.base}/mock/{fmt}", **extra)

    # ---- requests ----
    def last_body(self):
        r = self.server.last_llm()
        return r and r["body"]

    def last_prompt(self):
        r = self.server.last_llm()
        return prompt_text(r["body"]) if r else ""

    def payload(self):
        """buildPayload() result for the current state (no network)."""
        return self.js("() => buildPayload()")

    def dump(self):
        """Debug helper: a short description of the page state."""
        try:
            return json.dumps({"being": self.being_id, "idle": self.idle(), "status": self.status(),
                               "errors": self.page_errors[-3:]}, indent=1)
        except Exception as e:
            return f"<page unavailable: {e}>"
