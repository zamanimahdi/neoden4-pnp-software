"""The vision log ([Job] bVisionLog): summary, re-run with other parameters, camera scale.

    python tools/visionlog.py summary DIR
    python tools/visionlog.py rerun DIR [--k 34.5] [--thr 50] [--roi 0] [--footprint 0603]
    python tools/visionlog.py scale DIR [--footprint 0603]

DIR = <neoden_dir> (all days) or one day folder of VisionLog/<job>/<date>. Copy the folder
from the machine with FTP first, or run it there over SSH.

summary  per footprint: images, recognised, placed, rejections by reason, size ratio
         (measured / package) min / median / max.
rerun    the up-camera part vision again on the stored images (body outline, NeoDen4's
         algorithm) with other px/mm, threshold or ROI. For each footprint it shows how many are
         recognised now vs. then, how far the result moved, and the size ratios.
scale    up-camera px/mm suggested by the parts: the median of measured / package size,
         over the parts with a package size (k_new = k x median ratio). Use parts with sharp,
         well-known sizes (chip resistors / capacitors); mark the result as a starting point and
         check it with a known part (CALIBRATION.md).
"""
import argparse
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neojob import visionlog as VL  # noqa: E402
from neocam import vision as V  # noqa: E402


def day_dirs(path):
    if os.path.exists(os.path.join(path, "index.jsonl")):
        return [path]
    return VL.days(path)


def load(d, fn):
    return cv2.imread(os.path.join(d, fn), cv2.IMREAD_GRAYSCALE)


def stats(v):
    v = sorted(x for x in v if x is not None and not math.isnan(x))
    return f"{v[0]:.3f} / {v[len(v) // 2]:.3f} / {v[-1]:.3f} (n={len(v)})" if v else "-"


def size_ratios(r):
    """measured / package along the package's own axes (w/h swapped for a 90 deg stack)"""
    lw = r.get("package_lw")
    if not lw or not r.get("valid") or not lw[0] or not lw[1]:
        return None
    w, h = r["w"], r["h"]
    if round((r.get("stack_angle") or 0) / 90) % 2:
        w, h = h, w
    return w / lw[0], h / lw[1]


def cmd_summary(a):
    for d in day_dirs(a.dir):
        recs = VL.read(d)
        print(f"\n{d}: {len(recs)} records")
        by = {}
        for r in recs:
            if r["kind"] != "part":
                continue
            by.setdefault(r.get("footprint") or "?", []).append(r)
        for fp, rs in sorted(by.items()):
            res = [x for r in rs for x in r.get("results", [])]
            rej = {}
            for x in res:
                if x.get("reject"):
                    rej[x["reject"]] = rej.get(x["reject"], 0) + 1
            rat = [size_ratios(r) for r in rs]
            print(f"  {fp:14s} images {len(rs):4d}  recognised {sum(1 for r in rs if r.get('valid')):4d}  "
                  f"placed {sum(1 for x in res if x.get('placed')):4d}  rejected {rej or '-'}")
            print(f"  {'':14s} size ratio W {stats([x[0] for x in rat if x])}   H {stats([x[1] for x in rat if x])}")
        marks = [r for r in recs if r["kind"] == "mark"]
        if marks:
            print(f"  marks: {len(marks)} images, found {sum(1 for r in marks if r.get('found'))}")
        pads = [r for r in recs if r["kind"] == "pad"]
        if pads:
            print(f"  pad views: {len(pads)}, pads found per view {stats([r.get('blobs') for r in pads])}")


def cmd_rerun(a):
    for d in day_dirs(a.dir):
        recs = VL.read(d)
        bgs = {r["id"]: r for r in recs if r["kind"] == "bg"}
        by = {}
        for r in recs:
            if r["kind"] != "part" or (a.footprint and r.get("footprint") != a.footprint):
                continue
            img = load(d, r["file"])
            b = bgs.get(r.get("bg_id"))
            bg = load(d, b["file"]) if b else np.zeros_like(img)
            k = a.k or r.get("px_per_mm") or 34.5
            roi = a.roi or r.get("roi") or 500
            n = V.analyze_nozzle(img, bg, int(roi), k, a.thr)
            e = by.setdefault(r.get("footprint") or "?", {"n": 0, "then": 0, "now": 0, "moved": [], "ratio_w": [], "ratio_h": []})
            e["n"] += 1
            e["then"] += 1 if r.get("valid") else 0
            e["now"] += 1 if n.valid else 0
            if n.valid and r.get("valid") and r.get("method") == "body":
                e["moved"].append(math.hypot(n.x - r["x"], n.y - r["y"]))
            q = size_ratios({**r, "valid": n.valid, "w": n.w, "h": n.h})
            if q:
                e["ratio_w"].append(q[0]); e["ratio_h"].append(q[1])
        print(f"\n{d}  (k {a.k or 'as logged'}, threshold {a.thr}, roi {a.roi or 'as logged'})")
        for fp, e in sorted(by.items()):
            print(f"  {fp:14s} {e['n']:4d} images: recognised then {e['then']}, now {e['now']}; "
                  f"result moved (mm) {stats(e['moved'])}")
            print(f"  {'':14s} size ratio W {stats(e['ratio_w'])}   H {stats(e['ratio_h'])}")


def cmd_scale(a):
    ks, rs = [], []
    for d in day_dirs(a.dir):
        for r in VL.read(d):
            if r["kind"] != "part" or (a.footprint and r.get("footprint") != a.footprint):
                continue
            q = size_ratios(r)
            if q and r.get("method") == "body":
                rs += list(q)
                ks.append(r.get("px_per_mm") or 34.5)
    if len(rs) < 10:
        sys.exit(f"only {len(rs) // 2} parts with a package size: place more first")
    rs.sort()
    med = rs[len(rs) // 2]
    k = sorted(ks)[len(ks) // 2]
    print(f"{len(rs) // 2} parts: median measured / package = {med:.4f}")
    print(f"up camera px/mm now {k:.3f} -> suggested {k * med:.3f}  ([Vision] fUpPxPerMm)")
    print("note: a part's outline in the image is a little larger than its datasheet size (edges, metal ends);"
          " confirm with a known part before writing it")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["summary", "rerun", "scale"])
    ap.add_argument("dir")
    ap.add_argument("--k", type=float)
    ap.add_argument("--thr", type=int, default=50)
    ap.add_argument("--roi", type=int, default=0)
    ap.add_argument("--footprint")
    a = ap.parse_args(argv)
    {"summary": cmd_summary, "rerun": cmd_rerun, "scale": cmd_scale}[a.cmd](a)


if __name__ == "__main__":
    main()
