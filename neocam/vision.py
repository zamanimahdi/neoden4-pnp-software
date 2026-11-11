"""NeoDen4 vision, ported from NeoDen4.exe V4.1.3 to OpenCV 4.

Constants are the original ones. Float -> int is truncation, like the original (x87 RC=11).
Known bugs of the original are fixed and marked "fix:".

Image conventions: 8-bit gray, image +x = machine +X, image +y = machine -Y.
Angles: degrees, counter-clockwise positive in the machine frame.
"""
import math
from dataclasses import dataclass

import cv2
import numpy as np

UP_PX_PER_MM = 34.5          # up-looking camera (0x4C22AC)
HEAD_PX_PER_MM = 112.0       # head camera (0x4C2794)
EXPOSURE_K = 50.0            # up camera exposure = trunc(50 * fBrightness)


# ---------------------------------------------------------------- orientation
def orient_up(raw):
    """up camera frame as NeoDen4 uses it: transpose, then flip both axes (CImgBoard::grabFrame)"""
    return cv2.flip(cv2.transpose(raw), -1)


def orient_head(raw):
    """head camera frame: 180 degree rotation (CImgHead::capture)"""
    return cv2.flip(raw, -1)


# ---------------------------------------------------------------- helpers
def _threshold(img, t):
    return cv2.threshold(img, t, 255, cv2.THRESH_BINARY)[1]


def _open(img, n):
    k = np.ones((3, 3), np.uint8)
    return cv2.dilate(cv2.erode(img, k, iterations=n), k, iterations=n)


def merged_contour_points(roi):
    """all points of all contours (RETR_TREE, CHAIN_APPROX_SIMPLE), or None"""
    cs, _ = cv2.findContours(roi.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    return np.vstack([c.reshape(-1, 2) for c in cs]).astype(np.int32)


def neoden_rect(pts):
    """minAreaRect with NeoDen's (OpenCV 2.4.9 based) semantics, independent of the OpenCV version.
    Returns cx, cy (px, ROI coords), w_px (side nearest the x axis), h_px, angle (machine CCW, (-45, 45])."""
    rect = cv2.minAreaRect(pts)
    box = cv2.boxPoints(rect)
    e1, e2 = box[1] - box[0], box[2] - box[1]
    ex, ey = (e1, e2) if abs(e1[0]) >= abs(e1[1]) else (e2, e1)
    if ex[0] < 0:
        ex = -ex
    ang_img = math.degrees(math.atan2(ex[1], ex[0]))
    return rect[0][0], rect[0][1], float(np.hypot(*ex)), float(np.hypot(*ey)), -ang_img


@dataclass
class PartResult:
    valid: bool
    x: float = 0.0          # mm, part centre relative to the nozzle (ROI centre), machine axes
    y: float = 0.0
    w: float = 0.0          # mm, side along machine X
    h: float = 0.0          # mm, side along machine Y
    angle: float = 0.0      # deg, machine CCW
    rect_px: tuple = None   # (cx, cy, w, h, angle) in frame pixels, for drawing
    empty: bool = False     # nothing differs from the empty-nozzle background: no part was picked


# ---------------------------------------------------------------- up camera
def analyze_nozzles(frame, background, flags, sub_pos, k=UP_PX_PER_MM):
    """all four nozzles in one up-camera shot (CImgBoard::analyzeNozzles).
    frame, background: oriented 1024x1024; flags: 4 bools; sub_pos: 4 (x, y) px nozzle centres
    (Config.ini Position/dPosSubMouses0..3)."""
    work = _open(_threshold(cv2.subtract(frame, background), 30), 1)
    out = []
    for i in range(4):
        if not flags[i]:
            out.append(PartResult(False))
            continue
        rx, ry = int(sub_pos[i][0] - 100), int(sub_pos[i][1] - 100)
        pts = merged_contour_points(work[max(ry, 0):ry + 200, max(rx, 0):rx + 200])
        if pts is None:
            out.append(PartResult(False))
            continue
        cx, cy, w, h, a = neoden_rect(pts)
        # fix: the original keeps 2.4's unswapped w/h here, which breaks the size check for
        # rotated parts; use the same semantics as analyze_nozzle
        out.append(PartResult(True, (cx - 100) / k, (100 - cy) / k, w / k, h / k, a,
                              (cx + max(rx, 0), cy + max(ry, 0), w, h, a)))
    return out


def tip_centre(img, x, y, search=60, area=170):
    """an empty nozzle's tip near (x, y) px: the dark spot (the tip's hole, or a big nozzle's dark
    inside) nearest there, the centroid of its filled area, so the nozzle's turn does not change it.
    Needs a bright exposure (~600 for an empty nozzle). Returns (x, y, radius) px or None."""
    h, w = img.shape
    area = int(min(area, x, y, w - x, h - y))            # a nozzle near the image edge (4-up shot)
    if area < search + 20:
        return None
    X0, Y0 = int(round(x)) - area, int(round(y)) - area
    R = cv2.GaussianBlur(img[Y0:Y0 + 2 * area, X0:X0 + 2 * area], (0, 0), 1.5).astype(np.float32)
    c = R[area - search:area + search, area - search:area + search]
    sy, sx = np.unravel_index(np.argmin(c), c.shape)
    sx, sy = sx + area - search, sy + area - search
    lo, hi = float(R[sy, sx]), float(np.percentile(R, 90))
    if hi - lo < 15:
        return None
    _, lab, stats, _ = cv2.connectedComponentsWithStats((R < lo + 0.3 * (hi - lo)).astype(np.uint8), connectivity=8)
    L = lab[sy, sx]
    x0, y0, ww, hh, _ = stats[L]
    if x0 == 0 or y0 == 0 or x0 + ww >= 2 * area or y0 + hh >= 2 * area:
        return None
    cs, _ = cv2.findContours((lab == L).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros(R.shape, np.uint8)
    cv2.drawContours(filled, cs, -1, 1, -1)
    m = cv2.moments(filled, True)
    return X0 + m["m10"] / m["m00"], Y0 + m["m01"] / m["m00"], math.sqrt(m["m00"] / math.pi)


def fit_circle(pts):
    """least-squares circle through points [(x, y)]: (cx, cy, r, rms)"""
    P = np.asarray(pts, float)
    A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
    c, *_ = np.linalg.lstsq(A, P[:, 0] ** 2 + P[:, 1] ** 2, rcond=None)
    r = math.sqrt(max(0.0, c[2] + c[0] ** 2 + c[1] ** 2))
    d = np.hypot(P[:, 0] - c[0], P[:, 1] - c[1]) - r
    return float(c[0]), float(c[1]), r, float(np.sqrt((d * d).mean()))


def analyze_at(frame, background, x, y, p, k=UP_PX_PER_MM, thr=50):
    """analyze_nozzle for a nozzle at (x, y) px of a bigger frame (the 4-up shot): the frame is
    cut around it and the part's offset is from (x, y) itself, to the sub-pixel"""
    ix, iy = int(round(x)), int(round(y))
    h, w = frame.shape
    p = min(p, ix, iy, w - ix, h - iy)
    if p < 20:
        return PartResult(False)
    r = analyze_nozzle(frame[iy - p:iy + p, ix - p:ix + p], background[iy - p:iy + p, ix - p:ix + p], p, k, thr)
    if r.valid:
        r.x += (ix - x) / k
        r.y += (y - iy) / k
        if r.rect_px:
            r.rect_px = (r.rect_px[0] + ix - p, r.rect_px[1] + iy - p) + tuple(r.rect_px[2:])
    return r


def perspective_fix(r, axis_px, centre_px, k, height_mm, mag_per_mm):
    """a part measured at axis_px of the up camera picture (4-up shot), its underside height_mm
    nearer the lens than the tip plane the axis was found in: the lens draws it bigger and pushed
    away from the picture's middle. Returns r with offset and size in tip-plane mm."""
    m = mag_per_mm * height_mm
    if not r.valid or not m:
        return r
    dx = r.x * k - (axis_px[0] - centre_px[0]) * m          # px, image x = machine X
    dy = -r.y * k - (axis_px[1] - centre_px[1]) * m         # px, image y = machine -Y
    r.x, r.y = dx / (k * (1 + m)), -dy / (k * (1 + m))
    r.w, r.h = r.w / (1 + m), r.h / (1 + m)
    return r


def roi_half_size(pkg_x_mm, pkg_y_mm, k=UP_PX_PER_MM):
    """ROI half size for analyze_nozzle (CProcPaist::runVisionForNozzles): 2x the part, 100..500 px"""
    if not pkg_x_mm and not pkg_y_mm:
        return 500
    return min(max(int(max(pkg_x_mm, pkg_y_mm) * k), 100), 500)


def analyze_nozzle(frame, background, p, k=UP_PX_PER_MM, thr=50):
    """one nozzle centred in the frame (CImgBoard::analyzeNozzle); p = ROI half size in px;
    thr = threshold on (frame - background), NeoDen4: 50"""
    work = _open(_threshold(cv2.subtract(frame, background), thr), 2)
    r, c = work.shape
    x0, y0 = c // 2 - p, r // 2 - p
    pts = merged_contour_points(work[y0:y0 + 2 * p, x0:x0 + 2 * p])
    if pts is None:
        return PartResult(False, empty=True)
    cx, cy, w, h, a = neoden_rect(pts)
    return PartResult(True, (cx - p) / k, (p - cy) / k, w / k, h / k, a, (cx + x0, cy + y0, w, h, a))


def _line_angle(dx, dy):
    if dx == 0:
        return 89.99999 if dy > 0 else 270.0
    t = math.atan(dy / dx) + (math.pi if dx < 0 else 0.0)
    d = t * 180.0 / math.pi
    return d + 360.0 if d < 0 else d


def find_hull_edge_angle(hull, second):
    """(ok, (x, y) corner px, theta deg) from the convex hull of a large part (0x004295E0)"""
    n = len(hull)
    a = b = None
    for i in range(n):
        p0, p1 = hull[i - 1], hull[i]
        dx, dy = int(p1[0] - p0[0]), int(p1[1] - p0[1])
        if math.sqrt(dx * dx + dy * dy) < 50:
            continue
        t = _line_angle(dx, dy)
        if not second:
            if 165 < t < 195:
                b = (t, p0, p1)
            elif 255 < t < 285:
                a = (t, p0, p1)
        else:
            if t < 15:
                b = (t, p0, p1)
            elif t > 345:
                b = (t - 360, p0, p1)
            elif 75 < t < 105:
                a = (t, p0, p1)
    if a is None or b is None or not (85 <= a[0] - b[0] <= 95):
        return False, None, 0.0
    tb = b[0] - 360 if b[0] > 90 else b[0]
    theta = -0.5 * tb - 0.5 * (a[0] - 90)
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = b[1], b[2], a[1], a[2]
    c1 = y2 * x1 - x2 * y1
    c2 = x3 * y4 - x4 * y3
    den = (y1 - y2) * (x4 - x3) - (x2 - x1) * (y3 - y4)
    if den == 0:
        return False, None, 0.0
    x = int(((x2 - x1) * c2 - (x4 - x3) * c1) / den)
    y = int(((y3 - y4) * c1 - c2 * (y1 - y2)) / den)
    return True, (x, y), theta


def analyze_hull_view(frame, background, second, k=UP_PX_PER_MM):
    """one of the two half views of a large part (CImgBoard::analyzeNozzleHull).
    Returns (ok, x_mm, y_mm, theta): the corner relative to the frame centre."""
    work = _open(_threshold(cv2.subtract(frame, background), 50), 2)
    r, c = work.shape
    x0, y0 = c // 2 - 400, r // 2 - 400
    pts = merged_contour_points(work[y0:y0 + 800, x0:x0 + 800])
    if pts is None:
        return False, 0.0, 0.0, 0.0
    hull = cv2.convexHull(pts, clockwise=False).reshape(-1, 2)
    ok, corner, theta = find_hull_edge_angle(hull, second)
    if not ok:
        return False, 0.0, 0.0, 0.0
    return True, (corner[0] - 400) / k, (400 - corner[1]) / k, theta


def analyze_large_part(view1, bg1, view2, bg2, k=UP_PX_PER_MM):
    """both views (head at +L/2,+W/2 then -L/2,-W/2): averaged corner offsets and edge angle"""
    ok1, x1, y1, t1 = analyze_hull_view(view1, bg1, False, k)
    if not ok1:
        return PartResult(False)
    ok2, x2, y2, t2 = analyze_hull_view(view2, bg2, True, k)
    if not ok2:
        return PartResult(False)
    return PartResult(True, (x1 + x2) * 0.5, (y1 + y2) * 0.5, 0.0, 0.0, (t1 + t2) * 0.5)


def average_frames(frames):
    """rotation-averaged nozzle image (frmXYLockFaceUp::onTurnClicked).
    fix: the original divides 36 frames by 32 and wraps above 255."""
    acc = np.zeros(frames[0].shape, np.float64)
    for f in frames:
        acc += f
    return np.clip(acc / len(frames), 0, 255).astype(np.uint8)


@dataclass
class SizeCheck:
    ok: bool
    rx: float = 0.0          # measured / expected, along machine X
    ry: float = 0.0
    exp_w: float = 0.0       # expected size along X / Y (package size, swapped for 90 / 270 deg)
    exp_h: float = 0.0
    swapped: bool = False
    reason: str = ""


def size_check(res, pkg_x, pkg_y, stack_angle=0.0, lo=0.8, hi=1.25, orientation="stack", lo_y=None, hi_y=None):
    """Size check after vision (CProcPaist::checkPickedComponent), with adjustable limits.

    NeoDen4.exe: ratio = measured / package size, reject below 0.8 or above 1.25, W/H swapped when the
    stack angle is +-90 (exact compare). Fixes here:
      * the stack angle is rounded to the nearest 90 deg (270, -90, 89.9 ... all swap);
      * orientation="auto" pairs W/H with the package sides in whichever way is closer to 1
        (for when the stack angle is unreliable); "stack" follows the stack angle like NeoDen4;
      * separate limits per axis (lo_y / hi_y, default = lo / hi);
      * results without a measured size (large-part mode) are not judged (ok, reason says so).
    """
    lo_y = lo if lo_y is None else lo_y
    hi_y = hi if hi_y is None else hi_y
    if not res.valid:
        return SizeCheck(False, reason="no part seen")
    if res.w <= 0 or res.h <= 0:
        return SizeCheck(True, reason="size not measured in this vision mode: not checked")
    if not pkg_x or not pkg_y:
        return SizeCheck(True, reason="no package size: not checked")
    quarter = int(round((stack_angle % 360) / 90.0)) % 4
    swap = quarter in (1, 3)
    if orientation == "auto":
        a = abs(np.log(res.w / pkg_x)) + abs(np.log(res.h / pkg_y))
        b = abs(np.log(res.w / pkg_y)) + abs(np.log(res.h / pkg_x))
        swap = b < a
    ew, eh = (pkg_y, pkg_x) if swap else (pkg_x, pkg_y)
    rx, ry = res.w / ew, res.h / eh
    ok = lo <= rx <= hi and lo_y <= ry <= hi_y
    why = "" if ok else (f"size {res.w:.2f} x {res.h:.2f} mm vs {ew:.2f} x {eh:.2f} mm: "
                         f"ratio {rx:.2f} / {ry:.2f} outside {lo:.2f}..{hi:.2f}"
                         + (f" / {lo_y:.2f}..{hi_y:.2f}" if (lo_y, hi_y) != (lo, hi) else ""))
    return SizeCheck(ok, rx, ry, ew, eh, swap, why)


def size_ok(res, pkg_x, pkg_y, stack_angle, lo=0.8, hi=1.25):
    """NeoDen4-compatible boolean form of size_check"""
    return size_check(res, pkg_x, pkg_y, stack_angle, lo, hi).ok


# ---------------------------------------------------------------- closed-loop angle correction
def fold90(a):
    """angle difference folded into (-45, 45]: a rectangle looks the same every 90 deg"""
    a = (a + 45.0) % 90.0 - 45.0
    return 45.0 if a == -45.0 else a


def rotate_xy(x, y, deg):
    t = math.radians(deg)
    return x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)


@dataclass
class VerifyResult:
    ok: bool
    angle: float = 0.0        # part angle on the nozzle at nozzle angle 0 (use as PartResult.angle for placement)
    x: float = 0.0            # part offset at nozzle angle 0 (mm, machine axes)
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    nozzle_angle: float = 0.0  # nozzle angle the part was last checked at
    tries: int = 0
    residuals: list = None     # residual angle after each correction
    reason: str = ""

    def as_part(self):
        return PartResult(self.ok, self.x, self.y, self.w, self.h, self.angle)


def correct_and_verify(measure, rotate, tol_angle=0.5, tol_offset=0.1, max_tries=3, start_angle=0.0):
    """Closed-loop angle correction over the up camera.

    NeoDen4.exe measures once and turns the nozzle by -angle on the way to the board, without checking.
    Here, still over the camera:
        measure(): -> PartResult of a fresh image (nozzle centred, like analyze_nozzle)
        rotate(deg): turn the nozzle to absolute angle `deg` and wait
    1. measure the part angle a0 at nozzle angle phi0 (start_angle);
    2. turn the nozzle to phi = phi0 - a0, so the part should sit square (0 deg);
    3. take a new image: the residual angle must be within tol_angle, and the part offset must
       be where turning the first offset by (phi - phi0) puts it, within tol_offset (a part that
       slipped on the nozzle, or a nozzle that turns the wrong way, fails this);
    4. otherwise correct by the residual again, up to max_tries; then give up (reject).
    A residual of about twice the first angle means the rotation sign is the opposite of the
    vision angle (machine-specific, not known in general): reported as such.
    The result is expressed at nozzle angle 0, so placement() can use it unchanged.
    """
    phi = start_angle
    r = measure()
    if not r.valid:
        return VerifyResult(False, tries=0, residuals=[], reason="no part seen")
    a0, (x0, y0) = r.angle, (r.x, r.y)
    residuals = []
    target = phi - r.angle
    for i in range(1, max_tries + 1):
        rotate(target)
        phi = target
        r = measure()
        if not r.valid:
            return VerifyResult(False, tries=i, residuals=residuals, nozzle_angle=phi, reason="part lost after turning the nozzle")
        res = fold90(r.angle)
        residuals.append(res)
        ex, ey = rotate_xy(x0, y0, phi - start_angle)     # where the first offset should have moved to
        drift = math.hypot(r.x - ex, r.y - ey)
        if i == 1 and abs(a0) > 2 * tol_angle and abs(fold90(res - 2 * a0)) < max(tol_angle, 0.25 * abs(a0)):
            return VerifyResult(False, tries=i, residuals=residuals, nozzle_angle=phi,
                                reason=f"nozzle turned the wrong way: residual {res:+.2f} deg is about 2 x {a0:+.2f} deg "
                                       f"(rotation sign is opposite to the vision angle)")
        if abs(res) <= tol_angle and drift <= tol_offset:
            bx, by = rotate_xy(r.x, r.y, -phi)          # offset referred back to nozzle angle 0
            return VerifyResult(True, r.angle - phi, bx, by, r.w, r.h, phi, i, residuals,
                                f"verified after {i} correction(s): residual {res:+.2f} deg, offset error {drift:.3f} mm")
        if drift > tol_offset and abs(res) <= tol_angle:
            return VerifyResult(False, tries=i, residuals=residuals, nozzle_angle=phi,
                                reason=f"part moved on the nozzle: offset {drift:.3f} mm off the expected place")
        target = phi - res                               # correct the residual again
    return VerifyResult(False, tries=max_tries, residuals=residuals, nozzle_angle=phi,
                        reason=f"angle not within {tol_angle} deg after {max_tries} correction(s): residuals "
                               + ", ".join(f"{v:+.2f}" for v in residuals))


# ---------------------------------------------------------------- head camera
def histogram_mid_level(img):
    """(first level from below + first from above with >= 3040 pixels) // 2"""
    h = np.bincount(img.ravel(), minlength=256)
    big = np.nonzero(h >= 3040)[0]
    lo = int(big[0]) if len(big) else 256
    hi = int(big[-1]) if len(big) else -1
    return (lo + hi) // 2


def contour_centroid(c):
    """perimeter-weighted centre of the edge midpoints (integer midpoints, like the original)"""
    c = c.reshape(-1, 2).astype(np.int64)
    p0 = np.roll(c, 1, axis=0)
    L = np.hypot(*(c - p0).T.astype(np.float64))
    s = L.sum()
    if s == 0:
        return float(c[0, 0]), float(c[0, 1])
    m = np.trunc((c + p0) / 2.0)                    # C integer division (toward zero)
    return float((m[:, 0] * L).sum() / s), float((m[:, 1] * L).sum() / s)


@dataclass
class MarkResult:
    precision: float        # rA / rP, 1.0 = perfect circle; 0 = not found
    x: float = 0.0          # px, image coords
    y: float = 0.0
    r: float = 0.0          # px, area radius
    threshold: int = 0


def find_mark_best(img, dmin_mm, dmax_mm, px_per_mm=HEAD_PX_PER_MM, min_dia_mm=1.0, min_q=0.85, min_fill=0.8):
    """round fiducial (COcvFindMark::findMarkBest): 25 thresholds 32..224, best circularity.
    Stricter than NeoDen4 (which takes area radius / perimeter radius >= 0.8, so a square pad, 0.886,
    passed; real fiducials on a panel frame, a little out of round, pass 0.85): roundness >= min_q, the
    area fills >= min_fill of its enclosing circle (a square 0.64: this is what rejects pads),
    and the diameter is at least min_dia_mm (a fiducial is a circle of 1 mm or more)"""
    src = cv2.pyrUp(cv2.pyrDown(img))[:img.shape[0], :img.shape[1]]
    rmin, rmax = 0.5 * max(dmin_mm, min_dia_mm) * px_per_mm, 0.5 * max(dmax_mm, min_dia_mm) * px_per_mm
    best = MarkResult(0.0)
    for kk in range(25):
        t = 32 + 8 * kk
        cs, _ = cv2.findContours(_threshold(src, t), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            rp = int(cv2.arcLength(c, True)) / 6.28
            if rp < 30 or rp > 250:
                continue
            ra = math.sqrt(cv2.contourArea(c) / 3.14)
            if ra < rmin or ra > rmax:
                continue
            q = ra / rp
            if q >= min_q and q > best.precision:
                (_, _), re = cv2.minEnclosingCircle(c)
                if re <= 0 or cv2.contourArea(c) / (math.pi * re * re) < min_fill:
                    continue
                x, y = contour_centroid(c)
                best = MarkResult(q, x, y, ra, t)
    return best


def mark_offset_mm(res, size=800, px_per_mm=HEAD_PX_PER_MM):
    """head move that centres the mark (CProcLoadPcb::moveToMark), mm in machine axes"""
    return (res.x - size / 2) / px_per_mm, (size / 2 - res.y) / px_per_mm


def auto_exposure_mark(grab, set_exposure, start=150, passes=3):
    """fiducial auto exposure (CImgHead::initMark): exp *= 160 / histogram mid level.
    grab() -> oriented 800x800 head frame; set_exposure(e).
    fix: the original computes a 4th value it never applies; here the last value is applied."""
    e = start
    for _ in range(passes):
        set_exposure(e)
        m = histogram_mid_level(grab())
        if m <= 0:                               # fix: original divides by zero
            break
        e = max(1, int(160.0 / m * e))
    set_exposure(e)
    return e


def auto_exposure_median(grab, set_exposure, target=45, start=20, passes=4, lo=1, hi=2000):
    """exposure that puts the image's median grey level at `target` (pads: the solder mask is
    the median, the tinned pads stay at the top). Inner lamp on the BOARD-A board: exposure 12-20."""
    e = start
    for _ in range(passes):
        set_exposure(e)
        m = float(np.median(grab()))
        if m < 1:
            e = min(hi, e * 4)
            continue
        new = int(min(hi, max(lo, round(e * target / m))))
        if abs(new - e) <= max(1, e // 20):
            e = new
            break
        e = new
    set_exposure(e)
    return e


def auto_exposure_mean(grab, set_exposure, start=50, passes=5):
    """mean-brightness auto exposure (CImgHead::initPosTest): exp *= 127 / mean (32x32 window)"""
    e = start
    for _ in range(passes):
        set_exposure(e)
        m = int(grab().mean())
        if m < 1:
            return 0
        e = max(1, int(127.0 / m * e))
    set_exposure(e)
    return e


def bad_mark(img):
    """CProcPaist::checkBadMark: mean brightness outside 63..191"""
    m = int(img.mean())
    return m < 63 or m > 191


# ---------------------------------------------------------------- placement
def normalize_angle(a):
    while a > 180:
        a -= 360
    while a < -180:
        a += 360
    return a


def placement(x, y, comp_angle, stack_angle, cam_angle, pcb_angle, vision=None, nozzle_offset=(0.0, 0.0)):
    """target head position and nozzle angle (CProcPaist::preparePlacement).
    x, y: placement point of nozzle 1 in machine mm; cam_angle = Config fAngleCamLookUp;
    vision: PartResult of this nozzle or None; nozzle_offset = posCamLookUpMouse{n-1} - posCamLookUpMouse0."""
    a = normalize_angle(comp_angle - stack_angle + cam_angle + pcb_angle)
    if vision is not None:
        if not vision.valid:
            return None
        a = normalize_angle(a - vision.angle)
        t = a * 3.1415926 / 180
        x -= vision.x * math.cos(t) - vision.y * math.sin(t)
        y -= vision.x * math.sin(t) + vision.y * math.cos(t)
    return x + nozzle_offset[0], y + nozzle_offset[1], a
