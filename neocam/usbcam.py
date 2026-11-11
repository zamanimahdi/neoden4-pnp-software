"""Native driver for the NeoDen4's (new) cameras over libusb, for Linux: replaces NeodenCamera.dll.

The machine's cameras are Cypress FX3 devices (VID 04B4, the cyusb3 driver on XP; firmware in
the camera, nothing to download). Protocol from NeodenCamera.dll (RE notes: CAMSERVER.md):

  vendor request   bmRequestType 0x40, bRequest = cmd, wValue, wIndex, 6 data bytes
                   [cmd, 0, wValue lo, hi, wIndex lo, hi]           (CamMgr::SendVendorCmd)
     0xB1 capture (trigger)   0xB2 LED (value)   0xB3 reset (+ bulk endpoint reset)
     0xB4 exposure            0xB5 gain          0xB6 window left/top   0xB7 window width/height
  frame            8-bit gray, w*h bytes on the first bulk IN endpoint; the read is queued
                   first, then 0xB1 is sent (CamMgr::ReadAsyncChunked); 1000 ms timeout
  camera ids       from the product string (CamMgr::OpenAll): 'H' -> 1 (head), 'B..4' -> 4,
                   'B..1' -> 5 (up-looking), 'F' -> 2 (a second 'F' -> 3)

    cams = UsbCameras.open()            # same methods as neocam.CamClient
    cams.window(UP, 1024, 1024); cams.exposure(UP, 150); img = cams.grab(UP)

Needs pyusb and libusb-1.0 (apt install python3-usb) and access to the device (udev rule in
the image: neodenusb.rules).
"""
import threading
import time

import numpy as np

UP, HEAD = 0, 1
VID = 0x04B4
PIDS = (0x00F0, 0x00F1, 0x00F3, 0x4720, 0x00B0, 0x00BC)     # cyusb3.inf on the machine
# the cameras of the user's machine enumerate as 52CB:52CB ("Neoden HangZhou H0001 / B0001
# Camera Shibz", seen on the machine's USB bus on 2026-11-02)
KNOWN = {VID: PIDS, 0x52CB: (0x52CB,)}
SENSOR_W, SENSOR_H = 1280, 1024
CMD_CAPTURE, CMD_LED, CMD_RESET, CMD_EXP, CMD_GAIN, CMD_LT, CMD_WH = 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7


class CamError(RuntimeError):
    pass


def classify(names):
    """product strings -> DLL camera ids, exactly like CamMgr::OpenAll"""
    ids = []
    for s in names:
        s = s or ""
        if s[:1] == "H":
            ids.append(1)
        elif s[:1] == "B":
            ids.append(4 if s[4:5] == "4" else 5 if s[4:5] == "1" else 0)
        elif s[:1] == "F":
            ids.append(3 if 2 in ids else 2)
        else:
            ids.append(0)
    return ids


class _Cam:
    def __init__(self, dev, ep_in, cam_id, name):
        self.dev, self.ep, self.id, self.name = dev, ep_in, cam_id, name
        self.w, self.h = SENSOR_W, SENSOR_H
        self.lock = threading.Lock()

    def vendor(self, cmd, value=0, index=0):
        data = bytes([cmd, 0, value & 255, value >> 8 & 255, index & 255, index >> 8 & 255])
        n = self.dev.ctrl_transfer(0x40, cmd, value & 0xFFFF, index & 0xFFFF, data, 1000)
        if n != 6:
            raise CamError(f"camera {self.id}: vendor 0x{cmd:02X} sent {n} of 6 bytes")

    def read(self, n, timeout_ms):
        return self.dev.read(self.ep, n, timeout_ms)


class UsbCameras:
    """All NeoDen FX3 cameras on the USB bus; logical cams 0 = up (DLL id 5), 1 = head (id 1)."""

    def __init__(self, cams):
        self.cams = {c.id: c for c in cams}

    @classmethod
    def open(cls, backend=None, find=None):
        """find(): optional replacement of usb.core.find (tests)"""
        if find is None:
            import usb.core
            import usb.util
            find = lambda: [d for v in KNOWN for d in (usb.core.find(find_all=True, idVendor=v, backend=backend) or [])]
        devs = [d for d in find() if d.idProduct in KNOWN.get(getattr(d, "idVendor", VID), ())]
        if not devs:
            raise CamError("no NeoDen camera on USB (VID 04B4 or 52CB)")
        names = []
        for d in devs:
            try:
                names.append(d.product)
            except Exception:                       # no permission to read strings: udev rule missing
                names.append("")
        cams = []
        try:
            for d, cid, name in zip(devs, classify(names), names):
                if not cid:
                    continue
                c = cls._open_one(d, cid, name)
                if c:
                    cams.append(c)
        except Exception:
            # release every handle taken so far: a half-open camera kept the device claimed and
            # every later open failed with "Resource busy" (seen on the machine, 2026-11-02)
            for d in devs:
                try:
                    import usb.util
                    usb.util.dispose_resources(d)
                except Exception:
                    pass
            raise
        if not cams:
            raise CamError("NeoDen cameras found but none identified (product strings: %s)" % names)
        return cls(cams)

    @staticmethod
    def _open_one(d, cid, name):
        try:
            if d.is_kernel_driver_active(0):
                d.detach_kernel_driver(0)
        except Exception:
            pass
        try:
            cfg = d.get_active_configuration()       # already configured: leave it (set_configuration
        except Exception:                            # on a configured device can fail with EBUSY)
            cfg = None
        if cfg is None:
            d.set_configuration()
        intf = d.get_active_configuration()[(0, 0)]
        ep = next((e for e in intf if (e.bmAttributes & 3) == 2 and e.bEndpointAddress & 0x80), None)
        if ep is None:
            return None
        c = _Cam(d, ep.bEndpointAddress, cid, name)
        try:                                        # img_init: flush 4 KB (500 ms) once
            c.read(0x1000, 500)
        except Exception:
            pass
        return c

    # ---- same API as neocam.CamClient ----
    def _cam(self, cam):
        cid = cam & 0x7F if cam & 0x80 else (5 if cam == UP else 1)
        c = self.cams.get(cid)
        if c is None:
            raise CamError(f"camera {cid} not connected ({', '.join(f'{k}: {v.name}' for k, v in self.cams.items())})")
        return c

    def info(self):
        return "usbcam " + " ".join(f"id{c.id}={c.name!r}:{c.w}x{c.h}" for c in self.cams.values())

    def exposure(self, cam, value):
        c = self._cam(cam)
        with c.lock:
            c.vendor(CMD_EXP, max(1, int(value)))
        time.sleep(0.05)

    def gain(self, cam, value):
        c = self._cam(cam)
        with c.lock:
            c.vendor(CMD_GAIN, int(value))
        time.sleep(0.05)

    def window(self, cam, w=1024, h=1024):
        """centred w x h window of the 1280 x 1024 sensor (CameraNew::setWindow)"""
        if not (16 <= w <= SENSOR_W and 16 <= h <= SENSOR_H):
            raise CamError("window must be 16..1280 x 16..1024")
        c = self._cam(cam)
        with c.lock:
            c.vendor(CMD_LT, (SENSOR_W - w) // 2, (SENSOR_H - h) // 2)
            time.sleep(0.05)
            c.vendor(CMD_WH, w, h)
            time.sleep(0.05)
            c.w, c.h = w, h

    def led(self, cam, value):
        c = self._cam(cam)
        with c.lock:
            c.vendor(CMD_LED, int(value))

    def reset(self, cam):
        c = self._cam(cam)
        with c.lock:
            c.vendor(CMD_RESET)
            try:
                c.dev.clear_halt(c.ep)
            except Exception:
                pass

    def grab(self, cam, timeout_ms=1000):
        """one frame, 8-bit gray (h, w), as delivered (not rotated)"""
        c = self._cam(cam)
        n = c.w * c.h
        with c.lock:
            box = {}

            def rd():                               # the read must be pending before the trigger
                try:
                    box["data"] = c.read(n, timeout_ms)
                except Exception as e:
                    box["err"] = e
            t = threading.Thread(target=rd, daemon=True)
            t.start()
            time.sleep(0.002)
            c.vendor(CMD_CAPTURE)
            t.join(timeout_ms / 1000 + 1)
        if "err" in box or len(box.get("data", ())) != n:
            raise CamError(f"grab camera {c.id}: {box.get('err', 'short frame %d of %d' % (len(box.get('data', ())), n))}")
        return np.frombuffer(bytes(box["data"]), np.uint8).reshape(c.h, c.w).copy()

    def close(self):
        try:
            import usb.util
            for c in self.cams.values():
                usb.util.dispose_resources(c.dev)
        except Exception:
            pass
