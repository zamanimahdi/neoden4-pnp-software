"""a new pick-and-place file for an existing job (job editor -> Board): fitted onto the job, the part
that really moved is found"""
import math
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neojob import load_job  # noqa: E402
from neoserver.align import AlignService  # noqa: E402

JOB = os.path.join(os.environ.get("NEODEN_DATA", ""), "Proc", "BOARD-C main-1.csv")


def test_repnp_finds_the_moved_part():
    job = load_job(JOB)
    t = math.radians(30)
    rows = ['"Designator","Center-X(mm)","Center-Y(mm)","Rotation","Layer"']
    moved = job.comps[5].name
    for c in job.comps:
        x, y = c.x + (0.3 if c.name == moved else 0.0), c.y
        # the CAD frame: the job frame turned back by 30 deg and shifted
        u, v = math.cos(-t) * x - math.sin(-t) * y - 40.0, math.sin(-t) * x + math.cos(-t) * y + 12.0
        rows.append(f'"{c.name}","{u:.4f}","{v:.4f}","{(c.rot - 30.0) % 360:.2f}","TopLayer"')
    d = tempfile.mkdtemp()
    p = os.path.join(d, "pnp.csv")
    open(p, "w").write("\n".join(rows) + "\n")
    r = AlignService(None, None, None, print).repnp({"job": JOB, "pnp": p})
    assert abs(r["fit"]["theta"] - 30.0) < 0.01 and not r["fit"]["mirror"], r["fit"]
    diff = [q for q in r["parts"] if math.hypot(q["dx"], q["dy"]) >= 0.02 or abs(q["drot"]) >= 0.5]
    assert [q["name"] for q in diff] == [moved], diff[:5]
    assert abs(diff[0]["dx"] - 0.3) < 0.01, diff[0]
