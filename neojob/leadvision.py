"""Up-camera vision on the part's leads (pins) instead of its body outline.

NeoDen4 measures the outline of the bright part (CImgBoard::analyzeNozzle): for many parts the
body is not where the leads are (moulding tolerance, bent leads, a tab). Here the leads are
found and matched to the part's own pad pattern from the Gerber (neojob.pads), so the leads
are put on the pads. Settings: [LeadVision] in Config.ini (NeoConfig.LEADVISION_DEFAULTS).

    leads = local_leads(pads, (cx, cy), comp_rot)           # footprint frame, pad centroid = 0
    r = analyze_leads(frame, background, roi_px, k, leads, stack_angle, cfg)
    r.x, r.y: lead pattern centre relative to the nozzle (mm, machine axes); r.angle: its turn
    against the expected orientation (deg, CCW) - the same meaning as NeoDen's part result.
"""
import math

import cv2
import numpy as np

from .padalign import Blob, match


def local_leads(pads, comp_rot):
    """pads (job frame) -> [(x, y, area)] in the footprint frame around the pad centroid"""
    cx = sum(p[0] for p in pads) / len(pads)
    cy = sum(p[1] for p in pads) / len(pads)
    c, s = math.cos(math.radians(-comp_rot)), math.sin(math.radians(-comp_rot))
    return [(c * (p[0] - cx) - s * (p[1] - cy), s * (p[0] - cx) + c * (p[1] - cy), p[2] * p[3]) for p in pads], (cx, cy)


def rot(pts, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(c * x - s * y, s * x + c * y) for x, y, *_ in pts]


class LeadResult:
    def __init__(self, valid, x=0.0, y=0.0, angle=0.0, w=0.0, h=0.0, matched=0, expected=0, rms=0.0, message="", blobs=None, fitted=None):
        self.valid, self.x, self.y, self.angle, self.w, self.h = valid, x, y, angle, w, h
        self.matched, self.expected, self.rms, self.message = matched, expected, rms, message
        self.blobs, self.fitted = blobs or [], fitted or []
        self.rect_px = None

    def as_dict(self):
        return {k: getattr(self, k) for k in ("valid", "x", "y", "angle", "w", "h", "matched", "expected", "rms", "message")}


def auto_thresholds(roi):
    """leads are the brightest of three levels (background, body, metal): Otsu, then Otsu again on
    what is above it; both are tried"""
    t1, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    hi = roi[roi > t1]
    out = []
    if hi.size > 50:
        t2, _ = cv2.threshold(hi.reshape(1, -1), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        if t2 > t1 + 10:
            out.append(float(t2))
    return out + [float(t1)]


def find_lead_blobs(frame, background, p, k, threshold=0, min_mm2=0.01, max_mm2=4.0):
    """bright blobs (leads) in the ROI of half size p around the image centre (mm, nozzle frame).
    Returns [(blobs, threshold)], one per threshold tried."""
    diff = cv2.subtract(frame, background) if background is not None and background.shape == frame.shape else frame
    r, c = diff.shape
    x0, y0 = max(c // 2 - p, 0), max(r // 2 - p, 0)
    roi = cv2.GaussianBlur(diff[y0:y0 + 2 * p, x0:x0 + 2 * p], (3, 3), 0)
    res = []
    for t in (auto_thresholds(roi) if threshold <= 0 else [float(threshold)]):
        _, bw = cv2.threshold(roi, t, 255, cv2.THRESH_BINARY)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
        out = []
        for i in range(1, n):
            a = stats[i][4] / (k * k)
            if min_mm2 <= a <= max_mm2:
                px, py = cent[i][0] + x0, cent[i][1] + y0
                b = Blob(px, py, a, stats[i][2] / k, stats[i][3] / k)
                b.x, b.y = (px - c / 2) / k, (r / 2 - py) / k
                out.append(b)
        res.append((out, t))
    return res


def analyze_leads(frame, background, p, k, leads, stack_angle, cfg):
    """match the lead blobs to the expected pattern (leads turned by the stack angle); tries the
    four quarter turns (symmetric parts) and keeps the best, the smallest turn on a tie"""
    g = lambda key: cfg.leadvision(key)
    areas = [a for _, _, a in leads]
    tries = find_lead_blobs(frame, background, p, k, int(g("nThreshold")),
                            max(float(g("fMinLeadMm2")), min(areas) * 0.1), min(float(g("fMaxLeadMm2")), max(areas) * 2.0))
    best, blobs = None, []
    for bl, t in tries:
        if len(bl) < 2:
            continue
        r = _match_leads(bl, leads, stack_angle, float(g("fMatchRadiusMm")))
        if r and (best is None or r[0] > best[0]):
            best, blobs = r, bl
    if best is None:
        n = max((len(b) for b, _ in tries), default=0)
        return LeadResult(False, expected=len(leads), message=f"leads not found ({n} blobs; thresholds {', '.join(f'{t:.0f}' for _, t in tries)})",
                          blobs=tries[0][0] if tries else [])
    bc = (sum(b.x for b in blobs) / len(blobs), sum(b.y for b in blobs) / len(blobs))
    return _result(best, blobs, bc, leads, g)


def _match_leads(blobs, leads, stack_angle, radius):
    bc = (sum(b.x for b in blobs) / len(blobs), sum(b.y for b in blobs) / len(blobs))
    cands = []
    for q in (0, 90, 180, 270):
        exp = rot(leads, stack_angle + q)
        pred = [(x + bc[0], y + bc[1]) for x, y in exp]               # start: pattern on the blobs' centre
        # lead blobs are smaller than pads: no area check, but the radius keeps it local
        fit, pairs, rms = match(pred, blobs, bc, radius)
        if fit is None:
            continue
        cands.append((len(pairs), rms or 0.0, q, fit, pairs, pred))
    if not cands:
        return None
    # a symmetric part fits several quarter turns equally well: keep the feeder's orientation
    # (pin 1) unless another turn is clearly better
    top = max(c[0] for c in cands)
    rmin = min(c[1] for c in cands if c[0] == top)
    near = [c for c in cands if c[0] == top and c[1] <= rmin * 1.5 + 0.01]
    n, rms, q, fit, pairs, pred = min(near, key=lambda c: min(c[2], 360 - c[2]))
    return ((n, -rms), q, fit, pairs, rms, pred)


def _result(best, blobs, bc, leads, g):
    _, q, (dx, dy, dth), pairs, rms, pred = best
    cx, cy = bc[0] + dx, bc[1] + dy                                    # pattern centre (pred centre = bc)
    ang = ((q + dth + 180) % 360) - 180
    fitted = [(math.cos(math.radians(dth)) * (x - bc[0]) - math.sin(math.radians(dth)) * (y - bc[1]) + cx,
               math.sin(math.radians(dth)) * (x - bc[0]) + math.cos(math.radians(dth)) * (y - bc[1]) + cy) for x, y in pred]
    xs, ys = [x for x, _ in rot(leads, 0)], [y for _, y in rot(leads, 0)]
    r = LeadResult(True, cx, cy, ang, max(xs) - min(xs), max(ys) - min(ys), len(pairs), len(leads), rms or 0.0, blobs=blobs, fitted=fitted)
    need = max(2, int(math.ceil(len(leads) * float(g("fMinMatched")))))
    if len(pairs) < need:
        r.valid, r.message = False, f"only {len(pairs)} of {len(leads)} leads found (need {need})"
    elif (rms or 0) > float(g("fMaxRmsMm")):
        r.valid, r.message = False, f"leads do not fit the pads well (rms {rms:.3f} mm)"
    else:
        r.message = f"{len(pairs)}/{len(leads)} leads, centre {cx:+.3f}, {cy:+.3f} mm, turn {ang:+.2f} deg, rms {rms:.3f} mm"
    return r


def annotate(frame, r, k):
    img = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    h, w = img.shape[:2]
    to_px = lambda x, y: (int(round(x * k + w / 2)), int(round(h / 2 - y * k)))
    for b in r.blobs:
        cv2.circle(img, (int(b.px), int(b.py)), 5, (0, 220, 255), 2)
    for x, y in r.fitted:
        cv2.drawMarker(img, to_px(x, y), (0, 220, 0), cv2.MARKER_CROSS, 12, 2)
    cv2.drawMarker(img, (w // 2, h // 2), (0, 0, 255), cv2.MARKER_CROSS, 30, 1)
    if r.valid:
        cv2.drawMarker(img, to_px(r.x, r.y), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 20, 2)
    ok, buf = cv2.imencode(".png", img)
    import base64
    return base64.b64encode(buf.tobytes()).decode() if ok else ""


def wanted(cfg, comp, stack):
    if not cfg.leadvision("bEnabled"):
        return False
    refs = [s.strip() for s in str(cfg.leadvision("sRefs")).split(",") if s.strip()]
    fps = [s.strip().lower() for s in str(cfg.leadvision("sFootprints")).split(",") if s.strip()]
    fp = (comp.footprint or (stack.footprint if stack else "") or "").lower()
    return comp.name in refs or "*" in fps or (fp and fp in fps)
