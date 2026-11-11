"""bulk parts under the head camera (neocam.bulk), on real pictures of the machine (one part centred per
picture, BOARD-A day 2026-11-03): body, leads per side and pitch; the package by a taught example;
the direction of a part whose leads are not symmetric (SOT-23-5: 3 + 2)"""
import os
import sys

import cv2

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.bulk import analyze, teach, match  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "data", "bulk")
K = 115.2


def part(i):
    return analyze(cv2.imread(os.path.join(D, f"c{i:02d}.png"), 0), K)


def test_leads_and_bodies():
    soic = part(15)
    assert sorted(soic.leads[:2]) == [4, 4] and abs(soic.pitch - 1.27) < 0.06, soic.leads
    assert 4.8 < soic.L < 5.3 and 3.8 < soic.W < 4.3, (soic.L, soic.W)
    tssop = part(2)
    assert tssop.leads[:2] == [10, 10] and abs(tssop.pitch - 0.65) < 0.04, tssop.leads
    sot = part(5)
    assert sorted(sot.leads[:2]) == [2, 3] and abs(sot.pitch - 0.95) < 0.08, sot.leads


def test_package_and_direction():
    T = [teach(part(15), "SOIC-8"), teach(part(2), "TSSOP-20"), teach(part(5), "SOT-23-5"), teach(part(6), "TO-277"),
         teach(part(10), "square"), teach(part(3), "chip")]
    for i, name in ((13, "SOIC-8"), (9, "SOT-23-5")):
        t, dist, turn, conf = match(part(i), T)
        assert t["name"] == name and dist < 0.3, (i, t["name"], dist)
    t, dist, turn, conf = match(part(9), T)
    assert turn == 180 and conf > 0.3, (turn, conf)          # 2 + 3 against the taught 3 + 2


# ---- from below (up camera, exposure 300): parts of the bulk container held by nozzle 1, put back
# after (2026-11-03): the metal pattern (leads, the exposed pad, tabs) against a taught part
import math  # noqa: E402

import numpy as np  # noqa: E402

from neocam.bulk import analyze_bottom, teach_bottom, match_bottom  # noqa: E402

KU = 32.14


def bottom(i):
    return cv2.imread(os.path.join(D, f"b{i:02d}.png"), 0)


def test_bottom_patterns():
    for i, n in ((5, 5), (9, 5), (6, 3), (14, 3), (15, 9), (13, 9)):    # SOT-23-5: 5 leads; TO-277: 3 pads; SOIC-8 EP: 8 + pad
        b = analyze_bottom(bottom(i), KU)
        assert b.ok and len(b.blobs) == n, (i, len(b.blobs))


def test_bottom_match_and_direction():
    sot = teach_bottom(analyze_bottom(bottom(5), KU), "SOT-23-5")
    m = match_bottom(analyze_bottom(bottom(9), KU), sot)
    assert m["missing"] == 0 and m["extra"] == 0 and m["sure"] >= 2, m          # 3 + 2 leads: the turn is known
    to = teach_bottom(analyze_bottom(bottom(6), KU), "TO-277")
    m = match_bottom(analyze_bottom(bottom(14), KU), to)
    assert m["missing"] == 0 and m["sure"] >= 2 and m["rms"] < 0.08, m
    so = teach_bottom(analyze_bottom(bottom(15), KU), "SOIC-8 EP")
    m = match_bottom(analyze_bottom(bottom(13), KU), so)
    assert m["missing"] == 0 and m["sure"] == 0, m                               # symmetric underside: pin 1 from the top


def test_bottom_missing_lead():
    """one lead of a SOIC-8 painted over with the nozzle's grey: one missing (the part goes to the trash)"""
    img = bottom(13)
    b = analyze_bottom(img, KU)
    so = teach_bottom(analyze_bottom(bottom(15), KU), "SOIC-8 EP")
    lead = min((q for q in b.blobs if q[2] < 1.0), key=lambda q: q[0])               # a lead at one end
    a = math.radians(b.angle)
    x0, y0 = (img.shape[1] - 1024) / 2 + 512, (img.shape[0] - 1024) / 2 + 512      # this crop is the picture's middle
    X = b.cx + (lead[0] * math.cos(a) - lead[1] * math.sin(a)) * KU
    Y = b.cy - (lead[0] * math.sin(a) + lead[1] * math.cos(a)) * KU
    m_ = img.copy()
    cv2.circle(m_, (int(X), int(Y)), int(0.45 * KU), int(np.percentile(img, 30)), -1)
    m = match_bottom(analyze_bottom(m_, KU), so)
    assert m["missing"] == 1, m


def test_pin1_bevel_direction():
    """SOIC-8: the bevel along the pin-1 row; parts 15, 13, 4 have it on one side, 11, 7, 1 on the other
    (read by eye on the pictures): the turn against any one of them as the taught part is right"""
    side = {15: -1, 13: -1, 4: -1, 11: 1, 7: 1, 1: 1}
    P = {i: part(i) for i in side}
    for ref in (15, 11, 7):
        T = [teach(P[ref], "SOIC-8")]
        for i in side:
            t, dist, turn, conf = match(P[i], T)
            assert turn == (0 if side[i] == side[ref] else 180), (ref, i, turn, conf, P[i].how)
    # the feeder's pin-1 test switched off: the picture decides (no claim on these)
    t, dist, turn, conf = match(P[4], [teach(P[15], "SOIC-8")], use_pin1=False)
    assert P[4].how == "picture"


def test_to277_direction_by_its_tabs():
    """TO-277: one wide tab on one end, two on the other (only a little lighter than the floor): the
    ends are read for wide terminals too, and the tabs alone give the direction"""
    a, b = part(6), part(14)
    assert a.leads[2:] == [2, 1] and b.leads[2:] == [2, 1], (a.leads, b.leads)
    t, dist, turn, conf = match(b, [teach(a, "TO-277")])
    assert b.how == "leads" and turn == 0 and conf == 1.0


def test_fixed_direction():
    """a container whose parts the operator lays all the same way: no package needed, only the small
    turn is measured, the side is the one laid (a part read at -178 deg, laid at 0: turned +2)"""
    import json
    import tempfile
    from types import SimpleNamespace
    from neojob.bulkfeed import BulkFeeder
    d = tempfile.mkdtemp()
    json.dump([{"id": 3, "name": "by hand", "orient": "fixed", "fixed_angle": 0, "parts": [{"x": 10.0, "y": 20.0}]}],
              open(os.path.join(d, "bulk.json"), "w"))
    bf = BulkFeeder(d, None, None, None, lambda t: None)
    part = SimpleNamespace(ok=True, cx=500, why="", touching=False, angle=-178.0, how="")
    bf.look = lambda x, y: (part, (x + 0.01, y), None)
    x, y, phi, p = bf.next_part(3)
    assert abs(x - 10.01) < 1e-9 and abs(phi - 2.0) < 1e-9, (x, phi)
    part.angle = 87.0                                   # read 90 off (a square part): the side nearest the laid one
    bf.queue[3] = [{"x": 1.0, "y": 1.0}]
    assert abs(bf.next_part(3)[2] - (-3.0)) < 1e-9


def test_layout_matrix():
    """the bulk feeders from a matrix: numbered row by row from block 1, the step from block to block;
    a block's own settings kept, its last scan dropped when it moves"""
    import json
    import tempfile
    from types import SimpleNamespace
    from neoserver.bulk import BulkService
    d = tempfile.mkdtemp()
    json.dump([{"id": 2, "name": "SOIC", "package": "SOIC-8", "x0": 0, "y0": 0, "x1": 1, "y1": 1, "parts": [{"x": 0, "y": 0}], "count": 1}],
              open(os.path.join(d, "bulk.json"), "w"))
    mc = SimpleNamespace(_owner=lambda: "me", job_running=False)
    bs = BulkService(SimpleNamespace(neoden_dir=d), mc, None, lambda *a: None)
    r = bs.set_grid({"client": "me"}, {"cols": 3, "rows": 2, "x0": 200, "y0": 40, "w": 40, "h": 25, "px": 45, "py": 30})
    cs = {c["id"]: c for c in json.load(open(os.path.join(d, "bulk.json")))}
    assert r["containers"] == 6 and sorted(cs) == [1, 2, 3, 4, 5, 6]
    assert (cs[2]["x0"], cs[2]["y0"], cs[2]["x1"], cs[2]["y1"]) == (245.0, 40.0, 285.0, 65.0) and cs[2]["package"] == "SOIC-8"
    assert "parts" not in cs[2] and (cs[4]["row"], cs[4]["col"], cs[4]["x0"], cs[4]["y0"]) == (2, 1, 200.0, 70.0)
    try:
        bs.set_grid({"client": "me"}, {"cols": 6, "rows": 6, "w": 10, "h": 10, "px": 12, "py": 12})
        assert False, "36 blocks"
    except ValueError:
        pass
