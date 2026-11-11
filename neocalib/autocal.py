"""Auto-calibration of the up camera and the nozzles, run on the machine from the Calibration page
(neoserver /api/calib/*). The method is PP's tools/upcam_cal.py, here as a background task:

1. Up-camera scale and angle: nozzle 1 over the up camera (Config posCamLookUpMouse0); the head
   moves by known steps (a cross and four diagonals, one-sided slow approach) and the nozzle tip is
   followed in the image (template match). Fit: the 2 x 2 matrix image px per machine mm ->
   px/mm X / Y, the camera's angle against the machine axes, skew (neocalib.motion).
2. Each nozzle's rotation centre: the nozzle over the camera, turned 0 / 90 / 180 / -90 deg; its tip
   runs on a circle whose centre is the real axis. The head position that puts the axis on the
   image centre is the new posCamLookUpMouse<n-1>; the radius is the tip runout. A nozzle whose tip
   is not found (empty mount) is skipped and keeps its old value.
2b. Z tilt (o["ztilt"], default on): each nozzle lowered over the camera to 0 / 2.5 / 5 / 7.5 mm
   (o["zmax"]) at 0 / 90 / 180 / -90 deg; the hole's centre at each depth, its pixel offset scaled by
   the hole's apparent size (the tip comes closer to the lens), gives how far the tip moves sideways
   per mm down: a fixed part (the Z axis) and a part that turns with the nozzle (a bent nozzle),
   neocalib.ztilt. The runner corrects the pick and place points by it.
3. The four nozzle points of the 4-nozzle picture (dPosSubMouses0..3), measured: the head at
   posCamLookUpMouseMouseCenter, all nozzles turned 0 / 90 / 180 / -90 deg, each tip followed
   around the point predicted by image centre + A (centre - pos_i). Measured, not computed, because
   the lens is not linear (about 32 px/mm in the middle, 34 px/mm in the corners on this machine).
   A nozzle that is not seen keeps the predicted point (marked "computed").

The up-camera angle written to Default/fAngleCamLookUp is minus the image angle of describe(): on this
machine NeoDen4's own calibration left +0.40 deg where describe() measures -0.44 deg.

Nothing is written here: the result lists old and new values; the page writes the ones the user
accepts into Config.ini (with its usual backup).

    ac = AutoCal(machine, cameras, config_path, log)
    ac.start(client, {"scale": True, "nozzles": [1, 2, 3, 4], "exposure": 600, "step": 5})
    ac.status()          # {state, step, progress, log, result}
    ac.cancel()
"""
import math
import threading
import time

import cv2
import numpy as np

from .motion import EXPECTED_A, default_pattern, describe, fit_matrix

IDLE, RUNNING, DONE, FAILED, CANCELLED = "idle", "running", "done", "failed", "cancelled"


class Cancelled(Exception):
    pass


def locate(img, tpl):
    """sub-pixel position of the template's centre in img (normalised cross-correlation), quality"""
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    _, q, _, (x, y) = cv2.minMaxLoc(r)
    dx = dy = 0.0
    if 0 < x < r.shape[1] - 1:
        a, b, c = r[y, x - 1], r[y, x], r[y, x + 1]
        dx = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    if 0 < y < r.shape[0] - 1:
        a, b, c = r[y - 1, x], r[y, x], r[y + 1, x]
        dy = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    return x + dx + tpl.shape[1] / 2, y + dy + tpl.shape[0] / 2, float(q)


def locate_near(img, tpl, pred, radius):
    """locate() inside a window around the predicted centre: the other nozzles look the same and
    can be in the frame too (they are 15 mm apart)"""
    th, tw = tpl.shape
    h, w = img.shape
    x0 = int(max(0, pred[0] - radius - tw / 2)); x1 = int(min(w, pred[0] + radius + tw / 2))
    y0 = int(max(0, pred[1] - radius - th / 2)); y1 = int(min(h, pred[1] + radius + th / 2))
    if x1 - x0 <= tw or y1 - y0 <= th:
        return pred[0], pred[1], 0.0
    u, v, q = locate(img[y0:y1, x0:x1], tpl)
    return u + x0, v + y0, q


def tip(img, c=60):
    """the nozzle tip near the image centre: the darkest small spot in the middle (its hole)"""
    h, w = img.shape
    cut = cv2.GaussianBlur(img[h // 2 - c:h // 2 + c, w // 2 - c:w // 2 + c], (0, 0), 2)
    y, x = np.unravel_index(np.argmin(cut), cut.shape)
    return float(w // 2 - c + x), float(h // 2 - c + y), 150.0


def centre(img, roi=170, inner=60, at=None):
    """nozzle tip centre (px): the dark spot in the middle (or around `at`), the centroid of its filled
    area (the same whatever the nozzle's turn). None if the spot runs out of the search area or is
    not dark."""
    h, w = img.shape
    ax, ay = (w // 2, h // 2) if at is None else (int(round(at[0])), int(round(at[1])))
    ax, ay = min(max(ax, roi), w - roi), min(max(ay, roi), h - roi)
    X0, Y0 = ax - roi, ay - roi
    R = cv2.GaussianBlur(img[Y0:Y0 + 2 * roi, X0:X0 + 2 * roi], (0, 0), 1.5).astype(np.float32)
    c = R[roi - inner:roi + inner, roi - inner:roi + inner]
    y, x = np.unravel_index(np.argmin(c), c.shape)
    sx, sy = x + roi - inner, y + roi - inner
    lo, hi = float(R[sy, sx]), float(np.percentile(R, 90))
    if hi - lo < 15:                                           # nothing dark in the middle: no nozzle
        return None
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


def four_points(A, centre_pos, nozzle_pos, size=1024):
    """pixel point of each nozzle axis in the 4-nozzle picture (head at centre_pos)"""
    out = []
    for p in nozzle_pos:
        d = np.asarray(A) @ (np.asarray(centre_pos, float) - np.asarray(p, float))
        out.append([round(size / 2 + float(d[0]), 1), round(size / 2 + float(d[1]), 1)])
    return out


class AutoCal:
    """machine: neoserver.hw.Machine (board(), lock, busy_call(), _owner()); cams: neoserver.hw.Cameras
    (dev(), lock); cfg_loader(): neocam.config.NeoConfig of the machine's Config.ini"""

    def __init__(self, machine, cams, cfg_loader, log=print):
        self.m, self.cams, self.cfg_loader, self.log = machine, cams, cfg_loader, log
        self.state, self.step, self.progress, self.lines, self.result = IDLE, "", 0.0, [], None
        self._cancel = threading.Event()
        self.thread = None

    # ---- API ----
    def status(self, q=None):
        return {"state": self.state, "step": self.step, "progress": round(self.progress, 3),
                "log": self.lines[-200:], "result": self.result}

    def cancel(self, q=None):
        self._cancel.set()
        if self.m.mb:
            self.m.mb.cancel()
        return {"state": self.state}

    def start(self, client, opts):
        if self.state == RUNNING:
            raise RuntimeError("auto-calibration is already running")
        if self.m._owner() != client:
            raise PermissionError("take control first")
        if getattr(self.m, "job_running", False):
            raise PermissionError("a job is running")
        self._cancel.clear()
        self.state, self.step, self.progress, self.lines, self.result = RUNNING, "starting", 0.0, [], None
        self.thread = threading.Thread(target=self._run, args=(dict(opts),), daemon=True)
        self.thread.start()
        return {"state": self.state}

    # ---- helpers ----
    def _say(self, t):
        self.lines.append(t)
        self.log(f"autocal: {t}")

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled("cancelled")

    def grab(self):
        """up-camera frame, turned like the web UI shows it, after the image stops moving"""
        from .settle import grab_settled
        with self.cams.lock:
            d = self.cams.dev()
            raw = grab_settled(lambda: d.grab(0)).frame
        return np.ascontiguousarray(np.flip(raw.T, (0, 1)) if raw.shape[0] == raw.shape[1] else np.flip(raw, (0, 1)))

    def move(self, mb, x, y):
        self._check()
        with self.m.lock:
            mb.move_xy(x + 1.0, y + 1.0, slow=False)              # one-sided approach to every point
            mb.move_xy(x, y, slow=True)

    # ---- the run ----
    def _run(self, o):
        res = {"scale": None, "nozzles": {}, "four_points": None, "proposed": []}
        cfg = self.cfg_loader()
        mb = None
        try:
            with self.m.busy_call():                             # no control hand-over meanwhile
                mb = self.m.board()
                with self.m.lock:
                    if mb.pos is None or mb.xy_state() != 0:
                        self.step = "homing"; self._say("XY not homed: homing")
                        mb.home_xy()
                    mb.raise_all()
                    mb.head_led(0)                                # the head lamp dazzles the up camera
                    mb.up_light(True)
                with self.cams.lock:
                    self.cams.dev().exposure(0, int(o.get("exposure", 600)))
                nozzles = [int(n) for n in o.get("nozzles", [1, 2, 3, 4])]
                zt = bool(o.get("ztilt", True))
                steps = (1 if o.get("scale", True) else 0) + len(nozzles) * (2 if zt else 1) + (1 if o.get("four", True) else 0)
                done = 0
                pos = [list(p) for p in cfg.nozzle_positions]
                # ---- 1. scale and angle
                if o.get("scale", True):
                    self.step = "up-camera scale"
                    x0, y0 = pos[0]
                    self.move(mb, x0, y0)
                    ref = self.grab()
                    tx, ty, tr = tip(ref)
                    half = int(tr * 1.1)
                    tpl = ref[int(ty) - half:int(ty) + half, int(tx) - half:int(tx) + half].copy()
                    ux, uy, _ = locate(ref, tpl)
                    pts, pattern = [], default_pattern(float(o.get("step", 3.0)))         # small: the lens is not linear further out
                    E = np.asarray(EXPECTED_A["up"], float)
                    A0 = E / np.linalg.norm(E[:, 0]) * cfg.pxmm(0)          # nominal: where the tip should go
                    for i, (dX, dY) in enumerate(pattern):
                        self.move(mb, x0 + dX, y0 + dY)
                        pred = np.array([ux, uy]) + A0 @ np.array([dX, dY])
                        u, v, q = locate_near(self.grab(), tpl, pred, 0.15 * math.hypot(dX, dY) * cfg.pxmm(0) + 40)
                        if q < 0.6:
                            raise RuntimeError(f"nozzle 1 tip not followed at {dX:+.2f}, {dY:+.2f} mm (match {q:.2f})")
                        pts.append((dX, dY, u - ux, v - uy, q))
                        self.progress = (done + (i + 1) / len(pattern)) / steps
                    A, b, r = fit_matrix(pts)
                    k_x, k_y, ang, skew, ok = describe(A, "up")
                    res["scale"] = {"matrix": np.asarray(A).tolist(), "k_x": k_x, "k_y": k_y, "angle_deg": ang, "skew_deg": skew,
                                    "orientation_ok": bool(ok), "fit_rms_px": float(np.sqrt(np.mean(np.asarray(r) ** 2)))}
                    self._say(f"up camera: {k_x:.3f} / {k_y:.3f} px/mm, angle {ang:+.3f} deg, skew {skew:+.3f} deg, "
                              f"fit {res['scale']['fit_rms_px']:.2f} px" + ("" if ok else ", ORIENTATION WRONG"))
                    self.move(mb, x0, y0)
                    done += 1
                else:
                    k = cfg.pxmm(0)
                    ca = math.radians(cfg.cam_angle)
                    A = np.array([[k * math.cos(ca), -k * math.sin(ca)], [k * math.sin(ca), k * math.cos(ca)]]) @ np.array([[-1.0, 0.0], [0.0, 1.0]])
                kmean = float(np.mean([np.linalg.norm(np.asarray(A)[:, 0]), np.linalg.norm(np.asarray(A)[:, 1])]))
                Ainv = np.linalg.inv(np.asarray(A))
                # ---- 2. each nozzle's axis
                for n in nozzles:
                    self.step = f"nozzle {n}"
                    x, y = pos[n - 1]
                    with self.m.lock:
                        mb.raise_all()
                    self.move(mb, x, y)
                    seen, img = [], None
                    for j, deg in enumerate((0, 90, 180, -90, 0)):
                        self._check()
                        with self.m.lock:
                            mb.rotate(n, deg, wait=True)
                        time.sleep(0.3)
                        img = self.grab()
                        c3 = centre(img)
                        if c3 is None or c3[2] > float(o.get("max_hole_px", 25)):     # no tip: nothing, or the empty holder's bore
                            break
                        seen.append(c3)
                        self.progress = (done + (j + 1) / 5) / steps
                    with self.m.lock:
                        mb.rotate(n, 0, wait=True)
                    done += 1
                    if len(seen) < 5:
                        res["nozzles"][n] = {"ok": False, "why": "tip not found (empty or removed nozzle?)", "pos_now": [x, y]}
                        self._say(f"nozzle {n}: tip not found - skipped, keeps {x:.3f}, {y:.3f}")
                        continue
                    cx, cy, rr = fit_circle([s[:2] for s in seen[:4]])
                    h, w = img.shape
                    d = Ainv @ np.array([cx - w / 2, cy - h / 2])
                    new = [round(x - d[0], 4), round(y - d[1], 4)]
                    res["nozzles"][n] = {"ok": True, "axis_px": [cx, cy], "runout_mm": rr / kmean, "pos_now": [x, y], "pos_new": new,
                                         "change_mm": [round(-d[0], 4), round(-d[1], 4)],
                                         "back_to_0_px": math.hypot(seen[4][0] - seen[0][0], seen[4][1] - seen[0][1])}
                    pos[n - 1] = new
                    self._say(f"nozzle {n}: {x:.4f}, {y:.4f} -> {new[0]:.4f}, {new[1]:.4f} "
                              f"(change {-d[0]:+.4f}, {-d[1]:+.4f} mm), runout {rr / kmean * 1000:.0f} um")
                # ---- 2b. Z tilt: the tip at a few depths and turns, each nozzle on its (new) axis
                if zt:
                    from .ztilt import fit_ztilt
                    zmax = min(9.0, float(o.get("zmax", 7.5)))
                    levels = [round(zmax * i / 3, 2) for i in range(4)]
                    res["ztilt"] = {}
                    for n in nozzles:
                        if not res["nozzles"].get(n, {}).get("ok"):
                            continue
                        self.step = f"nozzle {n} Z tilt"
                        x, y = pos[n - 1]
                        with self.m.lock:
                            mb.raise_all()
                        self.move(mb, x, y)
                        meas, r0, radii = [], None, {}
                        for a_i, deg in enumerate((0, 90, 180, -90)):
                            with self.m.lock:
                                mb.set_z(n, 0.0, wait=True)
                                mb.rotate(n, deg, wait=True)
                            for z in levels:
                                self._check()
                                with self.m.lock:
                                    mb.set_z(n, z, speed=50, wait=True)
                                time.sleep(0.3)
                                img = self.grab()
                                c3 = centre(img)
                                if c3 is None:
                                    continue
                                h, w = img.shape
                                if z == 0 and r0 is None:
                                    r0 = c3[2]                            # the motion fit's px/mm holds at Z 0 only
                                mag = (c3[2] / r0) if r0 else 1.0            # closer to the lens: bigger
                                radii.setdefault(z, []).append(c3[2])
                                if not 0.9 <= mag <= 1.6:                 # blurred / mis-found at this depth
                                    continue
                                e = Ainv @ np.array([c3[0] - w / 2, c3[1] - h / 2]) / mag
                                meas.append((deg, z, float(e[0]), float(e[1])))
                            self.progress = (done + (a_i + 1) / 4) / steps
                        with self.m.lock:
                            mb.set_z(n, 0.0, wait=True)
                            mb.rotate(n, 0, wait=True)
                        done += 1
                        self._say(f"nozzle {n} hole radius per depth: " + ", ".join(f"{z:g} mm {np.mean(r):.1f} px" for z, r in sorted(radii.items())))
                        try:
                            f = fit_ztilt(meas)
                            # a depth where the tip is too blurred to find well: its points off the fit -> left out
                            from .ztilt import shift as _sh
                            def resid(m_):
                                th, z, ex, ey = m_
                                base = [q for q in meas if q[0] == th and q[1] == 0]
                                if not base:
                                    return 0.0
                                sx, sy = _sh(f["fix"], f["turn"], f["sign"], th, z)
                                return math.hypot(ex - base[0][2] - sx, ey - base[0][3] - sy)
                            per = {z: float(np.mean([resid(m_) for m_ in meas if m_[1] == z])) for z in sorted({m_[1] for m_ in meas})}
                            self._say(f"nozzle {n} fit per depth: " + ", ".join(f"{z:g} mm {v * 1000:.0f} um" for z, v in per.items()))
                            bad = [z for z, v in per.items() if z > 0 and v > max(0.010, 3 * float(np.median(list(per.values()))))]
                            if bad and len(per) - len(bad) >= 2:
                                meas = [m_ for m_ in meas if m_[1] not in bad]
                                f = fit_ztilt(meas)
                                self._say(f"nozzle {n}: depths {', '.join(f'{z:g}' for z in bad)} mm left out (the tip too blurred there)")
                        except ValueError as e:
                            self._say(f"nozzle {n} Z tilt: {e}")
                            continue
                        at = levels[-1] + 2.3                     # about the place depth
                        res["ztilt"][n] = {**f, "depths": levels, "n": len(meas)}
                        mx = max(math.hypot(f["fix"][0] + c * f["turn"][0], f["fix"][1] + c * f["turn"][1]) for c in (-1, 1)) * at
                        self._say(f"nozzle {n} Z tilt: fixed {f['fix'][0] * 1000:+.1f}, {f['fix'][1] * 1000:+.1f} um/mm, "
                                  f"turning {math.hypot(*f['turn']) * 1000:.1f} um/mm, up to {mx * 1000:.0f} um at {at:.1f} mm down "
                                  f"(fit {f['rms'] * 1000:.1f} um/mm)")
                # ---- 3. the 4-nozzle picture points: measured around the predicted points
                c_pos = cfg.pt("Position/posCamLookUpMouseMouseCenter")
                if any(c_pos) and o.get("four", True):
                    self.step = "4-nozzle picture"
                    pred = four_points(A, c_pos, pos)
                    with self.m.lock:
                        mb.raise_all()
                    self.move(mb, c_pos[0], c_pos[1])
                    seen = {i: [] for i in range(4)}
                    for deg in (0, 60, 120, 180, -120, -60):
                        self._check()
                        with self.m.lock:
                            for n in range(1, 5):
                                mb.rotate(n, deg, wait=False)
                            mb.wait_idle(0, 5.0)
                        time.sleep(0.3)
                        img = self.grab()
                        for i in range(4):
                            c3 = centre(img, roi=110, inner=45, at=pred[i])
                            if c3 is not None and c3[2] <= float(o.get("max_hole_px", 25)):
                                seen[i].append(c3)
                    with self.m.lock:
                        for n in range(1, 5):
                            mb.rotate(n, 0, wait=False)
                        mb.wait_idle(0, 5.0)
                    pts, how = [], []
                    for i in range(4):
                        # a dark speck next to the hole (darker at some turns) is not the hole: keep the spots of
                        # the usual size, then leave out the one the circle fits worst (as camvision.four_up_start)
                        rs = sorted(c[2] for c in seen[i])
                        P_ = [c[:2] for c in seen[i] if rs and abs(c[2] - rs[len(rs) // 2]) <= 0.3 * rs[len(rs) // 2]]
                        def circ_rms(Q):
                            cx_, cy_, r_ = fit_circle(Q)
                            return float(np.sqrt(np.mean([(math.hypot(q[0] - cx_, q[1] - cy_) - r_) ** 2 for q in Q])))
                        while len(P_) >= 5 and circ_rms(P_) > 1.0:              # off the circle: leave the worst out
                            P_.pop(min(range(len(P_)), key=lambda j_: circ_rms(P_[:j_] + P_[j_ + 1:])))
                        if len(P_) >= 4:
                            cx, cy, _ = fit_circle(P_)
                            pts.append([round(cx, 1), round(cy, 1)]); how.append("measured")
                        else:
                            pts.append(pred[i]); how.append("computed")
                    res["four_points"], res["four_how"] = pts, how
                    self._say("4-nozzle points: " + ", ".join(f"{i + 1}: {p[0]:.1f}, {p[1]:.1f} ({h_})" for i, (p, h_) in enumerate(zip(pts, how))))
                # ---- what the page may write
                P = res["proposed"]
                if res["scale"]:
                    P.append({"key": "Vision/fUpPxPerMm", "label": "Up camera scale (px/mm)", "old": cfg.pxmm(0),
                              "new": round((res["scale"]["k_x"] + res["scale"]["k_y"]) / 2, 3), "type": "f", "default": True})
                    P.append({"key": "Default/fAngleCamLookUp", "label": "Up camera angle (deg)", "old": cfg.cam_angle,
                              "new": round(-res["scale"]["angle_deg"], 3), "type": "f", "default": False,
                              "note": "= minus the measured image angle (as NeoDen4's own value on this machine); check with a placed part before using it"})
                for n, r in sorted(res["nozzles"].items()):
                    if r["ok"]:
                        small = math.hypot(*r["change_mm"]) < 0.07
                        P.append({"key": f"Position/posCamLookUpMouse{n - 1}", "label": f"Nozzle {n} over the up camera", "old": r["pos_now"],
                                  "new": r["pos_new"], "type": "pt", "default": not small,
                                  "note": "within the measurement noise (1-2 px): keep the old one unless it is known to be off" if small else None})
                had = lambda k: list(cfg.pt(k)) if (k + ".x") in getattr(cfg, "raw", {}) else None
                for n, f in sorted((res.get("ztilt") or {}).items()):
                    P.append({"key": f"Calib/ptZTiltFix{n}", "label": f"Nozzle {n} Z tilt, fixed (mm per mm down)", "old": had(f"Calib/ptZTiltFix{n}"),
                              "new": [round(f["fix"][0], 5), round(f["fix"][1], 5)], "type": "pt", "default": False,
                              "note": "a tilted up camera looks the same as a tilted Z: check with a placement test before using it"})
                    P.append({"key": f"Calib/ptZTiltTurn{n}", "label": f"Nozzle {n} Z tilt, turning with the nozzle (mm per mm)", "old": had(f"Calib/ptZTiltTurn{n}"),
                              "new": [round(f["turn"][0], 5), round(f["turn"][1], 5)], "type": "pt", "default": False})
                if res.get("ztilt"):
                    sg = [f["sign"] for f in res["ztilt"].values()]
                    P.append({"key": "Calib/fZTiltSign", "label": "Z tilt: turn direction", "old": None,
                              "new": 1 if sg.count(1) >= sg.count(-1) else -1, "type": "f", "default": False})
                if res["four_points"]:
                    for i, p in enumerate(res["four_points"]):
                        meas = res.get("four_how", ["measured"] * 4)[i] == "measured"
                        P.append({"key": f"Position/dPosSubMouses{i}", "label": f"Nozzle {i + 1} point in the 4-nozzle picture (px)",
                                  "old": list(cfg.pt(f"Position/dPosSubMouses{i}")), "new": p, "type": "pt", "default": meas,
                                  "note": None if meas else "not seen (empty nozzle?): computed from the axes, the lens is not linear there"})
                self.result = res
                self.state, self.step, self.progress = DONE, "done", 1.0
                self._say("done: check the new values, then Apply")
        except Cancelled:
            self.state, self.step = CANCELLED, "cancelled"
            self.result = res
            self._say("cancelled")
        except Exception as e:
            if self._cancel.is_set():                            # mb.cancel() interrupts a wait with neomach's Cancelled
                self.state, self.step, self.result = CANCELLED, "cancelled", res
                self._say("cancelled")
                return
            self.state, self.step = FAILED, "failed"
            self.result = res
            self._say(f"failed: {type(e).__name__}: {e}")
        finally:
            try:
                if mb is not None:
                    with self.m.lock:
                        mb.up_light(False)
                        mb.raise_all()
                with self.cams.lock:
                    self.cams.dev().exposure(0, int(50 * float(cfg.raw.get("Default/fBrightness", 3.0))))
            except Exception:
                pass
