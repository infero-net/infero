#!/usr/bin/env python3
"""Compare two regression runs, e.g. dev against a branch with a fix.

  python3 compare.py dev.json fix.json

Prints only the tests whose outcome changed, grouped by what the change means:
  REGRESSION   passed on the first run, fails on the second
  FIXED        failed or was a known bug (xfailed) on the first run, passes on the second
  NEW / GONE   test exists in only one run
Exit code 1 if there is any regression.
"""
import json
import sys

GOOD = {"passed", "xfailed", "skipped"}   # xfailed = a known bug still behaves as recorded
KNOWN_BUG_PASSED = "is marked as a known bug but the test passed"   # harness/bugs.py


def load(path):
    with open(path) as f:
        data = json.load(f)
    return data, {r["test"]: r for r in data["results"]}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    da, a = load(sys.argv[1])
    db, b = load(sys.argv[2])
    print(f"A: {da['src']} ({da['browser']}, {len(a)} tests)\nB: {db['src']} ({db['browser']}, {len(b)} tests)\n")
    groups = {"REGRESSION": [], "FIXED": [], "CHANGED": [], "NEW": [], "GONE": []}
    for t in sorted(set(a) | set(b)):
        ra, rb = a.get(t), b.get(t)
        if not ra:
            groups["NEW"].append((t, "-", rb["outcome"], rb.get("message", "")))
            continue
        if not rb:
            groups["GONE"].append((t, ra["outcome"], "-", ""))
            continue
        oa, ob = ra["outcome"], rb["outcome"]
        if oa == ob:
            continue
        if oa == "xfailed" and ob == "failed" and KNOWN_BUG_PASSED in rb.get("message", ""):
            groups["FIXED"].append((t, oa, "passed (known bug no longer reproduces)", ""))
        elif oa in GOOD and ob == "failed":
            groups["REGRESSION"].append((t, oa, ob, rb.get("message", "")))
        elif oa in ("failed", "xfailed") and ob in ("passed", "xpassed"):
            groups["FIXED"].append((t, oa, ob, ""))
        else:
            groups["CHANGED"].append((t, oa, ob, rb.get("message", "")))
    any_change = False
    for name, rows in groups.items():
        if not rows:
            continue
        any_change = True
        print(f"{name} ({len(rows)})")
        for t, oa, ob, msg in rows:
            print(f"  {t}\n      {oa} -> {ob}" + (f"   {msg}" if msg else ""))
        print()
    if not any_change:
        print("No differences.")
    sys.exit(1 if groups["REGRESSION"] else 0)


if __name__ == "__main__":
    main()
