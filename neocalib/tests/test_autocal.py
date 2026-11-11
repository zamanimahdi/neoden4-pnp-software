"""neocalib.autocal on the machine simulator with a synthetic up camera: the nozzle tip (a grey disc
with a dark hole, turning with a small runout) moves in the image as A (head - axis). The true scale
is 32.15 px/mm at 0.44 deg (Config says 34.5 / 0), the stored nozzle positions are 0.2-0.4 mm off,
nozzle 2 is empty.

    python neocalib/tests/test_autocal.py
"""
import math
import os
import sys
import threading
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from neocalib.autocal import AutoCal, four_points  # noqa: E402
from neocalib.motion import EXPECTED_A  # noqa: E402
from neocam.config import NeoConfig  # noqa: E402
from neoserver.hw import Machine  # noqa: E402

K, ANG = 32.15, 0.44
R = np.array([[math.cos(math.radians(ANG)), -math.sin(math.radians(ANG))], [math.sin(math.radians(ANG)), math.cos(math.radians(ANG))]])
A_TRUE = K * R @ (EXPECTED_A["up"] / np.linalg.norm(EXPECTED_A["up"][:, 0]))
AXIS = {1: (60.20, 40.10), 2: (75.40, 40.00), 3: (60.30, 55.30), 4: (75.10, 55.20)}      # true axes (machine mm)
RUNOUT = {1: 0.030, 3: 0.050, 4: 0.020}
TILT = {1: ((0.0, 0.0), (0.0, 0.0)), 3: ((0.004, -0.003), (0.0, 0.0)), 4: ((0.0, 0.0), (0.005, 0.002))}  # mm per mm: fixed, bent
STORED = {1: (60.0, 40.4), 2: (75.0, 40.0), 3: (60.6, 55.0), 4: (75.3, 55.5)}
CENTRE = (67.7, 47.7)
KD = 4.9e-7                     # lens: radial factor 1 + KD r^2 (32.15 px/mm in the middle, ~34.1 at r = 350 px)


def lens(p):
    d = np.asarray(p, float) - 512
    return 512 + d * (1 + KD * float(d @ d))


class FakeCams:
    def __init__(self, sim):
        self.sim, self.lock = sim, threading.Lock()
        self.rng = np.random.default_rng(3)

    def dev(self):
        return self

    def exposure(self, cam, v):
        pass

    def frame(self):
        head = np.array(self.sim.xy_true_mm())
        img = np.full((1024, 1024), 205, np.uint8)
        for n, ax in AXIS.items():
            if n not in RUNOUT:
                continue                                          # nozzle 2: empty mount
            a = math.radians(self.sim.angle[n])
            tipmm = np.array([math.cos(a), math.sin(a)]) * RUNOUT[n]
            fx, tr = TILT[n]                                      # lower = further sideways (Z axis tilt + bent nozzle)
            tipmm = tipmm + self.sim.z[n] * (np.array(fx) + np.array([math.cos(a) * tr[0] - math.sin(a) * tr[1], math.sin(a) * tr[0] + math.cos(a) * tr[1]]))
            p = lens(np.array([512, 512]) + A_TRUE @ (head - np.array(ax)) + A_TRUE @ tipmm)
            if not (-100 < p[0] < 1124 and -100 < p[1] < 1124):
                continue
            cv2.circle(img, (int(round(p[0] * 16)), int(round(p[1] * 16))), int(1.0 * K * 16), 120, -1, cv2.LINE_AA, 4)
            cv2.circle(img, (int(round(p[0] * 16)), int(round(p[1] * 16))), int(0.25 * K * 16), 25, -1, cv2.LINE_AA, 4)
        img = cv2.GaussianBlur(img, (0, 0), 1.2).astype(np.float32) + self.rng.normal(0, 2, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)


def main():
    m = Machine("sim://", lambda t, *a: None)
    m.home_at_start = False
    mb = m.board()
    mb.home_xy()
    sim = mb.link.port.sim
    cams = FakeCams(sim)
    cfg = NeoConfig({}, None)
    for n, p in STORED.items():
        cfg.set_pt(f"Position/posCamLookUpMouse{n - 1}", p)
    cfg.set_pt("Position/posCamLookUpMouseMouseCenter", CENTRE)
    cfg.set("Vision/fUpPxPerMm", 34.5)
    ac = AutoCal(m, cams, lambda: cfg, log=lambda t: None)
    ac.grab = lambda: cams.frame()                               # the frame as the UI shows it
    assert m.take("t1")[0]
    t0 = time.time()
    ac.start("t1", {"scale": True, "nozzles": [1, 2, 3, 4], "step": 3})
    while ac.status()["state"] == "running":
        time.sleep(0.2)
    s = ac.status()
    print("\n".join(s["log"]))
    assert s["state"] == "done", s["state"]
    r = s["result"]
    sc = r["scale"]
    print(f"scale {sc['k_x']:.3f} / {sc['k_y']:.3f} px/mm (true {K}), angle {sc['angle_deg']:+.3f} (true {ANG:+.2f})  [{time.time() - t0:.0f} s]")
    assert abs(sc["k_x"] - K) < 0.2 and abs(sc["k_y"] - K) < 0.2, sc                # the lens adds ~0.4 % at 3 mm
    assert abs(abs(sc["angle_deg"]) - ANG) < 0.05, sc
    for n in (1, 3, 4):
        e = math.hypot(r["nozzles"][n]["pos_new"][0] - AXIS[n][0], r["nozzles"][n]["pos_new"][1] - AXIS[n][1])
        ro = r["nozzles"][n]["runout_mm"]
        print(f"nozzle {n}: error {e * 1000:.0f} um, runout {ro * 1000:.0f} um (true {RUNOUT[n] * 1000:.0f})")
        assert e < 0.01 and abs(ro - RUNOUT[n]) < 0.01
    assert not r["nozzles"][2]["ok"]                              # empty mount: skipped, old value kept
    keys = [p["key"] for p in r["proposed"]]
    assert "Vision/fUpPxPerMm" in keys and "Position/posCamLookUpMouse1" not in keys and "Position/posCamLookUpMouse0" in keys
    # 4-nozzle points: measured through the lens (the straight-line prediction is ~20 px off there)
    pts, how = r["four_points"], r["four_how"]
    want = [lens(p).tolist() for p in four_points(A_TRUE, CENTRE, [AXIS[n] for n in (1, 2, 3, 4)])]
    flat = four_points(A_TRUE, CENTRE, [AXIS[n] for n in (1, 2, 3, 4)])
    for i in (0, 2, 3):
        e, ef = math.hypot(pts[i][0] - want[i][0], pts[i][1] - want[i][1]), math.hypot(flat[i][0] - want[i][0], flat[i][1] - want[i][1])
        print(f"4-nozzle point {i + 1}: measured {e:.1f} px from the truth (a straight-line prediction: {ef:.1f} px)")
        assert how[i] == "measured" and e < 1.5 and ef > 10, (i, pts[i], want[i])
    assert how[1] == "computed"                                   # empty nozzle 2: predicted, unchecked
    prop = {p["key"]: p for p in r["proposed"]}
    assert not prop["Position/dPosSubMouses1"]["default"] and prop["Position/dPosSubMouses0"]["default"]
    assert abs(prop["Default/fAngleCamLookUp"]["new"] + sc["angle_deg"]) < 0.002 and not prop["Default/fAngleCamLookUp"]["default"]
    # Z tilt: found back (nozzle 1 straight, 3 a tilted axis, 4 a bent nozzle), and the shift the runner
    # applies at a place depth equals where the tip really is
    from neocalib.ztilt import shift
    for n in (1, 3, 4):
        f = r["ztilt"][n]
        fx, tr = TILT[n]
        print(f"nozzle {n} Z tilt: fixed {f['fix'][0] * 1000:+.2f}, {f['fix'][1] * 1000:+.2f} um/mm (true {fx[0] * 1000:+.1f}, {fx[1] * 1000:+.1f}), "
              f"turning {f['turn'][0] * 1000:+.2f}, {f['turn'][1] * 1000:+.2f} (true {tr[0] * 1000:+.1f}, {tr[1] * 1000:+.1f}), sign {f['sign']:+d}")
        for th in (0.0, 37.0, -90.0):
            got = np.array(shift(f["fix"], f["turn"], f["sign"], th, 9.7))
            a = math.radians(th)
            true = 9.7 * (np.array(fx) + np.array([math.cos(a) * tr[0] - math.sin(a) * tr[1], math.sin(a) * tr[0] + math.cos(a) * tr[1]]))
            assert np.hypot(*(got - true)) < 0.008, (n, th, got, true)          # within 8 um at the place depth
    assert "Calib/ptZTiltFix1" in keys and "Calib/ptZTiltFix2" not in keys
    # cancel works
    assert m.take("t1")[0]                                         # the long run may have let the lock lapse
    ac.start("t1", {"scale": True, "nozzles": [1]})
    time.sleep(0.5)
    ac.cancel()
    while ac.status()["state"] == "running":
        time.sleep(0.1)
    assert ac.status()["state"] == "cancelled"
    # another client cannot start it
    try:
        ac.start("other", {})
        raise AssertionError("started without control")
    except PermissionError:
        pass
    print("all ok")


if __name__ == "__main__":
    main()
