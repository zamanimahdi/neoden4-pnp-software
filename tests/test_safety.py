"""Audit fixes (2026-11): parts left on nozzles go to the waste box, a resume on an edited job is
refused, a 180 deg panel's marks are looked for where the transform places them, a used-up tray asks
for a refill and the tray counters are saved back."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from neojob import Runner, load_job, YES  # noqa: E402
from neojob.jobfile import save_tray_counters  # noqa: E402
from neomach import Mainboard  # noqa: E402
from test_neojob import JOB, make  # noqa: E402

TRAY_JOB = "\r\n".join([
    "#Feeder,Feeder ID,Type,Nozzle,X,Y,Angle,Footprint,Value,Pick height,Pick delay,Place Height,Place Delay,Vacuum detection,Threshold,Vision Alignment,Speed,",
    "stack,50,1,1,100.00,200.00,0.00,,QFN/X,0.50,0,0.00,0,No,-40,1,100,2,2,110.00,210.00,2,2,No,No,-40,-40,-40,-40,",
    "#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip",
    "comp,50,1,U1,X,QFN,10.00,10.00,0.00,No,",
]) + "\r\n"


def test_held_parts_go_to_the_waste_box():
    mb = Mainboard.connect("sim://")
    mb.valve(4, -1)                                     # not homed: no move, the part stays on (said so)
    log = []
    assert mb.drop_held((380.0, 20.0), log.append) == [] and mb.held() == [4]
    assert any("not reached" in l for l in log)
    mb.valve(4, 0)
    mb.home_xy()
    mb.valve(2, -1)
    mb.valve(3, -1)
    mb.valve(3, 0)                                      # placed: not held any more
    log = []
    assert mb.held() == [2]
    assert mb.drop_held((380.0, 20.0), log.append) == [2]
    assert mb.held() == [] and abs(mb.pos[0] - 380.0) < 0.01 and abs(mb.pos[1] - 20.0) < 0.01
    mb.valve(1, -1)                                     # no waste box set: left on (sucked) for the operator
    assert mb.drop_held((0.0, 0.0), log.append) == [] and mb.held() == [1]
    assert any("no waste box" in l for l in log)


def test_resume_refused_after_an_edit():
    job = load_job(JOB)
    res = {"parts": Runner.fingerprint(job), "done": [[0, 0]], "panel": 0, "comp": 1}
    r, mb, log, answers = make(JOB)
    edited = load_job(JOB)
    del edited.comps[0]                                 # a part deleted: the indices mean other parts now
    with pytest.raises(ValueError, match="changed"):
        r.run(edited, resume=res)
    assert Runner.fingerprint(load_job(JOB)) == res["parts"]       # unchanged: the same fingerprint


def test_marks_of_a_panel_turned_180():
    r, mb, log, answers = make(JOB)
    mb.home_xy()
    job = load_job(JOB)
    P = job.panels[0]
    P.angle = 180
    A = job.anchor()
    cam = r.cfg.head_cam_offset
    seen = []

    class Where:                                        # the mark is found where the camera looks
        def mark(self, m, runner):
            x, y = mb.pos[0] - cam[0], mb.pos[1] - cam[1]
            seen.append((m, x, y))
            return x, y

    from neojob.runner import RUN
    r.vision, r.job, r.state = Where(), job, RUN          # marks are read inside a run
    r.locate_marks(P)
    assert seen
    for m, x, y in seen:                                # P + (A - m), as MarkTransform places a 180 panel
        assert abs(x - (P.x + A[0] - m.x)) < 0.02 and abs(y - (P.y + A[1] - m.y)) < 0.02, (m, x, y)


def test_tray_used_up_and_counters_saved():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "tray.csv")
    open(p, "w", newline="").write(TRAY_JOB)
    job = load_job(p)
    st = job.stack(50)
    assert (st.cols, st.rows, st.cur_col, st.cur_row) == (2, 2, 2, 2)
    st.next_array_pos()                                 # the last cell
    assert getattr(st, "used_up", False) and (st.cur_col, st.cur_row) == (1, 1)
    assert save_tray_counters(p, {50: (2, 1)})
    again = load_job(p).stack(50)
    assert (again.cur_col, again.cur_row) == (2, 1)
    assert not os.path.exists(p + ".tmp")
    # the run asks for a refill before taking cell 1 again (stopped right after: no sim pick, its retries)
    r, mb, log, answers = make(p)
    r.job = job

    class Taken(Exception):
        pass

    def stop_here():
        raise Taken()
    st.next_array_pos = stop_here
    with pytest.raises(Taken):
        r.pick({"stack": st, "nozzle": 1, "comp": job.comps[0]})
    assert any("used up" in a for a in answers) and not getattr(st, "used_up", False)
