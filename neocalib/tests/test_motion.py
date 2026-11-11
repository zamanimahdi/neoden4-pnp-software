"""Tests for neocalib.motion with synthetic cameras bound to the machine simulator.

    python neocalib/tests/test_motion.py

A known px/mm per axis, camera angle and skew are built into the synthetic camera; the
calibration must find them back from the machine moves (neomach sim://), through
make_grab() and neocam's orientation functions, with blur and noise.
"""
import math
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from neomach import Mainboard  # noqa: E402
from neocalib import CalibrationError, CalResult, calibrate, make_grab  # noqa: E402
from neocalib.motion import EXPECTED_A  # noqa: E402

R_WORLD = 160.0                 # texture resolution, px / mm


def rot(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def true_matrix(camera, kx, ky, angle, skew):
    """Measured matrix A (image shift per machine mm) with the given scale, angle, skew."""
    E = EXPECTED_A[camera]
    cx = kx * rot(angle - skew / 2) @ E[:, 0]
    cy = ky * rot(angle + skew / 2) @ E[:, 1]
    return np.column_stack([cx, cy])


def pcb_texture(w, h, seed):
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 40, np.uint8)
    for _ in range(int(w * h / 4000)):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 60)), y + int(rng.integers(8, 60))),
                          int(rng.integers(90, 230)), -1)
        else:
            cv2.circle(img, (x, y), int(rng.integers(5, 30)), int(rng.integers(90, 230)), -1)
    noise = cv2.GaussianBlur(rng.normal(0, 25, (h, w)).astype(np.float32), (0, 0), 6)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


class FakeCam:
    """Stands in for neocam.CamClient: returns RAW frames (as the camera sends them) of a
    synthetic scene seen at the simulator's current XY position."""

    def __init__(self, sim, camera, A_meas, size, start_mm, seed=1, noise=3.0, blur=1.0, wrong_orient=False,
                 plain=False):
        self.sim, self.camera, self.size = sim, camera, size
        self.A_cam = -A_meas if camera == "head" else A_meas     # head: the scene moves the other way
        self.start = np.array(start_mm, float)
        self.rng = np.random.default_rng(seed + 100)
        self.noise, self.blur, self.wrong = noise, blur, wrong_orient
        if camera == "head":
            span = 2 * (size / 100 + 4)                          # mm of board around the start point
            n = int(span * R_WORLD)
            self.tex = pcb_texture(n, n, seed)
            self.w0 = self.start - span / 2                      # world mm at texture pixel (0, 0)
        else:
            n = int(14 * R_WORLD)                                # 14 mm square object (nozzle + part)
            obj = np.full((n, n), 25, np.uint8)
            c = n // 2
            cv2.circle(obj, (c, c), int(2.5 * R_WORLD), 120, -1)                 # nozzle body
            part = pcb_texture(int(6 * R_WORLD), int(3.2 * R_WORLD), seed + 7)
            if plain:                                            # plain part, for the blob method
                part = np.full_like(part, 200)
            ph, pw = part.shape
            obj[c - ph // 2:c - ph // 2 + ph, c - pw // 2:c - pw // 2 + pw] = np.maximum(part, 60)
            self.tex = obj

    def _render(self):
        C = np.array(self.sim.xy_mm(), float)
        s = self.size
        c = np.array([s / 2, s / 2])
        Minv = R_WORLD * np.linalg.inv(self.A_cam)
        if self.camera == "head":
            off = R_WORLD * (C - self.w0) - Minv @ c
        else:
            off = np.array(self.tex.shape[::-1], float) / 2 - Minv @ c - R_WORLD * (C - self.start)
        M = np.hstack([Minv, off.reshape(2, 1)])
        img = cv2.warpAffine(self.tex, M, (s, s), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=25)
        if self.blur:
            img = cv2.GaussianBlur(img, (0, 0), self.blur)
        img = img + self.rng.normal(0, self.noise, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)

    def grab(self, cid, timeout_ms=1000):
        d = self._render()                                       # what vision should see
        if self.wrong:
            d = d.T.copy()                                       # e.g. a missing transpose
        if self.camera == "up":                                  # raw such that orient_up(raw) == d
            return cv2.transpose(cv2.flip(d, -1))
        return cv2.flip(d, -1)                                   # orient_head(raw) == d


def run(camera, kx, ky, angle, skew, size, method="phase", wrong=False, plain=False):
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    mb.home_xy()
    start = (200.0, 150.0)
    mb.move_xy(*start)
    A = true_matrix(camera, kx, ky, angle, skew)
    cam = FakeCam(sim, camera, A, size, start, wrong_orient=wrong, plain=plain)
    res = calibrate(mb, make_grab(cam, camera, mb), camera, settle_s=0.0, method=method)
    mb.close()
    return res, A


def check(name, res, kx, ky, angle, skew, tol_k=0.003, tol_a=0.05):
    ok = (abs(res.k_x / kx - 1) < tol_k and abs(res.k_y / ky - 1) < tol_k
          and abs(res.angle_deg - angle) < tol_a and abs(res.skew_deg - skew) < tol_a
          and res.orientation_ok and res.drift_px < 0.3)
    print(f"{name}: {res.summary()}")
    assert ok, f"{name}: expected k {kx}/{ky}, angle {angle}, skew {skew}"


def test_head():
    res, A = run("head", 110.4, 111.2, 0.6, 0.15, 800)
    check("head", res, 110.4, 111.2, 0.6, 0.15)
    # pixel -> mm through the inverse matrix, including angle and skew
    d = np.array([1.234, -0.567])
    px = A @ d
    back = res.img_to_mm(*px)
    assert abs(back[0] - d[0]) < 0.003 and abs(back[1] - d[1]) < 0.003, back


def test_up():
    res, _ = run("up", 35.1, 34.2, -0.8, -0.1, 1024)
    check("up", res, 35.1, 34.2, -0.8, -0.1)


def test_up_blob():
    res, _ = run("up", 34.5, 34.5, 0.3, 0.0, 1024, method="blob", plain=True)
    check("up (blob)", res, 34.5, 34.5, 0.3, 0.0, tol_k=0.01, tol_a=0.3)


def test_wrong_orientation():
    res, _ = run("head", 112, 112, 0.0, 0.0, 800, wrong=True)
    assert not res.orientation_ok, "a transposed image must be flagged"
    print("wrong orientation flagged:", res.summary().split("orientation ")[1].split(";")[0])


def test_save_load_and_no_texture():
    res, _ = run("up", 34.5, 34.5, 0.0, 0.0, 1024)
    f = os.path.join(tempfile.mkdtemp(), "cal_up.json")
    res.save(f)
    r2 = CalResult.load(f)
    assert abs(r2.k_x - res.k_x) < 1e-12 and r2.img_to_mm(10, 0) == res.img_to_mm(10, 0)
    # a blank view cannot be calibrated: clear error, no garbage result
    mb = Mainboard.connect("sim://")
    mb.home_xy()
    mb.move_xy(100, 100)
    rng = np.random.default_rng(3)
    blank = lambda: np.clip(rng.normal(30, 2, (400, 400)), 0, 255).astype(np.uint8)   # noqa: E731
    try:
        calibrate(mb, blank, "head", settle_s=0.0, min_response=0.2)
        raise AssertionError("calibrated on a blank image")
    except CalibrationError as e:
        print("blank view rejected:", str(e)[:60], "...")
    mb.close()


if __name__ == "__main__":
    test_head()
    test_up()
    test_up_blob()
    test_wrong_orientation()
    test_save_load_and_no_texture()
    print("all ok")
