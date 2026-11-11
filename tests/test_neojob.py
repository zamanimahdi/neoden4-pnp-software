"""Job runner (neojob) on the machine simulator with a fake camera.

    python tests/test_neojob.py

- the mark transform: a board shifted and turned 0.7 deg is placed where it really is;
- a real job file (Proc/BOARD-C main-1.csv, fixed table) runs to the end on sim://;
- pause / stop / resume: a stopped job continues with the parts it had not placed.
"""
import math
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.config import NeoConfig  # noqa: E402
from neocam.vision import PartResult  # noqa: E402
from neojob import MarkTransform, Runner, load_job, YES  # noqa: E402
from neomach import Mainboard  # noqa: E402

NL = chr(10)
DATA = os.environ.get("NEODEN_DATA", "")
JOB = os.path.join(DATA, "Proc", "BOARD-C main-1.csv")


class FakeVision:
    """marks: the design point turned by `theta` about (0,0) and shifted; parts: small offsets"""
    def __init__(self, theta=0.7, shift=(1.5, -0.8)):
        self.theta, self.shift = math.radians(theta), shift
        self.calls = 0

    def real(self, x, y):
        c, s = math.cos(self.theta), math.sin(self.theta)
        return c * x - s * y + self.shift[0], s * x + c * y + self.shift[1]

    def start(self, job, runner):
        pass

    def mark(self, m, runner):
        return self.real(m.x, m.y)

    def bad_mark(self, exposure, runner):
        return 120.0

    def parts(self, slots, runner):
        self.calls += 1
        return {s["nozzle"]: PartResult(True, 0.05, -0.03, 2.0, 1.25, 1.5) for s in slots}


def test_transform():
    fv = FakeVision()
    tf = MarkTransform()
    d = [(215.104, 230.487), (313.232, 142.761), (100.0, 120.0)]
    tf.set_points(d, [fv.real(*p) for p in d])
    tf.set_params(0.0, (203.02, 127.77), (203.02, 127.77), (203.02, 127.77), True, 0)
    for p in [(250.0, 180.0), (300.0, 230.0)]:
        x, y, a = tf.point(*p)
        rx, ry = fv.real(*p)
        assert abs(x - rx) < 1e-6 and abs(y - ry) < 1e-6 and abs(a - 0.7) < 1e-9, (x, y, a, rx, ry)
    print("transform: OK (shift + 0.7 deg found from the marks)")


def make(job_path):
    cfg = NeoConfig.load(os.path.join(DATA, "Config.ini"))
    cfg.raw["Job/bSettle"] = False
    cfg.raw["Job/bSeeTurnSee"] = False                 # on in its own test
    cfg.raw["Job/bStats"] = False                      # nothing written next to the reference data
    cfg.raw["Job/bPickLearn"] = False
    mb = Mainboard.connect("sim://")
    log = []
    answers = []

    def ask(text, buttons):
        answers.append(text)
        return YES
    r = Runner(mb, cfg, FakeVision(), ask, log=log.append)
    return r, mb, log, answers


def test_run():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    todo = sum(1 for c in job.comps if not c.skip and not job.stack(c.stack).skip)
    r, mb, log, answers = make(p)
    t0 = time.time()
    assert r.run(job), "\n".join(log[-20:])
    assert r.placed == todo, (r.placed, todo)
    assert not Runner.resume_file(p)                      # finished: nothing to resume
    assert any("mark 1" in l for l in log) and any("placed R37" in l for l in log)
    assert not answers, answers
    print(f"run: OK ({r.placed} parts on the simulator in {time.time() - t0:.1f} s)")


def test_stop_resume():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    todo = sum(1 for c in job.comps if not c.skip and not job.stack(c.stack).skip)
    r, mb, log, _ = make(p)
    th = threading.Thread(target=r.run, args=(job,))
    th.start()
    while r.placed < 5 and th.is_alive():
        time.sleep(0.01)
    r.pause()
    time.sleep(0.5)
    assert r.state == 1, r.state                          # paused
    n = r.placed
    time.sleep(0.5)
    assert r.placed == n                                  # nothing moves while paused
    r.stop()
    th.join(60)
    first = r.placed
    res = Runner.resume_file(p)
    assert res and len(res["done"]) == first and 0 < first < todo, (res, first)
    r2, *_ = make(p)
    assert r2.run(load_job(p), resume=res)
    assert first + r2.placed == todo, (first, r2.placed, todo)
    print(f"pause / stop / resume: OK ({first} + {r2.placed} = {todo})")


def test_test_place_one_part():
    """placement test (feeder settings): one part only, even a skipped one; the resume record is kept"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip)
    r, mb, log, _ = make(p)
    assert r.run(job, only=i), NL.join(log[-20:])
    assert r.placed == 1, (r.placed, NL.join(log[-20:]))
    assert any(f"placed {job.comps[i].name} " in l for l in log)
    assert any("test: " in l for l in log), NL.join(log[-10:])
    assert not Runner.resume_file(p)
    print(f"test place: OK ({job.comps[i].name} alone)")


class TurnedVision(FakeVision):
    """the second picture (the part already turned to its place angle) shows that turn + 0.4 deg"""
    def parts(self, slots, runner):
        self.calls += 1
        return {s["nozzle"]: PartResult(True, 0.05, -0.03, 2.0, 1.25, 1.5 + (s["target"][2] + 0.4 if "target" in s else 0.0)) for s in slots}


def test_second_look():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    for st in job.stacks.values():
        st.second_look = True
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip and job.stack(c.stack).vision > 0)
    r, mb, log, _ = make(p)
    r.vision = TurnedVision()
    assert r.run(job, only=i), NL.join(log[-20:])
    look = [l for l in log if "second look" in l]
    assert look and "turn +0.40 deg" in look[0], NL.join(log[-15:])
    print("second look: OK (" + look[0] + ")")


class SlipVision(FakeVision):
    """a part that slips on the nozzle: each turn lands short, a little less each time (0.8, 0.3,
    0.05 deg); the offset changes with the turn"""
    def __init__(self):
        super().__init__()
        self.looks = 0

    def parts(self, slots, runner):
        self.calls += 1
        out = {}
        for s in slots:
            if "target" not in s:
                out[s["nozzle"]] = PartResult(True, 0.05, -0.03, 2.0, 1.25, 1.5)
                continue
            slip = (0.8, 0.3, 0.05)[min(self.looks, 2)]
            self.looks += 1
            out[s["nozzle"]] = PartResult(True, 0.05 + 0.01 * self.looks, -0.03, 2.0, 1.25, 1.5 + s["target"][2] + slip)
        return out


def test_see_turn_see():
    """single-part vision: looked at, turned, looked at again until the turn reads right; every turn
    ends from the same side; nothing turns after the last look, whose offset is placed"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip and job.stack(c.stack).vision > 0)
    r, mb, log, _ = make(p)
    r.cfg.raw["Job/bSeeTurnSee"] = True
    r.vision = SlipVision()
    turns = []
    rot = mb.rotate
    mb.rotate = lambda n, a, *x, **k: (turns.append((n, round(a, 3))), rot(n, a, *x, **k))[1]
    placed = []
    pl = r.place
    r.place = lambda s, first: (placed.append((s["target"], mb.angle[s["nozzle"]])), pl(s, first))[1]
    assert r.run(job, only=i), NL.join(log[-20:])
    looks = [l for l in log if "second look" in l]
    assert len(looks) == 3 and "turn +0.80" in looks[0] and "turn +0.30" in looks[1] and "left" in looks[2], NL.join(looks)
    (X, Y, A), at = placed[0]
    assert abs(at - A) < 1e-6, (at, A)                         # placed at the last look's angle
    k = [t for t in turns if abs(t[1] - A) < 1e-6]
    assert k and turns.index(k[-1]) > 0 and abs(turns[turns.index(k[-1]) - 1][1] - (A - 1.5)) < 1e-6, turns   # from 1.5 short
    print("see, turn, see: OK (" + looks[-1] + ")")


class NoMarkVision(FakeVision):
    """the marks are never found by the camera"""
    def mark(self, m, runner):
        self.tries = getattr(self, "tries", 0) + 1
        return None


def test_mark_not_found_by_hand():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip)
    r, mb, log, answers = make(p)
    r.vision = NoMarkVision()
    assert r.run(job, only=i), NL.join(log[-20:])
    assert r.vision.tries == 5 * len(job.marks), r.vision.tries         # where it should be + 4 points around
    assert answers and answers[0].startswith("Mark 1 not found"), answers[:1]
    assert r.placed == 1
    print("mark not found -> by hand: OK")


def test_edit_while_paused():
    """pause, skip a feeder and change a pick height in the job file, continue: taken at once"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    r, mb, log, _ = make(p)
    th = threading.Thread(target=r.run, args=(job,))
    th.start()
    while r.placed < 3 and th.is_alive():
        time.sleep(0.01)
    r.pause()
    while r.state != 1 and th.is_alive():
        time.sleep(0.01)
    done_before = {i for _, i in r.done}
    lines = open(p, encoding="utf-8").read().split(NL)
    for i, l in enumerate(lines):
        t = l.split(",")
        if t[0] == "stack" and t[1] == "7":
            t[20] = "Yes"                                 # Skip (stack k19)
            lines[i] = ",".join(t)
        if t[0] == "stack" and t[1] == "8":
            t[9] = "0.50"                                 # Pick height
            lines[i] = ",".join(t)
    open(p, "w", encoding="utf-8").write(NL.join(lines))
    r.resume()
    th.join(120)
    on7 = [i for i, c in enumerate(job.comps) if c.stack == 7 and not c.skip]
    placed7 = [i for i in on7 if (0, i) in r.done]
    assert set(placed7) <= done_before, placed7           # nothing from feeder 7 after the pause
    assert job.stack(8).pick_height == 0.5
    assert any("stack 7: skip" in l for l in log) and any("stack 8: pick_height" in l for l in log), log[-10:]
    print(f"edit while paused: OK (feeder 7 skipped from then on, {len(on7) - len(placed7)} parts left out; pick height taken)")


if __name__ == "__main__":
    test_transform()
    if not os.path.exists(JOB):
        print("job run: SKIPPED (no NeoDen data)")
        sys.exit(0)
    test_run()
    test_stop_resume()
    test_edit_while_paused()


class FakeBulk:
    """a bulk feeder with two parts in it (turned 30 and -45 deg), then empty"""
    def __init__(self):
        self.parts = [(150.0, 60.0, 30.0, None), (152.0, 61.0, -45.0, None)]
        self.scans = 0
        self.picked = []

    on_fail = "next"
    misses = 0                                          # the first picks that do not come up

    def container(self, cid):
        return {"id": cid, "package": "SOIC-8", "z_pick": 10.0, "on_fail": self.on_fail}

    def next_part(self, cid):
        q = self.queue.get(cid)
        if q:                                           # put back to be looked at again (as BulkFeeder)
            f = q.pop(0)
            return f["x"], f["y"], 0.0, None
        return self.parts.pop(0) if self.parts else None

    def scan(self, cid):
        self.scans += 1
        return []

    def update(self, cid, **kw):
        pass

    pick_z = staticmethod(lambda c: c.get("z_pick"))

    queue = {}

    def pick_at(self, n, x, y, z, extra=1.0):
        self.picked.append((n, x, y, z))                 # straight down at the feeder's height
        if self.misses:
            self.misses -= 1
            return False
        return True

    def pick_by_feel(self, n, x, y, z_hint=None, extra=1.0):
        raise AssertionError("the height is on the feeder: no stepping down")


def test_bulk_feeder_pick_and_refill():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    idx = [k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip]
    st = job.stack(job.comps[idx[0]].stack)
    st.bulk, st.angle = 1, 90.0
    r, mb, log, answers = make(p)
    fb = FakeBulk()
    r.bulk_feeder = lambda: fb
    asked = []
    r._ask = lambda text, buttons: (asked.append(text), "ignore")[1]
    angles = []
    prep = r.prepare
    r.prepare = lambda s, tf, res: (angles.append(s.get("pick_angle")), prep(s, tf, res))[1]
    assert r.run(job, only=idx[0]), NL.join(log[-15:])
    assert fb.picked and angles[0] == 120.0, (fb.picked, angles)        # 90 (the stack) + 30 (how it lay)
    # the next part of that feeder: the container is empty after the second -> scanned again -> refill asked
    fb.parts = []
    r2, *_ = make(p)
    r2.bulk_feeder = lambda: fb
    asked2 = []
    r2._ask = lambda text, buttons: (asked2.append(text), "ignore")[1]
    r2.run(load_job(p) if False else job, only=idx[0])
    assert fb.scans >= 1 and asked2 and "is empty" in asked2[0], (fb.scans, asked2)


def test_bulk_not_picked():
    """a part that does not come up: retry = the same part again; skip = the component is skipped"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    idx = [k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip]
    st = job.stack(job.comps[idx[0]].stack)
    st.bulk = 1
    r, mb, log, answers = make(p)
    fb = FakeBulk(); fb.on_fail, fb.misses = "retry", 1
    fb.queue = {}
    r.bulk_feeder = lambda: fb
    assert r.run(job, only=idx[0]), NL.join(log[-15:])
    assert len(fb.picked) == 2 and fb.picked[0][1:3] == fb.picked[1][1:3], fb.picked     # the same place twice
    r2, *_ = make(p)
    fb2 = FakeBulk(); fb2.on_fail, fb2.misses = "skip", 1
    fb2.queue = {}
    r2.bulk_feeder = lambda: fb2
    log2 = []
    r2.log = lambda t, *a: log2.append(t)
    r2.run(job, only=idx[0])
    assert len(fb2.picked) == 1 and any("skipped" in l for l in log2), NL.join(log2[-10:])


class OffsetVision(FakeVision):
    """every part sits 0.2 mm right, 0.1 mm down of the nozzle's axis"""
    def parts(self, slots, runner):
        self.calls += 1
        return {s["nozzle"]: PartResult(True, 0.2, -0.1, 2.0, 1.25, 1.5) for s in slots}


def test_pick_learn_and_place_checks():
    """the pick point follows the parts' offset; the after-place checks: a part that comes back on the
    nozzle is thrown away and another placed; per part (own setting) over its feeder's"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    idx = [k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip and job.stack(c.stack).vision > 0]
    sid = job.comps[idx[0]].stack
    same = [k for k in idx if job.comps[k].stack == sid]
    r, mb, log, _ = make(p)
    r.cfg.raw.update({"Job/bPickLearn": True, "Job/nPickLearnSamples": 2})
    r.vision = OffsetVision()
    picks = []
    mv = mb.move_xy
    mb.move_xy = lambda x, y, *a, **k: (picks.append((round(x, 3), round(y, 3))), mv(x, y, *a, **k))[1]
    for k in same[:3]:
        assert r.run(job, only=k), NL.join(log[-15:])
    learnt = [l for l in log if "pick point corrected" in l]
    assert learnt and "+0.160, -0.080" in learnt[0], NL.join(log[-20:])
    import json as _j
    assert _j.load(open(p + ".picklearn.json"))[str(sid)]["dx"] == 0.16
    # vacuum after placing: the first time the part is still on the nozzle
    job.stack(sid).place_check = "vacuum"
    r2, mb2, log2, _ = make(p)
    r2.vision = FakeVision()
    seq = iter([-45, -45, -45] + [0] * 50)
    mb2.vacuum = lambda n: next(seq)
    disc = []
    r2.discard = lambda s: (disc.append(s["comp"].name), s.update(held=False))
    assert r2.run(job, only=idx[0]), NL.join(log2[-15:])
    assert disc and any("came back" in l for l in log2), NL.join(log2[-15:])
    # its own setting wins over the feeder's
    job.comps[idx[0]].place_check = "none"
    r3, mb3, log3, _ = make(p)
    r3.vision = FakeVision()
    mb3.vacuum = lambda n: -45                          # would always look like a part on the nozzle
    assert r3.run(job, only=idx[0]) and not any("came back" in l for l in log3)


class SizeVision(FakeVision):
    """every part picked is 3.2 x 1.6 mm (a 1206) whatever the feeder says"""
    def parts(self, slots, runner):
        self.calls += 1
        return {s["nozzle"]: PartResult(True, 0.0, 0.0, 3.2, 1.6, 0.0) for s in slots}


def test_wrong_reel_told():
    """parts of another size twice in a row: the operator is told plainly (wrong reel?); Ignore takes
    the size for the run, so the part is placed"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip and job.stack(c.stack).vision > 0)
    st = job.stack(job.comps[i].stack)
    st.size_check, st.part_l, st.part_w = True, 1.6, 0.8
    r, mb, log, answers = make(p)
    r.vision = SizeVision()
    asked = []
    r._ask = lambda text, buttons: (asked.append(text), "ignore")[1]
    assert r.run(job, only=i), NL.join(log[-15:])
    assert asked and "right reel" in asked[0] and "3.20 x 1.60" in asked[0], asked


def test_cycles_filled():
    """several nozzles pick together: Assign plans the nozzles (an allowed one not yet used in the cycle),
    the run fills a cycle from a few parts ahead and mixes feeder speeds; every part is still placed once"""
    from neojob import assign as A
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    for st in job.stacks.values():                       # every feeder takes every nozzle
        st.nozzles = "1234"
    for c in job.comps:
        c.nozzle = 1                                     # as a job made part by part: all on nozzle 1
    todo = sum(1 for c in job.comps if not c.skip and not job.stack(c.stack).skip)
    one = len(A.cycles(job, 0, False))
    assert one == todo, (one, todo)                      # one part per cycle
    changes = A.plan_nozzles(job)
    planned = A.cycles(job, 12, True)
    assert changes and len(planned) <= (todo + 3) // 4 + 2, (len(planned), todo)
    r, mb, log, answers = make(p)
    sizes = []
    cyc = r.cycle
    r.cycle = lambda slots, tf, pn: (sizes.append(len(slots)), cyc(slots, tf, pn))[1]
    assert r.run(job), "\n".join(log[-20:])
    assert r.placed == todo and sum(sizes) == todo, (r.placed, sum(sizes), todo)
    assert len(sizes) == len(planned) and max(sizes) == 4, sizes
    print(f"cycles: OK ({todo} parts in {len(sizes)} cycles instead of {one})")
