"""the bulk-feeder holder (neojob.holder): a plate of pockets placed on the table from two of its marks"""
import math
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neojob import holder as H  # noqa: E402


def seen(tpl, name, x0, y0, deg):
    m = next(m for m in tpl["marks"] if m["name"] == name)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return x0 + c * m["x"] - s * m["y"], y0 + s * m["x"] + c * m["y"]


def inside(pt, corners):
    """pt inside the convex quadrilateral (counter-clockwise or clockwise)"""
    sg = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        sg.append((b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0]))
    return all(v >= -1e-6 for v in sg) or all(v <= 1e-6 for v in sg)


def test_template():
    t = H.check(H.template("PP feeder 20"))
    assert len(t["pockets"]) == 20
    p = {p["n"]: p for p in t["pockets"]}
    assert p[1]["y"] > p[11]["y"] and p[10]["w"] == 37 and p[20]["w"] == 37 and p[9]["w"] == 25     # 1 .. 10 the upper row
    assert abs(p[2]["x"] - p[1]["x"] - 27) < 1e-6 and abs(p[10]["x"] + p[10]["w"] - 289.8526) < 1e-3


def test_straight():
    t = H.template("PP feeder 20")
    r = H.place(t, "L", seen(t, "L", 50, 100, 0), "R", seen(t, "R", 50, 100, 0), margin=1.5)
    assert abs(r["angle"]) < 1e-6 and abs(r["error"]) < 1e-6
    p = {p["n"]: p for p in r["pockets"]}
    assert (p[1]["x0"], p[1]["y0"], p[1]["x1"], p[1]["y1"]) == pytest.approx((50 + 9.8526 + 1.5, 100 + 51 + 1.5, 50 + 34.8526 - 1.5, 100 + 97 - 1.5), abs=2e-3)
    assert p[20]["x1"] - p[20]["x0"] == pytest.approx(34.0, abs=2e-3)


@pytest.mark.parametrize("deg", [3.0, -4.0, 90.0, 92.5, 180.0, -88.0])
def test_turned(deg):
    t = H.template("PP feeder 20")
    r = H.place(t, "L", seen(t, "L", 200, 150, deg), "C2", seen(t, "C2", 200, 150, deg))
    assert abs(((r["angle"] - deg + 180) % 360) - 180) < 1e-6
    for p in r["pockets"]:                      # the feeder's rectangle lies inside its turned pocket
        for pt in ((p["x0"], p["y0"]), (p["x1"], p["y0"]), (p["x1"], p["y1"]), (p["x0"], p["y1"])):
            assert inside(pt, p["corners"]), (deg, p["n"])
        assert p["x1"] - p["x0"] > 15 and p["y1"] - p["y0"] > 15
    m = {m["name"]: m for m in r["marks"]}
    assert (m["C4"]["x"], m["C4"]["y"]) == pytest.approx(seen(t, "C4", 200, 150, deg), abs=2e-3)


def test_refused():
    t = H.template("PP feeder 20")
    a = seen(t, "L", 50, 100, 0)
    with pytest.raises(ValueError, match="apart"):                 # the wrong hole taken: 3 mm off
        H.place(t, "L", a, "R", (a[0] + 293.3, a[1]))
    with pytest.raises(ValueError, match="askew"):
        H.place(t, "L", seen(t, "L", 50, 100, 30), "R", seen(t, "R", 50, 100, 30))
