"""GOLDEN EQUIVALENCE: every wizard flow of golden_flows.py run on the real KFG tool (real Tk,
its real dialogs) and through neosetup.Project, in separate processes with the same hash
seed; every output file must be byte-identical, and every step must have shown the same
messages (same kind, title and text) and the same Part Name / Footprint tables.

    python test_golden.py [flow ...]       (exit code 1 on any difference)

Needs a display for the real side (Windows desktop); flows whose fixtures are missing are
skipped. KEEP_GOLDEN=1 keeps the output folders (printed).
"""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import golden_flows as G  # noqa: E402

fails = []


def check(label, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + label + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


def side(name, flow, out):
    env = dict(os.environ, PYTHONHASHSEED="0", KFG_SETTINGS="")
    p = subprocess.run([sys.executable, os.path.join(HERE, "golden_flows.py"), name, flow, out],
                       capture_output=True, text=True, env=env, timeout=900)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-4000:])
    return p.returncode == 0


def first_diff(a, b):
    la = a.decode("utf-8", "replace").splitlines()
    lb = b.decode("utf-8", "replace").splitlines()
    d = list(difflib.unified_diff(la, lb, "real", "neo", n=0, lineterm=""))
    return "\n".join(d[:12])


def compare(flow, real, neo):
    files = sorted(f for f in os.listdir(real) if f != "results.json")
    nfiles = sorted(f for f in os.listdir(neo) if f != "results.json")
    check("%s: same files written" % flow, files == nfiles, "%s vs %s" % (files, nfiles))
    for f in files:
        if f not in nfiles:
            continue
        a = open(os.path.join(real, f), "rb").read()
        b = open(os.path.join(neo, f), "rb").read()
        check("%s: %s byte-identical (%d bytes)" % (flow, f, len(a)), a == b,
              "" if a == b else "\n" + first_diff(a, b))
    ra = json.load(open(os.path.join(real, "results.json"), encoding="utf-8"))
    rb = json.load(open(os.path.join(neo, "results.json"), encoding="utf-8"))
    for sa, sb in zip(ra["steps"], rb["steps"]):
        label = "%s: step %s" % (flow, " ".join(str(x) for x in sa["step"]))
        same = sa["messages"] == sb["messages"]
        extra = ""
        if not same:
            extra = "\n   real: %r\n   neo:  %r" % (sa["messages"], sb["messages"])
        if "result" in sa or "result" in sb:
            same_r = sa.get("result") == sb.get("result")
            if not same_r:
                extra += "\n   result differs"
            same = same and same_r
        check(label + " -- same messages%s" % (" and tables" if "result" in sa else ""),
              same, extra)


def main(argv):
    flows = argv[1:] or list(G.FLOWS)
    keep = os.environ.get("KEEP_GOLDEN")
    for flow in flows:
        missing = [p for p in G.FLOWS[flow]["needs"] if not p or not os.path.exists(p)]
        if missing:
            print("SKIP  %s: fixture(s) not found: %s" % (flow, missing))
            continue
        base = tempfile.mkdtemp(prefix="neosetup-golden-%s-" % flow)
        real, neo = os.path.join(base, "real"), os.path.join(base, "neo")
        ok = side("real", flow, real) and side("neo", flow, neo)
        check("%s: both sides ran" % flow, ok)
        if ok:
            compare(flow, real, neo)
        if keep:
            print("      kept: %s" % base)
        else:
            shutil.rmtree(base, ignore_errors=True)
    print()
    print("FAILED:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
