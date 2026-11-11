"""Closed-loop angle correction and the adjustable size check, on a simulated nozzle.

A part sits on the nozzle with offset (ox, oy) mm and angle p0; turning the nozzle by phi turns
both. Each "camera image" is rendered from that state and measured with analyze_nozzle, so the
whole loop (image -> vision -> rotate -> image) is exercised.

    python tests/test_vision_verify.py
"""
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam import vision as V  # noqa: E402

K = V.UP_PX_PER_MM


class Nozzle:
    def __init__(self, ox, oy, p0, w=2.0, h=1.25, gain=1.0, slip_after=None):
        self.ox, self.oy, self.p0, self.w, self.h = ox, oy, p0, w, h
        self.gain = gain              # actual turn = gain x commanded (1 ok, -1 wrong way, 0.97 short)
        self.phi = 0.0                # actual nozzle angle
        self.cmd = 0.0
        self.slip_after = slip_after  # after this many turns the part slips 0.3 mm
        self.turns = 0
        rng = np.random.default_rng(7)
        self.bg = rng.normal(20, 3, (1024, 1024)).clip(0, 255).astype(np.uint8)

    def rotate(self, deg):
        self.phi += (deg - self.cmd) * self.gain
        self.cmd = deg
        self.turns += 1
        if self.slip_after is not None and self.turns > self.slip_after:
            self.ox += 0.3

    def image(self):
        x, y = V.rotate_xy(self.ox, self.oy, self.phi)
        ang = self.p0 + self.phi
        img = self.bg.copy()
        box = cv2.boxPoints(((512 + x * K, 512 - y * K), (self.w * K, self.h * K), -ang))   # image y down
        cv2.fillConvexPoly(img, np.round(box * 16).astype(np.int32), 200, cv2.LINE_AA, 4)
        return img

    def measure(self):
        return V.analyze_nozzle(self.image(), self.bg, 200)


def test_verify_converges():
    n = Nozzle(0.3, -0.2, 12.0)
    r = V.correct_and_verify(n.measure, n.rotate, tol_angle=0.5, tol_offset=0.1)
    assert r.ok, r.reason
    assert r.tries == 1 and abs(r.residuals[0]) < 0.5
    assert abs(r.angle - 12.0) < 0.5, r.angle                       # part angle at nozzle 0
    assert abs(r.x - 0.3) < 0.05 and abs(r.y + 0.2) < 0.05, (r.x, r.y)
    assert abs(n.p0 + n.phi) < 0.6                                  # the part really ends square


def test_verify_needs_second_correction():
    # nozzle turns 10 % short (3 deg left over; below ~1 deg a 2 mm part at 34.5 px/mm shows no
    # measurable edge tilt, so smaller residuals are not resolvable by this camera)
    n = Nozzle(0.1, 0.1, 30.0, gain=0.9)
    r = V.correct_and_verify(n.measure, n.rotate, tol_angle=0.5, tol_offset=0.1, max_tries=3)
    assert r.ok and r.tries == 2, (r.reason, r.residuals)
    assert abs(r.residuals[0]) > 0.5 and abs(r.residuals[1]) <= 0.5


def test_wrong_rotation_sign_detected():
    n = Nozzle(0.0, 0.0, 10.0, gain=-1.0)
    r = V.correct_and_verify(n.measure, n.rotate)
    assert not r.ok and "wrong way" in r.reason, r.reason


def test_slipped_part_rejected():
    n = Nozzle(0.2, 0.0, 8.0, slip_after=0)
    r = V.correct_and_verify(n.measure, n.rotate, tol_offset=0.1)
    assert not r.ok and "moved on the nozzle" in r.reason, r.reason


def test_gives_up_after_max_tries():
    n = Nozzle(0.0, 0.0, 20.0, gain=0.5)                            # turns only half way each time
    r = V.correct_and_verify(n.measure, n.rotate, tol_angle=0.2, max_tries=2)
    assert not r.ok and "after 2 correction" in r.reason, r.reason


def test_size_check_rotated_stacks():
    r = V.PartResult(True, 0, 0, 1.25, 2.0, 3.0)                    # measured: 1.25 along X, 2.0 along Y
    for a in (90, -90, 270, 89.9, 450):                             # all mean "turned a quarter"
        assert V.size_check(r, 2.0, 1.25, a).ok, a
    assert not V.size_check(r, 2.0, 1.25, 0).ok                     # unturned stack: wrong pairing
    assert V.size_check(r, 2.0, 1.25, 0, orientation="auto").ok     # auto pairs by closeness


def test_size_check_adjustable_limits():
    r = V.PartResult(True, 0, 0, 2.3, 1.25, 0.0)                    # 15 % long
    assert V.size_check(r, 2.0, 1.25, 0, lo=0.8, hi=1.25).ok
    c = V.size_check(r, 2.0, 1.25, 0, lo=0.9, hi=1.1)
    assert not c.ok and abs(c.rx - 1.15) < 1e-6 and "outside 0.90..1.10" in c.reason
    assert V.size_check(r, 2.0, 1.25, 0, lo=0.9, hi=1.2, lo_y=0.95, hi_y=1.05).ok


def test_size_check_skips_unmeasured():
    assert V.size_check(V.PartResult(True, 0, 0, 0, 0, 1.0), 20, 15, 0).ok    # large-part mode
    assert not V.size_check(V.PartResult(False), 2, 1, 0).ok


def test_four_nozzle_size_rotated():
    # 4-up shot of 0805 parts picked from a 90 deg stack: W/H come from the edge nearest the X axis
    sub = [(161, 872), (888, 871), (159, 169), (887, 160)]
    rng = np.random.default_rng(3)
    bg = rng.normal(20, 3, (1024, 1024)).clip(0, 255).astype(np.uint8)
    fr = bg.copy()
    for x, y in sub:
        box = cv2.boxPoints(((x, y), (1.25 * K, 2.0 * K), 4.0))
        cv2.fillConvexPoly(fr, np.round(box).astype(np.int32), 200)
    for r in V.analyze_nozzles(fr, bg, [True] * 4, sub):
        assert r.valid and V.size_check(r, 2.0, 1.25, 90).ok, (r.w, r.h)


if __name__ == "__main__":
    n = 0
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); n += 1; print("ok", name)
    print(f"{n} verify/size tests passed")
