"""Known bugs: tests assert the CORRECT behaviour; where the app under test still has a known bug,
the test is reported as an expected failure (xfailed) with the bug id from BUGS.md.

  with known_bug("BUG-03"):
      assert ...                      # xfailed while the bug exists, passes once it's fixed

  with known_bug("ISO-1", unless=app.fixed):
      assert ...                      # bug fixed by the multi-being isolation PR

If the assertions pass while the bug is still marked, the test FAILS with "fixed?" so the marker
gets removed (same as pytest's strict xfail).
"""
from contextlib import contextmanager

import pytest


@contextmanager
def known_bug(bug_id, unless=False, reason="", intermittent=False):
    """intermittent=True: the bug depends on timing, so a pass is accepted (not reported as "fixed?")."""
    if unless:
        yield
        return
    try:
        yield
    except AssertionError as e:
        pytest.xfail(f"{bug_id} (known bug, see BUGS.md){': ' + reason if reason else ''} — {str(e).splitlines()[0][:200]}")
    else:
        if intermittent:
            return
        pytest.fail(f"{bug_id} is marked as a known bug but the test passed — fixed? Update BUGS.md and the test.")
