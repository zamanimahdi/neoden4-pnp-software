"""Camera pixel map: one camera's pixels against machine millimetres over the whole picture, not one px/mm.

A grid of known head moves (the head camera over a round mark, or nozzle 1's empty tip over the up
camera, at its tip plane Z 0) gives where the spot is in the picture for each offset d (mm). A 3rd order
polynomial F: d -> raw pixel fits scale X / Y, skew and the lens' barrel / pincushion (the up camera:
~32.15 px/mm in the middle, ~34.1 at the corners). The map straightens every picture (cv2.remap) to an
ideal camera: exactly k px/mm (the configured fUpPxPerMm / fHeadPxPerMm) everywhere, with the rotation
and orientation the camera has (the closest rotation to F's linear part), so the angle settings and
the code after it stay as they are. One plane only: the up camera's depth magnification
(fUpMagPerMm, the part's height H) stays on top of it.

Opt-in per camera ("on" in <data>/cammap.json, off after a calibration until switched on): with it on,
what was measured in raw pixels (dPosSubMouses, fAngleCamLookUp, the camera offset) is measured again.

    m = fit(d_mm, raw_px, k)           # d_mm, raw_px: (N, 2); returns the map dict (coefficients, rms)
    img = apply(img, cam, data_dir)    # straightened when that camera's map is on (cached remap tables)
"""
import json
import os
import threading

import numpy as np

FULL = 1024                       # the frame the map is made in (the window is a centred crop of it)


def _terms(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y, x ** 3, x * x * y, x * y * y, y ** 3], -1)


def fit(d_mm, raw_px, k, order=3, reject_px=2.0):
    """F: machine offset (mm) -> raw pixel (full 1024 frame) by least squares; the ideal camera L (k px/mm,
    F's rotation / orientation). Points the camera took for the spot but are another dark / bright thing are
    dropped (from the middle outwards, see below). Returns the map dict with the fit's residuals (px and
    mm) and the points left out"""
    d, p = np.asarray(d_mm, float), np.asarray(raw_px, float)
    keep = np.ones(len(d), bool)
    if reject_px and len(d) >= 20:
        # from the middle outwards: a line on the inner points, then wider and higher order, each time only the
        # points that agree (at the edge the spot search may take another dark / bright thing, 20 .. 30 px off)
        rad = np.hypot(d[:, 0], d[:, 1])
        R = float(rad.max()) or 1.0
        keep = rad <= 0.55 * R
        for o, tol, lim in ((1, 1.5 * reject_px, 0.8 * R), (min(2, order), 1.25 * reject_px, 1.1 * R),
                            (order, reject_px, 2 * R), (order, 0.75 * reject_px, 2 * R)):
            if keep.sum() < {1: 3, 2: 6, 3: 10}[o] + 3:
                keep = np.ones(len(d), bool)
                break
            T = _terms(d[keep, 0], d[keep, 1])[:, :{1: 3, 2: 6, 3: 10}[o]]
            C, *_ = np.linalg.lstsq(T, p[keep], rcond=None)
            e = np.sqrt(((p - _terms(d[:, 0], d[:, 1])[:, :{1: 3, 2: 6, 3: 10}[o]] @ C) ** 2).sum(1))
            keep = (e <= tol) & (rad <= lim)
    dropped = [[round(float(x), 3), round(float(y), 3)] for x, y in d[~keep]]
    d, p = d[keep], p[keep]
    T = _terms(d[:, 0], d[:, 1])
    nt = {1: 3, 2: 6, 3: 10}[order]
    T = T[:, :nt]
    C, *_ = np.linalg.lstsq(T, p, rcond=None)              # (nt, 2)
    res = p - T @ C
    # linear only, for comparison: what one matrix (no lens term) leaves
    Cl, *_ = np.linalg.lstsq(T[:, :3], p, rcond=None)
    res_l = p - T[:, :3] @ Cl
    A = C[1:3].T                                           # d -> px, 2 x 2 (columns: per mm X, per mm Y)
    U, S, Vt = np.linalg.svd(A)
    Q = U @ Vt                                             # the nearest rotation (with the camera's flip)
    sx, sy = np.linalg.norm(A[:, 0]), np.linalg.norm(A[:, 1])
    skew = float(np.degrees(np.arccos(np.clip(abs(A[:, 0] @ A[:, 1]) / (sx * sy), -1, 1))) - 90.0)
    ang = float(np.degrees(np.arctan2(Q[1, 0], Q[0, 0])))
    coef = np.zeros((10, 2))
    coef[:nt] = C
    m = {"coef": coef.tolist(), "c": C[0].tolist(), "Q": Q.tolist(), "k": float(k), "order": order,
         "sx": round(float(sx), 3), "sy": round(float(sy), 3), "skew_deg": round(skew, 3), "angle_deg": round(ang, 3),
         "rms_px": round(float(np.sqrt((res ** 2).sum(1).mean())), 3), "max_px": round(float(np.sqrt((res ** 2).sum(1)).max()), 3),
         "rms_linear_px": round(float(np.sqrt((res_l ** 2).sum(1).mean())), 3),
         "max_linear_px": round(float(np.sqrt((res_l ** 2).sum(1)).max()), 3), "n": int(len(d)),
         "dropped": dropped,
         "range_mm": [round(float(d[:, 0].min()), 2), round(float(d[:, 0].max()), 2), round(float(d[:, 1].min()), 2), round(float(d[:, 1].max()), 2)]}
    m["scale_centre"], m["scale_corner"] = _local_scale(m, 0.0, 0.0), _local_scale(m, *(0.9 * d[:, 0].max(), 0.9 * d[:, 1].max()))
    return m


def raw_of(m, dx, dy):
    """the raw pixel (full frame) where machine offset (dx, dy) mm is seen"""
    return _terms(dx, dy) @ np.asarray(m["coef"])


def _local_scale(m, dx, dy, e=0.05):
    a, b, c = raw_of(m, dx, dy), raw_of(m, dx + e, dy), raw_of(m, dx, dy + e)
    return [round(float(np.linalg.norm(b - a) / e), 3), round(float(np.linalg.norm(c - a) / e), 3)]


def tables(m, w, h):
    """cv2.remap tables for a w x h window (centred in the full frame): for each ideal pixel the raw pixel
    it shows. Ideal pixel p = c + k Q d, so d = Q^T (p - c) / k, raw = F(d)"""
    import cv2
    ox, oy = (FULL - w) / 2.0, (FULL - h) / 2.0
    c, Q, k = np.asarray(m["c"]), np.asarray(m["Q"]), float(m["k"])
    u, v = np.meshgrid(np.arange(w, dtype=np.float64) + ox, np.arange(h, dtype=np.float64) + oy)
    P = np.stack([u - c[0], v - c[1]], -1) / k
    D = P @ Q                                              # (Q^T p) for row vectors
    R = raw_of(m, D[..., 0], D[..., 1])
    mx = (R[..., 0] - ox).astype(np.float32)
    my = (R[..., 1] - oy).astype(np.float32)
    return cv2.convertMaps(mx, my, cv2.CV_16SC2)


# ---- stored maps and the cached tables ----
_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "data": {}, "tables": {}}


def path_of(data_dir):
    return os.path.join(data_dir, "cammap.json")


def load(data_dir):
    p = path_of(data_dir)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    with _lock:
        if _cache["path"] != p or _cache["mtime"] != mt:
            try:
                _cache["data"] = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                _cache["data"] = {}
            _cache.update(path=p, mtime=mt, tables={})
        return _cache["data"]


def save(data_dir, cam, m=None, on=None):
    """store camera cam's map (m) and / or its on / off; a new map starts off"""
    p = path_of(data_dir)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    e = d.get(str(cam), {})
    if m is not None:
        e = dict(m, on=False)
    if on is not None:
        if "coef" not in e:
            raise ValueError("no map for this camera yet: calibrate it first")
        e["on"] = bool(on)
    d[str(cam)] = e
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, p)
    return e


def is_on(cam, data_dir):
    e = load(data_dir).get(str(int(cam) & 0x7F)) or {}
    return bool(e.get("on")) and "coef" in e


def apply(img, cam, data_dir):
    """the picture straightened by camera cam's map when it is on (else as it is)"""
    if img is None or not data_dir:
        return img
    cam = int(cam) & 0x7F
    m = load(data_dir).get(str(cam)) or {}
    if not m.get("on") or "coef" not in m:
        return img
    import cv2
    h, w = img.shape[:2]
    key = (cam, w, h)
    with _lock:
        t = _cache["tables"].get(key)
    if t is None:
        t = tables(m, w, h)
        with _lock:
            _cache["tables"][key] = t
    return cv2.remap(img, t[0], t[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
