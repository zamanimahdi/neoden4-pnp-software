"""Up-camera calibration on the machine: scale, angle, and each nozzle's rotation centre.

    python tools/upcam_cal.py --host neoden.local [--nozzles 1,2,3,4] [--step 5]

1. Nozzle 1 over the up camera (Config posCamLookUpMouse0); the head moves by known steps
   (a cross and four diagonals, slow one-sided approach) and the nozzle tip is followed in the
   image (template match) -> the 2 x 2 matrix image px per machine mm: px/mm X / Y, the
   camera's angle against the machine axes, skew (neocalib.motion.fit_matrix / describe).
2. Each nozzle over the camera, turned 0 / 90 / 180 / 270 deg: its tip hole runs on a circle
   whose centre is the nozzle's real axis; the head position that puts the axis on the image
   centre is the corrected posCamLookUpMouse<n-1>; the circle's radius is the tip runout.

Nothing is written to the machine; the values to write are printed (and saved to
./upcam_cal_<time>/result.json with the images). Uses client id "live-vision".
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from neocalib.motion import default_pattern, describe, fit_matrix  # noqa: E402
from neocam.config import NeoConfig  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="neoden.local")
ap.add_argument("--nozzles", default="1,2,3,4")
ap.add_argument("--step", type=float, default=5.0, help="largest head move, mm")
ap.add_argument("--exposure", type=int, default=600, help="an empty nozzle needs more than a part (job: 150)")
ap.add_argument("--scale-from", default="", help="result.json of an earlier run: skip step 1, take its matrix")
a = ap.parse_args()
B = f"http://{a.host}"
C = "live-vision"
OUT = time.strftime("upcam_cal_%H%M%S")
os.makedirs(OUT, exist_ok=True)


def req(m, p, q=None, raw=False, timeout=60):
    r = urllib.request.Request(B + p + ("?" + urllib.parse.urlencode(q) if q else ""), data=b"" if m == "POST" else None, method=m)
    try:
        d = urllib.request.urlopen(r, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{p}: {e.code} {e.read()[:200]!r}")
    return d if raw else json.loads(d)


def mc(name, **q):
    return req("POST", "/api/mc/" + name, {"client": C, **q}, timeout=120)


def idle():
    for _ in range(120):
        if req("GET", "/api/mc/status").get("xy_state_name") == "idle":
            return
        time.sleep(0.5)


def move(x, y):
    mc("move", x=round(x, 4), y=round(y, 4), slow=1)
    idle()


def grab():
    d = req("GET", "/api/cam/grab", {"cam": 0, "settle": 1}, raw=True)
    return cv2.imdecode(np.frombuffer(d, np.uint8), cv2.IMREAD_GRAYSCALE)


def locate(img, tpl):
    """sub-pixel position of the template's centre in img (normalised cross-correlation)"""
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    _, q, _, (x, y) = cv2.minMaxLoc(r)
    dx = dy = 0.0
    if 0 < x < r.shape[1] - 1:
        a_, b_, c_ = r[y, x - 1], r[y, x], r[y, x + 1]
        dx = 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_) if (a_ - 2 * b_ + c_) else 0.0
    if 0 < y < r.shape[0] - 1:
        a_, b_, c_ = r[y - 1, x], r[y, x], r[y + 1, x]
        dy = 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_) if (a_ - 2 * b_ + c_) else 0.0
    return x + dx + tpl.shape[1] / 2, y + dy + tpl.shape[0] / 2, float(q)


def tip(img):
    """the nozzle tip near the image centre (it is centred there within a few px when the
    nozzle stands at its posCamLookUpMouse): the tip's hole is the darkest small spot in the
    middle; the disc around it is taken as the template (half size HALF px)"""
    h, w = img.shape
    c = 60
    cut = cv2.GaussianBlur(img[h // 2 - c:h // 2 + c, w // 2 - c:w // 2 + c], (0, 0), 2)
    y, x = np.unravel_index(np.argmin(cut), cut.shape)
    return float(w // 2 - c + x), float(h // 2 - c + y), 150.0


def centre(img, roi=170, inner=60):
    """nozzle tip centre (px): the dark spot in the middle (the tip's hole, or a big nozzle's dark
    inside), the centroid of its filled area - the same whatever the nozzle's turn. None if the
    spot runs out of the search area."""
    h, w = img.shape
    X0, Y0 = w // 2 - roi, h // 2 - roi
    R = cv2.GaussianBlur(img[Y0:Y0 + 2 * roi, X0:X0 + 2 * roi], (0, 0), 1.5).astype(np.float32)
    c = R[roi - inner:roi + inner, roi - inner:roi + inner]
    y, x = np.unravel_index(np.argmin(c), c.shape)
    sx, sy = x + roi - inner, y + roi - inner
    lo, hi = float(R[sy, sx]), float(np.percentile(R, 90))
    _, lab, stats, _ = cv2.connectedComponentsWithStats((R < lo + 0.3 * (hi - lo)).astype(np.uint8), connectivity=8)
    L = lab[sy, sx]
    x0, y0, ww, hh, _ = stats[L]
    if x0 == 0 or y0 == 0 or x0 + ww >= 2 * roi or y0 + hh >= 2 * roi:
        return None
    cs, _ = cv2.findContours((lab == L).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros(R.shape, np.uint8)
    cv2.drawContours(filled, cs, -1, 1, -1)
    M = cv2.moments(filled, True)
    return X0 + M["m10"] / M["m00"], Y0 + M["m01"] / M["m00"], math.sqrt(M["m00"] / math.pi)


def fit_circle(P):
    P = np.asarray(P, float)
    A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
    c, *_ = np.linalg.lstsq(A, P[:, 0] ** 2 + P[:, 1] ** 2, rcond=None)
    return float(c[0]), float(c[1]), math.sqrt(max(0.0, c[2] + c[0] ** 2 + c[1] ** 2))


assert req("POST", "/api/mc/lock", {"client": C, "hold": 1}).get("ok"), "the machine is controlled by another client"
stop = False


def hb():
    while not stop:
        try:
            req("POST", "/api/mc/lock", {"client": C, "hold": 1}, timeout=20)
        except Exception:
            pass
        time.sleep(4)


threading.Thread(target=hb, daemon=True).start()
cfg = NeoConfig.load_text(req("GET", "/api/file", {"path": "/opt/neoden/data/Config.ini"}, raw=True).decode("latin-1")) \
    if hasattr(NeoConfig, "load_text") else None
if cfg is None:
    p = os.path.join(OUT, "Config.ini")
    open(p, "wb").write(req("GET", "/api/file", {"path": "/opt/neoden/data/Config.ini"}, raw=True))
    cfg = NeoConfig.load(p)
result = {"before": {"fUpPxPerMm": cfg.pxmm(0), "fAngleCamLookUp": cfg.cam_angle,
                     "posCamLookUpMouse": [list(p) for p in cfg.nozzle_positions]}}
try:
    st = req("GET", "/api/mc/status")
    if not st.get("pos") or st.get("xy_state_name") != "idle":
        mc("home")
        idle()
    mc("raise")
    mc("led", v=0)                                    # the head camera's lamp dazzles the up camera
    mc("light", on=1)
    req("POST", "/api/cam/set", {"cam": 0, "exposure": a.exposure, "w": 1024, "h": 1024})

    # ---- 1. scale and angle from known head moves --------------------------------------
    if a.scale_from:
        old = json.load(open(a.scale_from))
        A = np.array(old["matrix_px_per_mm"])
        result["matrix_px_per_mm"], result["scale"] = old["matrix_px_per_mm"], old["scale"]
        k_x, k_y = old["scale"]["k_x"], old["scale"]["k_y"]
        print(f"scale from {a.scale_from}: k_x {k_x:.3f}, k_y {k_y:.3f} px/mm, angle {old['scale']['angle_deg']:+.3f} deg")
    else:
        x0, y0 = cfg.nozzle_positions[0]
        move(x0 + 1.0, y0 + 1.0)                          # one-sided approach to every point
        move(x0, y0)
        ref = grab()
        cv2.imwrite(os.path.join(OUT, "n1_ref.png"), ref)
        tx, ty, tr = tip(ref)
        half = int(tr * 1.1)
        tpl = ref[int(ty) - half:int(ty) + half, int(tx) - half:int(tx) + half].copy()
        ux, uy, _ = locate(ref, tpl)
        pts = []
        for dX, dY in default_pattern(a.step):
            move(x0 + dX + 1.0, y0 + dY + 1.0)
            move(x0 + dX, y0 + dY)
            u, v, q = locate(grab(), tpl)
            if q < 0.6:
                raise RuntimeError(f"tip not followed at {dX:+.2f}, {dY:+.2f} mm (match {q:.2f})")
            pts.append((dX, dY, u - ux, v - uy, q))
            print(f"  move {dX:+6.2f} {dY:+6.2f} mm -> image {u - ux:+8.2f} {v - uy:+8.2f} px  (match {q:.2f})", flush=True)
        A, b, res = fit_matrix(pts)
        k_x, k_y, angle, skew, ok = describe(A, "up")
        result["matrix_px_per_mm"] = np.asarray(A).tolist()
        result["scale"] = {"k_x": k_x, "k_y": k_y, "angle_deg": angle, "skew_deg": skew, "orientation_ok": bool(ok),
                           "fit_rms_px": float(np.sqrt(np.mean(np.asarray(res) ** 2)))}
        print(f"up camera: k_x {k_x:.3f}, k_y {k_y:.3f} px/mm, angle {angle:+.3f} deg, skew {skew:+.3f} deg, "
              f"fit rms {result['scale']['fit_rms_px']:.2f} px, orientation {'ok' if ok else 'WRONG'}")
    Ainv = np.linalg.inv(np.asarray(A))

    # ---- 2. each nozzle's rotation centre ----------------------------------------------
    result["nozzles"] = {}
    for n in [int(s) for s in a.nozzles.split(",") if s.strip()]:
        x, y = cfg.nozzle_positions[n - 1]
        mc("raise")
        move(x + 1.0, y + 1.0)
        move(x, y)
        seen = []
        for deg in (0, 90, 180, -90, 0):                # the controller takes -180..180
            mc("rotate", n=n, deg=deg)
            time.sleep(0.4)
            img = grab()
            cv2.imwrite(os.path.join(OUT, f"n{n}_{deg}.png"), img)
            c3 = centre(img)
            if c3 is None:
                raise RuntimeError(f"nozzle {n} at {deg} deg: no tip centre found")
            u, v, q = c3
            seen.append((u, v, q))
            print(f"  nozzle {n} at {deg:3d} deg: tip centre {u:.2f}, {v:.2f} px (radius {q:.1f} px)", flush=True)
        mc("rotate", n=n, deg=0)
        cx, cy, r = fit_circle([s[:2] for s in seen[:4]])
        h, w = img.shape
        d = Ainv @ np.array([cx - w / 2, cy - h / 2])        # the axis sits this far (mm) from where it should
        new = (x - d[0], y - d[1])
        back = math.hypot(seen[4][0] - seen[0][0], seen[4][1] - seen[0][1])
        result["nozzles"][n] = {"axis_px": [cx, cy], "runout_mm": r / ((k_x + k_y) / 2), "back_to_0_px": back,
                                "pos_now": [x, y], "pos_new": [round(new[0], 4), round(new[1], 4)],
                                "change_mm": [round(-d[0], 4), round(-d[1], 4)]}
        print(f"nozzle {n}: axis {cx:.1f}, {cy:.1f} px; runout {result['nozzles'][n]['runout_mm'] * 1000:.0f} um; "
              f"posCamLookUpMouse{n - 1} {x:.4f}, {y:.4f} -> {new[0]:.4f}, {new[1]:.4f} "
              f"(change {-d[0]:+.4f}, {-d[1]:+.4f} mm)", flush=True)
finally:
    stop = True
    try:
        mc("light", on=0)
    except Exception:
        pass
json.dump(result, open(os.path.join(OUT, "result.json"), "w"), indent=1)
print("saved", os.path.abspath(OUT))
