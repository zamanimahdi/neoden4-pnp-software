"""Runs every neosetup test file (each is a plain script; exit code != 0 on failure).

    python run_all.py [--no-golden]

--no-golden skips test_golden.py, which needs a display for the real Tk tool (e.g. on the
machine PC: the API tests run headless anywhere, the golden test on a Windows desktop).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    tests = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    if "--no-golden" in argv:
        tests.remove("test_golden.py")
    failed = []
    for t in tests:
        p = subprocess.run([sys.executable, os.path.join(HERE, t)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        n_pass = p.stdout.count("\nPASS") + p.stdout.startswith("PASS")
        n_fail = p.stdout.count("\nFAIL ") + p.stdout.startswith("FAIL ")
        print("%-28s %s  (%d pass, %d fail)" % (t, "ok  " if p.returncode == 0 else "FAIL",
                                                n_pass, n_fail))
        if p.returncode != 0:
            failed.append(t)
            tail = [l for l in p.stdout.splitlines() if l.startswith("FAIL")] or \
                p.stderr.strip().splitlines()[-5:]
            for l in tail[:20]:
                print("    " + l)
    print()
    print("FAILED:", failed if failed else "none")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
