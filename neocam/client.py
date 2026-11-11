"""Client for camserver (the camera server on the NeoDen4's own PC).

    cam = CamClient("192.168.1.50")         # default port 5600
    print(cam.info())
    cam.exposure(UP, 40); cam.gain(UP, 8)
    img = cam.grab(UP)                      # numpy uint8 (h, w), as delivered by the camera

Protocol (little-endian): request = u8 cmd, u8 cam, u16 flags, i32 a, i32 b, i32 c;
reply = i32 status (1 = ok), u32 len, then len bytes. See docs/CAMSERVER.md.
"""
import socket
import struct

import numpy as np

UP, HEAD = 0, 1                     # logical cameras: up-looking fixed camera, head camera
CMD_INFO, CMD_EXPOSURE, CMD_GAIN, CMD_WINDOW, CMD_LED, CMD_GRAB, CMD_RESET, CMD_BYE = range(1, 9)
_REQ = struct.Struct("<BBHiii")
_REP = struct.Struct("<iI")


class CamError(RuntimeError):
    pass


class CamClient:
    def __init__(self, host="127.0.0.1", port=5600, timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # -- transport --
    def _recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            k = self.sock.recv(n - len(buf))
            if not k:
                raise CamError("camserver closed the connection")
            buf += k
        return bytes(buf)

    def _call(self, cmd, cam=0, a=0, b=0, c=0):
        self.sock.sendall(_REQ.pack(cmd, cam, 0, a, b, c))
        status, n = _REP.unpack(self._recv(_REP.size))
        return status, (self._recv(n) if n else b"")

    def _ok(self, what, cmd, cam, *args):
        status, _ = self._call(cmd, cam, *args)
        if status != 1:
            raise CamError(f"{what} failed (status {status})")

    # -- API --
    def info(self):
        return self._call(CMD_INFO)[1].decode("ascii", "replace")

    def exposure(self, cam, value):
        self._ok("exposure", CMD_EXPOSURE, cam, int(value))

    def gain(self, cam, value):
        self._ok("gain", CMD_GAIN, cam, int(value))

    def window(self, cam, w=1024, h=1024):
        """centred w x h window of the 1280 x 1024 sensor"""
        self._ok("window", CMD_WINDOW, cam, int(w), int(h))

    def led(self, cam, value):
        """camera LED (new camera only)"""
        self._ok("led", CMD_LED, cam, int(value))

    def reset(self, cam):
        self._ok("reset", CMD_RESET, cam)

    def grab(self, cam, timeout_ms=1000):
        """one 8-bit gray frame, not rotated; raises CamError when the camera gives nothing"""
        old = self.sock.gettimeout()
        self.sock.settimeout(max(old or 0, timeout_ms / 1000 + 5))
        try:
            status, data = self._call(CMD_GRAB, cam, int(timeout_ms))
        finally:
            self.sock.settimeout(old)
        if status != 1 or len(data) < 4:
            raise CamError(f"grab cam {cam} failed")
        w, h = struct.unpack_from("<HH", data)
        return np.frombuffer(data, np.uint8, w * h, 4).reshape(h, w).copy()

    def close(self):
        try:
            self._call(CMD_BYE)
        except OSError:
            pass
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
