"""fiducials: only a circle of 1 mm or more is a mark; marks that do not match the board stop the run"""
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
from neocam.vision import find_mark_best  # noqa: E402

K = 115.0


def board(shape, size_mm):
    img = np.full((800, 800), 40, np.uint8)
    r = int(size_mm * K / 2)
    if shape == "circle":
        cv2.circle(img, (400, 400), r, 220, -1)
    else:
        cv2.rectangle(img, (400 - r, 400 - r), (400 + r, 400 + r), 220, -1)
    return img


def test_circle_is_a_mark():
    r = find_mark_best(board("circle", 1.2), 0.8, 3.0, K)
    assert r.precision > 0 and abs(r.x - 400) < 2 and abs(r.y - 400) < 2, r


def test_square_pad_is_not_a_mark():
    assert find_mark_best(board("square", 1.5), 0.8, 3.0, K).precision == 0


def test_small_circle_is_not_a_mark():
    assert find_mark_best(board("circle", 0.7), 0.5, 3.0, K).precision == 0      # under the 1 mm minimum


def test_marks_that_do_not_match_the_board_ask():
    from test_neojob import FakeVision, make, JOB
    from neojob import load_job

    class Wrong(FakeVision):                             # mark 2 is a round spot 2.2 mm off the real one
        def mark(self, m, runner):
            x, y = self.real(m.x, m.y)
            return (x + 2.0, y + 1.0) if m is runner.job.marks[-1] else (x, y)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "job.csv")
    open(p, "wb").write(open(JOB, "rb").read())
    job = load_job(p)
    i = next(k for k, c in enumerate(job.comps) if not c.skip and job.stack(c.stack) and not job.stack(c.stack).skip)
    r, mb, log, answers = make(p)
    r.vision = Wrong()
    asked = []
    r._ask = lambda text, buttons: (asked.append(text), "no")[1]
    assert not r.run(job, only=i)
    assert asked and asked[0].startswith("Marks do not match the board"), asked[:1]
    assert r.placed == 0
