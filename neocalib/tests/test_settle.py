"""Tests for neocalib.settle with a synthetic camera whose image rings after a move.

    python neocalib/tests/test_settle.py
"""
import math
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neocalib.settle import grab_settled  # noqa: E402
from test_motion import pcb_texture  # noqa: E402


class RingingCam:
    """After kick(), the image shifts by A exp(-t/tau) sin(2 pi f t) (x) and half that (y)."""

    def __init__(self, amp=4.0, tau=0.05, freq=23.0, frame_s=0.02, size=512):
        self.tex = pcb_texture(size + 64, size + 64, 3)
        self.amp, self.tau, self.freq, self.frame_s, self.size = amp, tau, freq, frame_s, size
        self.t_kick = -1e9
        self.rng = np.random.default_rng(1)

    def kick(self):
        self.t_kick = time.monotonic()

    def shift(self, t):
        dt = t - self.t_kick
        a = self.amp * math.exp(-dt / self.tau) * math.sin(2 * math.pi * self.freq * dt)
        return a, a / 2

    def grab(self):
        time.sleep(self.frame_s)
        sx, sy = self.shift(time.monotonic())
        M = np.float32([[1, 0, 32 + sx], [0, 1, 32 + sy]])
        img = cv2.warpAffine(self.tex, M, (self.size, self.size), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        img = img + self.rng.normal(0, 2.0, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)

    def displacement_now(self):
        sx, sy = self.shift(time.monotonic())
        return math.hypot(sx, sy)


def test_quiet():
    cam = RingingCam()
    r = grab_settled(cam.grab)
    print(f"quiet camera: settled {r.settled} in {r.time_s * 1000:.0f} ms, {r.frames} frames, motion {r.motion:.3f} px")
    assert r.settled and r.frames == 3 and r.time_s < 0.15


def test_ringing():
    cam = RingingCam(amp=4.0, tau=0.05, freq=23.0)
    cam.kick()
    r = grab_settled(cam.grab, tol_px=0.3)
    left = cam.displacement_now()
    print(f"ringing 4 px, tau 50 ms: settled {r.settled} in {r.time_s * 1000:.0f} ms ({r.frames} frames); "
          f"image still {left:.3f} px off when returned (NeoDen waits a fixed 250 ms)")
    assert r.settled and 0.08 < r.time_s < 0.35 and left < 0.5


def test_never_settles():
    cam = RingingCam(amp=6.0, tau=10.0, freq=17.0)
    cam.kick()
    r = grab_settled(cam.grab, max_s=0.3)
    print(f"keeps ringing: settled {r.settled} after {r.time_s * 1000:.0f} ms (max 300), frame returned anyway")
    assert not r.settled and r.frame is not None and 0.3 <= r.time_s < 0.45


def test_diff_method():
    cam = RingingCam(amp=4.0, tau=0.05, freq=23.0)
    cam.kick()
    r = grab_settled(cam.grab, method="diff", tol_diff=2.0)
    print(f"diff method: settled {r.settled} in {r.time_s * 1000:.0f} ms, last diff {r.motion:.2f} grey levels")
    assert r.settled and r.time_s < 0.4


if __name__ == "__main__":
    test_quiet()
    test_ringing()
    test_never_settles()
    test_diff_method()
    print("all ok")
