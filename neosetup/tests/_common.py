"""Shared bits of the neosetup tests: fixtures, check(), the summary line."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)

from golden_flows import GPS, GPS_MACHINE, SUP, SUP_RAR, SUP_KFG, truth  # noqa: E402,F401
from neosetup import Project, ToolError  # noqa: E402,F401

WORK = tempfile.mkdtemp(prefix="neosetup-test-")
fails = []


def check(label, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + label + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


def need(*paths):
    missing = [p for p in paths if not p or not os.path.exists(p)]
    if missing:
        print("SKIP: fixture(s) not found: %s" % missing)
        finish()
    return True


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:
        return e
    return None


def finish():
    print()
    print("FAILED:", fails if fails else "none")
    sys.exit(1 if fails else 0)
