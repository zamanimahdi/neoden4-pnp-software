"""Does the nozzle tip seem to move sideways as it goes down, and how: the same way everywhere
(a tilt: of the up camera, or of the head's Z) or away from the image middle (perspective)?

    python tools/zdepth_fourup.py --host neoden.local [--nozzles 1,3,4] [--depths 0,2.5,5,7.5]

The head stands at posCamLookUpMouseMouseCenter, so each nozzle is near a corner of the up camera
picture. One nozzle at a time goes down step by step; at each depth it turns 0/90/180/-90 deg and
the circle through its hole gives the axis (runout removed). The axis shift against depth 0 is
split into a radial part (along the line from the image middle) and the rest. Perspective
predicts shift = (axis0 - middle) x (r/r0 - 1) with r the hole radius; a tilt adds the same vector
for every nozzle. Writes ./zdepth_<time>/result.json and the pictures. Watch the nozzles: STOP
on the machine stops it.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocam import vision as V  # noqa: E402
from neocam.config import NeoConfig  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="neoden.local")
ap.add_argument("--nozzles", default="1,3,4")
ap.add_argument("--depths", default="0,2.5,5,7.5")
ap.add_argument("--exposure", type=int, default=600)
a = ap.parse_args()
B, C = f"http://{a.host}", "live-vision"
OUT = time.strftime("zdepth_%H%M%S")
os.makedirs(OUT, exist_ok=True)
NOZ = [int(s) for s in a.nozzles.split(",")]
DEPTHS = [float(s) for s in a.depths.split(",")]


def req(m, p, q=None, raw=False):
    r = urllib.request.Request(B + p + ("?" + urllib.parse.urlencode(q) if q else ""), data=b"" if m == "POST" else None, method=m)
    try:
        d = urllib.request.urlopen(r, timeout=120).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{p}: {e.code} {e.read()[:200]!r}")
    return d if raw else json.loads(d)


def mc(cmd, **q):
    return req("POST", "/api/mc/" + cmd, {"client": C, **q})


def grab():
    return cv2.imdecode(np.frombuffer(req("GET", "/api/cam/grab", {"cam": 0, "settle": 1}, raw=True), np.uint8), 0)


p = os.path.join(OUT, "Config.ini")
open(p, "wb").write(req("GET", "/api/file", {"path": "/opt/neoden/data/Config.ini"}, raw=True))
cfg = NeoConfig.load(p)
assert req("POST", "/api/mc/lock", {"client": C, "hold": 1}).get("ok"), "the machine is controlled by another client"
stop = False


def hb():
    while not stop:
        try:
            req("POST", "/api/mc/lock", {"client": C, "hold": 1})
        except Exception:
            pass
        time.sleep(4)


threading.Thread(target=hb, daemon=True).start()
res = {}
try:
    if not req("GET", "/api/mc/status").get("pos"):
        mc("home")
    mc("raise")
    mc("led", v=0)
    mc("light", on=1)
    req("POST", "/api/cam/set", {"cam": 0, "exposure": a.exposure, "w": 1024, "h": 1024})
    mc("move", x=cfg.four_up_position[0], y=cfg.four_up_position[1], slow=1)
    for _ in range(120):
        if req("GET", "/api/mc/status").get("xy_state_name") == "idle":
            break
        time.sleep(0.5)
    for n in NOZ:
        guess = cfg.sub_mouses[n - 1]
        r0 = None
        res[n] = []
        for z in DEPTHS:
            # where the axis should be: the calibrated point at depth 0 (small window: a dark speck
            # next to the hole fooled a wide one), then the trend of the depths seen so far
            if len(res[n]) >= 2:
                (z1, p1), (z2, p2) = [(q["z"], q["axis"]) for q in res[n][-2:]]
                f = (z - z2) / (z2 - z1)
                guess = (p2[0] + f * (p2[0] - p1[0]), p2[1] + f * (p2[1] - p1[1]))
            win = 20 if not res[n] else 30
            mc("z", n=n, mm=z)
            time.sleep(0.3)
            pts = []
            for t in (0, 90, 180, -90):
                mc("rotate", n=n, deg=t)
                time.sleep(0.3)
                img = grab()
                cv2.imwrite(os.path.join(OUT, f"n{n}_z{z:g}_{t}.png"), img)
                h = V.tip_centre(img, guess[0], guess[1], search=win)
                if h and (4 <= h[2] <= 15 if r0 is None else 0.85 * r0 <= h[2] <= 1.8 * r0):
                    pts.append(h)
            mc("rotate", n=n, deg=0)
            if len(pts) < 3:
                print(f"nozzle {n} at {z:g} mm: hole seen {len(pts)} of 4 times", flush=True)
                continue
            cx, cy, rr, rms = V.fit_circle([q[:2] for q in pts])
            r = float(np.median([q[2] for q in pts]))
            if r0 is None:
                r0 = r
            guess = (cx, cy)
            res[n].append({"z": z, "axis": [cx, cy], "r": r, "runout_px": rr, "rms_px": rms})
            print(f"nozzle {n} at {z:g} mm: axis {cx:.2f}, {cy:.2f} px, hole r {r:.1f} px, fit rms {rms:.2f} px", flush=True)
        mc("z", n=n, mm=0)
finally:
    stop = True
    try:
        mc("raise")
        mc("light", on=0)
        req("POST", "/api/mc/unlock", {"client": C})
    except Exception:
        pass

# ---- analysis: perspective (radial, grows with r/r0) vs the same vector for every nozzle ----
k4 = 34.1
cx0, cy0 = 512.0, 512.0
print("\nshift against depth 0, px (measured | perspective would give | rest):")
rest = {}
for n, rows in res.items():
    if not rows:
        continue
    a0, r0 = np.array(rows[0]["axis"]), rows[0]["r"]
    for row in rows[1:]:
        m = np.array(row["axis"]) - a0
        pers = (a0 - [cx0, cy0]) * (row["r"] / r0 - 1)
        rst = m - pers
        rest.setdefault(row["z"], []).append((n, rst))
        print(f"  nozzle {n} {row['z']:4.1f} mm: {m[0]:+6.2f} {m[1]:+6.2f} | {pers[0]:+6.2f} {pers[1]:+6.2f} | "
              f"{rst[0]:+6.2f} {rst[1]:+6.2f}  (rest {np.hypot(*rst) / k4 * 1000:.0f} um, "
              f"{np.hypot(*rst) / k4 * 1000 / row['z']:.1f} um/mm)")
for z, v in sorted(rest.items()):
    arr = np.array([x[1] for x in v])
    print(f"depth {z:g} mm: rest mean {arr.mean(0)[0]:+.2f} {arr.mean(0)[1]:+.2f} px, spread {arr.std(0)[0]:.2f} {arr.std(0)[1]:.2f} px "
          f"(same for all = a tilt of the camera or of every Z; different = per nozzle)")
json.dump({str(k): v for k, v in res.items()}, open(os.path.join(OUT, "result.json"), "w"), indent=1)
print("saved", os.path.abspath(OUT))
