"""Board transform with 3+ fiducials and local (pad) alignment.

    python tests/test_padalign.py

- affine (3 marks) and bilinear (4 marks) fits find a stretched / warped board;
- pad alignment finds a known shift + turn in synthetic head-camera images, for a small part
  (one view) and a 16 mm QFP (two views), also with noise and a missing pad;
- the pads file is built from a real board's Gerber and P&P (BOARD-C) for a job made by the
  wizard with the board turned 90 deg (skipped without those files).
"""
import math
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.config import NeoConfig  # noqa: E402
from neojob.padalign import PadAligner, detect_pads, wanted, SIZE  # noqa: E402
from neojob.transform import MarkTransform  # noqa: E402

K = 112.0
GPS = os.environ.get("BOARD_C_DIR", "")


def warp(x, y):
    """a board 0.15 % larger in X, skewed, and twisted between the corners"""
    return 1.0015 * x + 0.0008 * y + 3.0 + 2e-6 * x * y, -0.0006 * x + 0.9995 * y - 1.5 + 1e-6 * x * y


def test_fits():
    d = [(10.0, 10.0), (140.0, 12.0), (12.0, 95.0), (138.0, 97.0)]
    for fit, marks, tol_mid in (("affine", d[:3], 0.02), ("bilinear", d, 1e-6)):
        tf = MarkTransform()
        tf.fit = fit
        tf.set_points(marks, [warp(*p) for p in marks])
        tf.set_params(0.0, (0, 0), (0, 0), (0, 0), True, 0)
        for p in marks:
            x, y, _ = tf.point(*p)
            assert math.dist((x, y), warp(*p)) < 1e-6 + (0.02 if fit == "affine" else 0), (fit, p)
        x, y, a = tf.point(75.0, 50.0)
        err = math.dist((x, y), warp(75.0, 50.0))
        assert err < tol_mid, (fit, err)
        neo = MarkTransform(); neo.set_points(marks[:2], [warp(*p) for p in marks[:2]])
        neo.set_params(0.0, (0, 0), (0, 0), (0, 0), True, 0)
        e2 = math.dist(neo.point(75.0, 50.0)[:2], warp(75.0, 50.0))
        print(f"{fit}: error in the middle {err * 1000:.1f} um (NeoDen 2-mark fit: {e2 * 1000:.0f} um)")


def cfg():
    c = NeoConfig({}, None)
    c.raw.update({"Vision/fHeadPxPerMm": K})
    return c


class FakeBoard:
    """renders bright pads (true machine positions) seen by the head camera at `view`"""
    def __init__(self, pads_true, noise=6, seed=1):
        self.pads, self.view = pads_true, (0.0, 0.0)
        self.rng = np.random.default_rng(seed)
        self.noise = noise

    def move(self, x, y):
        self.view = (x, y)

    def grab(self):
        img = np.full((SIZE, SIZE), 40, np.uint8)
        for x, y, w, h, a in self.pads:
            cx, cy = (x - self.view[0]) * K + SIZE / 2, SIZE / 2 - (y - self.view[1]) * K
            box = cv2.boxPoints(((cx, cy), (w * K, h * K), -a))
            cv2.fillPoly(img, [np.round(box).astype(np.int32)], 200)
        img = cv2.GaussianBlur(img, (5, 5), 1.2)
        return np.clip(img + self.rng.normal(0, self.noise, img.shape), 0, 255).astype(np.uint8)


def turn(pads, centre, d):
    dx, dy, dth = d
    c, s = math.cos(math.radians(dth)), math.sin(math.radians(dth))
    out = []
    for x, y, w, h in pads:
        ux, uy = x - centre[0], y - centre[1]
        out.append((c * ux - s * uy + centre[0] + dx, s * ux + c * uy + centre[1] + dy, w, h, dth))
    return out


def soic8(cx, cy):
    return [[cx + (i - 1.5) * 1.27, cy + sy * 2.7, 0.6, 1.5] for sy in (-1, 1) for i in range(4)]


def qfp64(cx, cy):
    p = 0.5
    out = []
    for i in range(16):
        o = (i - 7.5) * p
        out += [[cx + o, cy - 7.7, 0.28, 1.5], [cx + o, cy + 7.7, 0.28, 1.5], [cx - 7.7, cy + o, 1.5, 0.28], [cx + 7.7, cy + o, 1.5, 0.28]]
    return out


def chip(cx, cy, pitch, w, h, turned=False):
    """two-pad passive: 0402 pitch 1.0 pads 0.5 x 0.6; 0603 pitch 1.6 pads 0.8 x 0.95"""
    if turned:
        return [[cx, cy - pitch / 2, h, w], [cx, cy + pitch / 2, h, w]]
    return [[cx - pitch / 2, cy, w, h], [cx + pitch / 2, cy, w, h]]


def test_passives():
    """0402 / 0603 with neighbours close by: the neighbour's pads must not be taken"""
    for name, pads, d, neigh in (
            ("0402", chip(80.0, 60.0, 1.0, 0.5, 0.6), (0.06, -0.04, 1.5), chip(80.0, 61.2, 1.0, 0.5, 0.6) + chip(81.6, 60.0, 1.0, 0.5, 0.6, True)),
            ("0603 turned", chip(90.0, 60.0, 1.6, 0.8, 0.95, True), (-0.08, 0.05, -2.0), chip(91.5, 60.0, 1.6, 0.8, 0.95, True)),
            ("0402 with a via", chip(70.0, 60.0, 1.0, 0.5, 0.6), (0.1, 0.0, 0.0), [[70.0, 60.75, 0.3, 0.3]])):
        c = (sum(p[0] for p in pads) / 2, sum(p[1] for p in pads) / 2)
        true = turn(pads, c, d) + [(x, y, w, h, 0.0) for x, y, w, h in neigh]
        b = FakeBoard(true, seed=3)
        r = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(pads, c, identity_tf())
        assert r.ok, (name, r.message)
        assert abs(r.dx - d[0]) < 0.012 and abs(r.dy - d[1]) < 0.012 and abs(r.dtheta - d[2]) < 0.6, (name, r)
        print(f"{name}: {r.message}, spacing {r.scale:.3f} (true {d[0]:+.3f}, {d[1]:+.3f}, {d[2]:+.1f})")
    # an 0603's pads where an 0402's should be: refused by the spacing check
    b = FakeBoard([(x, y, w, h, 0.0) for x, y, w, h in chip(50.0, 50.0, 1.6, 0.5, 0.6)])
    r = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(chip(50.0, 50.0, 1.0, 0.5, 0.6), (50.0, 50.0), identity_tf())
    assert not r.ok, r


def identity_tf():
    tf = MarkTransform()
    tf.set_params(0.0, (0, 0), (0, 0), (0, 0), True, 0)
    return tf


def test_align():
    for name, pads, d, drop in (("SOIC-8", soic8(120.0, 80.0), (0.12, -0.07, 0.8), False),
                                ("QFP-64 (two views)", qfp64(200.0, 150.0), (-0.09, 0.15, -0.6), False),
                                ("SOIC-8, one pad missing", soic8(60.0, 40.0), (0.05, 0.05, 0.3), True)):
        c = (pads[0][0] + pads[-1][0]) / 2 if name.startswith("SOIC") else (200.0, 150.0)
        c = (sum(p[0] for p in pads) / len(pads), sum(p[1] for p in pads) / len(pads))
        true = turn(pads, c, d)
        if drop:
            true = true[1:]
        b = FakeBoard(true)
        pa = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None)
        r = pa.align(pads, c, identity_tf(), images=True)
        assert r.ok, (name, r.message)
        assert abs(r.dx - d[0]) < 0.01 and abs(r.dy - d[1]) < 0.01 and abs(r.dtheta - d[2]) < 0.05, (name, r)
        assert all(v["png"] for v in r.views)
        print(f"{name}: {r.message} (true {d[0]:+.3f}, {d[1]:+.3f}, {d[2]:+.2f}); {len(r.views)} view(s)")
    # nothing on the board where the pads should be: refused, not a wild correction
    b = FakeBoard([])
    r = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(soic8(10, 10), (10, 10), identity_tf())
    assert not r.ok, r


def test_real_board_effects():
    """what the BOARD-A board showed: uneven tinned pad ends and a lamp reflection"""
    rng = np.random.default_rng(7)
    pads = qfp64(150.0, 100.0)
    c = (150.0, 100.0)
    d = (0.07, -0.05, 0.4)
    true = []
    for x, y, w, h, _ in turn(pads, c, d):
        ext = rng.uniform(0, 0.4)                       # one end longer by 0..0.4 mm
        sgn = rng.choice((-1, 1))
        if w > h:
            true.append((x + sgn * ext / 2, y, w + ext, h, d[2]))
        else:
            true.append((x, y + sgn * ext / 2, w, h + ext, d[2]))

    class Board(FakeBoard):
        def grab(self):
            img = super().grab()
            # a soft lamp reflection of pad size, low contrast, near the pads
            cx, cy = int((146.0 - self.view[0]) * K + SIZE / 2), int(SIZE / 2 - (96.0 - self.view[1]) * K)
            cv2.ellipse(img, (cx, cy), (70, 90), 0, 0, 360, 95, -1)
            # pins of one net tied by a thin copper strip (0.08 mm), as U16 on the BOARD-A board
            for xa, xb, yy in ((146.25, 147.75, 92.3), (151.25, 152.25, 107.7)):
                pa = (int((xa - self.view[0]) * K + SIZE / 2), int(SIZE / 2 - (yy - self.view[1]) * K))
                pb = (int((xb - self.view[0]) * K + SIZE / 2), pa[1])
                cv2.line(img, pa, pb, 200, max(1, int(0.08 * K)))
            return cv2.GaussianBlur(img, (9, 9), 3)
    b = Board(true)
    pa = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None)
    r = pa.align(pads, c, identity_tf())
    assert r.ok, r.message
    assert r.matched >= 60, (r.matched, r.message)          # tied pins separated again
    err = math.hypot(r.dx - d[0], r.dy - d[1])
    assert err < 0.02 and abs(r.dtheta - d[2]) < 0.05, (r.dx, r.dy, r.dtheta)
    # the plain fit (every pad centre in both directions) for comparison
    from neojob import padalign as PA
    keep = PA.ALONG_WEIGHT
    PA.ALONG_WEIGHT = 1.0
    try:
        r2 = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(pads, c, identity_tf())
    finally:
        PA.ALONG_WEIGHT = keep
    err2 = math.hypot(r2.dx - d[0], r2.dy - d[1])
    print(f"uneven pad ends + reflection + tied pins: {r.matched}/64 pads, error {err * 1000:.1f} um, turn {r.dtheta - d[2]:+.3f} deg "
          f"(all-directions fit: {err2 * 1000:.1f} um, {r2.dtheta - d[2]:+.3f} deg)")


def test_hole_led():
    """reverse-mount LED (LD1..LD8 on the BOARD-A): its centre is the board hole between the pads;
    the pads are tinned unevenly, so they alone would put it off - the hole decides"""
    pads = chip(60.0, 40.0, 2.6, 0.9, 1.2)
    c = (60.0, 40.0)
    d = (0.07, -0.05, 1.0)
    hole = (c[0] + d[0], c[1] + d[1])
    true = turn(pads, c, d)
    true[0] = (true[0][0] - 0.12, true[0][1], true[0][2] + 0.24, true[0][3], true[0][4])   # tin runs outwards

    class HoleBoard(FakeBoard):
        def grab(self):
            img = super().grab().astype(np.int16)
            hx, hy = (hole[0] - self.view[0]) * K + SIZE / 2, SIZE / 2 - (hole[1] - self.view[1]) * K
            cv2.circle(img, (int(round(hx)), int(round(hy))), int(0.6 * K), 0, -1)        # a 1.2 mm hole: dark
            return np.clip(img, 0, 255).astype(np.uint8)
    b = HoleBoard(true, seed=4)
    r = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(pads, c, identity_tf(), hole=True)
    assert r.ok, r.message
    assert abs(r.dx - d[0]) < 0.02 and abs(r.dy - d[1]) < 0.02, r
    print("LED on its hole:", r.message)
    # not marked: found by the camera on its own (pads 1.7 mm apart, a clear hole)
    r = PadAligner(b.grab, b.move, cfg(), log=lambda *a: None).align(pads, c, identity_tf())
    assert r.ok and r.message.startswith("hole found by the camera"), r.message
    assert abs(r.dx - d[0]) < 0.02 and abs(r.dy - d[1]) < 0.02, r
    # the same pads with no hole (a 1206 resistor): no hole is made up, the pads decide
    plain = FakeBoard(turn(pads, c, d), seed=4)
    r = PadAligner(plain.grab, plain.move, cfg(), log=lambda *a: None).align(pads, c, identity_tf())
    assert r.ok and "hole" not in r.message, r.message
    print("no hole on a plain part:", r.message)


def test_drill_file():
    """an Altium NC drill file (Excellon, inch 2:5, leading zeros kept): the hole between an LED's
    pads is found; vias and holes elsewhere are not"""
    from neojob.pads import _excellon, hole_between
    text = "M48\n;Layer_Color=9474304\n;FILE_FORMAT=2:5\nINCH,LZ\n;TYPE=PLATED\nT1F00S00C0.01181\nT2F00S00C0.12205\n%\nT1\nX0047244Y0039370\nT2\nX0023622Y0015748\nM30\n"
    holes = _excellon(text)
    assert len(holes) == 2 and abs(holes[1][0] - 6.0) < 1e-3 and abs(holes[1][1] - 4.0) < 1e-3 and abs(holes[1][2] - 3.1) < 0.01, holes
    led = chip(6.0, 4.0, 2.6, 0.9, 1.2)
    h = hole_between(led, holes)
    assert h and abs(h[0] - 6.0) < 1e-3 and abs(h[2] - 3.1) < 0.01, h
    assert hole_between(chip(30.0, 30.0, 2.6, 0.9, 1.2), holes) is None
    print("drill file: LED hole", h)


def test_axes_two_marks():
    """a machine with scaled, skewed axes (true = K commanded): the 2-mark fit cannot see it, but
    with set_axes() every part of a rigid board lands where it truly is"""
    sx, sy, sk = 1.0014, 1.0020, -0.142
    K = np.array([[sx, 0.0], [sx * math.tan(math.radians(sk)), sy]])
    a = math.radians(-0.4)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    true = lambda p: R @ np.array(p) + (2.0, -1.5)                     # the board on the table
    cmd = lambda p: np.linalg.solve(K, true(p))                        # where the machine sees it
    marks = [(128.9, 92.1), (312.5, 275.5)]
    out = []
    for axes in ((1, 1, 0), (sx, sy, sk)):
        tf = MarkTransform()
        tf.set_points(marks, [tuple(cmd(m)) for m in marks])
        tf.set_params(0.0, (0, 0), (0, 0), (0, 0), True, 0)
        tf.set_axes(*axes)
        worst = max(math.dist(tf.point(x, y)[:2], cmd((x, y))) for x in (120, 240, 356) for y in (89, 130, 178))
        out.append(worst)
        print(f"axes {axes}: worst part error {worst * 1000:.1f} um")
    assert out[0] > 0.1 and out[1] < 1e-6, out


def test_wanted():
    c = cfg()
    class C: name, footprint = "U1", "QFP64"
    assert not wanted(c, C, None)
    c.raw["PadAlign/bEnabled"] = True
    c.raw["PadAlign/sFootprints"] = "qfp64, soic8"
    assert wanted(c, C, None)
    c.raw["PadAlign/sFootprints"] = ""
    c.raw["PadAlign/sRefs"] = "U1"
    assert wanted(c, C, None)


def test_pads_real():
    pnp = os.path.join(GPS, "Pick Place for BOARD-C Main(BOARD-C main).csv")
    gerb = os.path.join(GPS, "BOARD-C Main_2026-10-20_14-01-20.rar")
    if not (os.path.exists(pnp) and os.path.exists(gerb)):
        print("pads from a real Gerber: SKIPPED (no GPS board files)")
        return
    from neosetup import Project
    from neojob.pads import build, read_pnp
    p = Project()
    p.import_pnp(pnp)
    p.rotate_board_quarter(90)
    p.build_machine(top_only=True)
    job = os.path.join(tempfile.mkdtemp(), "gps.csv")
    p.write_machine_file(job)
    d = build(job, pnp, gerb)
    f = d["fit"]
    assert f["rms"] < 0.01 and abs(abs(f["theta"]) % 180 - 90) < 0.01, f
    top = [k for k, v in read_pnp(pnp).items() if "top" in v[3].lower()]
    have = [k for k in top if k in d["parts"]]
    assert len(have) > 0.8 * len(top), (len(have), len(top))
    n = sum(len(v["pads"]) for v in d["parts"].values())
    print(f"pads from a real Gerber: {len(have)}/{len(top)} top parts, {n} pads, fit {f['theta']:.2f} deg rms {f['rms'] * 1000:.1f} um")


if __name__ == "__main__":
    test_fits()
    test_align()
    test_passives()
    test_hole_led()
    test_drill_file()
    test_real_board_effects()
    test_wanted()
    test_pads_real()
    print("pad alignment: OK")


def test_panel_copies_matched(tmp_path):
    """a panelized P&P (each designator once per board) is matched copy by copy onto a job that lists
    every board's parts under the same designators (turned 90 deg, shifted)"""
    import math
    from neojob.pads import match_copies, read_pnp_rows
    from neojob.jobfile import load
    base = {"C1": (5.0, 3.0), "R2": (20.0, 12.0), "U3": (11.0, 25.0), "D4": (30.0, 4.0)}
    rows = ['"Designator","Center-X(mm)","Center-Y(mm)","Rotation","Layer"']
    job = ["#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip"]
    t = math.radians(90)
    for bx, by in ((0, 0), (40, 0), (0, 50), (40, 50)):
        for n, (x, y) in base.items():
            X, Y = x + bx, y + by
            rows.append(f'"{n}","{X}","{Y}","0","TopLayer"')
    for bx, by in ((40, 50), (0, 0), (40, 0), (0, 50)):                # another board order in the job
        for n, (x, y) in base.items():
            X, Y = x + bx, y + by
            jx, jy = 200 + math.cos(t) * X - math.sin(t) * Y, 30 + math.sin(t) * X + math.cos(t) * Y
            job.append(f"comp,1,1,{n},v,fp,{jx:.3f},{jy:.3f},0,No,")
    (tmp_path / "p.csv").write_text(chr(10).join(rows))
    (tmp_path / "j.csv").write_text(chr(10).join(job) + chr(10))
    j = load(str(tmp_path / "j.csv"))
    assert [c.key for c in j.comps][:5] == ["C1", "R2", "U3", "D4", "C1#2"]
    m = match_copies(j, read_pnp_rows(str(tmp_path / "p.csv")))
    assert len(m) == 16
    for c in j.comps:                                                  # each copy got its own board's row
        r = m[c.key]
        jx, jy = 200 + math.cos(t) * r[1] - math.sin(t) * r[2], 30 + math.sin(t) * r[1] + math.cos(t) * r[2]
        assert math.dist((jx, jy), (c.x, c.y)) < 0.01, (c.key, r)


def test_panel_copies_mirrored_and_quoted(tmp_path):
    """the bottom side up (the job X-mirrored against the P&P) still matches copy by copy; a value with a
    comma, written quoted, is read as one field (a ';' in its place)"""
    import math
    from neojob.pads import match_copies, read_pnp_rows
    from neojob.jobfile import load, unquote_line
    assert unquote_line('comp,39,1,Q9,"TLP293(GB-TPL,E",FP-11,196.41,115.14,0,No,') == \
        "comp,39,1,Q9,TLP293(GB-TPL;E,FP-11,196.41,115.14,0,No,"
    base = {"C1": (5.0, 3.0), "R2": (20.0, 12.0), "U3": (11.0, 25.0), "D4": (30.0, 4.0)}
    rows = ['"Designator","Center-X(mm)","Center-Y(mm)","Rotation","Layer"']
    job = ["#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip"]
    for bx, by in ((0, 0), (40, 0), (0, 50), (40, 50)):
        for n, (x, y) in base.items():
            rows.append(f'"{n}","{x + bx}","{y + by}","0","BottomLayer"')
    for bx, by in ((0, 0), (40, 0), (0, 50), (40, 50)):
        for n, (x, y) in base.items():
            v = '"a,b"' if n == "U3" else "v"
            job.append(f"comp,1,1,{n},{v},fp,{300 - (x + bx):.3f},{80 + y + by:.3f},0,No,")
    (tmp_path / "p.csv").write_text(chr(10).join(rows))
    (tmp_path / "j.csv").write_text(chr(10).join(job) + chr(10))
    j = load(str(tmp_path / "j.csv"))
    assert all(c.x > 200 for c in j.comps) and j.comps[2].value == "a;b"
    m = match_copies(j, read_pnp_rows(str(tmp_path / "p.csv")))
    assert len(m) == 16
    for c in j.comps:
        r = m[c.key]
        assert math.dist((300 - r[1], 80 + r[2]), (c.x, c.y)) < 0.01, (c.key, r)
