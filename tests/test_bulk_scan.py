"""the scan of one bulk feeder (neojob.bulkfeed.BulkFeeder.scan) on a drawn table: every part found where
it lies, with the fewest pictures that cover the pocket, taken at the normal speed"""
import json
import os
import sys
import threading

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neojob import bulkfeed as BF  # noqa: E402

K = 115.2                    # head camera px / mm
OFF = (-10.3, 36.45)         # nozzle 1 - head camera


class Table:
    """a bright floor with dark bodies, seen by a 1024 px head camera (X right, Y up)"""
    def __init__(self, x0, y0, w, h, parts):
        self.x0, self.y1 = x0 - 12, y0 + h + 12
        self.img = np.full((int((h + 24) * K), int((w + 24) * K)), 200, np.uint8)
        for x, y, L, W, a in parts:
            box = cv2.boxPoints((((x - self.x0) * K, (self.y1 - y) * K), (L * K, W * K), a))
            cv2.fillConvexPoly(self.img, box.astype(np.int32), 25)
        self.at, self.moves = (0.0, 0.0), []

    # the motion board
    def head_led(self, v): pass

    def move_xy(self, x, y, timeout=20, slow=False):
        self.at = (x - OFF[0], y - OFF[1])
        self.moves.append((self.at, slow))

    # the camera
    def grab(self, cam):
        u, v = int(round((self.at[0] - self.x0) * K)) - 512, int(round((self.y1 - self.at[1]) * K)) - 512
        return self.img[v:v + 1024, u:u + 1024].copy()


class Cfg:
    head_cam_offset = OFF

    def pxmm(self, cam):
        return K


class Vis:
    def __init__(self, t):
        self.dev, self.lock, self.seq, self.last = t, threading.Lock(), 0, {}


def feeder(tmp_path, monkeypatch, parts, x0=60.0, y0=150.0, w=22.0, h=43.0):
    import neocam.vision as V
    import neocalib

    class Shot:
        def __init__(self, f): self.frame = f
    monkeypatch.setattr(neocalib, "grab_settled", lambda grab, **kw: Shot(grab()))
    monkeypatch.setattr(V, "orient_head", lambda raw: raw)
    t = Table(x0, y0, w, h, parts)
    json.dump([{"id": 3, "x0": x0, "y0": y0, "x1": x0 + w, "y1": y0 + h}], open(tmp_path / "bulk.json", "w"))
    return BF.BulkFeeder(str(tmp_path), Vis(t), t, Cfg(), lambda s: None), t


def test_grid_covers_with_fewest():
    fov = 1024 / K
    way, xs, ys = BF.scan_grid(60, 82, 150, 193, fov)                  # a 25 x 46 pocket less its 1.5 mm margin
    assert (len(xs), len(ys)) == (3, 6) and len(way) == 18             # was 4 x 7 = 28
    for a, b, c in ((60, 82, xs), (150, 193, ys)):
        assert c[0] - fov / 2 <= a + 1e-6 and c[-1] + fov / 2 >= b - 1e-6          # edge to edge
        assert all(c[i + 1] - c[i] <= fov - BF.OVERLAP_MM + 1e-6 for i in range(len(c) - 1))   # neighbours overlap
    way, xs, ys = BF.scan_grid(0, 34, 0, 43, fov)                      # the wide pocket
    assert (len(xs), len(ys)) == (5, 6)
    assert BF.scan_grid(0, 5, 0, 5, fov)[0] == [(2.5, 2.5)]            # smaller than one picture: one, in its middle
    d = [abs(way[i + 1][0] - way[i][0]) + abs(way[i + 1][1] - way[i][1]) for i in range(len(way) - 1)]
    assert max(d) < fov                                                 # a snake: every move to a neighbour


def test_scan_finds_every_part(tmp_path, monkeypatch):
    # parts all over the pocket, some on the seams between two pictures, turned
    parts = [(62.5, 152.5, 3.0, 1.5, 0), (79.5, 190.5, 3.0, 1.5, 90), (71.0, 171.5, 5.0, 4.0, 30), (66.9, 158.2, 3.0, 1.5, -20),
             (75.1, 166.0, 2.0, 1.2, 45), (63.0, 185.0, 5.0, 4.0, 0), (78.0, 157.5, 3.0, 1.5, 10), (70.5, 181.9, 2.0, 1.2, 0)]
    bf, t = feeder(tmp_path, monkeypatch, parts)
    found = bf.scan(3)
    assert len(found) == len(parts), found
    for x, y, L, W, a in parts:
        f = min(found, key=lambda f: (f["x"] - x) ** 2 + (f["y"] - y) ** 2)
        assert abs(f["x"] - x) < 0.15 and abs(f["y"] - y) < 0.15, (x, y, f)
        assert abs(f["L"] - L) < 0.3 and abs(f["W"] - W) < 0.3, (L, W, f)
    assert len(t.moves) == 18 and not any(slow for _, slow in t.moves)             # fewest pictures, normal speed
    assert os.path.exists(tmp_path / "bulk_3.png") and bf.container(3)["count"] == len(parts)


def test_look_stays_gentle(tmp_path, monkeypatch):
    bf, t = feeder(tmp_path, monkeypatch, [(71.0, 171.5, 5.0, 4.0, 30)])
    bf.head_at(71.0, 171.5)
    assert t.moves[-1][1] is True                                       # the exact look at one part: the slow move


# ---- real pockets, packed full (holder "PP feeder 20", 2026-11-19): the stitched scan pictures at 1/4 size ----
POCKETS = {18: (8, 6.8, 6.1), 19: (9, 10.0, 4.1), 20: (6, 11.8, 7.7)}     # whole parts to find, their body L x W mm


def test_packed_pockets():
    """parts lying against each other, against the wall, leads into leads: every whole body found alone
    (the search before found 4 of 9 in pocket 19, one of them two bodies as one)"""
    for cid, (n, L, W) in POCKETS.items():
        mos = cv2.imread(os.path.join(ROOT, "tests", "data", "bulk", f"pocket_mos{cid}.png"), 0)
        bs = [b for b in BF.find_body_rects(mos, K * 0.25) if not (b["cut"] and b["odd"])]
        assert len(bs) == n, (cid, [(b["L"], b["W"]) for b in bs])
        for b in bs:
            assert abs(b["L"] - L) < 0.08 * L and abs(b["W"] - W) < 0.08 * W, (cid, b["L"], b["W"])
        for i, a in enumerate(bs):                                  # no two boxes on the same body
            for b in bs[i + 1:]:
                assert np.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"]) > 0.6 * W * K * 0.25, (cid, a, b)
        # the taught size given: the same bodies
        bs2 = [b for b in BF.find_body_rects(mos, K * 0.25, size=(L, W)) if not (b["cut"] and b["odd"])]
        assert len(bs2) == n, (cid, len(bs2))
