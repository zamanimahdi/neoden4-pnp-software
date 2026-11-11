"""Tests for the repeatability test, the error map and the board transform.

    python neocalib/tests/test_xy.py

The machine simulator gets real XY errors (scale per axis, skew, pulley runout, backlash);
the "camera" reports where a feature is relative to the head's TRUE position (sim.xy_true_mm),
so the calibrations must find those errors and the correction must remove them.
"""
import math
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from neomach import Mainboard  # noqa: E402
from neocalib import BoardTransform, ErrorMap, measure_grid, repeatability  # noqa: E402
from neocalib.xyerror import rot  # noqa: E402


def machine(**err):
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    mb.home_xy()
    for k, v in err.items():
        setattr(sim, k, v)
    return mb, sim


def point_measure(sim, feature, noise=0.0015, seed=0):
    """Feature at a fixed machine position; reading = feature - true head position + noise."""
    rng = np.random.default_rng(seed)
    f = np.array(feature, float)

    def measure():
        return tuple(f - np.array(sim.xy_true_mm()) + rng.normal(0, noise, 2))
    return measure


def test_repeatability():
    mb, sim = machine(backlash=[0.04, 0.02])
    target = (150.0, 120.0)
    mb.move_xy(*target)
    r = repeatability(mb, point_measure(sim, (150.3, 119.8)), target, cycles=5, settle_s=0.0)
    print("repeatability:", r.summary())
    # the carriage lags behind its motion: approached from +X it sits +b/2, from -X -b/2,
    # so the feature reading differs by -b (feature - head)
    assert abs(abs(r.backlash_x) - 0.04) < 0.004 and abs(abs(r.backlash_y) - 0.02) < 0.004, r
    assert r.spread_direct > 0.035 and r.spread_one_sided < 0.012, r
    mb.close()


def test_error_map():
    err = dict(err_scale=[1.003, 0.998], err_skew_deg=0.05, err_periodic=(0.03, 30.48), backlash=[0.03, 0.03])
    mb, sim = machine(**err)
    pitch, nx, ny = 10.0, 11, 9
    plate_origin, plate_angle = np.array([140.0, 130.0]), 0.7          # where the plate really lies
    R = rot(plate_angle)
    feat = lambda i, j: plate_origin + R @ np.array([i * pitch, j * pitch])   # noqa: E731
    rng = np.random.default_rng(5)
    state = {}

    def measure():                                    # nearest plate feature to the true head position
        h = np.array(sim.xy_true_mm())
        g = np.linalg.solve(R, h - plate_origin) / pitch
        i, j = int(round(g[0])), int(round(g[1]))
        state["ij"] = (i, j)
        return tuple(feat(i, j) - h + rng.normal(0, 0.0015, 2))
    # the user teaches node (0, 0) roughly (a little off) and the plate angle roughly
    em = measure_grid(mb, measure, plate_origin + (0.2, -0.15), pitch, nx, ny, angle_deg=0.6, settle_s=0.0)
    print("error map:", em.summary())
    assert abs(em.scale_x - 1.003) < 0.0003 and abs(em.scale_y - 0.998) < 0.0003, em.summary()
    assert abs(em.skew_deg - 0.05) < 0.01, em.summary()
    assert em.max_um > 20                             # the pulley runout is left for the table

    def true_error(points, use_map):
        mb.xy_map = em.correct if use_map else None
        errs = []
        for (x, y) in points:
            mb.move_xy(x, y, slow=True)
            # the target is a real (true) position relative to the plate frame anchor (grid centre)
            errs.append(np.linalg.norm(np.array(sim.xy_true_mm()) - target_true(x, y)))
        mb.xy_map = None
        return max(errs) * 1000

    # The map's frame equals the machine's true frame up to a shift (translation cannot be
    # seen by this calibration; the board fiducials take care of it). Find the shift at the
    # map's reference point, then the head must truly be at target + shift.
    ref = np.array(em.ref)
    mb.move_xy(*ref, slow=True)
    shift = np.array(sim.xy_true_mm()) - np.array(em.true_from_commanded(*ref))

    def target_true(x, y):
        return np.array([x, y]) + shift
    # between nodes: linear interpolation of the pulley runout (period 30.5 mm, 10 mm grid)
    # limits it to about A (pi h / lambda)^2 / 2 = 16 um here; on the nodes it is only noise
    mid = [tuple(feat(i, j) + np.array([3.3, 4.1])) for i in range(0, nx - 1, 3) for j in range(0, ny - 1, 2)]
    on = [tuple(feat(i, j)) for i in range(1, nx - 1, 3) for j in range(1, ny - 1, 3)]
    b_mid, a_mid = true_error(mid, False), true_error(mid, True)
    b_on, a_on = true_error(on, False), true_error(on, True)
    print(f"error map: worst placement error between nodes {b_mid:.0f} -> {a_mid:.1f} um, "
          f"on nodes {b_on:.0f} -> {a_on:.1f} um (without -> with the map)")
    assert b_mid > 100 and a_mid < 25 and a_on < 8
    f = os.path.join(tempfile.mkdtemp(), "errmap.json")
    em.save(f)
    em2 = ErrorMap.load(f)
    assert np.allclose(em2.correct(160, 150), em.correct(160, 150))
    mb.close()


def test_board_transform():
    # a real board: rotated 1.2 deg, scaled +0.3 % / -0.2 % (machine or PCB), a little skew
    A = rot(1.2) @ np.array([[1.003, 0.0005], [0.0, 0.998]])
    t = np.array([152.0, 98.0])
    true = lambda p: A @ np.asarray(p, float) + t    # noqa: E731
    marks = [(2, 3), (118, 4), (117, 76), (3, 75)]
    meas = [true(p) for p in marks]
    parts = [(x, y) for x in range(10, 115, 15) for y in range(8, 75, 11)]

    def worst(tr):
        return max(np.linalg.norm(np.array(tr.apply(*p)) - true(p)) for p in parts) * 1000
    neoden = worst(BoardTransform(marks[:2], meas[:2], two_mark_scale="none"))
    line = worst(BoardTransform(marks[:2], meas[:2]))
    full = BoardTransform(marks, meas)
    print(f"board transform, worst part error: NeoDen (2 marks, no scale) {neoden:.0f} um, "
          f"2 marks + line scale {line:.0f} um, 4 marks affine {worst(full):.3f} um")
    print("  ", full.summary())
    assert worst(full) < 0.01 and line < neoden
    assert abs(full.angle_at() - 1.2) < 0.03
    # a misread mark shows in the residuals
    bad = list(meas)
    bad[2] = bad[2] + np.array([0.3, 0.0])
    assert BoardTransform(marks, bad).bad_marks(0.05)
    # one mark: shift only
    one = BoardTransform([(5, 5)], [(155, 105)])
    assert one.apply(10, 10) == (160.0, 110.0)


if __name__ == "__main__":
    test_repeatability()
    test_error_map()
    test_board_transform()
    print("all ok")


def test_xy_correction_from_marks():
    """a machine with leaning Y and short axes: the marks of a precise board give the correction that
    puts the head truly on every point (the board's own rotation does not matter)"""
    import math
    from neocalib import XYCorrection
    L = np.array([[0.9994, 0.0027], [0.0, 0.9991]])
    ref = np.array([400.0, 211.0])
    a = math.radians(0.3)
    Rb = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    design = [(16, 99), (66, 50), (94, 99), (144, 50), (16, 198), (144, 198)]
    true = [Rb @ np.array(d) + (163, 81) for d in design]           # the board lies turned 0.3 deg
    cmd = [ref + L @ (t - ref) for t in true]                         # where the machine had to go
    c = XYCorrection.from_marks(design, cmd, ref, limits=[416.0, 400.0])
    assert abs(c.skew_deg - math.degrees(math.atan(0.0027 / 0.9991))) < 1e-6 and abs(c.scale_x - 0.9994) < 1e-9
    for p in [(100.0, 50.0), (300.0, 350.0), (50.0, 380.0)]:
        assert np.allclose(c.correct(*p), ref + L @ (np.array(p) - ref), atol=1e-9)
