"""Faster motion in a job: steps lost mid-job are homed away, the place approach is one-sided but
not slow, and a cycle's picks and places go in nearest-neighbour order.

    python tests/test_motion_plan.py
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neocam.config import NeoConfig  # noqa: E402
from neojob.runner import Runner  # noqa: E402
from neomach import StepLoss  # noqa: E402


class FakeMB:
    def __init__(self, pos=(0.0, 400.0)):
        self.pos, self._target, self.calls = pos, None, []

    def move_xy(self, x, y, wait=True, timeout=30.0, slow=False, speed=None):
        self.calls.append(("move", round(x, 3), round(y, 3), slow, speed))
        self.pos = self._target = (x, y)

    def home_xy(self):
        self.calls.append(("home",))
        self.pos = (0.0, 400.05)


def runner(mb):
    def ask(text, ignore=False):
        raise AssertionError(f"the operator was asked: {text}")
    r = Runner(mb, NeoConfig({}, None), None, ask, log=lambda *a: None)
    r.job = SimpleNamespace(test=False)
    return r


def test_step_loss_rehomed():
    """steps lost on the way (the controller's correction gave up): XY homed, the target sent again
    from one side, the move done; nobody is asked"""
    mb = FakeMB()
    r = runner(mb)
    tries = []

    def go():
        tries.append(1)
        mb.move_xy(120.0, 80.0, wait=False)
        if len(tries) == 1:
            raise StepLoss("XY X step lost (states: busy, X step lost)")
        return "there"
    assert r.retry("Movement to pick position", go) == "there"
    kinds = [c[0] for c in mb.calls]
    assert kinds == ["move", "home", "move", "move"], mb.calls
    assert mb.calls[2][3] is True                         # sent again with the one-sided approach
    print("step loss mid-job: homed again and back to work:", mb.calls)


def test_step_loss_asks_after_tries():
    mb = FakeMB()
    r = runner(mb)
    asked = []
    r._ask = lambda text, ignore=False: asked.append(text) or "no"
    r.ask = lambda text, ignore=False: asked.append(text) or "no"

    def always():
        raise StepLoss("XY X step lost")
    try:
        r.retry("move", always)
    except Exception:
        pass
    assert [c[0] for c in mb.calls].count("home") == 2 and asked, (mb.calls, asked)


def test_place_approach():
    """from below-left: straight there at the run speed; from elsewhere: 1 mm before, then the last mm
    at nPlaceApproachSpeed (not speed 10)"""
    mb = FakeMB(pos=(50.0, 60.0))
    r = runner(mb)
    r.speed_cache = 80
    r.approach(100.0, 100.0)
    assert mb.calls == [("move", 100.0, 100.0, False, None)], mb.calls
    mb.calls, mb.pos = [], (150.0, 120.0)
    r.approach(100.0, 100.0)
    assert mb.calls == [("move", 99.0, 99.0, False, None), ("move", 100.0, 100.0, False, 40)], mb.calls
    print("place approach:", mb.calls)


def test_nearest_order():
    pts = {"a": (300.0, 100.0), "b": (100.0, 100.0), "c": (120.0, 110.0), "d": (290.0, 95.0)}
    order = Runner.nearest_order(list(pts), (90.0, 100.0), lambda k: pts[k])
    assert order == ["b", "c", "d", "a"], order
    print("cycle route:", order)


def test_same_height():
    r = runner(FakeMB())
    r.packages = {"0603": (1.6, 0.8, 0.45), "SOT-23": (2.9, 1.3, 1.1), "BIG": (10.0, 10.0, 4.0)}
    s = lambda fp, h=0.0: {"stack": SimpleNamespace(part_h=h, footprint=fp), "comp": SimpleNamespace(footprint=fp)}
    assert r.same_height([s("0603"), s("SOT-23"), s("unknown")])
    assert not r.same_height([s("0603"), s("BIG")])
    assert not r.same_height([s("x", 0.5), s("y", 3.0)])


if __name__ == "__main__":
    test_step_loss_rehomed()
    test_step_loss_asks_after_tries()
    test_place_approach()
    test_nearest_order()
    test_same_height()
