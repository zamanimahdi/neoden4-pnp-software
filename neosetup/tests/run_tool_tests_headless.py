"""Runs the KFG tool's own tests (KFG_TOOL_DIR/tests/test_*.py) headless: the neosetup
tkinter stub is installed, then each test runs as it would normally (cwd = the tool's
folder, its own exec of csv_flip_tool.py). With --real also runs them on real Tk, to tell
UI-only failures from logic ones.

    python run_tool_tests_headless.py [--real] [test name ...]

Prints per file: checks passed / failed (the tests' own PASS / FAIL lines; their summary
line "FAILED: ..." is not counted), crashes, and the failing check names.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.path.dirname(os.path.dirname(HERE))
TOOL_DIR = os.environ.get("KFG_TOOL_DIR", "")

RUNNER = r"""
import os, runpy, sys
sys.path.insert(0, %r)
from neosetup import _tkstub as T
T.install(T.Dialogs())
sys.argv = [sys.argv[1]]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as e:
    sys.exit(e.code)
""" % PKG_PARENT


def run(test, headless):
    cmd = [sys.executable, "-c", RUNNER, test] if headless else [sys.executable, test]
    env = dict(os.environ, KFG_SETTINGS="", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=TOOL_DIR, capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace", timeout=900)
    out = p.stdout
    passed = len(re.findall(r"^PASS ", out, re.M))
    failed = re.findall(r"^FAIL +(.*)$", out, re.M)
    crash = None
    if "Traceback" in p.stderr:
        crash = p.stderr.strip().splitlines()[-1]
    skipped = len(re.findall(r"^SKIP", out, re.M))
    return {"passed": passed, "failed": failed, "crash": crash, "skipped": skipped}


def main(argv):
    real = "--real" in argv
    names = [a for a in argv[1:] if not a.startswith("--")]
    tests = sorted(f for f in os.listdir(os.path.join(TOOL_DIR, "tests"))
                   if f.startswith("test_") and f.endswith(".py"))
    if names:
        tests = [t for t in tests if any(n in t for n in names)]
    bad = 0
    for t in tests:
        path = os.path.join("tests", t)
        h = run(path, True)
        line = "%-36s headless: %3d pass %2d fail%s%s" % (
            t, h["passed"], len(h["failed"]), "  CRASH: " + h["crash"] if h["crash"] else "",
            "  (%d skip)" % h["skipped"] if h["skipped"] else "")
        if real:
            r = run(path, False)
            line += "   | real Tk: %3d pass %2d fail%s" % (
                r["passed"], len(r["failed"]), "  CRASH" if r["crash"] else "")
        print(line)
        for f in h["failed"]:
            print("      FAIL  %s" % f)
        bad += bool(h["failed"] or h["crash"])
    print()
    print("files with failures headless: %d of %d" % (bad, len(tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
