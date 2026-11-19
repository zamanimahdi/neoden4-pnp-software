"""Tall parts (Job/fTallPartMm): a tall part on the nozzle is never carried over a tall part on the board.

    python tests/test_tall_parts.py

BOARD-A (2026-11-19): 16 PC817 in two rows; each one knocked the one before it off on its way from the
camera. The nozzle's travel is ~8 mm above the board and two PC817 are 7 - 8 mm.
"""
import math
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
from neojob import Runner, load_job  # noqa: E402

F = Runner.free_seg
R = Runner.free_rect


def test_geometry():
    box = [(10.0, 10.0, 2.0, 3.0)]                       # a part at 10, 10: 4 x 6 mm
    h = (2.0, 3.0)                                       # the carried one: the same size
    assert not F((0, 10), (20, 10), h, box)              # straight over it
    assert F((0, 20), (20, 20), h, box)                  # 10 mm beside it
    assert F((0, 16.0), (20, 16.0), h, box)              # touching side by side (6 mm apart: 0 gap) is allowed
    assert not F((0, 15.5), (20, 15.5), h, box)          # 0.5 mm into it
    assert F((14.0, 30), (14.0, 10), h, box)             # down beside it to the neighbouring place (4 mm pitch)
    assert not F((13.0, 30), (13.0, 10), h, box)         # 1 mm too near
    assert not F((30, 30), (0, 0), h, box)               # a diagonal through it
    assert F((30, 30), (16, 30), h, box) and F((5, 5), (5, 5), h, [(20, 20, 1, 1)])
    assert not R((30, 30), (0, 0), h, box) and R((30, 30), (14.0, 10), h, box) and R((30, 30), (20, 20), h, box)


def run_sup(order=None):
    """the BOARD-A job's 16 optos alone on the simulator: -> (names in the order placed, moves made holding one)"""
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "data", "sup1214", "job.csv")):
        import pytest
        pytest.skip("the board's data is not in this copy")
    import test_neojob as T
    here = os.path.join(os.path.dirname(__file__), "data", "sup1214")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    lines = open(os.path.join(here, "job.csv"), encoding="utf-8", newline="").read().split("\r\n")
    for i, l in enumerate(lines):                        # only feeder 29 (PC817) is placed
        t = l.split(",")
        if t[0] == "stack" and t[2] == "0":
            t += [""] * (34 - len(t))
            t[20] = "" if t[1] == "29" else "Yes"
            lines[i] = ",".join(t)
    if order:
        comps = [i for i, l in enumerate(lines) if l.startswith("comp,")]
        optos = [i for i in comps if lines[i].split(",")[1] == "29"]
        by = {lines[i].split(",")[3]: lines[i] for i in optos}
        for i, name in zip(optos, order):
            lines[i] = by[name]
    open(p, "w", encoding="utf-8", newline="").write("\r\n".join(lines))
    open(p + ".pads.json", "wb").write(open(os.path.join(here, "job.csv.pads.json"), "rb").read())
    job = load_job(p)
    assert sum(1 for c in job.comps if c.stack == 29) == 16 and not job.stack(29).skip
    r, mb, log, answers = T.make(p)
    r.vision = T.FakeVision(theta=0.0, shift=(0.0, 0.0))
    from neocam.config import NeoConfig
    cfg = NeoConfig.load(os.path.join(here, "Config.ini"))
    for k in ("Job/bSettle", "Job/bSeeTurnSee", "Job/bStats", "Job/bPickLearn"):
        cfg.raw[k] = False
    cfg.raw["Job/bAutoZMap"] = False
    r.cfg = cfg
    placed, moves, state = [], [], {"s": None}
    pick, place, move = r.pick, r.place, mb.move_xy

    def pick2(s):
        pick(s)
        if s.get("tall") and s["held"]:
            state["s"] = s

    def place2(s, first):
        ok = place(s, first)
        if ok:
            placed.append(s["comp"].name)
        state["s"] = None
        return ok

    def move2(x, y, *a, **k):
        s = state["s"]
        if s is not None and mb.pos:
            o = r.nozzle_offset(s["nozzle"])
            boxes = r.tall_boxes(0, r.tf_now, skip=s["index"]) if getattr(r, "tf_now", None) is not None else []
            hx, hy, _, _ = r.tall_half(s["comp"], s["stack"])
            turned = "target" in s
            h = (hx, hy) if turned else (max(hx, hy),) * 2
            A, B = (mb.pos[0] - o[0], mb.pos[1] - o[1]), (x - o[0], y - o[1])
            one_axis = abs(A[0] - B[0]) < 1e-6 or abs(A[1] - B[1]) < 1e-6
            free = F(A, B, h, boxes) if one_axis else R(A, B, h, boxes)
            moves.append((s["comp"].name, A, B, free, len(boxes)))
        return move(x, y, *a, **k)
    r.pick, r.place, mb.move_xy = pick2, place2, move2
    assert r.run(job), "\n".join(log[-25:])
    return placed, moves, log, answers


def test_sup1214_optos_never_cross():
    placed, moves, log, answers = run_sup()
    assert len(placed) == 16 and len(set(placed)) == 16, placed
    bad = [m for m in moves if not m[3]]
    assert not bad, bad[:5]
    assert not answers, answers
    assert any(m[4] for m in moves)                       # tall parts were on the board during the moves
    return placed, log


def test_any_list_order_is_safe():
    """the job's own order does not matter: the worst one (each row from the camera's side) is safe too"""
    order = ["U11_1", "U10_1", "U9_1", "pc817_1", "U7_1", "U6_1", "U5_1", "U3_1", "U11", "U10", "U9", "pc817", "U7", "U6", "U5", "U3"]
    placed, moves, log, answers = run_sup(order)
    assert len(placed) == 16 and not [m for m in moves if not m[3]] and not answers, (placed, answers)


def test_tall_parts_are_placed_last():
    """every lower part of the panel first, whatever the list's order (2026-11-19: the PC817 went on in the
    middle of the job, in the lower parts' way)"""
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "data", "sup1214", "job.csv")):
        import pytest
        pytest.skip("the board's data is not in this copy")
    import test_neojob as T
    here = os.path.join(os.path.dirname(__file__), "data", "sup1214")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    lines = open(os.path.join(here, "job.csv"), encoding="utf-8", newline="").read().split("\r\n")
    for i, l in enumerate(lines):                        # feeders 29 (PC817), 7 and 6 (0805) only
        t = l.split(",")
        if t[0] == "stack" and t[2] == "0":
            t += [""] * (34 - len(t))
            t[20] = "" if t[1] in ("29", "7", "6") else "Yes"
            lines[i] = ",".join(t)
    open(p, "w", encoding="utf-8", newline="").write("\r\n".join(lines))
    open(p + ".pads.json", "wb").write(open(os.path.join(here, "job.csv.pads.json"), "rb").read())
    job = load_job(p)
    r, mb, log, answers = T.make(p)
    r.vision = T.FakeVision(theta=0.0, shift=(0.0, 0.0))
    from neocam.config import NeoConfig
    cfg = NeoConfig.load(os.path.join(here, "Config.ini"))
    for k in ("Job/bSettle", "Job/bSeeTurnSee", "Job/bStats", "Job/bPickLearn", "Job/bAutoZMap"):
        cfg.raw[k] = False
    r.cfg = cfg
    seq, sizes = [], []
    place, cyc = r.place, r.cycle

    def place2(s, first):
        ok = place(s, first)
        if ok:
            seq.append(s["comp"].stack)
        return ok
    r.place = place2
    r.cycle = lambda slots, tf, pn: (sizes.append([s["comp"].stack for s in slots]), cyc(slots, tf, pn))[1]
    assert r.run(job), "\n".join(log[-25:])
    todo = sum(1 for c in job.comps if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip and 1 <= c.nozzle <= 4)
    assert len(seq) == todo and seq.count(29) == 16, (len(seq), todo)
    first_tall = seq.index(29)
    assert all(x == 29 for x in seq[first_tall:]) and first_tall == todo - 16, seq      # all 16 at the very end
    assert all(c == [29] for c in sizes if 29 in c)                                    # each alone on the head
    assert not Runner.resume_file(p) and not answers
    return todo


def test_low_parts_as_before():
    """a job without tall parts: nothing of the rule runs"""
    import test_neojob as T
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(T.JOB, "rb").read())
    job = load_job(p)
    r, mb, log, answers = T.make(p)
    assert r.run(job) and not any("tall part" in l for l in log)


if __name__ == "__main__":
    test_geometry(); print("ok geometry")
    placed, log = test_sup1214_optos_never_cross()
    print("ok BOARD-A optos:", " ".join(placed))
    print("   ", sum("placed one axis after the other" in l for l in log), "of 16 placed one axis after the other,", sum("to the camera one axis" in l for l in log), "went to the camera that way")
    test_any_list_order_is_safe(); print("ok worst list order")
    n = test_tall_parts_are_placed_last(); print("ok tall parts last:", n, "parts, the 16 PC817 at the end, each alone")
    test_low_parts_as_before(); print("ok low parts as before")
