"""Motion-based camera calibration: px/mm per axis, camera angle and skew, without a
reference part.

The machine moves XY by known amounts (one motor step = 0.009525 mm) and the image
shift is measured; a least-squares fit gives the full 2 x 2 matrix

    [dx_px, dy_px]^T = A @ [dX_mm, dY_mm]^T          (image shift per machine move)

From A: px/mm along machine X and Y (k_x, k_y), the camera angle, the skew between the
axes, the orientation (mirror), the fit residuals and the drift back at the start point.
Use img_to_mm() (inverse of A) to turn a pixel offset into a machine offset; it includes
the angle and skew, which NeoDen4.exe's constants (34.5 / 112 px/mm, fAngleCamLookUp) do not.

  head camera: looks down at the board; aim it at anything with texture (a PCB, fiducials).
               The camera moves, the scene shifts the opposite way.
  up camera:   a nozzle (with or without a part) over the camera; the nozzle moves, the
               image follows it.

Images are measured in the vision frame (neocam.vision.orient_up / orient_head), so the
result applies directly to the vision results. Moves use the slow, one-sided approach
(move_xy(slow=True)), so backlash does not enter the fit.

    from neocalib import calibrate, make_grab
    res = calibrate(mb, make_grab(cam, "head", mb), "head")
    print(res.summary()); res.save("cal_head.json")
"""
import json
import math
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

# NeoDen4.exe constants - only used for the default step and the report
NOMINAL_PX_PER_MM = {"up": 34.5, "head": 112.0}
# expected sign of A in the vision frame: image +x = machine +X, image +y (down) = machine -Y
# for things that move WITH the machine (up camera); the head camera sees the opposite.
EXPECTED_A = {"up": np.array([[1.0, 0.0], [0.0, -1.0]]),
              "head": np.array([[-1.0, 0.0], [0.0, 1.0]])}


@dataclass
class CalResult:
    camera: str
    A: list                         # 2 x 2, px per mm (columns: machine X, machine Y)
    b: list                         # fitted image offset at the start point (px, ~0)
    k_x: float                      # px / mm along machine X
    k_y: float                      # px / mm along machine Y
    angle_deg: float                # camera rotation vs the expected orientation
    skew_deg: float                 # deviation of the two axes from 90 deg in the image
    orientation_ok: bool            # mirror / axis signs as expected for this camera
    rms_px: float                   # fit residual
    max_px: float
    drift_px: float                 # image shift back at the start point (repeatability)
    step_mm: float
    method: str
    n: int
    date: str = ""
    points: list = field(default_factory=list)      # (dX_mm, dY_mm, dx_px, dy_px, quality)

    @property
    def matrix(self):
        return np.array(self.A, float)

    def img_to_mm(self, dx_px, dy_px):
        """Pixel offset in the image -> machine offset (mm), through the inverse of A."""
        v = np.linalg.solve(self.matrix, np.array([dx_px, dy_px], float))
        return float(v[0]), float(v[1])

    def mm_to_img(self, dX_mm, dY_mm):
        v = self.matrix @ np.array([dX_mm, dY_mm], float)
        return float(v[0]), float(v[1])

    def summary(self):
        nom = NOMINAL_PX_PER_MM.get(self.camera)
        rel = (f" (NeoDen constant {nom:g}: X {100 * (self.k_x / nom - 1):+.2f} %, "
               f"Y {100 * (self.k_y / nom - 1):+.2f} %)") if nom else ""
        return (f"{self.camera} camera: k_x {self.k_x:.3f}, k_y {self.k_y:.3f} px/mm{rel}; "
                f"angle {self.angle_deg:+.3f} deg, skew {self.skew_deg:+.3f} deg; "
                f"fit rms {self.rms_px:.2f} px (max {self.max_px:.2f}), drift {self.drift_px:.2f} px; "
                f"orientation {'ok' if self.orientation_ok else 'UNEXPECTED (image mirrored or axes swapped?)'}; "
                f"{self.n} points, step {self.step_mm:g} mm, {self.method}")

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(**json.load(f))


class CalibrationError(RuntimeError):
    pass


# ---- image shift ---------------------------------------------------------------

def _roi(img, frac):
    h, w = img.shape[:2]
    mh, mw = int(h * (1 - frac) / 2), int(w * (1 - frac) / 2)
    return img[mh:h - mh, mw:w - mw]


def shift_phase(ref, img, roi=0.8):
    """Sub-pixel shift of img relative to ref (phase correlation, Hann window).
    Returns (dx, dy, response); response near 1 = sharp peak, near 0 = no match."""
    a = _roi(ref, roi).astype(np.float32)
    b = _roi(img, roi).astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
    return dx, dy, resp


def blob_threshold(img):
    """(Otsu threshold, invert) for blob_centroid, taken once from the reference frame."""
    g = cv2.GaussianBlur(img, (5, 5), 0)
    t, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return t, np.count_nonzero(bw) > bw.size / 2


def blob_centroid(img, thr=None):
    """Centroid of the foreground at threshold thr = (t, invert) (Otsu of img if None), or None.
    For a plain object: a nozzle hole, a plain part."""
    t, inv = thr or blob_threshold(img)
    g = cv2.GaussianBlur(img, (5, 5), 0)
    _, bw = cv2.threshold(g, t, 255, cv2.THRESH_BINARY)
    if inv:
        bw = 255 - bw
    # whole-mask moments (stable when the object breaks into several pieces), ignoring
    # pieces that touch the image border (partly outside the view)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw)
    h, w = bw.shape
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, cw, ch = stats[i, :4]
        keep[i] = x > 0 and y > 0 and x + cw < w and y + ch < h
    if not keep.any():
        return None
    m = cv2.moments(keep[lab].astype(np.uint8), binaryImage=True)
    if m["m00"] == 0:
        return None
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def shift_blob(ref, img):
    thr = blob_threshold(ref)                       # same threshold for both frames
    c0, c1 = blob_centroid(ref, thr), blob_centroid(img, thr)
    if c0 is None or c1 is None:
        return 0.0, 0.0, 0.0
    return c1[0] - c0[0], c1[1] - c0[1], 1.0


# ---- fit -------------------------------------------------------------------------

def fit_matrix(points):
    """points: [(dX_mm, dY_mm, dx_px, dy_px, ...)] -> A (2x2), b (2), residuals (px)."""
    M = np.array([[p[0], p[1], 1.0] for p in points])
    P = np.array([[p[2], p[3]] for p in points])
    sol, *_ = np.linalg.lstsq(M, P, rcond=None)         # 3 x 2
    A = sol[:2].T                                       # 2 x 2
    b = sol[2]
    res = np.linalg.norm(M @ sol - P, axis=1)
    return A, b, res


def describe(A, camera):
    """k_x, k_y, angle vs the expected orientation, skew, orientation_ok."""
    cx, cy = A[:, 0], A[:, 1]
    k_x, k_y = float(np.linalg.norm(cx)), float(np.linalg.norm(cy))
    E = EXPECTED_A[camera]
    ex, ey = E[:, 0], E[:, 1]
    ang_x = math.degrees(math.atan2(ex[0] * cx[1] - ex[1] * cx[0], ex @ cx))
    ang_y = math.degrees(math.atan2(ey[0] * cy[1] - ey[1] * cy[0], ey @ cy))
    angle = (ang_x + ang_y) / 2
    skew = ang_y - ang_x
    ok = (np.sign(np.linalg.det(A)) == np.sign(np.linalg.det(E))) and abs(angle) < 45
    return k_x, k_y, angle, skew, bool(ok)


def default_pattern(step):
    """Start, a cross at half and full step, four diagonals (all relative to the start)."""
    s, h, d = step, step / 2, step * 0.7071
    return [(0, 0), (h, 0), (s, 0), (-h, 0), (-s, 0), (0, h), (0, s), (0, -h), (0, -s),
            (d, d), (-d, d), (d, -d), (-d, -d)]


# ---- procedure ----------------------------------------------------------------------

def calibrate(mb, grab, camera, step_mm=None, pattern=None, settle_s=0.25, method="phase",
              min_response=0.05, repeat=1, progress=None):
    """Run the motion calibration at the current XY position.

    mb: neomach.Mainboard (homed; the head stays at its current position as the centre)
    grab(): returns one gray frame in the vision frame (see make_grab)
    camera: "up" or "head"
    step_mm: default 20 % of the field of view at the nominal px/mm
    progress(i, n, point): optional callback; mb.cancel() stops the moves (Cancelled)
    """
    if camera not in EXPECTED_A:
        raise ValueError("camera must be 'up' or 'head'")
    start = mb.position()
    if start is None:
        raise CalibrationError("position unknown: home the machine first")
    measure = shift_phase if method == "phase" else shift_blob
    ref = None
    points = []
    pat = pattern
    for rep in range(repeat):
        if ref is None:
            mb.move_xy(start[0], start[1], slow=True)
            time.sleep(settle_s)
            ref = grab()
            h, w = ref.shape[:2]
            if step_mm is None:
                step_mm = round(0.2 * min(w, h) / NOMINAL_PX_PER_MM[camera], 2)
            pat = pattern or default_pattern(step_mm)
        for i, (dX, dY) in enumerate(pat):
            mb.move_xy(start[0] + dX, start[1] + dY, slow=True)
            time.sleep(settle_s)
            img = grab()
            dx, dy, q = measure(ref, img)
            if q < min_response:
                raise CalibrationError(
                    f"no image match at offset ({dX:+.2f}, {dY:+.2f}) mm (response {q:.3f}): "
                    "too little texture in view, too large a step, or out of focus")
            points.append((float(dX), float(dY), float(dx), float(dy), float(q)))
            if progress:
                progress(i + 1 + rep * len(pat), len(pat) * repeat, points[-1])
    mb.move_xy(start[0], start[1], slow=True)
    time.sleep(settle_s)
    dx, dy, _ = measure(ref, grab())
    drift = math.hypot(dx, dy)
    A, b, res = fit_matrix(points)
    k_x, k_y, angle, skew, ok = describe(A, camera)
    return CalResult(camera, A.tolist(), b.tolist(), k_x, k_y, angle, skew, ok,
                     float(np.sqrt(np.mean(res ** 2))), float(res.max()), drift,
                     float(step_mm), method, len(points), time.strftime("%Y-%m-%d %H:%M:%S"), points)


def make_grab(cam_client, camera, mb=None, led=1):
    """grab() for calibrate(): one frame from neocam's CamClient in the vision frame,
    with the camera's light on (up camera: mb.up_light; head camera: mb.head_led(led))."""
    from neocam.client import UP, HEAD
    from neocam.vision import orient_head, orient_up
    cid, orient = (UP, orient_up) if camera == "up" else (HEAD, orient_head)

    def grab():
        if mb is not None:
            if camera == "up":
                mb.up_light(True)
            else:
                mb.head_led(led)
        try:
            return orient(cam_client.grab(cid))
        finally:
            if mb is not None:
                if camera == "up":
                    mb.up_light(False)
                else:
                    mb.head_led(0)
    return grab
