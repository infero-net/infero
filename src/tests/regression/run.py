#!/usr/bin/env python3
"""Run the regression suite.

  python3 src/tests/regression/run.py                         # all offline tests, Chromium
  python3 src/tests/regression/run.py --type fn,loop          # only some test types
  python3 src/tests/regression/run.py --browser all           # Chromium, Firefox and WebKit in turn
  python3 src/tests/regression/run.py --src ../other/src --json other.json
  python3 src/tests/regression/run.py -- -k compress -x       # anything after -- goes to pytest

Test types: fn (function), loop, reload, match (JS–Python), device (local relay + agent.py).
Exit code is non-zero if any test failed (expected failures of known bugs don't count).
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = ("fn", "loop", "reload", "match", "device")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", help="app directory to test (default: this repo's src/)")
    ap.add_argument("--browser", default="chromium", help="chromium, firefox, webkit or all")
    ap.add_argument("--type", default="", help="comma-separated test types (default: all)")
    ap.add_argument("--json", help="result file; with --browser all, the browser name is added")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    browsers = ["chromium", "firefox", "webkit"] if a.browser == "all" else [a.browser]
    types = [t for t in a.type.split(",") if t]
    bad = [t for t in types if t not in TYPES]
    if bad:
        ap.error(f"unknown type(s): {bad}; choose from {TYPES}")
    extra = [x for x in a.pytest_args if x != "--"]
    worst = 0
    for b in browsers:
        cmd = [sys.executable, "-m", "pytest", HERE, "--browser", b, "-q"]
        if a.src:
            cmd += ["--src", os.path.abspath(a.src)]
        if types:
            cmd += ["-m", " or ".join(types)]
        if a.headed:
            cmd += ["--headed"]
        if a.json:
            out = a.json
            if len(browsers) > 1:
                root, ext = os.path.splitext(a.json)
                out = f"{root}.{b}{ext or '.json'}"
            cmd += ["--json", os.path.abspath(out)]
        print(f"\n=== {b} ===\n$ {' '.join(cmd + extra)}", flush=True)
        rc = subprocess.call(cmd + extra, cwd=HERE)
        worst = max(worst, rc)
    sys.exit(worst)


if __name__ == "__main__":
    main()
