"""neocam.usbcam against fake FX3 devices (no hardware, no libusb needed).

    python tests/test_usbcam.py
"""
import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam import usbcam as U  # noqa: E402


class Ep:
    def __init__(self, addr, attr):
        self.bEndpointAddress, self.bmAttributes = addr, attr


class FakeDev:
    """an FX3 camera: queues a frame on the bulk IN endpoint only after the 0xB1 trigger"""

    def __init__(self, product, pid=0x00F1):
        self.idProduct, self.product = pid, product
        self.log, self.w, self.h, self.exp = [], 1280, 1024, 25
        self.trig = threading.Event()

    def is_kernel_driver_active(self, i): return False
    def set_configuration(self): pass
    def get_active_configuration(self): return {(0, 0): [Ep(0x01, 2), Ep(0x81, 2)]}

    def ctrl_transfer(self, rt, cmd, value, index, data, timeout):
        assert rt == 0x40 and len(data) == 6 and data[0] == cmd and data[2] == value & 255 and data[4] == index & 255
        self.log.append((cmd, value, index))
        if cmd == U.CMD_WH: self.w, self.h = value, index
        if cmd == U.CMD_EXP: self.exp = value
        if cmd == U.CMD_CAPTURE: self.trig.set()
        return 6

    def read(self, ep, n, timeout):
        assert ep == 0x81
        if n == 0x1000:
            raise TimeoutError("flush")                       # nothing pending at init
        if not self.trig.wait(timeout / 1000):
            raise TimeoutError("no frame")
        self.trig.clear()
        img = np.full((self.h, self.w), min(255, self.exp), np.uint8)
        img[0, 0] = 7
        return bytearray(img.tobytes())[:n]

    def clear_halt(self, ep): self.log.append(("halt", ep))


def test_classify():
    assert U.classify(["HEAD CAM", "BOT 1 x", "BOT 4", "FLY", "FLY", "X"]) == [1, 5, 4, 2, 3, 0]


def test_open_window_grab():
    head, up = FakeDev("HCAM_V1"), FakeDev("Bcam1", pid=0x00F0)
    cams = U.UsbCameras.open(find=lambda: [head, up, FakeDev("x", pid=0x1234)])
    assert set(cams.cams) == {1, 5} and "id1='HCAM_V1'" in cams.info()
    cams.window(U.UP, 1024, 1024)
    assert up.log[-2:] == [(U.CMD_LT, 128, 0), (U.CMD_WH, 1024, 1024)]     # centred like CameraNew::setWindow
    cams.exposure(U.UP, 150)
    img = cams.grab(U.UP)
    assert img.shape == (1024, 1024) and img[0, 0] == 7 and img[5, 5] == 150
    cams.window(U.HEAD, 800, 800)
    assert head.log[-2:] == [(U.CMD_LT, 240, 112), (U.CMD_WH, 800, 800)]
    assert cams.grab(U.HEAD).shape == (800, 800)
    cams.led(U.HEAD, 2); cams.reset(U.HEAD)
    assert (U.CMD_LED, 2, 0) in head.log and (U.CMD_RESET, 0, 0) in head.log and ("halt", 0x81) in head.log


def test_errors():
    try:
        U.UsbCameras.open(find=lambda: [])
        assert False
    except U.CamError as e:
        assert "no NeoDen camera" in str(e)
    cams = U.UsbCameras.open(find=lambda: [FakeDev("HCAM")])
    try:
        cams.grab(U.UP)
        assert False
    except U.CamError as e:
        assert "camera 5 not connected" in str(e)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
