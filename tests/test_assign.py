"""neojob.assign: the machine feeder table into the job's stacks, feeder / nozzle of the parts.

    python tests/test_assign.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neojob.assign import assign, summary  # noqa: E402
from neojob.jobfile import Comp, Job, Stack  # noqa: E402


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def main():
    job = Job()
    job.stacks = {7: Stack(7, 0, "13", footprint="", value="0603/10K 0603", pitch=4, feed_strength=60, peel_strength=80),   # short line: no feed data
                  8: Stack(8, 0, "4", footprint="0603", value="100NF", pitch=4, feed_strength=60,
                           tokens=[""] * 16 + ["4", "60", "80"]),             # the job gives them: the job wins
                  9: Stack(9, 0, "1", footprint="0603", value="100NF", skip=True),
                  20: Stack(20, 1, "2", value="QFP/STM32"),
                  30: Stack(30, 0, "1", value="100N"), 31: Stack(31, 0, "1", value="100N")}
    job.comps = [Comp(7, 1, "R1", "10K 0603", "0603", 0, 0, 0, False),          # right feeder, nozzle 1 allowed
                 Comp(7, 2, "R2", "10K 0603", "0603", 0, 0, 0, False),          # nozzle 2 not allowed -> 1
                 Comp(9, 1, "C1", "100NF", "0603", 0, 0, 0, False),             # skipped stack but it matches: kept
                 Comp(3, 1, "C2", "100nf", "0603", 0, 0, 0, False),             # wrong feeder -> 8, nozzle -> 4
                 Comp(5, 1, "U9", "XYZ", "SOT23", 0, 0, 0, False),              # no feeder at all
                 Comp(5, 1, "FID", "FID", "FID", 0, 0, 0, True),                # skip: untouched
                 Comp(31, 1, "C30", "100N", "0805", 0, 0, 0, False),           # two 100N feeders, no footprint: stays on 31
                 Comp(99, 1, "C31", "100N", "0603", 0, 0, 0, False)]           # its stack is gone: first 100N feeder
    cfg = Cfg({"Feed/lstFeed6.nLoad": "2", "Feed/lstFeed6.nMoment": "70", "Peel/lstPeel6.nMoment": "90",
               "Stack/lstStack7.nFeedId": "12", "Feed/lstFeed11.nMoment": "40"})  # stack 8 uses feeder 12
    rep = assign(job, cfg)
    print("\n".join(summary(rep)))
    s7, s8 = job.stacks[7], job.stacks[8]
    assert (s7.pitch, s7.feed_strength, s7.peel_strength) == (2, 70, 90), vars(s7)
    assert (s8.pitch, s8.feed_strength) == (4, 60)                                # the job's own values, not the table's 40
    assert job.stacks[20].feed_strength == 60                                     # tray: no feeder data
    c = job.comps
    assert (c[0].stack, c[0].nozzle) == (7, 1) and c[1].nozzle == 1
    assert c[2].stack == 9
    assert (c[3].stack, c[3].nozzle) == (8, 4)
    assert rep["no_feeder"] == ["U9"] and c[5].stack == 5
    assert c[6].stack == 31 and c[7].stack == 30
    assert ("C31", "stack", 99, 30) in rep["parts"] and not any(p[0] == "C30" for p in rep["parts"])
    print("all ok")


if __name__ == "__main__":
    main()
