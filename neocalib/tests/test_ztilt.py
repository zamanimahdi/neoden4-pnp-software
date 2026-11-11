"""Z tilt fit: a tilted Z axis plus a bent nozzle, measured at 4 turns and 4 depths, found back"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from neocalib.ztilt import fit_ztilt, shift  # noqa: E402


def test_fixed_and_turning_tilt():
    fix, turn = (0.004, -0.002), (0.006, 0.001)            # mm per mm
    for sign in (1, -1):
        meas = []
        for th in (0, 90, 180, -90):
            for z in (0, 2.5, 5, 7.5):
                dx, dy = shift(fix, turn, sign, th, z)
                meas.append((th, z, dx + 0.02, dy - 0.01))       # plus the offset at z = 0
        f = fit_ztilt(meas)
        assert f["sign"] == sign and f["rms"] < 1e-9, f
        assert all(abs(a - b) < 1e-9 for a, b in zip(f["fix"], fix)) and all(abs(a - b) < 1e-9 for a, b in zip(f["turn"], turn)), f
        dx, dy = shift(f["fix"], f["turn"], f["sign"], 37.0, 9.7)
        ex, ey = shift(fix, turn, sign, 37.0, 9.7)
        assert abs(dx - ex) < 1e-9 and abs(dy - ey) < 1e-9
