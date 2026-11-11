"""Pick points the head cannot reach are found before the job starts (neojob.reach)."""
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neojob.reach import problems  # noqa: E402


def _st(x, y, skip=False, **k):
    return types.SimpleNamespace(type=k.get("type", 0), x=x, y=y, end_x=k.get("end_x", x), end_y=k.get("end_y", y),
                                 cols=k.get("cols", 1), rows=k.get("rows", 1), skip=skip, footprint="0603", value="R")


def _job(stacks, comps, test=False):
    return types.SimpleNamespace(stacks=stacks, comps=[types.SimpleNamespace(stack=s, nozzle=n, skip=sk) for s, n, sk in comps],
                                 stack=lambda sid: stacks.get(sid), test=test)


CFG = types.SimpleNamespace(nozzle_offset=lambda n: {1: (0, 0), 2: (-21.1, 0), 3: (-0.1, -20.7), 4: (-21.2, -20.7)}[n])


def test_reach():
    stacks = {1: _st(30, 394), 2: _st(30, 407.15), 3: _st(10, 100), 4: _st(30, 407.15, skip=True),
              5: _st(300, 380, type=1, end_x=420, end_y=390, cols=5, rows=3)}
    job = _job(stacks, [(1, 1, False), (2, 1, False), (2, 3, False), (3, 2, False), (4, 1, False), (5, 1, False)])
    p = problems(job, CFG)
    assert [s.split(":")[0] for s in p] == ["feeder 2 (0603 R)", "feeder 3 (0603 R)", "feeder 5 (0603 R)", "feeder 5 (0603 R)"], p
    assert "7.10 mm past Y 400.05" in p[0] and "11.10 mm past X 0" in p[1]
    job.test = True                                                  # a test run picks the skipped feeder too
    assert any(s.startswith("feeder 4") for s in problems(job, CFG))
    assert problems(job, CFG, only=0) == []
    stacks[2].bulk = 3                                               # a bulk feeder: picked in its container
    assert not any(s.startswith("feeder 2") for s in problems(job, CFG))


def test_feeder_issues():
    from neojob.reach import feeder_issues
    stacks = {1: _st(30, 100), 2: _st(30, 407.15), 3: _st(40, 100)}
    stacks[1].part_h = 0.45
    stacks[3].footprint = "SOP8"
    for st in stacks.values():
        st.part_h = getattr(st, "part_h", 0.0)
    job = _job(stacks, [(1, 1, False), (2, 1, False), (3, 1, False)])
    for c in job.comps:
        c.footprint = ""
    out = feeder_issues(job, CFG, {"SOP8": (5.0, 4.0, 1.5)})
    assert 1 not in out and 3 not in out
    assert out[2][0].startswith("no part height") and "past Y 400.05" in out[2][1]
