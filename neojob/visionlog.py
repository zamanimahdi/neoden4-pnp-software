"""Vision log: every image the job takes, with the result that was used, to calibrate the vision
later (tools/visionlog.py). Temporary by design: [Job] bVisionLog switches it on, and the whole
folder can simply be deleted.

    <neoden_dir>/VisionLog/<job>/<YYYY-MM-DD>/
        index.jsonl            one JSON object per line: {"id", "kind", "file", ...} and later
                               {"id", "result": {...}} lines that complete an image's record
        000123-up-n2.png       the frame as the vision read it (oriented, before any threshold)
        bg-n2-<time>.png       empty-nozzle backgrounds of the run (needed to re-run part vision)

kind: "part" (up camera, body or leads), "mark" (head camera), "pad" (pad alignment view),
"bg" (empty nozzle). The folder is kept under [Job] nVisionLogMaxMB: the oldest days go first.
"""
import json
import os
import shutil
import threading
import time

import cv2


class VisionLog:
    def __init__(self, root, job_name, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.on = bool(cfg.job("bVisionLog"))
        self.base = os.path.join(root, "VisionLog", os.path.splitext(os.path.basename(job_name))[0] or "job")
        self.dir = os.path.join(self.base, time.strftime("%Y-%m-%d"))
        self.lock = threading.Lock()
        self.n = 0
        self.bytes = None
        if self.on:
            os.makedirs(self.dir, exist_ok=True)
            idx = os.path.join(self.dir, "index.jsonl")
            if os.path.exists(idx):
                self.n = sum(1 for _ in open(idx, encoding="utf-8"))

    def _size(self, root):
        return sum(os.path.getsize(os.path.join(b, f)) for b, _, fs in os.walk(root) for f in fs)

    def _trim(self):
        """keep VisionLog/ under the cap: delete whole day folders, oldest first (never today's)"""
        cap = float(self.cfg.job("nVisionLogMaxMB")) * 1e6
        root = os.path.dirname(self.base)
        if self.bytes is None:
            self.bytes = self._size(root)
        if self.bytes <= cap:
            return
        days = sorted((d for j in os.listdir(root) for d in [os.path.join(root, j, x) for x in os.listdir(os.path.join(root, j))]
                       if os.path.isdir(d) and os.path.normpath(d) != os.path.normpath(self.dir)), key=os.path.basename)
        for d in days:
            if self.bytes <= cap * 0.8:
                break
            s = self._size(d)
            shutil.rmtree(d, ignore_errors=True)
            self.bytes -= s
            self.log(f"vision log: removed {d} ({s / 1e6:.0f} MB) to stay under {cap / 1e6:.0f} MB")

    def add(self, kind, img, **meta):
        """save one image and its record; returns its id (or None when the log is off)"""
        if not self.on or img is None:
            return None
        with self.lock:
            self.n += 1
            i = self.n
            tag = meta.get("nozzle")
            fn = f"{i:06d}-{kind}" + (f"-n{tag}" if tag else "") + ".png"
            try:
                ok, buf = cv2.imencode(".png", img)
                open(os.path.join(self.dir, fn), "wb").write(buf.tobytes())
                rec = {"id": i, "kind": kind, "file": fn, "time": time.strftime("%H:%M:%S"), **meta}
                with open(os.path.join(self.dir, "index.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=_plain) + "\n")
                self.bytes = (self.bytes or 0) + len(buf) + 300
                if i % 20 == 1:
                    self._trim()
            except OSError as e:
                self.log(f"vision log: {e} (switched off)")
                self.on = False
                return None
            return i

    def result(self, i, **res):
        """what was decided about image i afterwards (size check, reject reason, placed)"""
        if not self.on or i is None:
            return
        with self.lock:
            with open(os.path.join(self.dir, "index.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": i, "result": res}, default=_plain) + "\n")

    def background(self, n, img, **meta):
        return self.add("bg", img, nozzle=n, **meta)


def _plain(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def read(day_dir):
    """records of one day folder, the result lines merged into their image's record"""
    recs = {}
    for line in open(os.path.join(day_dir, "index.jsonl"), encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "result" in d and d["id"] in recs:
            recs[d["id"]].setdefault("results", []).append(d["result"])
        elif "kind" in d:
            recs[d["id"]] = d
    return [recs[k] for k in sorted(recs)]


def days(root):
    """all day folders under <neoden_dir>/VisionLog"""
    base = os.path.join(root, "VisionLog")
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, j, d) for j in os.listdir(base) if os.path.isdir(os.path.join(base, j))
                  for d in os.listdir(os.path.join(base, j)) if os.path.exists(os.path.join(base, j, d, "index.jsonl")))


def summary(root):
    """per job/day and footprint: images, found, rejected (by reason), size ratios"""
    out = []
    for d in days(root):
        recs = read(d)
        fps = {}
        for r in recs:
            if r["kind"] != "part":
                continue
            f = fps.setdefault(r.get("footprint") or "?", {"images": 0, "found": 0, "placed": 0, "rejected": {}, "ratio_w": [], "ratio_h": []})
            f["images"] += 1
            f["found"] += 1 if r.get("valid") else 0
            for res in r.get("results", []):
                if res.get("placed"):
                    f["placed"] += 1
                if res.get("reject"):
                    f["rejected"][res["reject"]] = f["rejected"].get(res["reject"], 0) + 1
                if res.get("ratio"):
                    f["ratio_w"].append(res["ratio"][0]); f["ratio_h"].append(res["ratio"][1])
        for f in fps.values():
            for k in ("ratio_w", "ratio_h"):
                v = sorted(f.pop(k))
                f[k.replace("ratio", "size_ratio")] = {"n": len(v), "min": v[0], "median": v[len(v) // 2], "max": v[-1]} if v else None
        size = sum(os.path.getsize(os.path.join(d, x)) for x in os.listdir(d))
        out.append({"dir": d, "job": os.path.basename(os.path.dirname(d)), "day": os.path.basename(d), "records": len(recs),
                    "kinds": {k: sum(1 for r in recs if r["kind"] == k) for k in ("part", "mark", "pad", "bg")},
                    "mb": round(size / 1e6, 1), "footprints": fps})
    return out
