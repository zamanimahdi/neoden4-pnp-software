"""What the job learns and counts while it runs:

PickLearner  each feeder's pick point corrected by where its parts really sit on the nozzle: the up
             camera measures every part's offset from the nozzle's axis; the mean of the last
             Job/nPickLearnSamples offsets is added to the feeder's pick point (kept next to the job:
             <job>.picklearn.json), so the nozzle goes to the middle of the part
Stats        every pick, vision result, placement and after-place check as one line of
             <data>/stats/<date>.jsonl; summary() adds them up per feeder, nozzle and part, warn()
             names a feeder or nozzle whose share of failures is high
placed_part  where a part landed: the head camera's picture before and after placing, their difference
             = the part; its rectangle against the place point and angle
"""
import json
import math
import os
import time
from collections import defaultdict, deque

import numpy as np


class PickLearner:
    def __init__(self, path, samples=5, max_mm=1.0, gain=0.8):
        self.path, self.samples, self.max_mm, self.gain = path, max(1, int(samples)), float(max_mm), float(gain)
        try:
            self.d = json.load(open(path, encoding="utf-8")) if path and os.path.exists(path) else {}
        except ValueError:
            self.d = {}

    def correction(self, sid):
        e = self.d.get(str(sid))
        return (e["dx"], e["dy"]) if e else (0.0, 0.0)

    def add(self, sid, dx, dy):
        """one part's offset from the nozzle's axis (mm, machine axes, the nozzle at 0 deg) after a pick
        at the corrected point; returns the new correction when it changed"""
        if not (math.hypot(dx, dy) <= self.max_mm):              # a part picked badly: not the feeder's error
            return None
        e = self.d.setdefault(str(sid), {"dx": 0.0, "dy": 0.0, "n": 0, "rest": []})
        e["rest"].append([round(dx, 4), round(dy, 4)])
        e["n"] += 1
        if len(e["rest"]) < self.samples:
            return None
        mx, my = (float(np.mean([r[0] for r in e["rest"]])), float(np.mean([r[1] for r in e["rest"]])))
        e["rest"] = []
        nx, ny = e["dx"] + self.gain * mx, e["dy"] + self.gain * my
        if math.hypot(nx, ny) > self.max_mm:                      # never further than this from the taught point
            f = self.max_mm / math.hypot(nx, ny)
            nx, ny = nx * f, ny * f
        e["dx"], e["dy"] = round(nx, 4), round(ny, 4)
        return e["dx"], e["dy"]

    def save(self):
        if self.path:
            tmp = self.path + ".tmp"
            json.dump(self.d, open(tmp, "w", encoding="utf-8"), indent=1)
            os.replace(tmp, self.path)


class Stats:
    """events as JSON lines, one file per day"""

    def __init__(self, data_dir, on=True):
        self.dir = os.path.join(data_dir, "stats") if data_dir else None
        self.on = bool(on and self.dir)
        self.recent = defaultdict(lambda: deque(maxlen=50))     # (kind, key) -> recent ok / fail
        self.warned = set()

    def add(self, kind, **f):
        f = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **f}
        for key in ("nozzle", "feeder"):
            if f.get(key) not in (None, "") and kind in ("pick", "vision"):
                self.recent[(key, f[key])].append(bool(f.get("ok", True)))
        if not self.on:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(os.path.join(self.dir, time.strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(f) + "\n")
        except OSError:
            pass

    def warn(self, pct=15.0, least=20):
        """feeders / nozzles whose failures in the last picks are pct % or more: new warnings only"""
        out = []
        for (key, v), q in self.recent.items():
            if len(q) >= least:
                bad = 100.0 * sum(1 for x in q if not x) / len(q)
                if bad >= pct and (key, v) not in self.warned:
                    self.warned.add((key, v))
                    what = f"nozzle {v}: clean or check it" if key == "nozzle" else f"feeder {v}: check the pick point, the tape and the feed"
                    out.append(f"warning: {bad:.0f} % failed in the last {len(q)} picks on {what}")
        return out


def read_stats(data_dir, days=7):
    """the events of the last `days` days"""
    d = os.path.join(data_dir, "stats")
    if not os.path.isdir(d):
        return []
    names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))[-max(1, int(days)):]
    ev = []
    for n in names:
        for line in open(os.path.join(d, n), encoding="utf-8"):
            try:
                ev.append(json.loads(line))
            except ValueError:
                pass
    return ev


def summary(events, warn_pct=15.0, least=20):
    """per feeder, nozzle and part: picks, pick failures, vision rejects, placed, after-place failures;
    the jobs (time, parts / hour); warnings"""
    groups = {"feeder": defaultdict(lambda: defaultdict(int)), "nozzle": defaultdict(lambda: defaultdict(int)),
              "part": defaultdict(lambda: defaultdict(int))}
    jobs, run_s, placed = 0, 0.0, 0
    for e in events:
        k = e.get("kind")
        if k == "job":
            jobs += 1
            run_s += float(e.get("seconds", 0) or 0)
            continue
        for g in groups:
            v = e.get(g)
            if v in (None, ""):
                continue
            c = groups[g][str(v)]
            if k == "pick":
                c["picks"] += 1
                c["pick_fail"] += 0 if e.get("ok") else 1
            elif k == "vision":
                c["vision_fail"] += 0 if e.get("ok") else 1
            elif k == "place":
                c["placed"] += 1
            elif k == "placecheck":
                c["checked"] += 1
                c["check_fail"] += 0 if e.get("ok") else 1
            elif k == "discard":
                c["discarded"] += 1
        if k == "place":
            placed += 1
    out = {}
    warnings = []
    for g, rows in groups.items():
        lst = []
        for v, c in rows.items():
            tries = c["picks"]
            bad = c["pick_fail"] + c["vision_fail"]
            pct = round(100.0 * bad / tries, 1) if tries else 0.0
            lst.append({"key": v, **{x: c[x] for x in ("picks", "pick_fail", "vision_fail", "placed", "discarded", "checked", "check_fail")}, "fail_pct": pct})
            if g != "part" and tries >= least and pct >= warn_pct:
                warnings.append(f"{g} {v}: {pct:.0f} % of {tries} picks failed" + (" — clean or check the nozzle" if g == "nozzle" else " — check its pick point, tape and feed"))
        out[g] = sorted(lst, key=lambda r: (-r["fail_pct"], r["key"]))
    return {**out, "jobs": jobs, "run_s": round(run_s), "placed": placed,
            "per_hour": round(placed / (run_s / 3600.0)) if run_s > 60 else None, "warnings": warnings}


def placed_part(before, after, k, length=0.0, width=0.0, min_diff=25):
    """the part placed between the two head-camera pictures (same place, centred on the place point):
    the changed area -> its rectangle. Returns {found, dx, dy (mm, machine: +X right, +Y up), angle (deg,
    CCW, the long side), L, W} or {found: False, why}"""
    import cv2
    a = cv2.GaussianBlur(before, (5, 5), 0).astype(np.int16)
    b = cv2.GaussianBlur(after, (5, 5), 0).astype(np.int16)
    d = np.abs(b - a).astype(np.uint8)
    m = (d > min_diff).astype(np.uint8)     # a fixed level above the camera's noise: a part changes a dark board
                                            # less than a bright pad, so no automatic (Otsu) split
    r = max(3, int(0.15 * k)) | 1
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((r * 2 + 1, r * 2 + 1), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((r, r), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return {"found": False, "why": "nothing changed where the part should be"}
    h, w = m.shape
    best = max(range(1, n), key=lambda i: st[i, 4] / (1.0 + 0.01 * math.hypot(cen[i][0] - w / 2, cen[i][1] - h / 2)))
    if st[best, 4] < 0.1 * k * k:
        return {"found": False, "why": "the change is too small for a part"}
    cs, _ = cv2.findContours((lab == best).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    (cx, cy), (a_, b_), ang = cv2.minAreaRect(max(cs, key=cv2.contourArea))
    if a_ < b_:
        a_, b_, ang = b_, a_, ang + 90
    out = {"found": True, "dx": (cx - w / 2) / k, "dy": (h / 2 - cy) / k, "angle": -((ang + 90) % 180 - 90),
           "L": a_ / k, "W": b_ / k, "px": (float(cx), float(cy)), "box": ((float(cx), float(cy)), (float(a_), float(b_)), float(ang))}
    if length > 0 and width > 0 and not (0.5 < out["L"] / max(length, width) < 1.8):
        out.update(found=False, why=f"the change ({out['L']:.2f} x {out['W']:.2f} mm) is not the part's size ({max(length, width):.2f} x {min(length, width):.2f})")
    return out
