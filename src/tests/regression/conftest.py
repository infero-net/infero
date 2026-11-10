"""Fixtures for the regression suite.

Options (see README.md):
  --src DIR        the app to test (default: this repo's src/)
  --browser NAME   chromium (default), firefox or webkit
  --headed         show the browser
  --json FILE      write one result per test (used by compare.py)
"""
import json
import os
import re
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import App, FakeServer, BASE_SETTINGS  # noqa: E402
from harness.cdn import CdnCache  # noqa: E402

REPO_SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
TYPES = ("fn", "loop", "reload", "match", "device")

# Playwright turns the back/forward cache off; real Chrome has it on (it matters for being locks).
CHROME_KEEP = dict(ignore_default_args=["--disable-back-forward-cache"])
CHROME_PATHS = ("/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome",
                "/snap/bin/chromium")


def pytest_addoption(parser):
    g = parser.getgroup("regression")
    g.addoption("--src", default=os.environ.get("REGRESSION_SRC", REPO_SRC), help="app directory to test")
    g.addoption("--browser", default=os.environ.get("BROWSER", "chromium"),
                choices=("chromium", "firefox", "webkit"))
    g.addoption("--headed", action="store_true")
    g.addoption("--json", default=None, help="write per-test results to this JSON file")


def pytest_configure(config):
    for t in TYPES:
        config.addinivalue_line("markers", f"{t}: {t} test (see CHECKLIST.md)")
    config.addinivalue_line("markers", "case(*ids): checklist case ids this test covers")
    config.addinivalue_line("markers", "fixed_only: only meaningful with the multi-being isolation fix")
    config._regression_results = []


def pytest_collection_modifyitems(config, items):
    for item in items:
        base = os.path.basename(str(item.fspath))
        for t in TYPES:
            if base.startswith(f"test_{t}_"):
                item.add_marker(getattr(pytest.mark, t))


# ---- result file -------------------------------------------------------------
def pytest_runtest_logreport(report):
    cfg = _CONFIG.get("config")
    if cfg is None:
        return
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        outcome = report.outcome
        if hasattr(report, "wasxfail"):
            outcome = "xfailed" if report.skipped else "xpassed"
        cfg._regression_results.append({
            "test": report.nodeid, "outcome": outcome, "cases": dict(report.user_properties).get("cases", []),
            "duration": round(report.duration, 2),
            "message": (_failure_message(report) if report.failed else getattr(report, "wasxfail", "")),
        })


_CONFIG = {}


def _failure_message(report):
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    if crash is not None:
        return crash.message[:400]
    return str(report.longrepr).splitlines()[-1][:400] if report.longrepr else ""


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    _CONFIG["config"] = session.config


def pytest_sessionfinish(session, exitstatus):
    cfg = session.config
    path = cfg.getoption("--json")
    if path:
        with open(path, "w") as f:
            json.dump({"src": os.path.abspath(cfg.getoption("--src")), "browser": cfg.getoption("--browser"),
                       "results": cfg._regression_results}, f, indent=1)


@pytest.fixture(autouse=True)
def _record_cases(request):
    m = request.node.get_closest_marker("case")
    if m:
        request.node.user_properties.append(("cases", list(m.args)))
    yield


# ---- server / browser ----------------------------------------------------------
@pytest.fixture(scope="session")
def src_dir(pytestconfig):
    d = os.path.abspath(pytestconfig.getoption("--src"))
    if not os.path.exists(os.path.join(d, "index.html")):
        pytest.exit(f"--src {d} has no index.html", returncode=4)
    return d


@pytest.fixture(scope="session")
def server(src_dir):
    s = FakeServer(src_dir).start()
    yield s
    s.stop()


@pytest.fixture(scope="session")
def cdn():
    return CdnCache()


@pytest.fixture(scope="session")
def pw():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    yield p
    p.stop()


# Downloads go to a non-hidden folder in the repo: a snap-packaged Chromium can't write to /tmp.
WORK_DIR = os.path.join(HERE, "_work")


@pytest.fixture(scope="session")
def browser(pw, pytestconfig):
    name = pytestconfig.getoption("--browser")
    headless = not pytestconfig.getoption("--headed")
    os.makedirs(os.path.join(WORK_DIR, "downloads"), exist_ok=True)
    common = dict(headless=headless, downloads_path=os.path.join(WORK_DIR, "downloads"))
    if name != "chromium":
        b = getattr(pw, name).launch(**common)
    else:
        exe = os.environ.get("CHROME")
        try:
            b = pw.chromium.launch(executable_path=exe, **common, **CHROME_KEEP) if exe else \
                pw.chromium.launch(**common, **CHROME_KEEP)
        except Exception:
            exe = next((p for p in CHROME_PATHS if shutil.which(p) or os.path.exists(p)), None)
            if not exe:
                raise
            b = pw.chromium.launch(executable_path=exe, **common, **CHROME_KEEP)
    yield b
    b.close()


def init_script(server, settings=None):
    s = dict(BASE_SETTINGS, apiEndpoint=f"{server.base}/mock/gemini", **(settings or {}))
    return ("(() => { try {"
            f"if (!localStorage.getItem('genesis_settings')) localStorage.setItem('genesis_settings', {json.dumps(json.dumps(s))});"
            f"localStorage.setItem('HUB_BASE', {json.dumps(server.base + '/hub')});"
            "} catch (e) {} })();")


def make_context(browser, server, cdn, settings=None, **kw):
    opts = dict(viewport={"width": 1280, "height": 800}, accept_downloads=True)
    opts.update(kw)
    ctx = browser.new_context(**opts)
    cdn.install(ctx)
    ctx.add_init_script(init_script(server, settings))
    return ctx


@pytest.fixture
def context(browser, server, cdn):
    server.reset()
    ctx = make_context(browser, server, cdn)
    yield ctx
    ctx.close()


@pytest.fixture
def new_app(context, server):
    """A factory: new_app() -> App on a fresh page in this test's context (not opened yet)."""
    apps = []

    def make():
        a = App(context.new_page(), server, context)
        apps.append(a)
        return a
    yield make


@pytest.fixture
def work_dir(request):
    """A per-test folder the browser can read and write (see WORK_DIR)."""
    d = os.path.join(WORK_DIR, re.sub(r"[^\w.-]+", "_", request.node.name)[:80])
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return d


@pytest.fixture
def app_in_context(browser, server, cdn):
    """A factory for apps in their own browser context: app_in_context(settings={...}, device_scale_factor=2).
    Not opened yet."""
    ctxs = []

    def make(settings=None, **kw):
        ctx = make_context(browser, server, cdn, settings, **kw)
        ctxs.append(ctx)
        return App(ctx.new_page(), server, ctx)
    server.reset()
    yield make
    for c in ctxs:
        c.close()


@pytest.fixture
def app(new_app):
    """The app, booted on a fresh being, auto-start cancelled, idle."""
    a = new_app()
    a.open("new")
    return a


@pytest.fixture(scope="module")
def module_app(browser, server, cdn):
    """One booted app shared by a module of function tests (fast; tests must not rely on order)."""
    server.reset()
    ctx = make_context(browser, server, cdn)
    a = App(ctx.new_page(), server, ctx)
    a.open("new")
    yield a
    ctx.close()


@pytest.fixture
def fnapp(module_app, server):
    """module_app with the fake server's script/recordings cleared and default settings restored."""
    server.reset()
    module_app.js("""base => {
        Object.assign(settings, base);
        saveSettingsToStorage(settings);
        window._overloadModel = null;
        cacheName = null; cachedLength = 0; _cachedStableHead = null;
    }""", dict(BASE_SETTINGS, apiEndpoint=f"{server.base}/mock/gemini"))
    module_app.wait_idle()
    return module_app


# ---- device tests: local relay + agent --------------------------------------------------------------
@pytest.fixture(scope="session")
def relay():
    from harness.devices import LocalRelay
    r = LocalRelay().start()
    yield r
    r.stop()


class Paired:
    """A page paired with a running local agent."""

    def __init__(self, app, agent, relay):
        self.app, self.agent, self.relay = app, agent, relay

    @property
    def name(self):
        return self.agent.name


def pair_page(app, relay, agent=None):
    """Pair this page with a (new) local agent through the page's own pairing flow."""
    from harness.devices import LocalAgent
    app.until("() => _relayConnected === true", timeout=20, msg="page connected to the relay")
    relay.before_pairing()
    app.js("() => startPairing()")
    cmd = app.until("() => { const t = document.getElementById('pair-cmd-text').textContent; "
                    "return /\\/pair\\/[A-Z0-9]{4}/.test(t) && t; }", timeout=15, msg="pairing code")
    code = re.search(r"/pair/([A-Z0-9]{4})", cmd).group(1)
    agent = agent or LocalAgent(relay)
    agent.pair(code)
    agent.start()
    app.until("n => settings.devices?.[n]?.online === true && !!settings.devices[n].keyB64", agent.name,
              timeout=30, msg=f"device {agent.name} paired and online")
    return agent


@pytest.fixture
def paired(app_in_context, relay):
    app = app_in_context(settings={"relayHost": relay.base})
    app.open("new")
    agents = []
    agent = pair_page(app, relay)
    agents.append(agent)
    p = Paired(app, agent, relay)
    p.extra_agents = agents
    yield p
    for a in agents:
        a.cleanup()
