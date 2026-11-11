"""The 4-up up-camera shot: nozzle axes from the empty tips turned 0/90/180/-90 deg, then the
offsets of four parts read from one image.

    python tests/test_fourup.py
"""
import math
import os
import sys

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam import vision as V  # noqa: E402

K = 32.15
AXES = [(178.3, 851.6), (852.9, 849.2), (176.1, 174.4), (850.4, 172.8)]     # px, as on the machine
RUNOUT = 0.13 * K                                                           # px


def tips(turn_deg, rng):
    """empty nozzles at a bright exposure: grey discs with a dark hole off the axis by the runout"""
    img = np.full((1024, 1024), 60, np.uint8)
    for i, (x, y) in enumerate(AXES):
        a = math.radians(turn_deg + 40 * i)
        hx, hy = x + RUNOUT * math.cos(a), y + RUNOUT * math.sin(a)
        cv2.circle(img, (int(round(x)), int(round(y))), 150, 110, -1, cv2.LINE_AA)
        cv2.circle(img, (int(round(hx * 8)), int(round(hy * 8))), 8 * 8, 25, -1, cv2.LINE_AA, shift=3)
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    return np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)


def test_axes_from_turned_tips():
    rng = np.random.default_rng(2)
    stored = [(161, 872), (888, 871), (159, 169), (887, 160)]               # Config dPosSubMouses (34.5 px/mm)
    seen = [[] for _ in AXES]
    for t in (0, 90, 180, -90):
        img = tips(t, rng)
        for i, (sx, sy) in enumerate(stored):
            r = V.tip_centre(img, sx, sy, search=int(1.2 * K))
            assert r is not None, (t, i)
            seen[i].append(r[:2])
    for i, pts in enumerate(seen):
        cx, cy, r, rms = V.fit_circle(pts)
        err = math.dist((cx, cy), AXES[i])
        assert err < 0.5 and abs(r - RUNOUT) < 0.7 and rms < 0.7, (i, cx, cy, r, rms)
        print(f"nozzle {i + 1}: axis {cx:.2f}, {cy:.2f} px (true {AXES[i][0]}, {AXES[i][1]}), runout {r / K * 1000:.0f} um")


def test_parts_in_one_shot():
    bg = np.full((1024, 1024), 20, np.uint8)
    img = bg.copy()
    parts = [(0.12, -0.08, 1.6, 0.8, 0.0), (-0.20, 0.05, 3.2, 1.6, 3.0), (0.0, 0.15, 2.0, 1.25, -2.0), (0.07, 0.07, 5.0, 5.0, 1.0)]
    for (x, y), (dx, dy, w, h, a) in zip(AXES, parts):
        c = (x + dx * K, y - dy * K)                                         # image y is down = machine -Y
        box = cv2.boxPoints((c, (w * K, h * K), -a))
        cv2.fillPoly(img, [np.round(box * 8).astype(np.int32)], 200, cv2.LINE_AA, shift=3)
    for (x, y), (dx, dy, w, h, a) in zip(AXES, parts):
        r = V.analyze_at(img, bg, x, y, V.roi_half_size(w, h, K), K)
        assert r.valid
        assert abs(r.x - dx) < 0.03 and abs(r.y - dy) < 0.03, (r, dx, dy)
        print(f"part {w} x {h}: offset {r.x:+.3f}, {r.y:+.3f} mm (true {dx:+.3f}, {dy:+.3f})")


def test_perspective_fix():
    """a 1 mm high part at the 4-up corner: drawn 0.87 % bigger and pushed outwards; the fix
    gives back its offset and size in the tip plane"""
    m, H = 0.0087, 1.0
    axis, c = AXES[0], (512.0, 512.0)
    bg = np.full((1024, 1024), 20, np.uint8)
    img = bg.copy()
    dx, dy, w, h = 0.10, -0.06, 2.0, 1.25
    s_ = 1 + m * H
    true_px = (axis[0] + dx * K, axis[1] - dy * K)
    seen = (c[0] + (true_px[0] - c[0]) * s_, c[1] + (true_px[1] - c[1]) * s_)     # what the lens draws
    box = cv2.boxPoints((seen, (w * K * s_, h * K * s_), 0))
    cv2.fillPoly(img, [np.round(box * 8).astype(np.int32)], 200, cv2.LINE_AA, shift=3)
    r = V.analyze_at(img, bg, axis[0], axis[1], 150, K)
    raw = (r.x, r.y)
    r = V.perspective_fix(r, axis, c, K, H, m)
    assert abs(r.x - dx) < 0.015 and abs(r.y - dy) < 0.015, (raw, r)
    assert math.dist(raw, (dx, dy)) > 0.1                  # unfixed: the outward push (~0.13 mm)
    assert abs(max(r.w, r.h) - w) < 0.05, r                 # the outline fit itself is ~1 px big
    print(f"1 mm high part at the corner: raw {raw[0]:+.3f}, {raw[1]:+.3f} -> fixed {r.x:+.3f}, {r.y:+.3f} mm (true {dx:+.3f}, {dy:+.3f})")


if __name__ == "__main__":
    test_axes_from_turned_tips()
    test_parts_in_one_shot()
    test_perspective_fix()
