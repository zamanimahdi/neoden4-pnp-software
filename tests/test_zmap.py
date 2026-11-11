"""Board height map: linear between the measured points, nozzle differences, the board as a floor."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neojob import zmap as Z  # noqa: E402
from neoserver.zmap_cal import bare_points  # noqa: E402

GRID = [(x, y) for y in (95, 181, 268) for x in (172, 221, 269, 318)]


def plane(x, y):
    return 10.5 + 0.001 * (x - 240) + 0.0008 * (y - 180)


def test_linear_between_points():
    m = Z.ZMap([{"x": x, "y": y, "z": plane(x, y)} for x, y in GRID])
    for x, y in ((200, 120), (300, 250), (160, 80), (330, 280)):     # inside and outside the points
        assert abs(m.z(x, y) - plane(x, y)) < 1e-9
    # a bump at one point: straight lines from it to its neighbours, nothing beyond them
    P = [{"x": x, "y": y, "z": 10.5} for x, y in GRID]
    P[5]["z"] = 10.7                                                 # (221, 181)
    m = Z.ZMap(P)
    assert abs(m.z(221, 181) - 10.7) < 1e-9
    assert abs(m.z(245, 181) - 10.6) < 0.01                          # half way to (269, 181)
    assert abs(m.z(318, 268) - 10.5) < 1e-9
    assert [i for i, _ in m.odd()] == [5]


def test_nozzles_and_floor():
    P = [{"x": x, "y": y, "z": 10.5} for x, y in GRID]
    m = Z.ZMap(P, True, {"2": 0.12, "3": -0.05})
    assert abs(m.delta(200, 200, 1)) < 1e-9 and abs(m.delta(200, 200, 2) - 0.12) < 1e-9
    hz = Z.Heights(m, 8.3)                                           # this board: 8.3 where the machine is at its middle
    assert abs(hz.touch(200, 200, 3) - 8.25) < 1e-9
    assert abs(hz.floor(200, 200, 3, 0.5) - (8.25 - 0.5 + Z.PRESS)) < 1e-9
    off = Z.Heights(Z.ZMap(P, False, {"2": 0.12}), 8.3)
    assert off.delta(200, 200, 2) == 0.0 and abs(off.touch(200, 200, 2) - 8.42) < 1e-9   # off: no shift, the floor stays
    assert Z.Heights(m, None).floor(200, 200) is None


def test_machine_map_and_board_height():
    d = tempfile.mkdtemp()
    job = os.path.join(d, "Proc", "j.csv")
    os.makedirs(os.path.dirname(job))
    assert Z.load(job, d) is None
    Z.save_machine(d, {"points": [{"x": x, "y": y, "z": plane(x, y)} for x, y in GRID], "on": True, "nozzles": {"4": 0.2}})
    hz = Z.load(job, d)
    assert hz.board_z is None and hz.touch(200, 200) is None and abs(hz.delta(240, 180, 4) - (plane(240, 180) - hz.machine.ref + 0.2)) < 1e-9
    Z.save(job, {"board_z": 9.0})
    hz = Z.load(job, d)
    assert abs(hz.touch(240, 180, 1) - (9.0 + plane(240, 180) - hz.machine.ref)) < 1e-9


def test_bare_points_off_the_parts():
    parts = {f"R{i}": {"pads": [[x, y, 5.0, 5.0]]} for i, (x, y) in enumerate((x, y) for x in range(0, 101, 10) for y in range(0, 61, 10))}
    pts = [p for p in bare_points(parts, 3, 2) if p]
    assert len(pts) == 6
    for x, y in pts:                                                 # 1.5 mm clear of every 5 mm box
        assert all(max(abs(x - px), abs(y - py)) >= 2.5 + 1.5 - 1e-6 or
                   ((max(0, abs(x - px) - 2.5)) ** 2 + (max(0, abs(y - py) - 2.5)) ** 2) ** 0.5 >= 1.5 - 1e-6
                   for p in parts.values() for px, py, _, _ in p["pads"])


def test_spots_keep_off_holes():
    from neoserver.zmap_cal import bare_spots
    parts = {"R1": {"pads": [[0.0, 0.0, 1.0, 1.0]]}, "R2": {"pads": [[40.0, 30.0, 1.0, 1.0]]}}
    # a 5.2 mm hole (its mask opening) right on the middle grid point (20, 15)
    spots = bare_spots(parts, 3, 3, clear=1.5, inset=0.0, keepout=[[20.0, 15.0, 5.2, 5.2]])
    mid = spots[4]
    assert mid and all(max(abs(x - 20) - 2.6, abs(y - 15) - 2.6) >= 1.5 - 1e-6 or
                       (max(0, abs(x - 20) - 2.6) ** 2 + max(0, abs(y - 15) - 2.6) ** 2) ** 0.5 >= 1.5 - 1e-6 for x, y in mid)
    assert len(mid) >= 2 and all(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= 2.0 - 1e-6 for a in mid for b in mid if a != b)


class _FS:
    """a fake seal: the board at 8.30; the pressure lags 3 steps behind (the coarse touch late)"""
    def __init__(self):
        self.calls = []

    def seal_depth(self, cfg, n, x, y, z_from=None, step=0.1, zmax=13, speed=20, settle=0.08):
        self.calls.append((z_from, step))
        z = z_from
        if z >= 8.30:                                     # already on the board: seals at once
            return z, 0
        while z <= zmax:
            if z >= 8.30 + 3 * step:
                return round(z, 3), 0
            z = round(z + step, 3)
        return None, 0


def test_touch_fine_from_above():
    from neoserver.zmap_cal import ZMapCal
    c = ZMapCal.__new__(ZMapCal)
    c.fs = _FS()
    z = c._touch(None, 1, 0, 0, 6.0, 13.0)
    assert abs(z - 8.36) < 0.03, z                         # coarse 8.6, fine from 8.0: 8.36
