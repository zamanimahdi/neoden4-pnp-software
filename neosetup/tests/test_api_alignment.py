"""Alignment from camera-measured points (2 and 3 points, board turned over or not)."""
import math

from _common import GPS, SUP, SUP_RAR, Project, ToolError, check, finish, need, raises, truth

need(GPS)


def worst(p, before, T):
    err = 0.0
    for c, (x, y) in zip(p.components(), before):
        tx, ty = T(x, y)
        err = max(err, math.hypot(c["x"] - tx, c["y"] - ty))
    return err


p = Project()
p.import_pnp(GPS)
sug = p.suggest_alignment()
check("sighting pairs are suggested", len(sug) >= 3 and sug[0]["baseline"] >= 10.0)
check("a pair never offers a fiducial, nor one part twice",
      all(not s["a"]["fiducial"] and s["a"]["index"] != s["b"]["index"] for s in sug))
check("a check point is suggested off the A-B line", sug[0]["c"] is not None and sug[0]["c_off_line"] > 5)
ident = p.identifiability()
check("identifiability per component", len(ident) == 80)

# ---- two points, board right way up ----
T = truth(False, 2.0, 150.0, 80.0)
before = [(c["x"], c["y"]) for c in p.components()]
s = sug[0]
ma = T(s["a"]["x"], s["a"]["y"])
mb = T(s["b"]["x"], s["b"]["y"])
plan = p.plan_alignment(s["a"]["index"], s["b"]["index"], ma, mb)
check("the plan finds the turn", abs(plan["theta"] - 2.0) < 1e-6 and plan["scale_ok"]
      and not plan["mirror"], "%.4f" % plan["theta"])
check("the plan carries the dialog's report", plan["report"][0].startswith("Side up       TOP"))
r = p.apply_alignment(s["a"]["designator"] if False else s["a"]["index"], s["b"]["index"], ma, mb)
check("applied: every part where the board really is", worst(p, before, T) < 0.002,
      "%.4f mm" % worst(p, before, T))
check("and B is where the camera should find it", abs(r["check_b"]["x"] - mb[0]) < 0.002)

# ---- a point sighted on the wrong part ----
q = Project()
q.import_pnp(GPS)
s = q.suggest_alignment()[0]
mb_bad = (mb[0] + 3.0, mb[1])
plan = q.plan_alignment(s["a"]["index"], s["b"]["index"], ma, mb_bad)
check("a wrong sighting shows as a scale warning", not plan["scale_ok"] and plan["warnings"])
e = raises(lambda: q.plan_alignment(s["a"]["index"], s["a"]["index"], ma, ma), ToolError)
check("two points too close are refused", e is not None and "apart" in e.text)

# ---- three points, a bottom-side board turned over ----
if SUP and SUP_RAR and __import__("os").name == "nt":
    b = Project()
    b.import_pnp(SUP)
    b.load_gerber(SUP_RAR)
    T = truth(True, -0.7, 280.0, 60.0)
    before = [(c["x"], c["y"]) for c in b.components()]
    s = b.suggest_alignment()[0]
    meas = [T(s[k]["x"], s[k]["y"]) for k in "abc"]
    plan = b.plan_alignment(s["a"]["index"], s["b"]["index"], meas[0], meas[1], "auto",
                            s["c"]["index"], meas[2])
    check("three points: turned over, decided by the third point",
          plan["mirror"] and plan["side"] == "bottom" and plan["fit"] == "three-point")
    check("residuals tiny, the other way up ruled out",
          max(plan["residuals"]) < 0.002 and plan["candidates"]["normal"]["rms"] > 1.0)
    b.apply_alignment(s["a"]["index"], s["b"]["index"], meas[0], meas[1], "auto",
                      s["c"]["index"], meas[2])
    # mirrored about X=0 first, then fitted: compare against T of the original points
    check("applied: the turned-over board lands right", worst(b, before, T) < 0.002,
          "%.4f mm" % worst(b, before, T))
else:
    print("SKIP  three-point bottom-side case (BOARD-A + Windows tar)")

theta, scale, pc, qc = Project.rigid_fit([(0, 0), (10, 0)], [(5, 5), (5, 15)])
check("rigid_fit: 90 degrees, scale 1", abs(theta - 90) < 1e-9 and abs(scale - 1) < 1e-9)
finish()
