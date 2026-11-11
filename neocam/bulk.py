"""Bulk parts (loose in a container) under the head camera: the part in the middle of the picture.

One picture per part, the part brought to the picture's centre (most accurate). Found:
- the body (dark against the container's light floor): centre, long axis angle, length / width;
- the leads (bright metal next to the body edges), counted per side, their pitch and the outer size;
- a signature (body size + leads per side, pitch) to tell packages apart;
- a normalised picture of the body (long axis along X, fixed scale) to tell the part's direction
  (0 / 180 deg, also 90 / 270 for square ones) against a taught example: the pin-1 dot, the bevel,
  the marking, a band. When the leads are not symmetric (SOT-23-5: 3 + 2), the leads decide.

    r = analyze(img, k)                 # img: oriented grey head picture, k: px per mm
    r -> Part(cx, cy, angle, L, W, leads=[n_top, n_bottom, n_right, n_left], pitch, outer, crop, ...)
    t = teach(r, name)                  # a template from a part placed the right way round
    m = match(r, [t, ...])              # -> (template, score, turn_deg): which package, how turned

Angles: degrees, machine CCW, of the part's long axis (the body's +u axis) against machine +X.
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

NORM_PX_MM = 20.0          # the normalised body picture: 20 px per mm


@dataclass
class Part:
    ok: bool
    why: str = ""
    cx: float = 0.0          # px in the picture
    cy: float = 0.0
    dx_mm: float = 0.0       # the part's centre from the picture centre, machine mm (+X right, +Y up)
    dy_mm: float = 0.0
    angle: float = 0.0       # deg CCW, the long axis, -90 .. 90
    L: float = 0.0           # body mm
    W: float = 0.0
    leads: list = field(default_factory=lambda: [0, 0, 0, 0])   # per side: +v (top), -v (bottom), +u (right), -u (left)
    pitch: float = 0.0       # mm, median spacing of the leads along a side
    outer: tuple = (0.0, 0.0)  # mm, leads included (L along u, W along v)
    lead_pts: list = field(default_factory=list)                # (u, v) mm of each lead, body frame
    crop: object = None      # normalised body picture (long axis along X)
    touching: bool = False   # another dark body touches it: do not pick it


def _floor(img):
    """the container floor's grey (the brightest large part of the picture)"""
    return float(np.percentile(img, 70))


def body_mask(img, frac=0.4, k=115.2):
    """the body: much darker than the floor; the leads' roots and the shadows are a middle grey, so
    the threshold follows the body's own darkness (seed < frac x floor, then 2.2 x the seed's median)"""
    g = cv2.GaussianBlur(img, (7, 7), 0)
    seed = g < frac * _floor(img)
    if seed.sum() < 50:
        return seed.astype(np.uint8)
    thr = min(frac * _floor(img), 2.2 * float(np.median(g[seed])) + 8)
    m = (g < thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))            # the marking inside the body
    # the leads' dark roots stick out of the body edge, ~0.4 mm wide: an opening of 0.55 mm takes them off
    r = max(3, int(0.55 * k)) | 1
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r)))


def analyze(img, k, min_mm=0.8, max_mm=16.0):
    H, W = img.shape
    m = body_mask(img, k=k)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    best, bd = None, 1e9
    for i in range(1, n):
        a = st[i, 4] / (k * k)
        if a < min_mm * min_mm * 0.5 or a > max_mm * max_mm:
            continue
        x, y, w, h = st[i, 0], st[i, 1], st[i, 2], st[i, 3]
        if w > 0.95 * W or h > 0.95 * H:                       # the container's wall, a shadow band
            continue
        d = math.hypot(cen[i][0] - W / 2, cen[i][1] - H / 2)
        if d < bd:
            best, bd = i, d
    if best is None:
        return Part(False, "no part in the picture")
    comp = (lab == best).astype(np.uint8)
    cs, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    (cx, cy), (a, b), ang = cv2.minAreaRect(c)
    x, y, w, h = cv2.boundingRect(c)
    cut = x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2
    if a < b:                                                  # long axis along u
        a, b, ang = b, a, ang + 90
    # image angle (y down) -> machine CCW
    angle = -((ang + 90) % 180 - 90)
    fill = cv2.contourArea(c) / max(1.0, a * b)
    p = Part(True, "", cx, cy, (cx - W / 2) / k, (H / 2 - cy) / k, angle, a / k, b / k)
    if cut:
        p.ok, p.why = False, "the part runs off the picture"
    elif fill < 0.8:
        p.touching = True
        p.why = "not a clean rectangle (touching another part, or tilted)"
    _leads(img, p, k, comp)
    p.crop = normalised(img, p, k)
    return p


def _frame(p):
    t = math.radians(p.angle)
    return np.array([math.cos(t), math.sin(t)]), np.array([-math.sin(t), math.cos(t)])   # u, v in machine axes


def _leads(img, p, k, body):
    """the leads, side by side, on the normalised picture (long axis along X): a strip just outside each
    body edge; where it differs from the floor (darker roots and shadows, brighter feet) is metal. Each
    run along the edge is one lead; their centres give the count and the pitch, their reach the outer size."""
    n_ = normalised(img, p, k, margin_mm=2.0)
    s = NORM_PX_MM
    H, W = n_.shape
    m = int(2.0 * s)
    bl, bt = m, m                                              # body: x in [bl, W - bl], y in [bt, H - bt]
    g = cv2.GaussianBlur(n_, (3, 3), 0).astype(np.float32)
    near, far = int(0.12 * s), int(1.8 * s)
    # each strip with row 0 next to the body edge and its columns along that edge
    strips = {0: g[bt - far:bt - near, bl:W - bl][::-1], 1: g[H - bt + near:H - bt + far, bl:W - bl],
              2: g[bt:H - bt, W - bl + near:W - bl + far].T, 3: g[bt:H - bt, bl - far:bl - near].T[::-1]}
    sides, pts, pitches, reach = [0, 0, 0, 0], [], [], [0.0, 0.0, 0.0, 0.0]
    for sd, strip in strips.items():
        if strip.size == 0:
            continue
        band = strip[int(0.02 * s):int(0.4 * s), :]                    # 0.14 .. 0.52 mm from the edge
        if band.size == 0:
            continue
        ref = float(np.percentile(band, 85))                              # the floor between the leads there
        mid = float(np.median(band))
        def find_runs(metal, min_w):
            on_ = metal.mean(axis=0) > 0.45
            out_, start = [], None
            for x, v in enumerate(list(on_) + [False]):
                if v and start is None:
                    start = x
                elif not v and start is not None:
                    if x - start >= min_w * s:
                        out_.append(((start + x - 1) / 2, (x - start) / s))
                    start = None
            return on_, out_
        # a lead's root is much darker, a plated foot brighter; a side that shows nothing so is looked at
        # again for wide, only slightly lighter terminals (a TO-277's tabs, a chip's ends)
        on, rw = find_runs((band < 0.65 * ref) | (band > mid + 45), 0.15)
        if sd >= 2:                                                       # the ends: wide terminals too
            on2, rw2 = find_runs((band < 0.65 * ref) | (band > mid + 22), 0.15)
            if len(rw2) > len(rw):
                on, rw = on2, rw2
        runs = [r for r, _ in rw]
        if on.mean() > 0.85:
            runs = []
        sides[sd] = len(runs)
        if len(runs) > 1:
            pitches += list(np.diff(sorted(runs)) / s)
        col = ((strip < 0.65 * ref) | (strip > mid + 45)).mean(axis=1) > 0.1
        reach[sd] = (np.nonzero(col)[0].max() + near) / s if col.any() and runs else 0.0
        for r in runs:
            off = r / s - (strip.shape[1] / s) / 2
            pts.append((sd, round(off, 3)))
    p.leads, p.lead_pts = sides, pts
    p.pitch = float(np.median(pitches)) if pitches else 0.0
    p.outer = (round(p.L + reach[2] + reach[3], 2), round(p.W + reach[0] + reach[1], 2))


def normalised(img, p, k, margin_mm=1.2):
    """the body (and its leads) turned so the long axis runs along +X, at NORM_PX_MM"""
    s = NORM_PX_MM / k
    M = cv2.getRotationMatrix2D((p.cx, p.cy), -p.angle, s)        # image rotation is clockwise-positive
    w, h = int((p.L + 2 * margin_mm) * NORM_PX_MM), int((p.W + 2 * margin_mm) * NORM_PX_MM)
    M[0, 2] += w / 2 - p.cx
    M[1, 2] += h / 2 - p.cy
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


def signature(p):
    """what tells packages apart: body size, leads per side (as a sorted pair per axis), pitch"""
    return {"L": round(p.L, 2), "W": round(p.W, 2), "long_sides": sorted(p.leads[:2]), "short_sides": sorted(p.leads[2:]),
            "pitch": round(p.pitch, 2), "outer": p.outer}


def teach(p, name):
    """a template from one part lying the right way round (as it must be placed: pin 1 where the
    footprint has it, at the stack's pick angle)"""
    return {"name": name, "sig": signature(p), "leads": list(p.leads), "crop": p.crop, "angle": p.angle,
            "profile": cross_profile(p), "pin1": pin1(p)}


def _ncc(a, b):
    a = a.astype(np.float32) - a.mean()
    b = b.astype(np.float32) - b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def _same_size(a, shape):
    return cv2.resize(a, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)


def sig_distance(a, b):
    """0 = same package; body size in mm, leads per side counted"""
    d = math.hypot(a["L"] - b["L"], a["W"] - b["W"])
    d += 0.6 * (abs(a["long_sides"][0] - b["long_sides"][0]) + abs(a["long_sides"][1] - b["long_sides"][1]))
    d += 0.6 * (abs(a["short_sides"][0] - b["short_sides"][0]) + abs(a["short_sides"][1] - b["short_sides"][1]))
    return d


def pin1(p, margin_mm=1.2):
    """the pin-1 mark on the body's top, on the normalised picture (long axis along +X, +v up):
    - the bevel: a darker band along one long edge (SOIC): side +1 (top, +v) or -1;
    - the dimple: the largest round dent near a corner (TSSOP, QFP): corner (su, sv) = (+-1, +-1).
    Returns {"bevel": +-1 | 0, "bevel_sure": .., "corner": (su, sv) | None, "corner_sure": ..}."""
    c = p.crop
    if c is None:
        return {"bevel": 0, "bevel_sure": 0.0, "corner": None, "corner_sure": 0.0}
    s = NORM_PX_MM
    m = int(margin_mm * s)
    body = c[m:c.shape[0] - m, m:c.shape[1] - m].astype(np.float32)
    out = {"bevel": 0, "bevel_sure": 0.0, "corner": None, "corner_sure": 0.0}
    if body.size == 0 or min(body.shape) < 10:
        return out
    h, w = body.shape
    # the profile across the body (rows), from the whole picture so the real edges are found
    prof = c[:, m + int(0.3 * s):c.shape[1] - m - int(0.3 * s)].astype(np.float32).mean(axis=1)
    inner_v = float(np.median(prof[m + int(0.8 * s):c.shape[0] - m - int(0.8 * s)])) if c.shape[0] > 2 * m + 2 * int(0.8 * s) else float(np.median(prof))
    floor_v = float(np.median(np.r_[prof[:int(0.6 * s)], prof[-int(0.6 * s):]]))
    dark = np.nonzero(prof < inner_v + 15)[0]                                 # the body itself (the leads are a middle grey)
    if len(dark) < 4:
        return out
    e0, e1 = int(dark[0]), int(dark[-1])                                      # the body's real edges (rows)
    band = int(0.6 * s)
    def extra(seg):                                                          # darkness beyond the body's own
        return float(np.clip(inner_v - 3 - seg, 0, None).sum()) / s
    top, bot = extra(prof[e0:e0 + band]), extra(prof[max(e0, e1 - band):e1 + 1])
    spread = max(1.0, top + bot)
    out["bevel"] = 1 if top > bot else -1                                     # picture top = +v
    out["bevel_sure"] = round(abs(top - bot) / spread, 2)
    # dimples: round, lighter or darker than the body around them, 0.25 .. 0.7 mm across
    eq = cv2.equalizeHist(cv2.GaussianBlur(body.astype(np.uint8), (5, 5), 0))
    best = None
    for img_ in (eq, 255 - eq):
        cs = cv2.HoughCircles(img_, cv2.HOUGH_GRADIENT, dp=1, minDist=int(0.6 * s), param1=80, param2=12,
                              minRadius=int(0.12 * s), maxRadius=int(0.38 * s))
        if cs is None:
            continue
        for x, y, r in cs[0]:
            su, sv = (1 if x > w / 2 else -1), (1 if y < h / 2 else -1)
            near = min(abs(x), abs(w - x)) < 0.35 * w and min(abs(y), abs(h - y)) < 0.45 * h
            if near and (best is None or r > best[0]):
                best = (r, su, sv)
    if best:
        out["corner"] = (best[1], best[2])
        out["corner_sure"] = round(float(best[0]) / s, 2)                       # its radius, mm
    return out


def cross_profile(p, n=64):
    """the darkness across the body (along v, the long axis averaged out: the marking mostly cancels),
    from 0.3 mm outside one long edge to 0.3 mm outside the other, resampled to n points, normalised.
    A bevel, a band, a dimple row make it lopsided; compared with the taught part's, normal or flipped."""
    c = p.crop
    if c is None:
        return None
    s = NORM_PX_MM
    m = int(1.2 * s)
    prof = c[:, m + int(0.3 * s):c.shape[1] - m - int(0.3 * s)].astype(np.float32).mean(axis=1)
    inner = float(np.median(prof[m + int(0.8 * s):len(prof) - m - int(0.8 * s)])) if len(prof) > 2 * m + 2 * int(0.8 * s) else float(np.median(prof))
    floor = float(np.median(np.r_[prof[:int(0.6 * s)], prof[-int(0.6 * s):]]))
    dark = np.nonzero(prof < inner + 15)[0]                                   # the body itself (the leads are a middle grey)
    if len(dark) < 4:
        return None
    e0, e1 = max(0, int(dark[0]) - int(0.3 * s)), min(len(prof) - 1, int(dark[-1]) + int(0.3 * s))
    seg = np.interp(np.linspace(e0, e1, n), np.arange(len(prof)), prof)
    seg = (seg - seg.mean()) / (seg.std() + 1e-6)
    return seg


def flip_by_profile(p, t):
    """0 or 180 against the template by the cross-body profile; (turn, how sure 0..1)"""
    a, b = cross_profile(p), t.get("profile")
    if a is None or b is None:
        return None, 0.0
    c0 = float(np.dot(a, b) / len(a))
    c180 = float(np.dot(a[::-1], b) / len(a))
    return (0 if c0 >= c180 else 180), round(abs(c0 - c180), 3)


def direction(p, t, use_pin1=True):
    """how the part is turned against the template (0 / 180, also 90 / 270 for a square body):
    (turn_deg, how sure, by what). In this order: leads that are not symmetric; the pin-1 bevel; the
    pin-1 dimple's corner; the body picture (marking) by correlation. use_pin1=False (the feeder's
    setting, when the camera cannot see the mark well) skips the pin-1 tests."""
    square = abs(p.L - p.W) < 0.15 * p.L
    cands = [0, 180] + ([90, 270] if square else [])
    rot = {0: [0, 1, 2, 3], 180: [1, 0, 3, 2], 90: [3, 2, 0, 1], 270: [2, 3, 1, 0]}
    lead_ok = {c: [p.leads[i] for i in rot[c]] == list(t["leads"]) for c in cands}
    if sum(lead_ok.values()) == 1:                              # the leads alone decide
        return next(c for c, ok in lead_ok.items() if ok), 1.0, "leads"
    if use_pin1 and not square and isinstance(t.get("pin1"), dict):
        a, b = pin1(p), t["pin1"]
        if a["bevel_sure"] >= 0.3 and b.get("bevel_sure", 0) >= 0.3:
            return (0 if a["bevel"] == b["bevel"] else 180), a["bevel_sure"], "pin-1 bevel"
        if a["corner"] and b.get("corner") and a["corner_sure"] >= 0.2:
            same = a["corner"] == tuple(b["corner"])
            flip = a["corner"] == (-b["corner"][0], -b["corner"][1])
            if same or flip:
                return (0 if same else 180), a["corner_sure"], "pin-1 dimple"
    scores = []
    for c in cands:
        img = np.rot90(p.crop, k=c // 90) if c in (90, 270) else (p.crop[::-1, ::-1] if c == 180 else p.crop)
        sc = _ncc(_same_size(img, t["crop"].shape), t["crop"]) + (0.5 if lead_ok[c] else 0.0)
        scores.append((sc, c))
    scores.sort(reverse=True)
    return scores[0][1], round(scores[0][0] - (scores[1][0] if len(scores) > 1 else 0.0), 3), "picture"


def match(p, templates, use_pin1=True):
    """the template this part is (smallest signature distance), how much it is to be turned to lie
    as the template did, how sure the direction is, and what decided it"""
    if not templates:
        return None, 0.0, 0.0, 0.0
    s = signature(p)
    t = min(templates, key=lambda q: sig_distance(s, q["sig"]))
    dist = sig_distance(s, t["sig"])
    turn, conf, how = direction(p, t, use_pin1)
    p.how = how
    return t, round(dist, 3), turn, conf


# ---------------------------------------------------------------- the part from below (up camera)
@dataclass
class Bottom:
    """the metal on the part's underside, seen by the up camera: leads, an exposed pad, QFN pads, tabs"""
    ok: bool
    why: str = ""
    cx: float = 0.0          # px, the metal pattern's centre (its bounding rectangle's)
    cy: float = 0.0
    angle: float = 0.0       # deg, the pattern rectangle's long axis (picture axes, CCW, y up), -90 .. 90
    L: float = 0.0           # mm, the pattern's outer size
    W: float = 0.0
    blobs: list = field(default_factory=list)     # (u, v, area mm2, w, h) in the pattern frame, mm
    leads: list = field(default_factory=lambda: [0, 0, 0, 0])   # small blobs per side: +v, -v, +u, -u
    pts: list = field(default_factory=list)       # (side, u, v) of those
    pitch: float = 0.0
    missing: list = field(default_factory=list)   # filled by match_bottom: template blobs not found (u, v)
    off_line: list = field(default_factory=list)


def analyze_bottom(img, k, roi_mm=11.0):
    """the part held by the nozzle (image centre = the nozzle): every piece of metal on its underside,
    bright on the nozzle's grey disc. Dirt (thin, ragged) is left out by its shape. The pattern's
    rectangle gives the angle (mod 90) and the centre; match_bottom() does the rest against a template."""
    H, W = img.shape
    r = int(roi_mm * k / 2)
    x0, y0 = max(0, W // 2 - r), max(0, H // 2 - r)
    c = cv2.GaussianBlur(img[y0:H // 2 + r, x0:W // 2 + r], (3, 3), 0)
    h, w = c.shape
    yy, xx = np.mgrid[:h, :w]
    inner = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (0.4 * min(h, w)) ** 2
    lo, hi = float(np.percentile(c[inner], 25)), float(np.percentile(c[inner], 99))
    if hi - lo < 40:
        return Bottom(False, "nothing bright on the nozzle (no part, or the exposure is wrong)")
    thr = lo + 0.45 * (hi - lo)
    metal = (c > thr).astype(np.uint8)
    metal = cv2.morphologyEx(metal, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(metal)
    keep = []
    for i in range(1, n):
        area = st[i, 4] / (k * k)
        if area < 0.02:
            continue
        cs, _ = cv2.findContours((lab == i).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cs, key=cv2.contourArea)
        hull = cv2.contourArea(cv2.convexHull(cnt))
        (bx, by), (bw, bh), _ = cv2.minAreaRect(cnt)
        if hull <= 0 or cv2.contourArea(cnt) / hull < 0.7 or min(bw, bh) / k < 0.12:
            continue                                            # dirt: a fibre, a ragged speck
        if math.hypot(cen[i][0] - w / 2, cen[i][1] - h / 2) > 0.45 * min(h, w):
            continue                                            # outside the nozzle's disc
        keep.append((i, cnt, area, bw / k, bh / k))
    if not keep:
        return Bottom(False, "no metal found on the part")
    # specks much smaller than the part's own metal (dust) would stretch the pattern: left out
    med = float(np.median([q[2] for q in keep]))
    keep = [q for q in keep if q[2] >= 0.25 * med] or keep
    allp = np.vstack([c_[1].reshape(-1, 2) for c_ in keep]).astype(np.float32)
    (pcx, pcy), (a, b), ang = cv2.minAreaRect(allp)
    if a < b:
        a, b, ang = b, a, ang + 90
    angle = -((ang + 90) % 180 - 90)
    bt = Bottom(True, "", pcx + x0, pcy + y0, angle, a / k, b / k)
    t = math.radians(angle)
    u_, v_ = np.array([math.cos(t), math.sin(t)]), np.array([-math.sin(t), math.cos(t)])
    for i, cnt, area, bw, bh in keep:
        mx, my = (cen[i][0] - pcx) / k, (pcy - cen[i][1]) / k
        bt.blobs.append((round(float(mx * u_[0] + my * u_[1]), 3), round(float(mx * v_[0] + my * v_[1]), 3), round(area, 3),
                         round(max(bw, bh), 3), round(min(bw, bh), 3)))
    _lead_rows(bt)
    return bt


def _lead_rows(bt):
    """the small blobs near the pattern's edges counted per side, and their pitch (for the operator)"""
    big = max(b[2] for b in bt.blobs)
    pts = []
    for u, v, area, bw, bh in bt.blobs:
        if area > 0.35 * big and area > 0.5:
            continue                                            # a pad, not a lead
        du, dv = bt.L / 2 - abs(u), bt.W / 2 - abs(v)
        if min(du, dv) > 0.9:
            continue                                            # not at an edge
        sd = (0 if v > 0 else 1) if dv <= du else (2 if u > 0 else 3)
        pts.append((sd, u, v))
    bt.pts = pts
    bt.leads = [sum(1 for p in pts if p[0] == sd) for sd in range(4)]
    pitches = []
    for sd in range(4):
        al = sorted(p[1] if sd < 2 else p[2] for p in pts if p[0] == sd)
        pitches += list(np.diff(al)) if len(al) > 1 else []
    bt.pitch = float(np.median(pitches)) if pitches else 0.0


def teach_bottom(b, name):
    """a bottom-view template from a part held the right way round"""
    return {"name": name, "blobs": [[float(x) for x in q] for q in b.blobs], "L": float(b.L), "W": float(b.W),
            "leads": [int(x) for x in b.leads], "angle": float(b.angle)}


def _rot(P, deg):
    a = math.radians(deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return P @ R.T


def match_bottom(b, t, tol=0.25):
    """the turn (0 / 90 / 180 / 270, added to the pattern angle) that lays this part's metal on the
    template's; every template blob looked for (position within tol mm, area within x0.5 .. x2):
    missing ones (a lead / pad not there: the part goes to the trash), extra ones, an rms; then a fine
    fit of the matched blobs: the exact angle and centre. sure = how many more blobs the next best turn
    misses (0: the underside is symmetric, it cannot tell that turn)."""
    if not b.ok or not b.blobs:
        return {"turn": 0, "missing": len(t["blobs"]), "extra": 0, "rms": 9.9, "sure": 0, "dangle": 0.0, "dx": 0.0, "dy": 0.0}
    P = np.array([(q[0], q[1]) for q in b.blobs])
    PA = np.array([q[2] for q in b.blobs])
    T = np.array([(q[0], q[1]) for q in t["blobs"]])
    TA = np.array([q[2] for q in t["blobs"]])
    square = abs(b.L - b.W) < 0.12 * b.L

    def pair_up(Q, tol_):
        used, pairs, miss = set(), [], []
        for j, (tp, ta) in enumerate(zip(T, TA)):
            d = np.hypot(*(Q - tp).T)
            ok = [(d[i], i) for i in range(len(Q)) if i not in used and d[i] <= tol_ and 0.5 <= PA[i] / ta <= 2.0]
            if ok:
                _, i = min(ok)
                used.add(i)
                pairs.append((i, j))
            else:
                miss.append(j)
        extra = sum(1 for i in range(len(Q)) if i not in used and PA[i] >= 0.6 * TA.min())
        return pairs, miss, extra

    def fit(Q, pairs):
        """rotation + shift laying Q's paired blobs on the template's (least squares): (deg, shift)"""
        if len(pairs) < 2:
            if pairs:
                i, j = pairs[0]
                return 0.0, T[j] - Q[i]
            return 0.0, np.zeros(2)
        A, B_ = Q[[i for i, _ in pairs]], T[[j for _, j in pairs]]
        ca, cb = A.mean(axis=0), B_.mean(axis=0)
        Hm = (A - ca).T @ (B_ - cb)
        da = math.degrees(math.atan2(Hm[0, 1] - Hm[1, 0], Hm[0, 0] + Hm[1, 1]))
        return da, cb - _rot(ca[None, :], da)[0]

    res = []
    for turn in ((0, 90, 180, 270) if square else (0, 180)):
        Q0 = _rot(P, turn)
        # coarse: the pattern centres may be off a few tenths (the outer rectangle moves with the metal)
        pairs, _, _ = pair_up(Q0, 2.5 * tol)
        da, sh = fit(Q0, pairs)
        Q = _rot(Q0, da) + sh
        pairs, miss, extra = pair_up(Q, tol)
        da2, sh2 = fit(Q, pairs)
        Q = _rot(Q, da2) + sh2
        rms = float(np.sqrt(np.mean([np.sum((Q[i] - T[j]) ** 2) for i, j in pairs]))) if pairs else 9.9
        res.append((len(miss), extra, rms, turn, pairs, miss, da + da2, _rot(sh[None, :], da2)[0] + sh2))
    res.sort(key=lambda r_: (r_[0], r_[1], r_[2]))
    miss, extra, rms, turn, pairs, missl, dangle, shift = res[0]
    sure = (res[1][0] + res[1][1] - miss - extra) if len(res) > 1 else len(T)
    dx, dy = float(shift[0]), float(shift[1])
    # where the missing ones should be, in this part's own frame (for drawing)
    b.missing = [tuple(_rot((_rot((T[j] - shift)[None, :], -dangle))[0][None, :], -turn)[0]) for j in missl]
    return {"turn": turn, "missing": miss, "extra": extra, "rms": round(rms, 3), "sure": sure,
            "dangle": round(dangle, 3), "dx": round(dx, 3), "dy": round(dy, 3)}


# ---------------------------------------------------------------- drawing what was found (for the operator)
def _to_img(cx, cy, angle, k, u, v):
    a = math.radians(angle)
    mx, my = u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a)
    return int(round(cx + mx * k)), int(round(cy - my * k))


def _box(img, cx, cy, angle, k, L, W, col, th):
    pts = [_to_img(cx, cy, angle, k, su * L / 2, sv * W / 2) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    cv2.polylines(img, [np.array(pts, np.int32)], True, col, th, cv2.LINE_AA)


def annotate_top(img, p, k, text=None, pin=None):
    """the head picture with the body (green), each lead found (yellow), the centre, the pin-1 mark
    (magenta) and a caption: what the operator sees on the screen"""
    v = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    th = max(2, int(img.shape[1] / 400))
    if not p.ok and p.cx == 0:
        cv2.putText(v, p.why or "no part", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        return v
    _box(v, p.cx, p.cy, p.angle, k, p.L, p.W, (60, 220, 60) if p.ok and not p.touching else (0, 140, 255), th)
    for sd, o in p.lead_pts:
        d = 0.35
        u, w_ = {0: (o, p.W / 2 + d), 1: (o, -(p.W / 2 + d)), 2: (p.L / 2 + d, -o), 3: (-(p.L / 2 + d), -o)}[sd]
        x, y = _to_img(p.cx, p.cy, p.angle, k, u, w_)
        cv2.circle(v, (x, y), int(0.18 * k), (0, 230, 255), th, cv2.LINE_AA)
    x, y = int(p.cx), int(p.cy)
    cv2.drawMarker(v, (x, y), (0, 0, 255), cv2.MARKER_CROSS, int(0.8 * k), th)
    ax, ay = _to_img(p.cx, p.cy, p.angle, k, p.L / 2 + 0.6, 0)      # the long axis' + direction
    cv2.arrowedLine(v, (x, y), (ax, ay), (255, 120, 0), th, cv2.LINE_AA, tipLength=0.08)
    pin = pin if pin is not None else pin1(p)
    if pin.get("bevel") and pin.get("bevel_sure", 0) >= 0.3:
        bx, by = _to_img(p.cx, p.cy, p.angle, k, 0, pin["bevel"] * (p.W / 2 - 0.25))
        cv2.circle(v, (bx, by), int(0.25 * k), (255, 0, 255), -1, cv2.LINE_AA)
    elif pin.get("corner") and pin.get("corner_sure", 0) >= 0.2:
        su, sv = pin["corner"]
        bx, by = _to_img(p.cx, p.cy, p.angle, k, su * (p.L / 2 - 0.5), sv * (p.W / 2 - 0.5))
        cv2.circle(v, (bx, by), int(0.25 * k), (255, 0, 255), -1, cv2.LINE_AA)
    lines = [f"{p.L:.2f} x {p.W:.2f} mm  {p.angle:+.1f} deg  leads {p.leads[0]}+{p.leads[1]} | {p.leads[2]}+{p.leads[3]}"
             + (f"  pitch {p.pitch:.2f}" if p.pitch else "")]
    if text:
        lines.insert(0, text)
    if p.why:
        lines.append(p.why)
    fs = img.shape[1] / 900
    for i, t in enumerate(lines):
        cv2.putText(v, t, (15, int(40 * fs) + i * int(38 * fs)), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), int(5 * fs) + 2, cv2.LINE_AA)
        cv2.putText(v, t, (15, int(40 * fs) + i * int(38 * fs)), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), int(2 * fs) + 1, cv2.LINE_AA)
    return v


def annotate_bottom(img, b, k, text=None):
    """the up-camera picture with every piece of metal found (yellow), the pattern's outline (green),
    missing leads / pads where the template has them (red X) and a caption"""
    v = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    th = max(1, int(img.shape[1] / 500))
    if not b.ok:
        cv2.putText(v, b.why or "no part", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        return v
    _box(v, b.cx, b.cy, b.angle, k, b.L, b.W, (60, 220, 60), th)
    for u, w_, area, bw, bh in b.blobs:
        x, y = _to_img(b.cx, b.cy, b.angle, k, u, w_)
        cv2.circle(v, (x, y), max(3, int(0.5 * math.sqrt(max(area, 0.01)) * k)), (0, 230, 255), th, cv2.LINE_AA)
    for u, w_ in b.missing:
        x, y = _to_img(b.cx, b.cy, b.angle, k, u, w_)
        r = int(0.3 * k)
        cv2.line(v, (x - r, y - r), (x + r, y + r), (0, 0, 255), th + 1)
        cv2.line(v, (x - r, y + r), (x + r, y - r), (0, 0, 255), th + 1)
    cv2.drawMarker(v, (int(b.cx), int(b.cy)), (0, 0, 255), cv2.MARKER_CROSS, int(0.8 * k), th)
    ax, ay = _to_img(b.cx, b.cy, b.angle, k, b.L / 2 + 0.5, 0)
    cv2.arrowedLine(v, (int(b.cx), int(b.cy)), (ax, ay), (255, 120, 0), th, cv2.LINE_AA, tipLength=0.08)
    lines = [f"{len(b.blobs)} metal  {b.L:.2f} x {b.W:.2f} mm  {b.angle:+.1f} deg" + (f"  MISSING {len(b.missing)}" if b.missing else "")]
    if text:
        lines.insert(0, text)
    fs = img.shape[1] / 900
    for i, t in enumerate(lines):
        cv2.putText(v, t, (10, int(30 * fs) + i * int(32 * fs)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * fs, (0, 0, 0), int(4 * fs) + 2, cv2.LINE_AA)
        cv2.putText(v, t, (10, int(30 * fs) + i * int(32 * fs)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * fs, (255, 255, 255), int(1.5 * fs) + 1, cv2.LINE_AA)
    return v
