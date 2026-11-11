"""neojob.learn: the learnt pick point, the counts and warnings, where a placed part landed"""
import math
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from neojob.learn import PickLearner, Stats, placed_part, read_stats, summary  # noqa: E402


def test_pick_learner():
    """the mean offset of every 5 parts moves the pick point (80 % of it); a part far off is not
    learnt; the point stays within the limit; kept in a file"""
    p = os.path.join(tempfile.mkdtemp(), "job.csv.picklearn.json")
    pl = PickLearner(p, samples=5, max_mm=0.5)
    for _ in range(4):
        assert pl.add(7, 0.10, -0.05) is None
    assert pl.add(7, 2.0, 0.0) is None                       # picked badly: not counted
    assert pl.add(7, 0.10, -0.05) == (0.08, -0.04)
    for _ in range(15):
        pl.add(7, 0.4, 0.0)                                   # it keeps moving: clamped to 0.5 mm in all
    dx, dy = pl.correction(7)
    assert abs(math.hypot(dx, dy) - 0.5) < 1e-3, (dx, dy)
    pl.save()
    assert PickLearner(p).correction(7) == (dx, dy) and PickLearner(p).correction(8) == (0.0, 0.0)


def test_stats_and_warnings():
    d = tempfile.mkdtemp()
    st = Stats(d)
    for i in range(30):
        st.add("pick", feeder=3, nozzle=2, part="10K/0603", ok=i % 4 != 0)      # 25 % fail
        st.add("pick", feeder=5, nozzle=1, part="1K/0603", ok=True)
        st.add("place", feeder=5, nozzle=1, part="1K/0603")
    w = st.warn(15.0)
    assert any("nozzle 2" in x for x in w) and any("feeder 3" in x for x in w) and not any("nozzle 1" in x for x in w), w
    assert st.warn(15.0) == []                                # once per job
    st.add("job", seconds=600, placed=30)
    s = summary(read_stats(d, 1))
    noz = {r["key"]: r for r in s["nozzle"]}
    assert noz["2"]["picks"] == 30 and noz["2"]["pick_fail"] == 8 and noz["1"]["placed"] == 30
    assert s["placed"] == 30 and s["per_hour"] == 180 and any("nozzle 2" in x for x in s["warnings"])


def test_placed_part():
    """a 1.6 x 0.8 mm part 0.2 mm right, 0.1 mm down, turned 5 deg: found from the before / after pictures"""
    k = 115.2
    rng = np.random.default_rng(3)
    before = np.clip(60 + rng.normal(0, 6, (800, 800)), 0, 255).astype(np.uint8)
    cv2.rectangle(before, (100, 380), (700, 420), 160, -1)    # a trace on the board
    after = before.copy()
    box = cv2.boxPoints(((400 + 0.2 * k, 400 + 0.1 * k), (1.6 * k, 0.8 * k), -5.0)).astype(np.int32)
    cv2.fillPoly(after, [box], 20)
    r = placed_part(before, after, k, 1.6, 0.8)
    assert r["found"] and abs(r["dx"] - 0.2) < 0.02 and abs(r["dy"] + 0.1) < 0.02, r
    assert abs(((r["angle"] - 5.0) + 90) % 180 - 90) < 1.0 and abs(r["L"] - 1.6) < 0.1, r
    assert not placed_part(before, before.copy(), k, 1.6, 0.8)["found"]       # nothing placed


def test_place_check_fields():
    from neojob.jobfile import _check
    assert _check(" Photo ") == "photo" and _check("both") == "both" and _check("") == "" and _check("x") == ""


def test_round_things_are_circles():
    """the tape finder reports a round pad / hole as round (drawn as a circle), a square pocket not"""
    from neocam.tape import find_pockets
    k = 115.2
    img = np.full((800, 800), 150, np.uint8)
    cv2.circle(img, (250, 400), int(0.75 * k), 30, -1)                 # a 1.5 mm round hole
    cv2.rectangle(img, (480, 330), (480 + int(1.2 * k), 330 + int(1.2 * k)), 30, -1)   # a 1.2 mm square pocket
    f = find_pockets(img, k)
    rnd = [q for q in f if q.get("round")]
    sq = [q for q in f if not q.get("round")]
    assert len(rnd) == 1 and abs(rnd[0]["px"] - 250) < 3 and abs(rnd[0]["r"] - 0.75) < 0.05, f
    assert sq and abs(sq[0]["px"] - (480 + 0.6 * k)) < 3, f


def test_pads_axis_and_across():
    """a part's long side must lie along the line of its pads: 2 pads, two rows (SOIC), square = none"""
    from neoserver.align import pads_axis, d180
    assert abs(pads_axis([[0, 0, 0.6, 0.9], [1.5, 0, 0.6, 0.9]]) - 0) < 1e-6           # 0603 along X
    assert abs(pads_axis([[0, 0, 0.9, 0.6], [0, 1.5, 0.9, 0.6]]) - 90) < 1e-6          # along Y
    assert abs(pads_axis([[0, 0, 1.0, 1.0], [1.0, 1.0, 1.0, 1.0]]) - 45) < 1e-6        # round pads: their line
    soic = [[x * 1.27, y, 0.6, 1.5] for x in range(4) for y in (0, 5.2)]
    assert abs(d180(pads_axis(soic), 0)) < 1e-6
    qfn = [[x, y, 0.3, 0.8] for x in (-2, 2) for y in (-1, 0, 1)] + [[y, x, 0.8, 0.3] for x in (-2, 2) for y in (-1, 0, 1)]
    assert pads_axis(qfn) is None
    # a part lying along X on the nozzle, rotation 90, pick angle 0: on the board along Y -> across X pads
    assert abs(d180((0 + 90 - 0) % 180, 0)) > 45 and abs(d180((0 + 90 - 90) % 180, 0)) < 45
