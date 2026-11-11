"""SMD tape under the head camera: the pockets (and the parts in them) and the sprocket holes, to set
a feeder's pick point (job file editor -> Stack setup -> camera).

    found = find_pockets(img, k)        # img: oriented grey head image, k: px per mm
    -> [{"px", "py", "w", "h", "angle", "kind": "pocket" | "hole", "dark": bool}], nearest pocket first

A pocket is a rectangle 0.4 .. 9 mm a side, darker (paper tape: punched hole; embossed tape: the
cavity) or brighter (a part lying in it) than the tape around it. A sprocket hole is a circle of
1.5 mm (EIA-481: 1.5 mm, 4 mm apart); it is reported so it is not taken for a pocket.
"""
import math

import cv2
import numpy as np


def _blobs(work, k, dark):
    cnts, hier = cv2.findContours(work, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)   # outer boundaries: no parent
    H, W = work.shape
    out = []
    for c, hh in zip(cnts, hier[0] if hier is not None else []):
        if hh[3] != -1:
            continue
        a = cv2.contourArea(c)
        if a < 0.12 * k * k or a > 60 * k * k:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1:
            continue                                        # cut by the picture edge: centre unknown
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
        if rw < 1 or rh < 1:
            continue
        wm, hm = rw / k, rh / k
        fill = a / (rw * rh)
        if 1.2 <= wm <= 1.9 and 1.2 <= hm <= 1.9 and 0.72 <= fill <= 0.86 and dark:
            kind = "hole"
        elif 0.4 <= min(wm, hm) and max(wm, hm) <= 9 and fill >= 0.75:
            kind = "pocket"
        else:
            continue
        if ang > 45:
            ang -= 90
            wm, hm = hm, wm
        per = cv2.arcLength(c, True)
        circ = 4 * math.pi * a / (per * per) if per > 0 else 0.0     # 1 = a circle, ~0.78 a square
        rnd = kind == "hole" or (circ >= 0.84 and 0.85 <= wm / hm <= 1.18)
        out.append({"px": float(cx), "py": float(cy), "w": round(wm, 3), "h": round(hm, 3),
                    "angle": round(float(-ang), 2), "kind": kind, "dark": dark, "area": a, "round": bool(rnd),
                    "r": round(math.sqrt(a / math.pi) / k, 3)})
    return out


def find_pockets(img, k):
    g = cv2.GaussianBlur(img, (5, 5), 0)
    ker = np.ones((3, 3), np.uint8)
    found = []
    for dark in (True, False):
        _, b = cv2.threshold(g, 0, 255, (cv2.THRESH_BINARY_INV if dark else cv2.THRESH_BINARY) | cv2.THRESH_OTSU)
        b = cv2.morphologyEx(b, cv2.MORPH_OPEN, ker)
        found += _blobs(b, k, dark)
    # the same spot seen in both polarities (a part inside its pocket): keep both, they differ in size;
    # drop near-duplicates of the same size
    keep = []
    for f in sorted(found, key=lambda f: -f["area"]):
        if any(math.hypot(f["px"] - q["px"], f["py"] - q["py"]) < 0.2 * k and abs(f["w"] - q["w"]) < 0.2 for q in keep):
            continue
        keep.append(f)
    H, W = img.shape[:2]
    bg = float(np.median(g))
    for f in keep:
        f["full"] = f["kind"] == "pocket" and is_full(g, f, k, bg)
    # a part lying in its pocket can be found twice (the pocket and the part): keep the part
    out = []
    for f in sorted(keep, key=lambda f: -f["area"]):
        inner = [q for q in keep if q is not f and q["kind"] == "pocket" and math.hypot(q["px"] - f["px"], q["py"] - f["py"]) < 0.3 * k
                 and q["area"] < f["area"]]
        if f["kind"] == "pocket" and inner:
            for q in inner:
                q["full"] = True
            continue
        out.append(f)
    keep = out
    keep.sort(key=lambda f: (f["kind"] != "pocket", math.hypot(f["px"] - W / 2, f["py"] - H / 2)))
    for f in keep:
        f.pop("area")
    return keep


def is_full(g, f, k, bg):
    """a part in the pocket? Inside the pocket (its inner 70 %): a part shows bright terminals / body
    and contrast; an empty pocket is a dark, flat hole (paper) or cavity (plastic)"""
    box = cv2.boxPoints(((f["px"], f["py"]), (f["w"] * k * 0.7, f["h"] * k * 0.7), -f["angle"])).astype(np.int32)
    m = np.zeros(g.shape, np.uint8)
    cv2.fillPoly(m, [box], 1)
    v = g[m > 0]
    if v.size < 20:
        return False
    hi, lo = float(np.percentile(v, 95)), float(np.percentile(v, 5))
    return hi > bg + 15 or (hi - lo) > 60


def annotate(img, found, k):
    """colour picture: pockets green (the nearest one thick), holes blue, crosshair red"""
    v = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    H, W = img.shape[:2]
    lw = max(1, W // 400)
    first = True
    for i, f in enumerate(found):
        col = (60, 220, 60) if f["kind"] == "pocket" else (255, 140, 40)
        box = cv2.boxPoints(((f["px"], f["py"]), (f["w"] * k, f["h"] * k), -f["angle"]))
        cv2.polylines(v, [box.astype(np.int32)], True, col, lw * (3 if first and f["kind"] == "pocket" else 1))
        cv2.drawMarker(v, (int(f["px"]), int(f["py"])), col, cv2.MARKER_CROSS, 14 * lw, lw)
        cv2.putText(v, str(i + 1), (int(f["px"]) + 6 * lw, int(f["py"]) - 6 * lw), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * lw, col, lw)
        if f["kind"] == "pocket":
            first = False
    cv2.line(v, (W // 2 - 20 * lw, H // 2), (W // 2 + 20 * lw, H // 2), (40, 40, 255), lw)
    cv2.line(v, (W // 2, H // 2 - 20 * lw), (W // 2, H // 2 + 20 * lw), (40, 40, 255), lw)
    return v
