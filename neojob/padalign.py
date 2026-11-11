"""Local (pad) alignment: before a part is placed, the head camera looks at its pads on the board
and the placement is corrected by where the pads really are (board warp, stretch, etching).

    pads = load_pads(job)["parts"]["U1"]["pads"]            # neojob.pads, job frame
    pa = PadAligner(grab, move, cfg)
    r = pa.align(pads, centre, tf)                           # r.dx, r.dy (mm), r.dtheta (deg)

Steps: the pads are put through the board transform (fiducials) to where they should be; the
camera looks there (one view, or two at opposite corners for a part larger than the view);
bright pads are found (threshold, blobs of pad size); each expected pad is paired with the
nearest blob; a rotation + shift is fitted on the pairs (outliers dropped) and applied about the
part centre. Settings: [PadAlign] in Config.ini (NeoConfig.PADALIGN_DEFAULTS).
"""
import base64
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

SIZE = 800                    # head camera window used for marks and pads (centre 400, 400)


@dataclass
class Blob:
    px: float
    py: float
    area_mm2: float
    w_mm: float
    h_mm: float
    x: float = 0.0            # machine (nozzle-1 frame), filled in by the caller
    y: float = 0.0
    contrast: float = 0.0     # mean grey inside - mean grey of a ring around it
    rect: tuple = None        # rotated rectangle in pixels ((cx, cy), (w, h), angle), cv2.minAreaRect


def detect_pads(img, k, threshold=0, invert=False, min_mm2=0.05, max_mm2=25.0, blur=3, min_contrast=0.4,
                split_mm=0.0):
    """bright (or dark with invert) blobs of pad size. threshold 0 = Otsu. Returns ([Blob], t).
    min_contrast: a blob must stand out from a ring around it by this share of the image's range
    (99th percentile - median). On the BOARD-A board (inner lamp) tinned pads stood 90..160 grey
    levels above their surroundings, lamp reflections on copper under the mask only 24..61.
    split_mm: break bright links narrower than this (pins of one net tied by copper / solder, as
    on the BOARD-A's LQFP64) by a morphological opening; give ~40 % of the narrowest pad width."""
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if blur:
        g = cv2.GaussianBlur(g, (blur | 1, blur | 1), 0)
    mode = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    if threshold <= 0:
        t, bw = cv2.threshold(g, 0, 255, mode | cv2.THRESH_OTSU)
    else:
        t, bw = cv2.threshold(g, threshold, 255, mode)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    sp = int(round(split_mm * k))
    if sp >= 3:                                  # cut narrow links between neighbouring pads
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (sp | 1, sp | 1)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw, 4)
    out = []
    H, W = g.shape
    gi = 255 - g if invert else g
    rng = float(np.percentile(gi, 99) - np.median(gi))
    ring_k = np.ones((9, 9), np.uint8)
    for i in range(1, n):
        x0, y0, w, h, a = stats[i]
        am = a / (k * k)
        if not min_mm2 <= am <= max_mm2:
            continue
        if x0 <= 0 or y0 <= 0 or x0 + w >= W or y0 + h >= H:     # cut by the image edge
            continue
        # contrast against a ring around the blob, in a window around it (cheap)
        X0, Y0, X1, Y1 = max(x0 - 6, 0), max(y0 - 6, 0), min(x0 + w + 6, W), min(y0 + h + 6, H)
        m = (lab[Y0:Y1, X0:X1] == i).astype(np.uint8)
        ring = cv2.dilate(m, ring_k).astype(bool) & ~m.astype(bool)
        win = gi[Y0:Y1, X0:X1]
        con = float(win[m.astype(bool)].mean() - (win[ring].mean() if ring.any() else 0.0))
        if min_contrast and con < min_contrast * rng:
            continue                                            # a lamp reflection, not a pad
        # clean the pad's shape before measuring it: closing fills notches and dark spots in the
        # tinning, opening takes off the stubs of tracks leaving the pad (both ~25 % of its width)
        sk = max(3, int(0.25 * min(w, h))) | 1
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (sk, sk))
        mc = cv2.morphologyEx(cv2.morphologyEx(np.pad(m, sk), cv2.MORPH_CLOSE, se), cv2.MORPH_OPEN, se)[sk:-sk, sk:-sk]
        if mc.sum() < 0.5 * m.sum():
            mc = m
        cs, _ = cv2.findContours(mc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rect = None
        px, py = float(cent[i][0]), float(cent[i][1])
        if cs:
            big = max(cs, key=cv2.contourArea)
            (rx, ry), (rw, rh), ra = cv2.minAreaRect(big)
            rect = ((rx + X0, ry + Y0), (rw, rh), ra)
            mo = cv2.moments(big)
            if mo["m00"] > 0:
                px, py = mo["m10"] / mo["m00"] + X0, mo["m01"] / mo["m00"] + Y0
        out.append(Blob(px, py, am, w / k, h / k, contrast=con, rect=rect))
    return out, float(t)


def machine_to_px(view, x, y, k, size=SIZE, cam_angle=0.0):
    """inverse of px_to_machine"""
    dx, dy = x - view[0], y - view[1]
    if cam_angle:
        c, s = math.cos(math.radians(-cam_angle)), math.sin(math.radians(-cam_angle))
        dx, dy = c * dx - s * dy, s * dx + c * dy
    return size / 2 + dx * k, size / 2 - dy * k


def _pad_template(w_px, h_px, ang, margin):
    """the Gerber pad as a picture: bright rectangle (turned by ang, image degrees) on a dark
    surround of `margin` px"""
    rad = math.radians(ang)
    bw = abs(w_px * math.cos(rad)) + abs(h_px * math.sin(rad))
    bh = abs(w_px * math.sin(rad)) + abs(h_px * math.cos(rad))
    W, H = int(math.ceil(bw + 2 * margin)) | 1, int(math.ceil(bh + 2 * margin)) | 1
    t = np.zeros((H, W), np.uint8)
    cv2.fillPoly(t, [np.round(cv2.boxPoints(((W / 2, H / 2), (w_px, h_px), ang))).astype(np.int32)], 255)
    return t


def _subpix(res, x, y):
    """parabola through the peak and its neighbours"""
    def off(a, b, c):
        d = a - 2 * b + c
        return 0.0 if d >= 0 else 0.5 * (a - c) / d
    h, w = res.shape
    dx = off(res[y, x - 1], res[y, x], res[y, x + 1]) if 0 < x < w - 1 else 0.0
    dy = off(res[y - 1, x], res[y, x], res[y + 1, x]) if 0 < y < h - 1 else 0.0
    return x + dx, y + dy


def find_pads_gerber(img, view, pads_m, sizes, k, turn, coarse_mm=0.6, fine_mm=0.15, min_score=0.45, cam_angle=0.0):
    """Find each expected pad by its own Gerber rectangle (template matching).
    pads_m: [(x, y)] expected machine positions; sizes: [(w, h)] mm (job frame); turn: board
    rotation (deg). 1) all pads of the view together give one coarse shift (no mix-up between
    equal neighbours, e.g. a 0.5 mm QFP pitch); 2) each pad alone, in a small window around its
    coarse place. Only this part's pads are looked at: pads of parts that are not in the job
    (not fitted) are never taken. Returns [(i, Blob)] for the pads found with a score >= min_score."""
    g = cv2.GaussianBlur(img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    H, W = g.shape
    ang = -turn                                          # image y points down
    pts = [machine_to_px(view, x, y, k, SIZE, cam_angle) for x, y in pads_m]
    margin = max(4, int(0.12 * k))
    inside = [i for i, (u, v) in enumerate(pts)
              if margin < u < W - margin and margin < v < H - margin]
    if not inside:
        return []
    # 1) coarse: every pad of this view drawn on one canvas
    cr = int(coarse_mm * k)
    us = [pts[i][0] for i in inside]; vs = [pts[i][1] for i in inside]
    ext = max(max(sizes[i]) for i in inside) * k / 2 + margin
    x0, y0 = int(max(0, min(us) - ext)), int(max(0, min(vs) - ext))
    x1, y1 = int(min(W, max(us) + ext)), int(min(H, max(vs) + ext))
    canvas = np.zeros((y1 - y0, x1 - x0), np.uint8)
    for i in inside:
        cv2.fillPoly(canvas, [np.round(cv2.boxPoints(((pts[i][0] - x0, pts[i][1] - y0),
                                                       (sizes[i][0] * k, sizes[i][1] * k), ang))).astype(np.int32)], 255)
    du = dv = 0.0
    sx0, sy0, sx1, sy1 = max(0, x0 - cr), max(0, y0 - cr), min(W, x1 + cr), min(H, y1 + cr)
    search = g[sy0:sy1, sx0:sx1]
    if search.shape[0] >= canvas.shape[0] and search.shape[1] >= canvas.shape[1] and canvas.any():
        res = cv2.matchTemplate(search, canvas, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > 0.2:
            fx, fy = _subpix(res, *loc)
            du, dv = fx + sx0 - x0, fy + sy0 - y0
    # 2) fine: each pad alone
    fr = max(3, int(fine_mm * k))
    out = []
    for i in inside:
        w_px, h_px = sizes[i][0] * k, sizes[i][1] * k
        tpl = _pad_template(w_px, h_px, ang, margin)
        th, tw = tpl.shape
        cu, cv_ = pts[i][0] + du, pts[i][1] + dv
        X0, Y0 = int(round(cu - tw / 2 - fr)), int(round(cv_ - th / 2 - fr))
        X1, Y1 = X0 + tw + 2 * fr, Y0 + th + 2 * fr
        if X0 < 0 or Y0 < 0 or X1 > W or Y1 > H:
            continue                                     # pad (with its search room) leaves the image
        res = cv2.matchTemplate(g[Y0:Y1, X0:X1], tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx < min_score:
            continue
        fx, fy = _subpix(res, *loc)
        px, py = X0 + fx + tw / 2, Y0 + fy + th / 2
        b = Blob(px, py, sizes[i][0] * sizes[i][1], sizes[i][0], sizes[i][1], contrast=float(mx),
                 rect=((px, py), (w_px, h_px), ang))
        b.x, b.y = px_to_machine(view, px, py, k, SIZE, cam_angle)
        out.append((i, b))
    return out


def px_to_machine(view, px, py, k, size=SIZE, cam_angle=0.0):
    """point at pixel (px, py) of the oriented head image taken with the crosshair on `view`"""
    dx, dy = (px - size / 2) / k, (size / 2 - py) / k
    if cam_angle:
        c, s = math.cos(math.radians(cam_angle)), math.sin(math.radians(cam_angle))
        dx, dy = c * dx - s * dy, s * dx + c * dy
    return view[0] + dx, view[1] + dy


def fit_rigid_about(pred, meas, centre):
    """rotation about `centre` + shift taking pred -> meas (least squares). (dx, dy, dtheta_deg)"""
    a = np.array(pred, float) - centre
    b = np.array(meas, float) - centre
    ca, cb = a.mean(0), b.mean(0)
    A, B = a - ca, b - cb
    th = math.atan2((A[:, 0] * B[:, 1] - A[:, 1] * B[:, 0]).sum(), (A * B).sum()) if len(a) > 1 else 0.0
    c, s = math.cos(th), math.sin(th)
    t = cb - np.array([c * ca[0] - s * ca[1], s * ca[0] + c * ca[1]])
    return float(t[0]), float(t[1]), math.degrees(th)


ALONG_WEIGHT = 0.15     # weight of a long pad's position along its length (uneven tinned ends)


def fit_directional(pred, meas, centre, axes, along=None):
    """least-squares shift + small turn about `centre`, pred -> meas, where a long pad counts
    fully ACROSS its width and only with weight `along` along its length: axes[i] = unit vector
    along pad i's length, or None for a square-ish pad (both directions count fully). The tinning
    makes the ends of long pads uneven (seen on the BOARD-A board), which moves their centres along
    the pad; across the pad the edges are clean. On a QFP the four sides fix everything across
    the pads; on an SOIC / SOT (all pads one way) the along direction still comes from the mean.
    Returns (dx, dy, dtheta_deg)."""
    along = ALONG_WEIGHT if along is None else along
    rows, rhs = [], []
    for (px, py), (mx, my), ax in zip(pred, meas, axes):
        rx, ry = px - centre[0], py - centre[1]
        dirs = [(1.0, 0.0, 1.0), (0.0, 1.0, 1.0)] if ax is None else [(-ax[1], ax[0], 1.0), (ax[0], ax[1], along)]
        for nx, ny, wt in dirs:
            # n . (shift + dth * (-ry, rx)) = n . (m - p)
            rows.append([wt * nx, wt * ny, wt * (nx * -ry + ny * rx)])
            rhs.append(wt * (nx * (mx - px) + ny * (my - py)))
    A, b = np.array(rows), np.array(rhs)
    if np.linalg.matrix_rank(A) < 3:
        sol = np.linalg.lstsq(A[:, :2], b, rcond=None)[0]
        return float(sol[0]), float(sol[1]), 0.0
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    return float(sol[0]), float(sol[1]), math.degrees(sol[2])


def directional_residual(p, m, centre, fit, ax):
    """distance between fitted pad and blob, across the pad only for long pads"""
    fx, fy = apply_about(p[0], p[1], centre, fit)
    ex, ey = m[0] - fx, m[1] - fy
    return math.hypot(ex, ey) if ax is None else abs(-ax[1] * ex + ax[0] * ey)


def pad_axes(pads, turn_deg=0.0, ratio=1.5):
    """unit vector along each pad's length (job frame turned by the board), None for square pads"""
    c, s = math.cos(math.radians(turn_deg)), math.sin(math.radians(turn_deg))
    out = []
    for p in pads:
        w, h = p[2], p[3]
        if max(w, h) < ratio * min(w, h):
            out.append(None)
        else:
            ux, uy = (1.0, 0.0) if w > h else (0.0, 1.0)
            out.append((c * ux - s * uy, s * ux + c * uy))
    return out


def pad_gap(pads):
    """mm between the inner edges of a part's pads (twice the middle-to-nearest-edge distance)"""
    mx, my = sum(q[0] for q in pads) / len(pads), sum(q[1] for q in pads) / len(pads)
    return 2 * min(max(0.0, math.dist((p[0], p[1]), (mx, my)) - (p[2] if abs(p[0] - mx) > abs(p[1] - my) else p[3]) / 2)
                   for p in pads)


def _fit_circle(P):
    """least-squares circle through points (N x 2): cx, cy, r"""
    A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
    c, *_ = np.linalg.lstsq(A, P[:, 0] ** 2 + P[:, 1] ** 2, rcond=None)
    return float(c[0]), float(c[1]), math.sqrt(max(0.0, c[2] + c[0] ** 2 + c[1] ** 2))


def _circle3(P):
    """circle through three points (3 x 2) or None"""
    (x1, y1), (x2, y2), (x3, y3) = P
    A = np.array([[x2 - x1, y2 - y1], [x3 - x1, y3 - y1]], float) * 2
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    cx, cy = np.linalg.solve(A, [x2 * x2 - x1 * x1 + y2 * y2 - y1 * y1, x3 * x3 - x1 * x1 + y3 * y3 - y1 * y1])
    return float(cx), float(cy), math.hypot(x1 - cx, y1 - cy)


def apply_about(x, y, centre, r):
    dx, dy, dth = r
    c, s = math.cos(math.radians(dth)), math.sin(math.radians(dth))
    ux, uy = x - centre[0], y - centre[1]
    return c * ux - s * uy + centre[0] + dx, s * ux + c * uy + centre[1] + dy


def match(pred, blobs, centre, radius, iters=6, areas=None, area_tol=(0.35, 3.0), axes=None):
    """pair expected pads with blobs, fit, drop outliers, repeat. Returns (fit, pairs, rms).
    areas: expected pad areas (mm2); a blob far from its pad's size is not taken (a neighbour's
    pad, paste, a via) - this matters for two-pad parts, where any two blobs would fit.
    axes: pad_axes(); long pads then count only across their width (fit_directional)."""
    fit = (0.0, 0.0, 0.0)
    pairs = []
    rms = None
    for _ in range(iters):
        moved = [apply_about(x, y, centre, fit) for x, y in pred]
        pairs = []
        used = set()
        for i, (x, y) in enumerate(moved):
            best, bd = None, radius
            for j, b in enumerate(blobs):
                if j in used:
                    continue
                if areas and not area_tol[0] * areas[i] <= b.area_mm2 <= area_tol[1] * areas[i]:
                    continue
                d = math.hypot(b.x - x, b.y - y)
                if d < bd:
                    best, bd = j, d
            if best is not None:
                used.add(best)
                pairs.append((i, best, bd))
        if len(pairs) < 2:
            return None, pairs, None
        ax = axes or [None] * len(pred)
        # outliers by the distance that counts (across long pads), so uneven ends don't drop pads
        dd = [directional_residual(pred[i], (blobs[j].x, blobs[j].y), centre, fit, ax[i]) for i, j, _ in pairs]
        med = sorted(dd)[len(dd) // 2]
        keep = [p for p, d in zip(pairs, dd) if d <= max(3 * med, 0.05)]
        P = [pred[i] for i, _, _ in keep]
        M = [(blobs[j].x, blobs[j].y) for _, j, _ in keep]
        if axes and any(ax[i] is not None for i, _, _ in keep):
            new = fit_directional(P, M, centre, [ax[i] for i, _, _ in keep])
        else:
            new = fit_rigid_about(P, M, centre)
        res = [directional_residual(pred[i], (blobs[j].x, blobs[j].y), centre, new, ax[i]) for i, j, _ in keep]
        rms = math.sqrt(sum(r * r for r in res) / len(res))
        if max(abs(a - b) for a, b in zip(new, fit)) < 1e-5:
            fit, pairs = new, keep
            break
        fit, pairs = new, keep
    return fit, pairs, rms


def spacing_scale(pred, meas):
    """found pad spacing / Gerber pad spacing (1.0 = right). For a 2-pad part the rotation + shift
    always fits exactly; this is the check that the two blobs really are its two pads."""
    a, b = np.array(pred, float), np.array(meas, float)
    da = np.sqrt(((a - a.mean(0)) ** 2).sum(1)).mean()
    db = np.sqrt(((b - b.mean(0)) ** 2).sum(1)).mean()
    return float(db / da) if da > 0 else 1.0


def _mean_radius(pts):
    """mean distance of points from their middle (mm): spacing_scale's denominator"""
    a = np.array(pts, float)
    return float(np.sqrt(((a - a.mean(0)) ** 2).sum(1)).mean())


@dataclass
class AlignResult:
    ok: bool
    dx: float = 0.0
    dy: float = 0.0
    dtheta: float = 0.0
    rms: float = 0.0
    scale: float = 1.0
    matched: int = 0
    expected: int = 0
    message: str = ""
    views: list = field(default_factory=list)       # [{"at": (x, y), "blobs": n, "threshold": t, "png": base64}]

    def as_dict(self):
        return {k: getattr(self, k) for k in ("ok", "dx", "dy", "dtheta", "rms", "scale", "matched", "expected", "message", "views")}


class PadAligner:
    """grab() -> oriented 800x800 head image; move(x, y) puts the camera crosshair on (x, y)
    (nozzle-1 frame, i.e. head = point + camera offset); cfg = NeoConfig"""

    def __init__(self, grab, move, cfg, log=print):
        self.grab, self.move, self.cfg, self.log = grab, move, cfg, log
        self.on_view = None                    # callback(img, meta) for the vision log

    def p(self, key):
        return self.cfg.padalign(key)

    def views_for(self, pts, k, sizes=None):
        """camera positions that together see every pad whole: the part's pad area is tiled with
        overlapping views (a big part such as an LQFP64 does not fit one view); views that would
        contain no pad (the middle of a QFP) are left out"""
        half_pad = [max(s) / 2 for s in sizes] if sizes else [0.0] * len(pts)
        x0 = min(p[0] - h for p, h in zip(pts, half_pad)); x1 = max(p[0] + h for p, h in zip(pts, half_pad))
        y0 = min(p[1] - h for p, h in zip(pts, half_pad)); y1 = max(p[1] + h for p, h in zip(pts, half_pad))
        fov = SIZE / k * 0.8                              # usable part of a view (pads near the edge are cut)
        nx, ny = max(1, math.ceil((x1 - x0) / fov)), max(1, math.ceil((y1 - y0) / fov))
        xs = [(x0 + x1) / 2] if nx == 1 else [x0 + fov / 2 + i * (x1 - x0 - fov) / (nx - 1) for i in range(nx)]
        ys = [(y0 + y1) / 2] if ny == 1 else [y0 + fov / 2 + j * (y1 - y0 - fov) / (ny - 1) for j in range(ny)]
        half = fov / 2
        out = [(x, y) for y in ys for x in xs
               if any(abs(p[0] - x) < half and abs(p[1] - y) < half for p in pts)]
        return out or [((x0 + x1) / 2, (y0 + y1) / 2)]

    def _find_hole(self, views, pads_m, pads, k, strict=False):
        """the round hole between a part's pads (machine x, y) or None. pads_m: the pads' corrected
        machine positions. The hole's rim is looked for on rays across the pad line only (the pads
        cover the hole's other two sides): every darker-inside edge on the rays, then the circle
        most of them agree on (the hole wall shows as a second, inner ring on one side; checked
        on LD1..LD8 of the BOARD-A: rim d 3.4..3.7 mm, fit rms ~12 um)."""
        mx, my = sum(p[0] for p in pads_m) / len(pads_m), sum(p[1] for p in pads_m) / len(pads_m)
        if not views:
            return None
        v = min(views, key=lambda v: math.dist(v["at"], (mx, my)))
        img = v["_img"] if v["_img"].ndim == 2 else cv2.cvtColor(v["_img"], cv2.COLOR_BGR2GRAY)
        cam = float(self.p("fHeadCamAngle"))
        # gap: twice the distance from the pads' middle to the nearest inner pad edge (Gerber)
        gx_, gy_ = sum(q[0] for q in pads) / len(pads), sum(q[1] for q in pads) / len(pads)
        gap = 2 * min(max(0.05, math.dist((p[0], p[1]), (gx_, gy_)) - (p[2] if abs(p[0] - gx_) > abs(p[1] - gy_) else p[3]) / 2)
                      for p in pads) * k
        cx, cy = machine_to_px(v["at"], mx, my, k, SIZE, cam)
        ax, ay = machine_to_px(v["at"], pads_m[0][0], pads_m[0][1], k, SIZE, cam)
        bx, by = machine_to_px(v["at"], pads_m[-1][0], pads_m[-1][1], k, SIZE, cam)
        axis = math.degrees(math.atan2(by - ay, bx - ax))
        bl = cv2.GaussianBlur(img, (0, 0), 2).astype(np.float32)
        sx, sy = cv2.Sobel(bl, cv2.CV_32F, 1, 0), cv2.Sobel(bl, cv2.CV_32F, 0, 1)
        cand, side_of = [], []
        for side in (90, 270):
            for da in np.linspace(-55, 55, 61):
                a = math.radians(axis + side + da)
                ux, uy = math.cos(a), math.sin(a)
                prof = []
                for rr in np.arange(0.2 * gap, gap, 1.0):
                    x, y = cx + ux * rr, cy + uy * rr
                    xi, yi = int(round(x)), int(round(y))
                    if not (0 <= xi < bl.shape[1] and 0 <= yi < bl.shape[0]):
                        break
                    prof.append((sx[yi, xi] * ux + sy[yi, xi] * uy, x, y))
                for j in range(1, len(prof) - 1):        # every darker-inside edge on the ray
                    if prof[j][0] > 12 and prof[j][0] >= prof[j - 1][0] and prof[j][0] >= prof[j + 1][0]:
                        cand.append(prof[j][1:])
                        side_of.append(side)
        if len(cand) < 30:
            return None
        # the hole wall gives a second ring on one side, traces and text more edges: the circle
        # most edges agree on (RANSAC) is the hole's rim
        P, S = np.array(cand), np.array(side_of)
        rng = np.random.default_rng(0)
        best, bn = None, 0
        for _ in range(1500):
            c3 = _circle3(P[rng.choice(len(P), 3, replace=False)])
            if c3 is None or not 0.25 * gap < c3[2] < gap or math.hypot(c3[0] - cx, c3[1] - cy) > 0.4 * gap:
                continue
            n = int((np.abs(np.hypot(P[:, 0] - c3[0], P[:, 1] - c3[1]) - c3[2]) < 2.5).sum())
            if n > bn:
                best, bn = c3, n
        if best is None:
            return None
        inl = np.abs(np.hypot(P[:, 0] - best[0], P[:, 1] - best[1]) - best[2]) < 2.5
        if min((S[inl] == 90).sum(), (S[inl] == 270).sum()) < (20 if strict else 15):   # both sides of the hole
            self.log(f"hole: rim seen on {(S[inl] == 90).sum()} / {(S[inl] == 270).sum()} rays only")
            return None
        Q = P[inl]
        fx, fy, fr = _fit_circle(Q)
        rms = float(np.sqrt(((np.hypot(Q[:, 0] - fx, Q[:, 1] - fy) - fr) ** 2).mean()))
        self.log(f"hole: centre {fx:.1f}, {fy:.1f} px, d {2 * fr / k:.3f} mm, rms {rms / k * 1000:.0f} um, {len(Q)} of {len(P)} edges")
        if rms > (0.03 if strict else 0.05) * k or math.hypot(fx - cx, fy - cy) > (0.25 if strict else 0.4) * gap:
            return None
        # the automatic search also needs most of the edges to lie on the circle: a real rim is one
        # strong edge on every ray (LD1..LD8: 21..24 % of the edges); a plain gap between pads (the SOD-123
        # D15 of the BOARD-B) has silkscreen and pad edges everywhere, a few of them on some circle (8 %)
        if strict and len(Q) < 0.15 * len(P):
            self.log(f"hole: only {len(Q)} of {len(P)} edges on the circle: no hole")
            return None
        v["_hole"] = (fx, fy, fr)
        return px_to_machine(v["at"], fx, fy, k, SIZE, cam)

    def align(self, pads, centre, tf, images=False, hole=False):
        """pads: [[x, y, w, h], ...] job frame; centre: part centre, job frame; tf: MarkTransform
        (machine = tf.point(x, y)). The correction is to be added to the placement point.
        hole: the part sits over a hole of the board between its pads (reverse-mount LED); its
        centre gives the position, the pads the turn."""
        k = self.cfg.pxmm(1)
        pred = [tf.point(p[0], p[1])[:2] for p in pads]
        cx_, cy_, board_turn = tf.point(*centre)
        c = (cx_, cy_)
        axes_all = pad_axes(pads, board_turn)
        if len(pred) < 2:
            return AlignResult(False, expected=len(pred), message="fewer than 2 pads known for this part")
        sizes = [p[2] * p[3] for p in pads]
        lo, hi = min(sizes) * 0.3, max(sizes) * 2.5
        blobs, views = [], []
        gerber = str(self.p("sMethod") or "gerber").lower() == "gerber"
        by_pad = {}                                      # gerber method: pad index -> (Blob, distance to view centre)
        for v in self.views_for(pred, k, [(p[2], p[3]) for p in pads]):
            self.move(*v)
            img = self.grab()
            if gerber:
                found = find_pads_gerber(img, v, pred, [(p[2], p[3]) for p in pads], k, board_turn,
                                         coarse_mm=float(self.p("fMatchRadiusMm")), cam_angle=float(self.p("fHeadCamAngle")),
                                         min_score=float(self.p("fMinScore")))
                bl = [b for _, b in found]
                for i, b in found:
                    dc = math.hypot(b.px - SIZE / 2, b.py - SIZE / 2)
                    if i not in by_pad or dc < by_pad[i][1]:
                        by_pad[i] = (b, dc)
                views.append({"at": [round(v[0], 3), round(v[1], 3)], "blobs": len(bl), "threshold": 0,
                              "_img": img, "_blobs": bl})
                if self.on_view:
                    self.on_view(img, {"at": list(v), "blobs": len(bl), "method": "gerber", "expected": [list(p) for p in pred],
                                       "found": [[b.x, b.y, b.contrast] for b in bl]})
                continue
            bl, t = detect_pads(img, k, int(self.p("nThreshold")), bool(self.p("bInvert")),
                                max(lo, float(self.p("fMinPadMm2"))), min(hi, float(self.p("fMaxPadMm2"))),
                                split_mm=0.4 * min(min(p[2], p[3]) for p in pads))
            for b in bl:
                b.x, b.y = px_to_machine(v, b.px, b.py, k, SIZE, float(self.p("fHeadCamAngle")))
            # a pad seen in two overlapping views counts once: keep the one nearer its view's centre
            for b in bl:
                dup = next((o for o in blobs if math.hypot(o.x - b.x, o.y - b.y) < 0.15), None)
                if dup is None:
                    blobs.append(b)
                elif math.hypot(b.px - SIZE / 2, b.py - SIZE / 2) < getattr(dup, "_dc", 1e9):
                    blobs[blobs.index(dup)] = b
                b._dc = math.hypot(b.px - SIZE / 2, b.py - SIZE / 2)
            views.append({"at": [round(v[0], 3), round(v[1], 3)], "blobs": len(bl), "threshold": t,
                          "_img": img, "_blobs": bl})
            if self.on_view:
                self.on_view(img, {"at": list(v), "blobs": len(bl), "threshold": t, "expected": [list(p) for p in pred],
                                   "found": [[b.x, b.y, b.area_mm2] for b in bl]})
        if gerber:
            blobs = [by_pad[i][0] for i in sorted(by_pad)]
        half = SIZE / k / 2 * 0.95
        seen = [i for i, p in enumerate(pred) if any(abs(p[0] - v["at"][0]) < half and abs(p[1] - v["at"][1]) < half for v in views)]
        sp = [pred[i] for i in seen]
        fit, pairs, rms = match(sp, blobs, c, float(self.p("fMatchRadiusMm")), areas=[sizes[i] for i in seen],
                                axes=[axes_all[i] for i in seen])
        if gerber and fit is not None:
            # stage 2: every Gerber rectangle at the corrected place and angle. A part that needed
            # several views, or lost pads at a view's edge (a big part, or a big first shift: U16,
            # U6 on the BOARD-A), is photographed AGAIN from the corrected place, so each pad lies
            # whole inside a view; a small part is refined on the same picture.
            moved = [apply_about(x, y, c, fit) for x, y in pred]
            psz = [(p[2], p[3]) for p in pads]
            again = len(views) > 1 or len(pairs) < len(sp)
            first = (views, seen, sp)
            if again:
                views2 = []
                for v in self.views_for(moved, k, psz):
                    self.move(*v)
                    views2.append({"at": [round(v[0], 3), round(v[1], 3)], "blobs": 0, "threshold": 0,
                                   "_img": self.grab(), "_blobs": [], "stage": 2})
                views = views2
            by_pad = {}
            for v in views:
                found = find_pads_gerber(v["_img"], v["at"], moved, psz, k, board_turn + fit[2],
                                         coarse_mm=0.1 if again else 0.05, fine_mm=0.1 if again else 0.08,
                                         cam_angle=float(self.p("fHeadCamAngle")), min_score=float(self.p("fMinScore")))
                for i, b in found:
                    dc = math.hypot(b.px - SIZE / 2, b.py - SIZE / 2)
                    if i not in by_pad or dc < by_pad[i][1]:
                        by_pad[i] = (b, dc)
                v["_blobs"] = [b for _, b in found]
                v["blobs"] = len(found)
                if again and self.on_view:
                    self.on_view(v["_img"], {"at": list(v["at"]), "blobs": len(found), "method": "gerber", "stage": 2,
                                             "expected": [list(p) for p in moved], "found": [[b.x, b.y, b.contrast] for _, b in found]})
            if again:                                   # the pads these views can show whole
                seen = [i for i, p in enumerate(moved)
                        if any(abs(p[0] - v["at"][0]) < half - psz[i][0] / 2 and abs(p[1] - v["at"][1]) < half - psz[i][1] / 2
                               for v in views)]
                sp = [pred[i] for i in seen]
            blobs2 = [by_pad[i][0] for i in sorted(by_pad)]
            fit2, pairs2, rms2 = match(sp, blobs2, c, float(self.p("fMatchRadiusMm")), areas=[sizes[i] for i in seen],
                                       axes=[axes_all[i] for i in seen])
            if fit2 is not None and (again or len(pairs2) >= len(pairs)):
                fit, pairs, rms, blobs = fit2, pairs2, rms2, blobs2
            elif again:                                  # stage 2 found nothing usable: keep stage 1
                views, seen, sp = first
        hole_xy = None
        found_auto = False
        if hole and fit is not None:
            # a part aligned on a hole of the board (reverse-mount LED: it shines through the hole
            # between its pads): the hole's centre is the part's centre, the turn comes from the pads
            hole_xy = self._find_hole(views, [apply_about(x, y, c, fit) for x, y in pred], pads, k)
        elif (fit is not None and len(pads) == 2 and bool(self.p("bAutoHole"))
              and pad_gap(pads) >= float(self.p("fAutoHoleGapMm"))):
            # not marked, but its pads stand far apart: a clear round hole between them (strict
            # test) makes it a hole part on its own
            hole_xy = self._find_hole(views, [apply_about(x, y, c, fit) for x, y in pred], pads, k, strict=True)
            hole = found_auto = hole_xy is not None
        if hole_xy is not None:
            fit = (hole_xy[0] - c[0], hole_xy[1] - c[1], fit[2])
        r = AlignResult(False, expected=len(sp), matched=len(pairs))
        if fit is None:
            r.message = f"only {len(pairs)} of {len(sp)} pads found"
        else:
            r.dx, r.dy, r.dtheta, r.rms = fit[0], fit[1], fit[2], rms
            r.scale = spacing_scale([sp[i] for i, _, _ in pairs], [(blobs[j].x, blobs[j].y) for _, j, _ in pairs])
            need = max(2, int(math.ceil(len(sp) * float(self.p("fMinMatched")))))
            if hole and hole_xy is not None:
                r.ok = True
                r.message = ("hole found by the camera: " if found_auto else "") + (f"hole centre: shift {fit[0]:+.3f}, {fit[1]:+.3f} mm, turn {fit[2]:+.3f} deg "
                             f"(from {len(pairs)}/{len(sp)} pads)")
            elif len(pairs) < need:
                r.message = f"only {len(pairs)} of {len(sp)} pads matched (need {need})"
            elif (abs(r.scale - 1) > float(self.p("fMaxScaleErr")) and
                  abs(r.scale - 1) * _mean_radius([sp[i] for i, _, _ in pairs]) > float(self.p("fMaxSpacingErrMm"))):
                r.message = f"pad spacing {r.scale:.3f} x the Gerber: not this part's pads (or wrong px/mm)"
            elif rms > float(self.p("fMaxRmsMm")):
                r.message = f"pads do not fit well (rms {rms:.3f} mm)"
            else:
                r.ok = True
                r.message = ("hole not clear, from the pads: " if hole else "") + f"{len(pairs)}/{len(sp)} pads, shift {fit[0]:+.3f}, {fit[1]:+.3f} mm, turn {fit[2]:+.3f} deg, rms {rms:.3f} mm"
        if images:
            for v in views:
                v["png"] = self.annotate(v, sp, pairs, blobs, c, fit, k, [(pads[i][2], pads[i][3]) for i in seen], board_turn)
        for v in views:
            v.pop("_img", None); v.pop("_blobs", None); v.pop("_hole", None)
        r.views = views
        return r

    @staticmethod
    def annotate(v, pred, pairs, blobs, centre, fit, k, sizes=None, turn=0.0):
        """image with rectangles: found pads (yellow, their own outline), expected pads from the
        Gerber (blue), the same pads after the fitted correction (green)"""
        img = cv2.cvtColor(v["_img"], cv2.COLOR_GRAY2BGR) if v["_img"].ndim == 2 else v["_img"].copy()
        at = v["at"]
        to_px = lambda x, y: ((x - at[0]) * k + SIZE / 2, SIZE / 2 - (y - at[1]) * k)

        def box(cxy, wh, ang, colour, th):
            pts = cv2.boxPoints((cxy, wh, ang))
            cv2.polylines(img, [np.round(pts).astype(np.int32)], True, colour, th, cv2.LINE_AA)
        for b in v["_blobs"]:
            if b.rect:
                box(b.rect[0], b.rect[1], b.rect[2], (0, 220, 255), 2)
            else:
                box((b.px, b.py), (b.w_mm * k, b.h_mm * k), 0, (0, 220, 255), 2)
        for i, (x, y) in enumerate(pred):
            w, h = (sizes[i] if sizes else (0.3, 0.3))
            box(to_px(x, y), (w * k, h * k), -turn, (255, 120, 0), 1)
            if fit:
                box(to_px(*apply_about(x, y, centre, fit)), (w * k, h * k), -(turn + fit[2]), (0, 220, 0), 1)
        if v.get("_hole"):                                # the board hole the part is centred on (magenta)
            hx, hy, hr = v["_hole"]
            cv2.circle(img, (int(round(hx)), int(round(hy))), int(round(hr)), (255, 0, 255), 2, cv2.LINE_AA)
            cv2.drawMarker(img, (int(round(hx)), int(round(hy))), (255, 0, 255), cv2.MARKER_CROSS, 24, 2)
        cv2.drawMarker(img, (SIZE // 2, SIZE // 2), (0, 0, 255), cv2.MARKER_CROSS, 30, 1)
        ok, buf = cv2.imencode(".png", img)
        return base64.b64encode(buf.tobytes()).decode() if ok else ""


def on_hole(cfg, ref, footprint=""):
    """is this part aligned on the board hole between its pads? [PadAlign] sHoleRefs / sHoleFootprints"""
    refs = [s.strip() for s in str(cfg.padalign("sHoleRefs")).split(",") if s.strip()]
    fps = [s.strip().lower() for s in str(cfg.padalign("sHoleFootprints")).split(",") if s.strip()]
    return ref in refs or bool(footprint and footprint.lower() in fps)


def wanted(cfg, comp, stack):
    """is local alignment switched on for this part? The part's own setting (job file), else its
    feeder's, else [PadAlign] bEnabled + sFootprints / sRefs"""
    own = getattr(comp, "pad_align", None)
    if own is not None:
        return bool(own)
    if stack is not None and getattr(stack, "pad_align", False):
        return True
    if not cfg.padalign("bEnabled"):
        return False
    refs = [s.strip() for s in str(cfg.padalign("sRefs")).split(",") if s.strip()]
    fps = [s.strip().lower() for s in str(cfg.padalign("sFootprints")).split(",") if s.strip()]
    if comp.name in refs:
        return True
    fp = (comp.footprint or (stack.footprint if stack else "") or "").lower()
    return "*" in fps or (fp and fp in fps)
