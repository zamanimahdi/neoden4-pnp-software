"""The live pad-alignment tools (neoserver/align.py) on the machine simulator with a camera that
draws a board under the head: fiducials and a QFP's pads, the board shifted and turned, the QFP
a little off its place, and the head camera slightly turned.

    python tests/test_align_live.py

Checks: detect finds the pads; camcal measures px/mm and the camera angle; marks finds the
three fiducials; part finds the QFP's own offset on top of the board transform.
"""
import json
import math
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from neoserver.align import AlignService  # noqa: E402
from neoserver.hw import Machine  # noqa: E402
from neojob.pads import pads_path  # noqa: E402

K = 112.0
CAM_ANGLE = 0.4                     # the real camera is turned this much (deg)
THETA, SHIFT = 0.5, (1.2, -0.7)     # board on the table vs. the job
QFP_OFF = (0.08, -0.05, 0.3)        # the QFP's pads vs. where the board transform puts them
DATA = os.environ.get("NEODEN_DATA", "")


def board(x, y):
    c, s = math.cos(math.radians(THETA)), math.sin(math.radians(THETA))
    return c * x - s * y + SHIFT[0], s * x + c * y + SHIFT[1]


def qfp(cx, cy):
    out = []
    for i in range(16):
        o = (i - 7.5) * 0.5
        out += [[cx + o, cy - 7.7, 0.28, 1.5], [cx + o, cy + 7.7, 0.28, 1.5], [cx - 7.7, cy + o, 1.5, 0.28], [cx + 7.7, cy + o, 1.5, 0.28]]
    return out


MARKS = [(150.0, 100.0), (250.0, 100.0), (150.0, 180.0)]
QC = (200.0, 140.0)
PADS = qfp(*QC)


class Log:
    def __call__(self, text, src="server"):
        pass


class Cam:
    """camera device: grab(1) draws what the head camera sees at the machine's position"""
    def __init__(self, mc, cam_off):
        self.mc, self.off = mc, cam_off
        self.items = []
        for m in MARKS:
            self.items.append(("c", board(*m), 1.0))
        cx, cy = board(*QC)
        dth = QFP_OFF[2] + THETA
        for x, y, w, h in PADS:
            bx, by = board(x, y)
            c, s = math.cos(math.radians(QFP_OFF[2])), math.sin(math.radians(QFP_OFF[2]))
            ux, uy = bx - cx, by - cy
            self.items.append(("r", (c * ux - s * uy + cx + QFP_OFF[0], s * ux + c * uy + cy + QFP_OFF[1]), (w, h, dth)))

    def info(self):
        return "fake board camera"

    def window(self, cam, w, h):
        pass

    def exposure(self, cam, v):
        pass

    def grab(self, cam):
        mb = self.mc.board()
        vx, vy = mb.pos[0] - self.off[0], mb.pos[1] - self.off[1]
        img = np.full((800, 800), 45, np.uint8)
        ca, sa = math.cos(math.radians(-CAM_ANGLE)), math.sin(math.radians(-CAM_ANGLE))

        def px(x, y):                    # machine -> pixel of a camera turned by CAM_ANGLE
            dx, dy = x - vx, y - vy
            dx, dy = ca * dx - sa * dy, sa * dx + ca * dy
            return 400 + dx * K, 400 - dy * K
        for kind, (x, y), a in self.items:
            if kind == "c":
                cv2.circle(img, tuple(int(round(v)) for v in px(x, y)), int(a / 2 * K), 210, -1, cv2.LINE_AA)
            else:
                w, h, d = a
                box = cv2.boxPoints((px(x, y), (w * K, h * K), -(d - CAM_ANGLE)))
                cv2.fillPoly(img, [np.round(box).astype(np.int32)], 200)
        img = cv2.GaussianBlur(img, (5, 5), 1.0)
        img = np.clip(img + np.random.default_rng(int(vx * 100) % 1000).normal(0, 4, img.shape), 0, 255).astype(np.uint8)
        return cv2.flip(img, -1)        # raw sensor image: CameraVision turns it back


class Cameras:
    def __init__(self, dev):
        import threading
        self.d, self.lock = dev, threading.Lock()

    def dev(self):
        return self.d


def main():
    d = tempfile.mkdtemp()
    data = os.path.join(d, "data")
    os.makedirs(os.path.join(data, "Proc"))
    ini = open(os.path.join(DATA, "Config.ini"), "rb").read() + (
        b"\n[Job]\nbSettle=false\nsMarkFit=affine\n[PadAlign]\nbEnabled=true\nsRefs=U1\n")
    open(os.path.join(data, "Config.ini"), "wb").write(ini)
    job = os.path.join(data, "Proc", "t.csv")
    open(job, "w").write(
        "stack,1,0,1,400.00,100.00,0.00,QFP64,U1,1.00,100,1.00,100,No,-40,1,50,4,50,80,No,No,-40,-40,-40,-40,\n"
        "pcb,Manual,Lock,100,100,350,200,Back,10,10,200,\n"
        "mark,Whole,Auto," + ",".join(f"{x},{y}" for x, y in MARKS) + ",\n"
        "test,No,\nmirror,200.00,140.00,0,No,\n"
        "comp,1,1,U1,QFP64,QFP64,200.00,140.00,0.00,No,\n")
    # the Gerber's round pads of no part: the three marks, and a test point that is no fiducial
    json.dump({"parts": {"U1": {"x": QC[0], "y": QC[1], "pads": PADS}},
               "fiducials": [[x, y, 1.0] for x, y in MARKS] + [[180.0, 120.0, 1.0]]}, open(pads_path(job), "w"))
    class St:
        neoden_dir = data
    mc = Machine("sim://", Log())
    mc.home_at_start = False            # the test homes itself (startup below), not in the background
    from neocam.config import NeoConfig
    cfg = NeoConfig.load(os.path.join(data, "Config.ini"))
    cams = Cameras(Cam(mc, cfg.head_cam_offset))
    al = AlignService(St, mc, cams, Log())
    assert mc.take("t")[0]
    mc.board().startup()
    # detect: the QFP's corner
    r = al.detect({"client": "t", "x": str(board(*QC)[0] - 5.5), "y": str(board(*QC)[1] - 5.5)})
    assert len(r["blobs"]) >= 5 and r["png"], len(r["blobs"])
    print(f"detect: {len(r['blobs'])} pads, threshold {r['threshold']:.0f}, mean {r['mean']}")
    # camcal over the same corner
    c = al.camcal({"client": "t", "x": str(board(*QC)[0] - 7.7), "y": str(board(*QC)[1]), "step": "1"})
    assert abs(c["px_per_mm"] - K) < 0.6 and abs(c["cam_angle_deg"] - CAM_ANGLE) < 0.1, c
    print(f"camcal: {c['px_per_mm']} px/mm, camera angle {c['cam_angle_deg']} deg (true {K}, {CAM_ANGLE})")
    # with the camera angle written in, marks and the part
    with open(os.path.join(data, "Config.ini"), "ab") as f:
        f.write(f"fHeadCamAngle={c['cam_angle_deg']}\n".encode())
    am = al.automarks({"client": "t", "job": job})
    got = [c for c in am["candidates"] if c["found"]]
    assert len(got) == 3 and sorted(am["suggest"]) == [0, 1, 2], am
    for c in got:
        bx, by = board(*c["design"])
        assert math.dist(c["measured"], (bx, by)) < 0.02, c
    print(f"automarks: {len(got)} of {len(am['candidates'])} Gerber candidates found, suggested {am['suggest']}")
    m = al.marks({"client": "t", "job": job})
    for mk in m["marks"]:
        assert math.dist(mk["measured"], board(*mk["design"])) < 0.02, mk
    print(f"marks: {len(m['marks'])} found, fit {m['fit']}, residuals {m['residuals_mm']}")
    assert mc.take("t", hold=True)[0]               # the searches above took longer than the lock lasts
    p = al.part({"client": "t", "job": job, "ref": "U1"})
    assert p["aligned"], p["message"]
    assert abs(p["dx"] - QFP_OFF[0]) < 0.02 and abs(p["dy"] - QFP_OFF[1]) < 0.02 and abs(p["dtheta"] - QFP_OFF[2]) < 0.06, p
    print(f"part U1: {p['message']} (true {QFP_OFF[0]:+.3f}, {QFP_OFF[1]:+.3f}, {QFP_OFF[2]:+.2f}); views {len(p['views'])}")
    assert al.parts({"job": job})["parts"][0]["ref"] == "U1"
    print("live pad alignment tools: OK")


if __name__ == "__main__":
    main()
