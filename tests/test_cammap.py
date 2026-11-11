"""neocalib.cammap: a camera with a lens (32.15 px/mm in the middle, more at the corners), X 1 % stronger
than Y, turned 0.4 deg: the map fitted from a grid brings every point onto an exact k px/mm grid; it is
used only when switched on."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocalib import cammap as M  # noqa: E402


def _camera(d):
    th = np.radians(0.4)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    d = np.atleast_2d(d)
    s = 32.15 * (1 + 0.0004 * (d ** 2).sum(1, keepdims=True))
    return np.array([512, 512]) + ((d * s * np.array([1.01, 1.0])) @ R.T) * np.array([1, -1])


def test_map_straightens():
    import cv2
    k = 34.06
    g = np.linspace(-12, 12, 7)
    D = np.array([(x, y) for x in g for y in g])
    m = M.fit(D, _camera(D) + np.random.default_rng(1).normal(0, 0.15, D.shape), k)
    assert m["rms_px"] < 0.3 and m["max_linear_px"] > 10, m          # the lens: one matrix is not enough
    assert m["scale_corner"][0] > m["scale_centre"][0] + 1
    raw = np.zeros((1024, 1024), np.uint8)
    pts = np.array([(x, y) for x in np.linspace(-10, 10, 5) for y in np.linspace(-10, 10, 5)])
    for u, v in _camera(pts):
        cv2.circle(raw, (int(round(u)), int(round(v))), 6, 255, -1)
    d = tempfile.mkdtemp()
    assert M.apply(raw, 0, d) is raw                                   # no map: as it is
    M.save(d, 0, m)
    assert M.apply(raw, 0, d) is raw                                   # a new map starts off
    M.save(d, 0, on=True)
    out = M.apply(raw, 0, d)
    n, lab, st, cen = cv2.connectedComponentsWithStats((out > 100).astype(np.uint8))
    ideal = np.array(m["c"]) + (pts @ np.array(m["Q"]).T) * k
    err = max(min(np.linalg.norm(cen[1:] - q, axis=1)) for q in ideal)
    assert n - 1 == 25 and err < 1.0, (n, err)                         # every dot where k px/mm puts it
    w = M.apply(raw[112:912, 112:912].copy(), 0, d)                    # an 800 window: the same map, cropped
    assert w.shape == (800, 800) and abs(int(w.astype(int).sum()) - int(out[112:912, 112:912].astype(int).sum())) < 0.02 * int(out.astype(int).sum())
