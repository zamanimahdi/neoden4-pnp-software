"""Vision tests on synthetic images with a known answer.

    python -m pytest tests -q      (or: python tests/test_vision.py)
"""
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam import vision as V  # noqa: E402

K = V.UP_PX_PER_MM


def up_scene(parts, size=1024, seed=0):
    """background (empty nozzles) and frame with rectangles: parts = [(cx, cy, w_px, h_px, img_angle)]"""
    rng = np.random.default_rng(seed)
    bg = rng.normal(20, 3, (size, size)).clip(0, 255).astype(np.uint8)
    for cx, cy, *_ in parts:
        cv2.circle(bg, (int(cx), int(cy)), 20, 60, -1)            # nozzle tip, also in the background
    fr = bg.copy()
    for cx, cy, w, h, a in parts:
        box = cv2.boxPoints(((cx, cy), (w, h), a))
        cv2.fillConvexPoly(fr, np.round(box).astype(np.int32), 200, lineType=cv2.LINE_AA)
    return fr, bg


def test_single_nozzle_offset_and_angle():
    # part 2.0 x 1.25 mm (0805), centre +1.0 mm / -0.5 mm from the nozzle, rotated 10 deg CCW (machine)
    w, h = 2.0 * K, 1.25 * K
    cx, cy = 512 + 1.0 * K, 512 + 0.5 * K            # image y down = machine -Y
    fr, bg = up_scene([(cx, cy, w, h, -10.0)])        # image angle -10 = machine +10 (CCW)
    r = V.analyze_nozzle(fr, bg, V.roi_half_size(2.0, 1.25))
    assert r.valid
    assert abs(r.x - 1.0) < 0.05 and abs(r.y + 0.5) < 0.05, (r.x, r.y)
    assert abs(r.angle - 10.0) < 0.5, r.angle
    assert abs(r.w - 2.0) < 0.1 and abs(r.h - 1.25) < 0.1, (r.w, r.h)
    assert V.size_ok(r, 2.0, 1.25, 0)


def test_angle_fold_near_90():
    # the same part rotated 80 deg: the long side is now along Y, angle folds to -10 and w/h swap
    fr, bg = up_scene([(512, 512, 2.0 * K, 1.25 * K, -80.0)])
    r = V.analyze_nozzle(fr, bg, 200)
    assert abs(r.angle - (-10.0)) < 0.5, r.angle
    assert abs(r.w - 1.25) < 0.1 and abs(r.h - 2.0) < 0.1
    assert V.size_ok(r, 2.0, 1.25, 90) and not V.size_ok(r, 2.0, 1.25, 0)


def test_four_nozzles():
    sub = [(161, 872), (888, 871), (159, 169), (887, 160)]     # sample Config.ini dPosSubMouses
    parts = [(x + 10, y - 5, 1.6 * K, 0.8 * K, 5.0) for x, y in sub]
    fr, bg = up_scene(parts)
    res = V.analyze_nozzles(fr, bg, [True, True, False, True], sub)
    assert [r.valid for r in res] == [True, True, False, True]
    for r in (res[0], res[1], res[3]):
        assert abs(r.x - 10 / K) < 0.05 and abs(r.y - 5 / K) < 0.05
        assert abs(r.angle + 5.0) < 0.5


def test_no_part():
    fr, bg = up_scene([])
    assert not V.analyze_nozzle(fr, bg, 300).valid


def test_large_part_hull():
    # 20 x 15 mm part, rotated 2 deg (machine CCW), centre offset (+0.3, -0.2) mm from the nozzle.
    # view 1: head at +L/2,+W/2 -> the camera sees the (-X,-Y) corner; view 2 the (+X,+Y) corner.
    L, W, ang = 20.0, 15.0, 2.0
    off = np.array([0.3, -0.2])
    t = math.radians(ang)
    R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])

    def view(shift):
        rng = np.random.default_rng(3)
        bg = rng.normal(20, 3, (1024, 1024)).clip(0, 255).astype(np.uint8)
        fr = bg.copy()
        corners = np.array([[-L / 2, -W / 2], [L / 2, -W / 2], [L / 2, W / 2], [-L / 2, W / 2]])
        pts = (corners @ R.T) + off - shift                        # machine mm relative to the camera
        px = np.stack([512 + pts[:, 0] * K, 512 - pts[:, 1] * K], 1)
        cv2.fillConvexPoly(fr, np.round(px).astype(np.int32), 200)
        return fr, bg

    f1, b1 = view(np.array([L / 2, W / 2]) * -1)
    f2, b2 = view(np.array([L / 2, W / 2]))
    ok1 = V.analyze_hull_view(f1, b1, False)
    ok2 = V.analyze_hull_view(f2, b2, True)
    assert ok1[0] and ok2[0], (ok1, ok2)
    r = V.analyze_large_part(f1, b1, f2, b2)
    assert r.valid
    # NeoDen's theta convention: minus the edge deviation in image coords = machine CCW angle
    assert abs(abs(r.angle) - ang) < 0.5, r.angle


def test_find_mark():
    rng = np.random.default_rng(5)
    img = rng.normal(60, 5, (800, 800)).clip(0, 255).astype(np.uint8)
    cv2.rectangle(img, (100, 350), (260, 450), 150, -1)                 # a pad (not round)
    cx, cy, d_mm = 437.3, 379.6, 1.0
    cv2.circle(img, (int(cx * 16), int(cy * 16)), int(d_mm / 2 * V.HEAD_PX_PER_MM * 16), 220, -1,
               lineType=cv2.LINE_AA, shift=4)
    r = V.find_mark_best(img, 0.8, 1.5)
    assert r.precision > 0.9, r
    assert abs(r.x - cx) < 1.0 and abs(r.y - cy) < 1.0, (r.x, r.y)
    assert abs(r.r - d_mm / 2 * V.HEAD_PX_PER_MM) < 3
    dx, dy = V.mark_offset_mm(r)
    assert abs(dx - (cx - 400) / 112) < 0.01 and abs(dy - (400 - cy) / 112) < 0.01
    assert V.find_mark_best(img, 2.0, 3.0).precision == 0.0           # wrong size range: nothing


def test_auto_exposure_converges():
    state = {"e": 1}
    base = np.full((800, 800), 1.0, np.float32)
    base[200:600, 200:600] = 2.0

    def grab():
        return np.clip(base * state["e"] * 0.5, 0, 255).astype(np.uint8)

    e = V.auto_exposure_mark(grab, lambda v: state.__setitem__("e", v))
    assert state["e"] == e
    assert 120 <= V.histogram_mid_level(grab()) <= 200, V.histogram_mid_level(grab())


def test_placement_math():
    from neocam.vision import PartResult
    # no vision: angle is comp - stack + camera constant + pcb
    x, y, a = V.placement(100, 50, 90, 0, 0.4, 0.0)
    assert (x, y) == (100, 50) and abs(a - 90.4) < 1e-9
    # vision says the part sits +0.5 mm X on the nozzle, 0 deg: place 0.5 mm less in X
    x, y, a = V.placement(100, 50, 0, 0, 0.0, 0.0, PartResult(True, 0.5, 0.0, 1, 1, 0.0))
    assert abs(x - 99.5) < 1e-6 and abs(y - 50) < 1e-6 and a == 0
    assert V.placement(0, 0, 0, 0, 0, 0, PartResult(False)) is None


def test_orientation():
    raw = np.arange(16, dtype=np.uint8).reshape(4, 4)
    assert (V.orient_up(raw) == raw[::-1, ::-1].T).all()
    assert (V.orient_head(raw) == raw[::-1, ::-1]).all()


if __name__ == "__main__":
    n = 0
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            n += 1
            print("ok", name)
    print(f"{n} vision tests passed")
