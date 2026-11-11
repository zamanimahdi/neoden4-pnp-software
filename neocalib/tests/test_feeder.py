"""Tests for neocalib.feeder with synthetic tape images seen by a head camera on the
machine simulator.

    python neocalib/tests/test_feeder.py
"""
import math
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neomach import Mainboard  # noqa: E402
from neocalib.feeder import (HOLE_PITCH, POCKET_ACROSS, POCKET_ALONG, calibrate_feeder,  # noqa: E402
                             calibrate_feeders)
from neocalib.motion import CalResult, describe  # noqa: E402
from test_motion import true_matrix  # noqa: E402

RES = 120.0                         # texture px / mm
CAM_OFFSET = (-31.5, 12.25)         # head camera vs nozzle 1 (mm)


def head_cal(A):
    k_x, k_y, ang, skew, ok = describe(A, "head")
    return CalResult("head", A.tolist(), [0, 0], k_x, k_y, ang, skew, ok, 0, 0, 0, 1.4, "test", 13)


class TapeScene:
    """Feeder base with one tape. The true pocket nearest `pocket` is at `pocket`;
    the tape runs at angle_deg; the pockets are on `side` (+1 left of the tape direction)."""

    def __init__(self, pocket, angle_deg=0.8, side=1, width=8, pocket_pitch=4.0, blank=False):
        self.w0 = np.asarray(pocket, float) - 12.0                  # world mm at texture (0, 0)
        n = int(24 * RES)
        img = np.full((n, n), 35, np.uint8)
        if not blank:
            a = math.radians(angle_deg)
            u = np.array([math.cos(a), math.sin(a)])
            nrm = np.array([-u[1], u[0]]) * side
            hole0 = np.asarray(pocket, float) - u * POCKET_ALONG - nrm * POCKET_ACROSS[width]
            px = lambda p: tuple(int(round(v)) for v in (np.asarray(p) - self.w0) * RES)    # noqa: E731
            e0, e1 = hole0 - nrm * 1.75, hole0 + nrm * (width - 1.75)
            strip = [e0 - u * 20, e0 + u * 20, e1 + u * 20, e1 - u * 20]
            cv2.fillPoly(img, [np.array([px(p) for p in strip], np.int32)], 190)
            for k in range(-5, 6):
                cv2.circle(img, px(hole0 + u * k * HOLE_PITCH), int(0.75 * RES), 25, -1)
            for m in range(-6, 7):
                c = hole0 + u * (POCKET_ALONG + m * pocket_pitch) + nrm * POCKET_ACROSS[width]
                for (L, W, val) in ((1.1, 1.9, 120), (0.8, 1.6, 230)):      # pocket, part
                    box = cv2.boxPoints(((c - self.w0) * RES, (L * RES, W * RES), angle_deg))
                    cv2.fillPoly(img, [box.astype(np.int32)], val)
        self.tex = cv2.GaussianBlur(img, (0, 0), 1.5)


class HeadCam:
    def __init__(self, sim, scene, A, size=1024, noise=3.0):
        self.sim, self.scene, self.A, self.size = sim, scene, np.asarray(A, float), size
        self.rng = np.random.default_rng(2)
        self.noise = noise

    def grab(self):
        H = np.array(self.sim.xy_true_mm())
        ccam = H - np.array(CAM_OFFSET)                             # camera centre, nozzle-1 frame
        c = np.array([self.size / 2, self.size / 2])
        Minv = -RES * np.linalg.inv(self.A)
        off = RES * (ccam - self.scene.w0) - Minv @ c
        M = np.hstack([Minv, off.reshape(2, 1)])
        img = cv2.warpAffine(self.scene.tex, M, (self.size, self.size),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=35)
        img = img + self.rng.normal(0, self.noise, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)


A = true_matrix("head", 111.6, 112.3, 0.3, 0.05)


def setup(scene, size=1024):
    mb = Mainboard.connect("sim://")
    mb.home_xy()
    cam = HeadCam(mb.link.port.sim, scene, A, size)
    return mb, cam


def run_case(name, true_pocket, expected, size=1024, tol=0.03, **scene_kw):
    width = scene_kw.get("width", 8)
    scene = TapeScene(true_pocket, **scene_kw)
    mb, cam = setup(scene, size)
    r = calibrate_feeder(mb, cam.grab, head_cal(A), expected, CAM_OFFSET, tape_width=width)
    err = math.hypot(r.pick[0] - true_pocket[0], r.pick[1] - true_pocket[1]) if r.pick else None
    print(f"{name}: {r.summary()}; error vs the true pocket "
          f"{err * 1000:.0f} um" if err is not None else f"{name}: {r.summary()}")
    assert r.ok, r.message
    assert err < tol, err
    assert abs(r.tape_angle_deg - scene_kw.get("angle_deg", 0.8)) < 0.15, r.tape_angle_deg
    mb.close()
    return r


def test_cases():
    p = (408.89, 113.46)
    off = (p[0] - 0.35, p[1] + 0.20)                                # stored position 0.4 mm off
    run_case("8 mm tape, 1024 window", p, off)
    r = run_case("8 mm tape, 800 window (holes at the edge)", p, off, size=800)
    assert r.views > 1
    run_case("tape the other way round", p, off, side=-1, angle_deg=-0.6)
    run_case("12 mm tape", p, off, width=12, angle_deg=0.4)
    # expected 1.6 mm along the tape from the pocket: snaps to the nearest pocket of the grid
    far = (p[0] + 1.6, p[1])
    scene = TapeScene(p, angle_deg=0.8)
    mb, cam = setup(scene)
    r = calibrate_feeder(mb, cam.grab, head_cal(A), far, CAM_OFFSET)
    u = np.array([math.cos(math.radians(0.8)), math.sin(math.radians(0.8))])
    t = (np.array(r.pick) - p) @ u
    print(f"expected 1.6 mm off along the tape: snapped {t:+.3f} mm along from the reference pocket")
    assert r.ok and min(abs(t), abs(t - 4.0)) < 0.03
    mb.close()


def test_no_tape_and_batch():
    p = (300.0, 150.0)
    scene = TapeScene(p, blank=True)
    mb, cam = setup(scene)
    r = calibrate_feeder(mb, cam.grab, head_cal(A), p, CAM_OFFSET)
    print("no tape:", r.summary())
    assert not r.ok and "hole" in r.message
    # batch: one feeder without tape does not stop the others
    scene2 = TapeScene((300.0, 150.0))
    cam.scene = scene2
    res = calibrate_feeders(mb, cam.grab, head_cal(A),
                            [("F1", (300.2, 149.9), {}), ("F2", (330.0, 150.0), {"max_dist": 1.0})],
                            CAM_OFFSET)
    print("batch:", [(n, r.ok) for n, r in res])
    assert res[0][1].ok and not res[1][1].ok
    mb.close()


if __name__ == "__main__":
    test_cases()
    test_no_tape_and_batch()
    print("all ok")
