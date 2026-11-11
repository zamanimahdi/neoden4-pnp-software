"""Up-camera vision on the leads (neojob.leadvision) on synthetic images.

    python tests/test_leadvision.py

A part is drawn on the nozzle as the up camera sees it (34.5 px/mm): a dark body and bright
leads, the body moulded off-centre against its leads (as real parts are), the part picked
off-centre and turned. The leads must be found where they are, so they go on the pads;
NeoDen's body outline is shown next to it, off by the moulding error.
"""
import math
import os
import sys

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.config import NeoConfig  # noqa: E402
from neocam import vision as V  # noqa: E402
from neojob.leadvision import analyze_leads, local_leads, annotate  # noqa: E402

K = 34.5
S = 1024


def draw(leads_true, lead_size, body, seed=0):
    """leads_true: [(x, y)] mm on the nozzle frame; body: (cx, cy, w, h, angle) mm"""
    bg = np.full((S, S), 30, np.uint8)
    cv2.circle(bg, (S // 2, S // 2), int(0.6 * K), 90, -1)                  # the empty nozzle tip
    img = bg.copy()
    to = lambda x, y: (S / 2 + x * K, S / 2 - y * K)
    cx, cy, w, h, a = body
    cv2.fillPoly(img, [np.round(cv2.boxPoints((to(cx, cy), (w * K, h * K), -a))).astype(np.int32)], 105)
    for (x, y), (lw, lh, la) in zip(leads_true, lead_size):
        cv2.fillPoly(img, [np.round(cv2.boxPoints((to(x, y), (lw * K, lh * K), -la))).astype(np.int32)], 235)
    rng = np.random.default_rng(seed)
    noisy = lambda m: np.clip(cv2.GaussianBlur(m, (3, 3), 0.8) + rng.normal(0, 3, m.shape), 0, 255).astype(np.uint8)
    return noisy(img), noisy(bg)


def turn(pts, deg, off):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(c * x - s * y + off[0], s * x + c * y + off[1]) for x, y in pts]


def sot23():
    return [[-0.95, -1.1, 0.6, 0.7], [0.95, -1.1, 0.6, 0.7], [0.0, 1.1, 0.6, 0.7]]


def qfp64():
    out = []
    for i in range(16):
        o = (i - 7.5) * 0.5
        out += [[o, -7.7, 0.28, 1.5], [o, 7.7, 0.28, 1.5], [-7.7, o, 1.5, 0.28], [7.7, o, 1.5, 0.28]]
    return out


def main():
    cfg = NeoConfig({}, None)
    cases = (("SOT-23", sot23(), 90.0, 0.0, (0.12, -0.08), 3.0, (0.4, 0.5), (2.9, 1.3), (0.15, 0.1)),
             ("QFP-64", qfp64(), 0.0, 90.0, (-0.10, 0.07), -1.2, (0.22, 1.0), (10.0, 10.0), (0.25, -0.2)))
    for name, pads, stack_angle, comp_rot, off, dang, lsz, bsz, mould in cases:
        # pads in the job frame: the footprint turned by comp_rot, at (50, 60)
        jp = [[*turn([(p[0], p[1])], comp_rot, (50.0, 60.0))[0], p[2], p[3]] for p in pads]
        leads, centre = local_leads(jp, comp_rot)
        # on the nozzle: footprint turned by stack angle + the pick error, picked off-centre
        true_leads = turn([(x, y) for x, y, _ in leads], stack_angle + dang, off)
        sizes = []
        for p in pads:
            w, h = (lsz[0], lsz[1]) if p[2] < p[3] else (lsz[1], lsz[0])
            sizes.append((w, h, stack_angle + dang))
        body = (off[0] + mould[0], off[1] + mould[1], bsz[0], bsz[1], stack_angle + dang)
        img, bg = draw(true_leads, sizes, body)
        r = analyze_leads(img, bg, 450, K, leads, stack_angle, cfg)
        assert r.valid, (name, r.message)
        assert abs(r.x - off[0]) < 0.02 and abs(r.y - off[1]) < 0.02 and abs(r.angle - dang) < 0.3, (name, r.as_dict())
        b = V.analyze_nozzle(img, bg, 450, K)
        print(f"{name}: leads {r.message}  |  body outline (NeoDen): centre {b.x:+.3f}, {b.y:+.3f} mm "
              f"(true leads {off[0]:+.3f}, {off[1]:+.3f}, turn {dang:+.1f})")
        assert annotate(img, r, K)
    # nothing on the nozzle
    img, bg = draw([], [], (0, 0, 0.01, 0.01, 0))
    r = analyze_leads(img, bg, 450, K, local_leads([[0, 0, 1, 1], [2, 0, 1, 1]], 0)[0], 0, cfg)
    assert not r.valid
    print("lead vision: OK")


if __name__ == "__main__":
    main()
