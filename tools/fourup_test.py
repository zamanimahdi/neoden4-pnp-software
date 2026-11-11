"""Check the 4-up shot against single-nozzle shots on the machine.

    python tools/fourup_test.py --host neoden.local empty     # nozzles EMPTY: axes, scale, backgrounds
    (pick a part on each nozzle, e.g. on the Manual page)
    python tools/fourup_test.py --host neoden.local parts     # one 4-up shot + one shot per nozzle

empty: at posCamLookUpMouseMouseCenter the nozzles turn together (as neojob does at job start),
each axis is fitted, the scale where they stand comes from their spacing, and the 4-up
background is kept; then each nozzle's own background at its posCamLookUpMouse.
parts: every part's offset from its nozzle axis, read in the 4-up shot (its scale) and in its own
shot (fUpPxPerMm), side by side. State in ./fourup_state/. Client id "live-vision", hold=1.
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
ap.add_argument("--k", type=float, default=32.15, help="px/mm at the image centre (single shots)")
ap.add_argument("phase", choices=("empty", "parts"))
a = ap.parse_args()
B, C, D = f"http://{a.host}", "live-vision", "fourup_state"
os.makedirs(D, exist_ok=True)
NOZ = [int(s) for s in a.nozzles.split(",")]


def req(m, p, q=None, raw=False):
    r = urllib.request.Request(B + p + ("?" + urllib.parse.urlencode(q) if q else ""), data=b"" if m == "POST" else None, method=m)
    try:
        d = urllib.request.urlopen(r, timeout=120).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{p}: {e.code} {e.read()[:200]!r}")
    return d if raw else json.loads(d)


def mc(cmd, **q):
    return req("POST", "/api/mc/" + cmd, {"client": C, **q})


def move(x, y):
    mc("move", x=round(x, 4), y=round(y, 4), slow=1)
    for _ in range(120):
        if req("GET", "/api/mc/status").get("xy_state_name") == "idle":
            return
        time.sleep(0.5)


def grab(exp):
    req("POST", "/api/cam/set", {"cam": 0, "exposure": exp, "w": 1024, "h": 1024})
    req("GET", "/api/cam/grab", {"cam": 0}, raw=True)                 # the new exposure's first frame
    return cv2.imdecode(np.frombuffer(req("GET", "/api/cam/grab", {"cam": 0, "settle": 1}, raw=True), np.uint8), 0)


p = os.path.join(D, "Config.ini")
open(p, "wb").write(req("GET", "/api/file", {"path": "/opt/neoden/data/Config.ini"}, raw=True))
cfg = NeoConfig.load(p)
EXP4, EXP1, EXPT = cfg.up_exposure(True), cfg.up_exposure(False), int(cfg.vision("nTipExposure"))
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
try:
    if not req("GET", "/api/mc/status").get("pos"):
        mc("home")
    mc("raise")
    mc("led", v=0)
    mc("light", on=1)
    if a.phase == "empty":
        move(*cfg.four_up_position)
        k0 = cfg.pxmm(0)
        seen = {n: [] for n in NOZ}
        for t in (0, 60, 120, 180, -120, -60):
            for n in NOZ:
                mc("rotate", n=n, deg=t, wait=0)
            for n in NOZ:
                mc("rotate", n=n, deg=t)
            time.sleep(0.3)
            img = grab(EXPT)
            for n in NOZ:
                r = V.tip_centre(img, *cfg.sub_mouses[n - 1], search=int(1.2 * k0))
                if r:
                    seen[n].append(r)
        for n in NOZ:
            mc("rotate", n=n, deg=0)
        axes = {}
        for n in NOZ:
            rs = sorted(t[2] for t in seen[n])
            pts = [t[:2] for t in seen[n] if abs(t[2] - rs[len(rs) // 2]) <= 0.3 * rs[len(rs) // 2]]
            while len(pts) >= 4:
                (f, i) = min(((V.fit_circle(pts[:i] + pts[i + 1:]), i) for i in range(len(pts))), key=lambda q: q[0][3])
                if V.fit_circle(pts)[3] <= 0.7 or f[3] > 0.5 * V.fit_circle(pts)[3]:
                    break
                pts.pop(i)
            cx, cy, r, rms = V.fit_circle(pts)
            axes[n] = [cx, cy]
            print(f"nozzle {n}: axis {cx:.1f}, {cy:.1f} px from {len(pts)} of {len(seen[n])} tips, runout {r / a.k * 1000:.0f} um, rms {rms:.2f} px")
        ratios = sorted(math.dist(axes[i], axes[j]) / math.dist(cfg.nozzle_positions[i - 1], cfg.nozzle_positions[j - 1])
                        for i in NOZ for j in NOZ if i < j)
        k4 = ratios[len(ratios) // 2]
        print(f"4-up scale {k4:.2f} px/mm (pairs: {', '.join(f'{v:.2f}' for v in ratios)})")
        cv2.imwrite(os.path.join(D, "bg4.png"), grab(EXP4))
        single = {}
        for n in NOZ:                                      # each nozzle's axis in its own shot
            move(*cfg.nozzle_positions[n - 1])
            pts = []
            for t in (0, 90, 180, -90):
                mc("rotate", n=n, deg=t)
                time.sleep(0.3)
                r = V.tip_centre(grab(EXPT), 512, 512, search=60)
                if r:
                    pts.append(r[:2])
            mc("rotate", n=n, deg=0)
            single[n] = list(V.fit_circle(pts)[:2]) if len(pts) >= 3 else [512.0, 512.0]
            print(f"nozzle {n}: own shot axis {single[n][0]:.1f}, {single[n][1]:.1f} px")
            cv2.imwrite(os.path.join(D, f"bg{n}.png"), grab(EXP1))
        json.dump({"axes": axes, "k4": k4, "single": single}, open(os.path.join(D, "state.json"), "w"))
        print("empty nozzles done: now pick a part on each of nozzles", NOZ, "and run the 'parts' phase")
    else:
        st = json.load(open(os.path.join(D, "state.json")))
        move(*cfg.four_up_position)
        img = grab(EXP4)
        cv2.imwrite(os.path.join(D, "parts4.png"), img)
        bg4 = cv2.imread(os.path.join(D, "bg4.png"), 0)
        four = {}
        for n in NOZ:
            x, y = st["axes"][str(n)]
            four[n] = V.analyze_at(img, bg4, x, y, int(4.5 * st["k4"]), st["k4"])
        for n in NOZ:
            move(*cfg.nozzle_positions[n - 1])
            one = grab(EXP1)
            cv2.imwrite(os.path.join(D, f"part{n}.png"), one)
            sx, sy = st["single"][str(n)]
            s = V.analyze_at(one, cv2.imread(os.path.join(D, f"bg{n}.png"), 0), sx, sy, 300, a.k)
            f = four[n]
            if not (s.valid and f.valid):
                print(f"nozzle {n}: single {'ok' if s.valid else 'NOT FOUND'}, 4-up {'ok' if f.valid else 'NOT FOUND'}")
                continue
            print(f"nozzle {n}: single  {s.x:+.3f} {s.y:+.3f} mm {s.angle:+6.2f} deg  {s.w:.2f} x {s.h:.2f} mm\n"
                  f"          4-up    {f.x:+.3f} {f.y:+.3f} mm {f.angle:+6.2f} deg  {f.w:.2f} x {f.h:.2f} mm"
                  f"   difference {(f.x - s.x) * 1000:+.0f}, {(f.y - s.y) * 1000:+.0f} um")
finally:
    stop = True
    try:
        mc("light", on=0)
        req("POST", "/api/mc/unlock", {"client": C})
    except Exception:
        pass
