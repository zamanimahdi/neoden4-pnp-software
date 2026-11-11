"""camserver simulator: same protocol, synthetic images, for working without the machine.

    python -m neocam.simserver [--port 5600] [--images DIR]

With --images, DIR/up.png and DIR/head.png (8-bit gray, raw as the camera sends them)
are served instead of the synthetic scenes (e.g. frames saved from the real machine).
Synthetic scenes (sent un-rotated, like the real cameras): up camera = nozzle tips at the
centre and at the four 4-up positions, each with a rotated 0603-like part; head camera =
PCB-like texture with a 1 mm fiducial. Exposure x gain scales the brightness.
Simulator only: LED value 0 on the up camera removes the parts (to take a background).
"""
import argparse
import os
import socket
import struct
import threading

import cv2
import numpy as np

from .client import (_REQ, _REP, CMD_INFO, CMD_EXPOSURE, CMD_GAIN, CMD_WINDOW, CMD_LED,
                     CMD_GRAB, CMD_RESET, CMD_BYE)

SENSOR_W, SENSOR_H = 1280, 1024


SUB_MOUSES = [(161, 872), (888, 871), (159, 169), (887, 160)]   # sample Config.ini, 4-up image


def scene_up(w, h, rng, parts=True):
    """as NeoDen4 sees it after orient_up: nozzle tips (centre + the four 4-up positions),
    with 0603-like parts (1.6 x 0.8 mm at 34.5 px/mm) when parts=True"""
    img = rng.normal(20, 3, (h, w)).clip(0, 255).astype(np.uint8)
    spots = [(w / 2, h / 2)] + [(x * w / 1024, y * h / 1024) for x, y in SUB_MOUSES]
    for cx, cy in spots:
        cv2.circle(img, (int(cx), int(cy)), 20, 60, -1)
    if parts:
        for k, (cx, cy) in enumerate(spots):
            box = cv2.boxPoints(((cx + 12 - 4 * k, cy - 7 + 3 * k), (55, 28), -8.0 + 4 * k))
            cv2.fillConvexPoly(img, np.round(box).astype(np.int32), 200, lineType=cv2.LINE_AA)
    return cv2.flip(cv2.transpose(img), -1)          # raw = inverse of orient_up (same operation)


def scene_head(w, h, rng, parts=True):
    """PCB-like texture with a 1 mm fiducial (112 px/mm), raw = inverse of orient_head"""
    img = rng.normal(70, 6, (h, w)).clip(0, 255).astype(np.uint8)
    cv2.rectangle(img, (w // 2 - 300, h // 2 - 60), (w // 2 - 160, h // 2 + 60), 150, -1)   # pad
    cv2.circle(img, ((w // 2 + 37) * 16, (h // 2 - 21) * 16), 56 * 16, 225, -1, cv2.LINE_AA, 4)
    return cv2.flip(img, -1)


class Sim:
    def __init__(self, images=None):
        self.cam = {0: dict(exp=25, gain=8, w=1024, h=1024), 1: dict(exp=25, gain=8, w=1024, h=1024)}
        self.files = {}
        if images:
            for c, n in ((0, "up.png"), (1, "head.png")):
                p = os.path.join(images, n)
                if os.path.exists(p):
                    self.files[c] = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        self.rng = np.random.default_rng(1)
        self.parts = True                                     # LED 0 on the up camera removes the parts

    def frame(self, cam):
        s = self.cam[cam & 1]
        if (cam & 1) in self.files:
            img = cv2.resize(self.files[cam & 1], (s["w"], s["h"]))
        else:
            img = (scene_up if cam == 0 else scene_head)(s["w"], s["h"], self.rng, self.parts)
        k = s["exp"] / 25.0 * s["gain"] / 8.0                 # brightness follows exposure x gain
        return np.clip(img.astype(np.float32) * k, 0, 255).astype(np.uint8)

    def handle(self, conn):
        def recv(n):
            b = bytearray()
            while len(b) < n:
                k = conn.recv(n - len(b))
                if not k:
                    raise ConnectionError
                b += k
            return bytes(b)
        try:
            while True:
                cmd, cam, _, a, b, _ = _REQ.unpack(recv(_REQ.size))
                c = self.cam[cam & 1]
                if cmd == CMD_INFO:
                    t = f"camserver sim backend=sim cams=2 up={self.cam[0]['w']}x{self.cam[0]['h']} head={self.cam[1]['w']}x{self.cam[1]['h']}".encode()
                    conn.sendall(_REP.pack(1, len(t)) + t)
                    continue
                if cmd == CMD_GRAB:
                    img = self.frame(cam)
                    data = struct.pack("<HH", img.shape[1], img.shape[0]) + img.tobytes()
                    conn.sendall(_REP.pack(1, len(data)) + data)
                    continue
                ok = 1
                if cmd == CMD_EXPOSURE:
                    c["exp"] = max(1, a)
                elif cmd == CMD_GAIN:
                    c["gain"] = a
                elif cmd == CMD_WINDOW:
                    ok = int(16 <= a <= SENSOR_W and 16 <= b <= SENSOR_H)
                    if ok:
                        c["w"], c["h"] = a, b
                elif cmd == CMD_LED:
                    if (cam & 1) == 0:
                        self.parts = bool(a)
                elif cmd == CMD_RESET:
                    pass
                elif cmd == CMD_BYE:
                    conn.sendall(_REP.pack(1, 0))
                    return
                else:
                    ok = -1
                conn.sendall(_REP.pack(ok, 0))
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()


def serve(port=5600, images=None, host="127.0.0.1", ready=None):
    sim = Sim(images)
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((host, port))
    ls.listen(1)
    if ready:
        ready.set()
    while True:
        conn, _ = ls.accept()
        threading.Thread(target=sim.handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5600)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--images")
    a = ap.parse_args()
    print(f"camserver simulator on {a.host}:{a.port}")
    serve(a.port, a.images, a.host)
