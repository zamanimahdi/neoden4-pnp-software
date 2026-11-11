"""Feeder pick-location calibration with the head camera (tape sprocket holes).

A tape feeder's pick position drifts (feeder mounted a little off, tape guide wear, a new
tape). The head camera looks at the pick position, finds the tape's sprocket holes and
computes where the pocket really is and how the tape is turned (EIA-481 geometry):

    sprocket hole  diameter 1.5 mm, pitch P0 = 4 mm
    pocket centre  P2 = 2.0 mm along the tape from a hole,
                   F across the tape from the hole line: 3.5 mm (8 mm tape), 5.5 (12),
                   7.5 (16), 11.5 (24); pocket pitch P1 (2 or 4 mm for 8 mm tape)

The pocket found is the one of that grid nearest to the expected pick position, so the
correction can never jump to the next pocket. The tape angle is the angle of the hole row.

Coordinates: like NeoDen4.exe's stored positions, in the "nozzle 1" frame (a stored XY is
where nozzle 1 goes). The head camera sits at cam_offset from nozzle 1
(getNozzleOffset(0) = dPosCamLookDownMouse1): to look at stored S the gantry moves to
S + cam_offset, i.e. the camera centre is at gantry - cam_offset.

    from neocalib.feeder import calibrate_feeder
    r = calibrate_feeder(mb, grab_head, cal_head, expected=(408.89, 113.46), cam_offset=(dx, dy))
    print(r.summary()); new_xy = r.pick; angle_fix = r.tape_angle_deg
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .settle import grab_settled

HOLE_D = 1.5
HOLE_PITCH = 4.0
POCKET_ALONG = 2.0
POCKET_ACROSS = {8: 3.5, 12: 5.5, 16: 7.5, 24: 11.5, 32: 14.2}


@dataclass
class FeederCal:
    ok: bool
    message: str
    expected: tuple
    pick: tuple = None              # corrected pick position (nozzle-1 frame, mm)
    dx: float = 0.0                 # pick - expected (mm)
    dy: float = 0.0
    tape_angle_deg: float = 0.0     # hole row direction vs the tape axis (deg)
    holes: list = field(default_factory=list)    # hole centres (mm) used
    line_rms_mm: float = 0.0
    pitch_mm: float = 0.0           # measured hole pitch (check of the camera scale)
    views: int = 1

    def summary(self):
        if not self.ok:
            return f"feeder at ({self.expected[0]:.2f}, {self.expected[1]:.2f}): FAILED - {self.message}"
        return (f"pick ({self.pick[0]:.3f}, {self.pick[1]:.3f}) = expected {self.dx:+.3f} / {self.dy:+.3f} mm, "
                f"tape {self.tape_angle_deg:+.2f} deg, {len(self.holes)} holes (line rms "
                f"{self.line_rms_mm * 1000:.0f} um, pitch {self.pitch_mm:.3f} mm), {self.views} view(s)")


# ---- image: round holes ---------------------------------------------------------

def find_holes(img, r_min_px, r_max_px, min_circ=0.75):
    """Round blobs of radius r_min..r_max px, either polarity, at a few thresholds around
    Otsu (CPU-light). Returns [(x, y, r, circularity)], duplicates merged."""
    g = cv2.GaussianBlur(img, (5, 5), 0)
    otsu, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    H, W = g.shape[:2]
    found = []
    for t in (otsu - 50, otsu - 25, otsu, otsu + 25, otsu + 50):
        if not 5 < t < 250:
            continue
        _, bw = cv2.threshold(g, t, 255, cv2.THRESH_BINARY)
        for mask in (bw, 255 - bw):
            cs, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            for c in cs:
                a = cv2.contourArea(c)
                if a <= 0:
                    continue
                r = math.sqrt(a / math.pi)
                if not r_min_px <= r <= r_max_px:
                    continue
                bx, by, bw_, bh_ = cv2.boundingRect(c)
                if bx <= 2 or by <= 2 or bx + bw_ >= W - 2 or by + bh_ >= H - 2:
                    continue                    # cut by the image border: its centre would be off
                per = cv2.arcLength(c, True)
                circ = 4 * math.pi * a / (per * per) if per else 0
                if circ < min_circ:
                    continue
                m = cv2.moments(c)
                found.append((m["m10"] / m["m00"], m["m01"] / m["m00"], r, circ))
    merged = []
    for h in sorted(found, key=lambda f: -f[3]):
        if all(math.hypot(h[0] - k[0], h[1] - k[1]) > k[2] * 0.5 for k in merged):
            merged.append(h)
    return merged


# ---- geometry: the hole row -----------------------------------------------------------

def fit_hole_row(pts, pitch=HOLE_PITCH, tol_line=0.15, tol_pitch=0.25):
    """From hole centres (mm) pick the largest set lying on one line at multiples of `pitch`
    (RANSAC over pairs) and fit it. Returns (point, unit direction, inliers, rms) or None."""
    pts = np.asarray(pts, float)
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = pts[j] - pts[i]
            L = np.linalg.norm(d)
            k = round(L / pitch)
            if k < 1 or abs(L - k * pitch) > tol_pitch:
                continue
            u = d / L
            n = np.array([-u[1], u[0]])
            rel = pts - pts[i]
            across = rel @ n
            along = rel @ u
            phase = along - np.round(along / pitch) * pitch
            inl = np.where((np.abs(across) < tol_line) & (np.abs(phase) < tol_pitch))[0]
            if best is None or len(inl) > len(best):
                best = inl
    if best is None or len(best) < 2:
        return None
    P = pts[best]
    c = P.mean(axis=0)
    _, _, vt = np.linalg.svd(P - c)
    u = vt[0]
    n = np.array([-u[1], u[0]])
    rms = float(np.sqrt(np.mean(((P - c) @ n) ** 2)))
    return c, u, P, rms


def pocket_near(holes_c, u, P, expected, tape_width=8, pocket_pitch=4.0, along=POCKET_ALONG):
    """Pocket centre (mm) of the tape grid nearest to `expected`, plus the measured hole pitch."""
    across = POCKET_ACROSS.get(int(tape_width))
    if across is None:
        raise ValueError(f"tape width must be one of {sorted(POCKET_ACROSS)}")
    s = (P - holes_c) @ u                       # hole positions along the row
    ref = holes_c + u * s.min()                 # one hole on the row
    k = np.round((s - s.min()) / HOLE_PITCH)
    pitch = float(np.polyfit(k, s, 1)[0]) if len(set(k)) > 1 else HOLE_PITCH
    # refine the reference hole as the best-fit grid origin
    s0 = float(np.mean(s - k * pitch))
    ref = holes_c + u * s0
    n = np.array([-u[1], u[0]])
    e = np.asarray(expected, float)
    side = 1.0 if (e - ref) @ n >= 0 else -1.0  # the pocket is on the side of the expected pick
    t = (e - ref) @ u                           # snap along the tape to the nearest pocket
    m = round((t - along) / pocket_pitch)
    pocket = ref + u * (along + m * pocket_pitch) + n * side * across
    return pocket, pitch


# ---- procedure ----------------------------------------------------------------------------

def _holes_world(img, cal, cam_centre, r_px):
    h, w = img.shape[:2]
    out = []
    for (x, y, r, q) in find_holes(img, r_px * 0.75, r_px * 1.3):
        dX, dY = cal.img_to_mm(x - w / 2, y - h / 2)
        out.append((cam_centre[0] - dX, cam_centre[1] - dY))   # see make_measure: -A^-1 q
    return out


def calibrate_feeder(mb, grab, cal, expected, cam_offset=(0.0, 0.0), tape_width=8, pocket_pitch=4.0,
                     tape_axis_deg=0.0, views="auto", settle=True, max_dist=2.0):
    """Look at one feeder and return FeederCal.

    expected: stored pick XY (nozzle-1 frame); cam_offset: head camera vs nozzle 1;
    cal: head camera CalResult; tape_axis_deg: nominal tape direction (0 = along X);
    views: "auto" (a second and third view along the tape if fewer than 3 holes are seen),
    1 or 3; max_dist: refuse corrections larger than this (wrong feeder / no tape)."""
    e = np.asarray(expected, float)
    off = np.asarray(cam_offset, float)
    r_px = 0.5 * HOLE_D * 0.5 * (cal.k_x + cal.k_y)
    ax = np.array([math.cos(math.radians(tape_axis_deg)), math.sin(math.radians(tape_axis_deg))])
    nx = np.array([-ax[1], ax[0]])
    half = 0.5 * POCKET_ACROSS.get(int(tape_width), 3.5)
    holes, nviews = [], 0

    def look(target):
        nonlocal nviews
        mb.move_xy(*(target + off), slow=True)
        img = grab_settled(grab).frame if settle else grab()
        nviews += 1
        n0 = len(holes)
        for p in _holes_world(img, cal, target, r_px):
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) > HOLE_D / 2 for q in holes):
                holes.append(p)
        return len(holes) - n0

    if views == 3:
        for sh in (0.0, -HOLE_PITCH * 0.75, HOLE_PITCH * 0.75):
            look(e + ax * sh)
    else:
        look(e)
        if views == "auto" and len(holes) < 3:
            # the hole row is F mm across from the pocket: in a small window it is cut off at
            # the edge. Look half way across on both sides, then along the tape on the better side.
            got = {s: look(e + nx * s * half) for s in (1, -1)}
            if len(holes) < 3:
                # continue on the side where the holes are (they are F mm across from the pocket)
                if holes:
                    s = 1 if np.mean([(np.asarray(q) - e) @ nx for q in holes]) >= 0 else -1
                else:
                    s = max(got, key=got.get)
                for sh in (-HOLE_PITCH * 0.75, HOLE_PITCH * 0.75):
                    look(e + nx * s * half + ax * sh)
    mb.move_xy(*(e + off), slow=True)
    res = FeederCal(False, "", tuple(map(float, e)), views=nviews)
    if len(holes) < 2:
        res.message = f"only {len(holes)} sprocket hole(s) found - no tape, wrong position or light?"
        return res
    row = fit_hole_row(holes)
    if row is None:
        res.message = f"{len(holes)} round blobs but no row of holes at {HOLE_PITCH:g} mm pitch"
        return res
    c, u, P, rms = row
    if u @ ax < 0:
        u = -u
    pocket, pitch = pocket_near(c, u, P, e, tape_width, pocket_pitch)
    d = pocket - e
    ang = math.degrees(math.atan2(u[1], u[0])) - tape_axis_deg
    ang = (ang + 90) % 180 - 90
    res.holes, res.line_rms_mm, res.pitch_mm = [tuple(map(float, p)) for p in P], rms, pitch
    res.tape_angle_deg = ang
    if np.hypot(*d) > max_dist:
        res.message = f"correction {np.hypot(*d):.2f} mm is larger than {max_dist} mm - check the feeder"
        res.pick, res.dx, res.dy = tuple(map(float, pocket)), float(d[0]), float(d[1])
        return res
    res.ok, res.message = True, "ok"
    res.pick, res.dx, res.dy = tuple(map(float, pocket)), float(d[0]), float(d[1])
    return res


def calibrate_feeders(mb, grab, cal, feeders, cam_offset=(0.0, 0.0), progress=None, **kw):
    """feeders: [(name, expected_xy, options dict)] -> [(name, FeederCal)]. Failures do not
    stop the run; each result says what went wrong."""
    out = []
    for i, (name, xy, opts) in enumerate(feeders):
        args = dict(kw)
        args.update(opts or {})
        try:
            r = calibrate_feeder(mb, grab, cal, xy, cam_offset, **args)
        except Exception as ex:                                    # one bad feeder must not stop the rest
            r = FeederCal(False, f"error: {ex}", tuple(xy))
        out.append((name, r))
        if progress:
            progress(i + 1, len(feeders), name, r)
    return out
