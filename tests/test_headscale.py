"""The head camera's scale for one board: a row of pads whose Gerber spacing alternates (0.660 / 0.635, as the
BOARD-B's TSSOP20) read at a known px/mm; the job's `headscale` line replaces Config's for that job."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neoserver import feedersetup as F  # noqa: E402


def test_row_scale_alternating_pitch():
    import cv2
    true_k, k0 = 116.8, 115.2                                  # the camera's real scale / what Config says
    xs = [0.0]
    for i in range(9):
        xs.append(xs[-1] + (0.660 if i % 2 == 0 else 0.635))
    xs = np.array(xs) - np.mean(xs)
    img = np.full((1024, 1024), 60, np.uint8)
    for x in xs:                                               # 0.36 x 1.31 mm pads, the row 2.87 mm above the centre
        u, v = 512 + x * true_k, 512 - 2.87 * true_k
        cv2.rectangle(img, (int(u - 0.18 * true_k), int(v - 0.65 * true_k)), (int(u + 0.18 * true_k), int(v + 0.65 * true_k)), 220, -1)
    pads = [[float(x), 2.87, 0.362, 1.312] for x in xs]
    rows = F._rows(pads, 0)
    assert len(rows) == 1 and len(rows[0][1]) == 10, rows
    cross, pos, pitch, size = rows[0]
    v = 512 - (cross - 0.0) * k0
    r = F._row_scale(img, [(g, 512 + g * k0) for g in pos], 512 - cross * true_k, 0.35 * size * k0, pitch * k0)
    assert r and abs(r[0] - true_k) < 0.3, r                   # the line through the pads, not the period


def test_job_head_scale():
    from neocam.config import NeoConfig
    d = tempfile.mkdtemp()
    p = os.path.join(d, "j.csv")
    open(p, "w").write("pcb,Manual,Lock,100,100,350,150,Back,10,10,0,\nheadscale,116.830,U3 2026-11-05,\n")
    assert F.job_head_scale(p) == 116.83
    ini = os.path.join(d, "Config.ini")
    open(ini, "w").write("[Vision]\nfHeadPxPerMm=115.2\n")
    cfg = NeoConfig.load(ini)
    k = cfg.pxmm(1)
    assert F.apply_head_scale(cfg, p) == 116.83 and cfg.pxmm(1) == 116.83 and k != 116.83
    open(p, "w").write("pcb,Manual,Lock,100,100,350,150,Back,10,10,0,\n")
    assert F.job_head_scale(p) is None
