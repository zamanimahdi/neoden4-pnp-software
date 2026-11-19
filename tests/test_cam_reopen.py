"""A failed camera grab: the old backend is closed (not only forgotten), the cameras are opened again
with the window / exposure / gain last set, and the grab is tried once more.

    python tests/test_cam_reopen.py

On the machine (2026-11-18) a dropped USB handle kept the device claimed: every later grab read
"[Errno 16] Resource busy" until neoserver was restarted.
"""
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import neocam.client  # noqa: E402
from neoserver.hw import Cameras  # noqa: E402

opened = []


class FakeCam:
    fail_next = 0

    def __init__(self, host, port):
        self.closed, self.calls = False, []
        opened.append(self)

    def window(self, cam, w, h): self.calls.append(("window", cam, w, h))
    def exposure(self, cam, v): self.calls.append(("exposure", cam, v))
    def gain(self, cam, v): self.calls.append(("gain", cam, v))
    def info(self): return "fake"
    def close(self): self.closed = True

    def grab(self, cam, timeout_ms=1000):
        if self.closed:
            raise OSError("[Errno 16] Resource busy")
        if FakeCam.fail_next:
            FakeCam.fail_next -= 1
            raise OSError("[Errno 16] Resource busy")
        return np.zeros((8, 8), np.uint8)


neocam.client.CamClient = FakeCam
logs = []
c = Cameras("tcp://fake:1", logs.append)

d = c.dev()
d.exposure(1, 77)                      # a vision task's own exposure
d.window(1, 512, 512)
assert d.grab(1).shape == (8, 8) and len(opened) == 1

FakeCam.fail_next = 1                  # one grab fails: closed, opened again, settings back, grabbed
assert c.dev().grab(1).shape == (8, 8)
assert len(opened) == 2 and opened[0].closed and not opened[1].closed
assert ("exposure", 1, 77) in opened[1].calls and ("window", 1, 512, 512) in opened[1].calls
assert any("opening the cameras again" in t for t in logs), logs

FakeCam.fail_next = 2                  # it fails again after the reopen: the error goes to the caller
try:
    c.dev().grab(1)
    raise AssertionError("no error")
except OSError:
    pass
assert len(opened) == 3 and opened[1].closed and opened[2].closed and c.cam is None

bmp = c.grab_bmp(1)                    # the next call opens the cameras once more
assert bmp[:2] == b"BM" and len(opened) == 4
c.info()
c.drop()
assert opened[3].closed and c.cam is None
print("test_cam_reopen: OK")

# a setting the camera already has is not sent again (each costs 50 - 100 ms of sleeps)
opened.clear(); FakeCam.fail_next = 0
c2 = Cameras("tcp://fake:1", lambda *a: None)
d = c2.dev()
n0 = len(opened[0].calls)
d.window(1, 800, 800); d.exposure(1, 40)
n1 = len(opened[0].calls)
assert n1 == n0 + 2
d.window(1, 800, 800); d.exposure(1, 40); d.exposure(0x80 | 1, 40)      # the same camera named the other way
assert len(opened[0].calls) == n1
d.exposure(1, 41)
assert opened[0].calls[-1] == ("exposure", 1, 41)
d.window(1, 1024, 1024); d.exposure(1, 41)                              # a new window: the exposure again
assert opened[0].calls[-2:] == [("window", 1, 1024, 1024), ("exposure", 1, 41)]
FakeCam.fail_next = 1                                                   # re-opened: sent again, then cached again
d.grab(1)
assert ("exposure", 1, 41) in opened[1].calls
n2 = len(opened[1].calls); d.exposure(1, 41)
assert len(opened[1].calls) == n2
print("test_cam_reopen: settings cache OK")
