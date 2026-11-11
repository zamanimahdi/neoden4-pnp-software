"""Vision log: a short job with [Job] bVisionLog on, on the simulator with a camera that shows
0603 chips on the nozzle; then the analysis tool (summary, rerun, scale).

    python tests/test_visionlog.py
"""
import io
import math
import os
import sys
import tempfile
import threading
from contextlib import redirect_stdout

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from neocam.config import NeoConfig  # noqa: E402
from neojob import Runner, load_job, CameraVision, YES  # noqa: E402
from neojob import visionlog as VL  # noqa: E402
from neomach import Mainboard  # noqa: E402
import visionlog as tool  # noqa: E402

DATA = os.environ.get("NEODEN_DATA", "")
K = 34.5
SCALE = 1.03          # the true camera scale is 3 % more than configured: `scale` must find it


class Dev:
    """up camera: a 0603 (1.6 x 0.8 mm) on the nozzle, a little off; head: plain board"""
    def __init__(self):
        self.rng = np.random.default_rng(5)
        self.empty = True

    def window(self, *a):
        pass

    def exposure(self, *a):
        pass

    def info(self):
        return "fake"

    def grab(self, cam):
        img = np.full((1024, 1024), 30, np.uint8)
        cv2.circle(img, (512, 512), int(0.6 * K), 90, -1)
        if cam == 0 and not self.empty:
            x, y = self.rng.normal(0, 0.1, 2)
            box = cv2.boxPoints(((512 + x * K * SCALE, 512 - y * K * SCALE), (1.6 * K * SCALE, 0.8 * K * SCALE), self.rng.normal(0, 2)))
            cv2.fillPoly(img, [np.round(box).astype(np.int32)], 200)
        self.empty = False
        return cv2.transpose(cv2.flip(img, -1)) if cam == 0 else cv2.flip(img, -1)


def main():
    d = tempfile.mkdtemp()
    data = os.path.join(d, "data")
    os.makedirs(os.path.join(data, "Proc"))
    open(os.path.join(data, "Config.ini"), "wb").write(open(os.path.join(DATA, "Config.ini"), "rb").read()
                                                        + b"\n[Job]\nbSettle=false\nbVisionLog=true\n")
    job = os.path.join(data, "Proc", "chips.csv")
    lines = ["stack,1,0,1,400.00,100.00,0.00,C0603,100N,0.50,0,1.00,0,No,-40,1,80,4,50,80,No,Yes,-40,-40,-40,-40,",
             "pcb,Manual,Lock,100,100,350,200,Back,10,10,200,", "mark,Whole,Auto,", "test,No,", "mirror,100.00,100.00,0,No,"]
    lines += [f"comp,1,1,C{i},100N,C0603,{100 + 3 * i:.2f},100.00,0.00,No," for i in range(8)]
    open(job, "w").write("\n".join(lines) + "\n")
    cfg = NeoConfig.load(os.path.join(data, "Config.ini"))
    mb = Mainboard.connect("sim://")
    dev = Dev()
    vis = CameraVision(dev, mb, cfg, threading.Lock(), log=lambda *a: None)
    log = []
    r = Runner(mb, cfg, vis, lambda t, b: YES, log=log.append, packages={"C0603": (1.6, 0.8, 0.45)})
    assert r.run(load_job(job)), "\n".join(log[-15:])
    days = VL.days(data)
    assert len(days) == 1, days
    recs = VL.read(days[0])
    parts = [x for x in recs if x["kind"] == "part"]
    assert len(parts) == 8 and sum(1 for x in recs if x["kind"] == "bg") == 1, [x["kind"] for x in recs]
    assert all(p.get("valid") and p.get("results") for p in parts), parts[0]
    assert all(any(x.get("placed") for x in p["results"]) for p in parts)
    assert all(os.path.exists(os.path.join(days[0], p["file"])) for p in recs)
    s = VL.summary(data)[0]["footprints"]["C0603"]
    assert s["images"] == 8 and s["placed"] == 8, s
    print(f"log: {len(recs)} records, C0603 {s}")
    out = io.StringIO()
    with redirect_stdout(out):
        tool.main(["summary", data])
        tool.main(["rerun", data, "--thr", "60"])
        tool.main(["scale", days[0]]) if len(parts) >= 5 else None
    text = out.getvalue()
    print(text)
    k_new = float(text.split("suggested ")[1].split()[0])
    k_now = cfg.pxmm(0)
    assert abs(k_new / k_now - SCALE) < 0.03, (k_new, k_now)   # outline is ~1 px larger per side
    print(f"vision log: OK (scale found {k_new / k_now:.3f}, true {SCALE})")


if __name__ == "__main__":
    main()
