"""Cameras for the job runner: up-camera part vision, fiducials and the bad mark.

`dev` is a camera object with grab(cam) / exposure(cam, v) / window(cam, w, h) (neocam.usbcam
or neocam.client); camera 0 looks up, 1 is on the head. Every image after a move is taken with
neocalib.grab_settled when [Job] bSettle is on (NeoDen4 waits a fixed 250 ms).
Vision modes 2 (four-nozzle shot) and 3 (large part, two views) are measured here one nozzle
at a time like mode 1 [simplification: same result for parts that fit in the single view].
"""
import math
import os
import re
import os
import threading
import time

import numpy as np

from neocam import vision as V

from . import leadvision as LV

UP, HEAD = 0, 1


def load_packages(path):
    """Config/pkg.csv: name, L, W, H (mm) -> {name: (L, W, H)}"""
    out = {}
    if not path or not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8", errors="replace"):
        t = line.strip().split(",")
        if len(t) >= 3:
            try:
                out[t[0].strip()] = (float(t[1]), float(t[2]), float(t[3]) if len(t) > 3 and t[3] else 0.0)
            except ValueError:
                pass
    return out


class CameraVision:
    def __init__(self, dev, mb, cfg, lock=None, log=print):
        self.dev, self.mb, self.cfg, self.log = dev, mb, cfg, log
        self.lock = lock or threading.Lock()
        self.bg = {}
        self.bg_id = {}
        self._said = False
        self.vlog = None                       # neojob.visionlog.VisionLog, set by the runner
        self.last = {}                         # cam -> (seq, image as shown): the mount screen shows them
        self.last_info = {}                    # cam -> {"seq", ...}: what vision found in that picture
        self.seq = 0

    # ---- frames ----
    def grab(self, cam):
        with self.lock:
            d = self.dev() if callable(self.dev) else self.dev
            raw_grab = lambda: np.asarray(d.grab(cam))
            if self.cfg.job("bSettle"):
                from neocalib import grab_settled
                r = grab_settled(raw_grab, tol_px=float(self.cfg.job("fSettleTolPx")), max_s=float(self.cfg.job("fSettleMaxS")))
                raw = r.frame
            else:
                time.sleep(0.25)
                raw = raw_grab()
        img = V.orient_up(raw) if cam == UP else V.orient_head(raw)
        if getattr(self.cfg, "path", None):                  # the camera's pixel map, when it is on
            from neocalib import cammap
            img = cammap.apply(img, cam, os.path.dirname(self.cfg.path))
        self.seq += 1
        self.last[UP if cam == UP else HEAD] = (self.seq, img)
        return img

    def setup(self, cam, exposure, size):
        with self.lock:
            d = self.dev() if callable(self.dev) else self.dev
            d.window(cam, size, size)
            if exposure:
                d.exposure(cam, int(exposure))

    # ---- acquireBackground: empty nozzles over the up camera ----
    def start(self, job, runner):
        used = sorted({c.nozzle for c in job.comps if not c.skip and job.stack(c.stack) and job.stack(c.stack).vision > 0 and 1 <= c.nozzle <= 4})
        self.bg = {}
        if not used or job.test:
            return
        for n in range(1, 5):
            self.mb.rotate(n, -5.0, 150, wait=False)
        for n in range(1, 5):
            self.mb.rotate(n, 0.0, 150, wait=False)
        self.setup(UP, self.cfg.up_exposure(), 1024)
        self.mb.up_light(True)
        self.tip = {}
        k = self.cfg.pxmm(UP)
        for n in used:
            runner.checkpoint()
            p = self.cfg.nozzle_positions[n - 1]
            self.mb.move_xy(*p, timeout=20)
            # the nozzle's AXIS (the part turns about it, not about the hole, which runs ~0.1 mm off it):
            # the hole is found at 6 turns of the empty nozzle (bright exposure) and a circle fitted
            # through them; part offsets are measured from its centre
            try:
                self.setup(UP, int(self.cfg.vision("nTipExposure") or 600), 1024)
                seen = []
                for a in (0.0, 60.0, 120.0, 180.0, -120.0, -60.0):
                    self.mb.rotate(n, a, 150, wait=True)
                    time.sleep(0.15)
                    timg = self.grab(UP)
                    H, W = timg.shape
                    t = V.tip_centre(timg, W / 2, H / 2, search=int(1.2 * k))
                    if t:
                        seen.append(t)
                self.mb.rotate(n, 0.0, 150, wait=True)
                pts = [t[:2] for t in seen]
                if len(pts) >= 4:
                    cx, cy, rr, rms = V.fit_circle(pts)
                    if rms < 0.03 * k and math.hypot(cx - W / 2, cy - H / 2) < 1.0 * k:
                        self.tip[n] = (cx, cy, sorted(t[2] for t in seen)[len(seen) // 2], rr)
                        self.log(f"vision: nozzle {n} axis {(cx - W / 2) / k:+.3f}, {(H / 2 - cy) / k:+.3f} mm from the picture centre "
                                 f"(hole runout {rr / k:.3f} mm)")
                    else:
                        self.log(f"vision: nozzle {n} axis not trusted (fit {rms / k:.3f} mm): offsets from the picture centre")
                else:
                    self.log(f"vision: nozzle {n} hole seen at {len(pts)} of 6 turns: offsets from the picture centre")
            except Exception as e:
                self.log(f"vision: nozzle {n} axis: {e}")
            self.setup(UP, self.cfg.up_exposure(), 1024)
            time.sleep(0.1)
            self.bg[n] = self.grab(UP)
            if self.vlog:
                self.bg_id[n] = self.vlog.background(n, self.bg[n], exposure=self.cfg.up_exposure(), px_per_mm=self.cfg.pxmm(UP))
        self.log(f"vision: backgrounds of nozzles {used}")
        self.four = {}
        # the 4-up shot: for every feeder whose vision is Joint (NeoDen4's "joint correction"), or for all
        # small parts with [Vision] bFourUp
        joint = any(job.stack(c.stack) and job.stack(c.stack).vision == 2 for c in job.comps if not c.skip)
        if self.cfg.vision("bFourUp") or joint:
            try:
                self.four_up_start(used, runner)
            except Exception as e:
                self.four = {}
                self.log(f"vision: 4-up shot off for this job ({e})")

    # ---- the 4-up shot: every nozzle's axis in one image, found again at each job start ----
    def four_up_start(self, used, runner, turns=(0.0, 60.0, 120.0, 180.0, -120.0, -60.0)):
        """the empty nozzles at posCamLookUpMouseMouseCenter, turned together: each tip runs on a
        circle around its nozzle's axis; the axis (px) is where the part's offset is measured
        from. Then the 4-up background at the parts' exposure."""
        k = self.cfg.pxmm(UP)
        self.mb.move_xy(*self.cfg.four_up_position, timeout=20)
        self.mb.head_led(0)                               # the head lamp dazzles the up camera
        self.setup(UP, int(self.cfg.vision("nTipExposure")), 1024)
        seen = {n: [] for n in used}
        for a in turns:
            runner.checkpoint()
            for n in used:
                self.mb.rotate(n, a, 150, wait=False)
            for n in used:
                self.mb.wait_idle(n, 5.0)
            time.sleep(0.2)
            img = self.grab(UP)
            for n in used:
                sx, sy = self.cfg.sub_mouses[n - 1]
                t = V.tip_centre(img, sx, sy, search=int(1.2 * k))      # the stored point may be off ~1 mm
                if t:
                    seen[n].append(t)
        for n in used:
            self.mb.rotate(n, 0.0, 150, wait=False)
        for n in used:
            # a dark speck taken for the hole: its size is off, or it is off the circle
            rs = sorted(t[2] for t in seen[n])
            pts = [t[:2] for t in seen[n] if rs and abs(t[2] - rs[len(rs) // 2]) <= 0.3 * rs[len(rs) // 2]]
            while len(pts) >= 4:
                fits = [(V.fit_circle(pts[:i] + pts[i + 1:]), i) for i in range(len(pts))]
                (f, i) = min(fits, key=lambda q: q[0][3])
                if V.fit_circle(pts)[3] <= 0.7 or f[3] > 0.5 * V.fit_circle(pts)[3]:
                    break
                pts.pop(i)
            if len(pts) < 3:
                self.log(f"vision 4-up: nozzle {n} tip seen {len(pts)} of {len(turns)} times: shot alone")
                continue
            cx, cy, r, rms = V.fit_circle(pts)
            if rms > 2.0:
                self.log(f"vision 4-up: nozzle {n} tips do not lie on a circle (rms {rms:.1f} px): shot alone")
                continue
            self.four[n] = (cx, cy)
            sx, sy = self.cfg.sub_mouses[n - 1]
            self.log(f"vision 4-up: nozzle {n} axis {cx:.1f}, {cy:.1f} px (stored {sx:.0f}, {sy:.0f}), "
                     f"runout {r / k * 1000:.0f} um")
        # the scale where the nozzles stand (the up camera's lens: ~32 px/mm in the middle, ~34 at
        # the corners on the NeoDen4 of 2026-11-02): nozzle spacing in px / in mm (posCamLookUpMouse)
        self.k4 = None
        ns = sorted(self.four)
        ratios = []
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                a, b = self.cfg.nozzle_positions[ns[i] - 1], self.cfg.nozzle_positions[ns[j] - 1]
                mm = math.dist(a, b)
                if mm > 5:
                    ratios.append(math.dist(self.four[ns[i]], self.four[ns[j]]) / mm)
        if ratios:
            ratios.sort()
            self.k4 = ratios[len(ratios) // 2]
            self.log(f"vision 4-up: {self.k4:.2f} px/mm where the nozzles stand (from their spacing)")
        if self.four:
            pts = list(self.four.values())
            mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            if len(pts) == 4:
                self.log(f"vision 4-up: the nozzles' middle is {(mx - 512) / k:+.2f}, {(512 - my) / k:+.2f} mm "
                         f"from the image centre (posCamLookUpMouseMouseCenter)")
            self.setup(UP, self.cfg.up_exposure(True), 1024)
            self.bg4 = self.grab(UP)

    def four_up_parts(self, slots, runner):
        """parts on several nozzles from one shot: {nozzle: PartResult}"""
        k = getattr(self, "k4", None) or self.cfg.pxmm(UP)
        out = {}

        def shoot():
            self.mb.move_xy(*self.cfg.four_up_position, timeout=20)
            self.setup(UP, self.cfg.up_exposure(True), 1024)
            return self.grab(UP)
        img = runner.retry("Photograph failed! (4-up)", shoot, ignore=True)
        if img is None or img is False:
            return out
        bg = self.bg4 if getattr(self, "bg4", None) is not None and self.bg4.shape == img.shape else np.zeros_like(img)
        info = {"seq": self.seq, "four": True, "parts": []}           # the mount screen draws them all
        self.last_info[UP] = info
        for s in slots:
            n, st, comp = s["nozzle"], s["stack"], s["comp"]
            pkg = self._package(s, runner)
            x, y = self.four[n]
            r = V.analyze_at(img, bg, x, y, V.roi_half_size(pkg[0], pkg[1], k) if pkg else int(2.5 * k), k)
            # the part's underside is its height nearer the lens: the feeder's H, else pkg.csv's
            ph = float(getattr(st, "part_h", 0) or 0) or (pkg[2] if pkg and len(pkg) > 2 and pkg[2] else 0.0)
            if ph:
                h, w = img.shape
                r = V.perspective_fix(r, (x, y), (w / 2, h / 2), k, ph, float(self.cfg.vision("fUpMagPerMm")))
            if self.vlog:
                s["vlog_id"] = self.vlog.add(
                    "part", img, job=os.path.basename(runner.job.path), panel=runner.panel + 1, ref=comp.name,
                    stack=st.id, feeder=runner.feeder_of(st.id)[0], footprint=st.footprint or comp.footprint,
                    value=comp.value, nozzle=n, camera="up", mode=st.vision, method="four-up",
                    exposure=self.cfg.up_exposure(True), px_per_mm=k, at_px=[x, y], stack_angle=st.angle,
                    package_lw=list(pkg[:2]) if pkg else None, valid=bool(r.valid), x=r.x, y=r.y, angle=r.angle, w=r.w, h=r.h,
                    four_up_pos=list(self.cfg.four_up_position), cam_angle=self.cfg.cam_angle)
            if not r.valid:
                runner.log(f"{comp.name}: image recognition failed (nozzle {n}, 4-up)")
            else:
                runner.log(f"{comp.name}: part {r.w:.2f} x {r.h:.2f} mm, offset {r.x:+.3f}, {r.y:+.3f} mm, "
                           f"{r.angle:+.2f} deg (4-up)")
            rect = getattr(r, "rect_px", None)
            info["parts"].append({"name": comp.name, "nozzle": n, "valid": bool(r.valid), "axis": [round(float(x), 2), round(float(y), 2)],
                                  **{q: round(float(getattr(r, q, 0) or 0), 3) for q in ("x", "y", "w", "h", "angle")},
                                  "rect": [round(float(v), 2) for v in rect] if rect is not None and len(rect) == 5 else None})
            out[n] = r
        return out

    def bulk_bottom(self, s, runner):
        """a part from a bulk feeder over the up camera: every lead / pad of the taught underside there
        (else not valid: the runner throws it away and takes another), the exact angle and centre"""
        from .bulkfeed import bottom_result
        bf = runner.bulk_feeder()
        st, n = s["stack"], s["nozzle"]
        c = bf.container(int(st.bulk)) or {}
        _, bottom_t = bf.template(c.get("package"))
        if not bottom_t:
            runner.log(f"bulk feeder {st.bulk}: no underside taught: the part is placed by the top view only")
            return V.PartResult(True, 0.0, 0.0, 1.0, 1.0, 0.0)
        img = bf.bottom_picture(n, int(c.get("exp_bottom", 300) or 300))
        k = self.cfg.pxmm(UP)
        ok, why, dx, dy, phi, b = bottom_result(img, k, bottom_t, s.get("bulk_phi", 0.0))
        self.last_info[UP] = {"seq": self.seq, "name": s["comp"].name, "nozzle": n, "valid": bool(ok), "empty": False,
                              "x": round(dx, 3), "y": round(dy, 3), "w": round(b.L, 3), "h": round(b.W, 3),
                              "angle": round(phi - s.get("bulk_phi", 0.0), 2), "rect": None}
        self.setup(UP, self.cfg.up_exposure(), 1024)              # back to the parts' exposure
        if not ok:
            runner.log(f"{s['comp'].name}: underside: {why}")
            return V.PartResult(False)
        d = (phi - s.get("bulk_phi", 0.0) + 180.0) % 360.0 - 180.0  # the turn found from below against the top's
        runner.log(f"{s['comp'].name}: underside all there, {d:+.2f} deg against the top view, centre {dx:+.3f}, {dy:+.3f} mm")
        return V.PartResult(True, dx, dy, b.L, b.W, d)

    def four_up_ok(self, s, runner):
        """can this part be read in the 4-up shot?"""
        st = s["stack"]
        if s["nozzle"] not in getattr(self, "four", {}) or st.vision not in (1, 2):
            return False
        if st.vision == 1 and not self.cfg.vision("bFourUp"):
            return False                                   # Individual: one nozzle at a time
        if LV.wanted(self.cfg, s["comp"], st):
            return False
        pkg = self._package(s, runner)
        if st.vision == 2:                                 # Joint, chosen for this feeder: any part that fits
            return not pkg or max(pkg[0], pkg[1]) <= float(self.cfg.vision("fFourUpMaxMm"))
        if pkg:
            return max(pkg[0], pkg[1]) <= float(self.cfg.vision("fFourUpMaxMm"))
        # no package size known (the BOARD-A job's stacks have no footprint): small chips only
        fp = (st.footprint or s["comp"].footprint or "").upper()
        return bool(re.search(r"(0201|0402|0603|0805|1206|SOT-?23|SOD-?323|SOD-?523)", fp))

    @staticmethod
    def _package(s, runner):
        """(length, width, ...) mm of the part's package: by the stack's footprint, else the part's"""
        for name in (s["stack"].footprint, s["comp"].footprint):
            if name and runner.packages.get(name):
                return runner.packages.get(name)
        return None

    # ---- parts on the nozzles (visionCheckNozzle) ----
    def parts(self, slots, runner):
        out = {}
        k = self.cfg.pxmm(UP)
        for s in slots:
            s.pop("joint", None)
        group = [s for s in slots if self.four_up_ok(s, runner)]
        if len(group) >= 2:                                # one shot for these, the rest one by one
            for s in group:
                s["joint"] = True
            out.update(self.four_up_parts(group, runner))
            slots = [s for s in slots if s not in group]
        for s in slots:
            st = s["stack"]
            if st.vision < 1:
                continue
            if getattr(st, "bulk", 0) and hasattr(runner, "bulk_feeder"):
                out[s["nozzle"]] = self.bulk_bottom(s, runner)
                continue
            if st.vision > 1 and not self._said:
                self.log("vision modes 2 and 3 are measured one nozzle at a time")
                self._said = True
            n = s["nozzle"]
            pkg = runner.packages.get(st.footprint)
            roi = V.roi_half_size(pkg[0], pkg[1], k) if pkg else 500
            comp = s["comp"]
            part = getattr(runner, "pads", {}).get(comp.key or comp.name)
            leads = None
            if part and LV.wanted(self.cfg, comp, st):
                leads, centre = LV.local_leads(part["pads"], comp.rot)
                s["lead_centre"] = centre
                ext = max(max(abs(x), abs(y)) for x, y, _ in leads) + 1.0
                roi = max(100, min(500, int(ext * k)))
            elif LV.wanted(self.cfg, comp, st):
                runner.log(f"{comp.name}: lead vision wanted, but no pads of it (build <job>.pads.json): body outline used")

            def shoot():
                self.mb.move_xy(*self.cfg.nozzle_positions[n - 1], timeout=20)
                img = self.grab(UP)
                bg = self.bg.get(n)
                if bg is None or bg.shape != img.shape:
                    bg = np.zeros_like(img)
                r = None
                if leads:
                    r = LV.analyze_leads(img, bg, roi, k, leads, st.angle, self.cfg)
                    runner.log(f"{comp.name}: leads: {r.message}")
                    if r.valid:
                        r.leads = True
                    elif self.cfg.leadvision("bBodyFallback"):
                        s.pop("lead_centre", None)
                        runner.log(f"{comp.name}: body outline used instead")
                        r = None
                if r is None:
                    r = V.analyze_nozzle(img, bg, roi, k)
                elif not r.valid:                   # leads not found: is anything on the nozzle at all?
                    r.empty = getattr(V.analyze_nozzle(img, bg, roi, k), "empty", False)
                if self.vlog:
                    s["vlog_id"] = self.vlog.add(
                        "part", img, job=os.path.basename(runner.job.path), panel=runner.panel + 1, ref=comp.name,
                        stack=st.id, feeder=runner.feeder_of(st.id)[0], footprint=st.footprint or comp.footprint,
                        value=comp.value, nozzle=n, camera="up", mode=st.vision,
                        method="leads" if getattr(r, "leads", False) else "body",
                        exposure=self.cfg.up_exposure(), px_per_mm=k, roi=roi, stack_angle=st.angle,
                        package_lw=list(pkg[:2]) if pkg else None, bg_id=self.bg_id.get(n), valid=bool(r.valid),
                        x=r.x, y=r.y, angle=r.angle, w=r.w, h=r.h, message=getattr(r, "message", ""),
                        nozzle_pos=list(self.cfg.nozzle_positions[n - 1]), cam_angle=self.cfg.cam_angle)
                return r
            r = runner.retry(f"Photograph failed! (nozzle {n})", shoot, ignore=True)
            if r is False or r is None:
                continue
            tip = getattr(self, "tip", {}).get(n) if st.vision == 1 else None    # joint / large: from the picture centre
            if tip and r.valid and not getattr(r, "leads", False):
                H, W = self.last[UP][1].shape if UP in self.last else (1024, 1024)
                r.x -= (tip[0] - W / 2) / k                  # offset from the nozzle's axis
                r.y -= (H / 2 - tip[1]) / k
            rect = getattr(r, "rect_px", None)
            self.last_info[UP] = {"seq": self.seq, "name": s["comp"].name, "nozzle": n, "valid": bool(r.valid),
                                  "empty": bool(getattr(r, "empty", False)),
                                  **{k: round(float(getattr(r, k, 0) or 0), 3) for k in ("x", "y", "w", "h", "angle")},
                                  "stack_angle": st.angle, "rect": [round(float(v), 2) for v in rect] if rect is not None and len(rect) == 5 else None,
                                  "tip": [round(float(v), 2) for v in tip] if tip else None}
            if not r.valid and getattr(r, "empty", False):
                runner.log(f"{s['comp'].name}: nozzle {n} empty, nothing was picked")
            elif not r.valid:
                runner.log(f"{s['comp'].name}: image recognition failed (nozzle {n})")
            else:
                runner.log(f"{s['comp'].name}: part {r.w:.2f} x {r.h:.2f} mm, offset {r.x:+.3f}, {r.y:+.3f} mm, {r.angle:+.2f} deg")
            out[n] = r
        return out

    # ---- fiducial (moveToMark): returns the mark in the nozzle-1 frame, or None ----
    def mark(self, m, runner):
        k = self.cfg.pxmm(HEAD)
        # lamp 2 (outer ring alone) is nearly dark on the user's machine (mean 17 vs 85 inner, 91 both,
        # measured 2026-11-02): a job's outer-lamp mark gets both rings
        led = 3 if m.led == 2 else (m.led or 1)
        self.mb.head_led(led)
        self.setup(HEAD, 0, 800)
        with self.lock:
            d = self.dev() if callable(self.dev) else self.dev
        cache = self.__dict__.setdefault("_mark_exp", {})
        key = (led, round(m.dmin, 2), round(m.dmax, 2))
        if m.exposure:
            self.setup(HEAD, m.exposure, 800)
        elif cache.get(key):
            # the auto exposure once per lamp and mark size in a run: the next marks start from it,
            # and their first search picture checks it (searched again only when the light is off)
            d.exposure(HEAD, int(cache[key]))
        else:
            cache[key] = V.auto_exposure_mark(lambda: self.grab(HEAD), lambda x: d.exposure(HEAD, int(x)), start=150)
        # Faster than NeoDen4's three centring moves: one picture after the move, ONE slow (one-sided,
        # backlash-free) move onto the mark, one picture; the mark's offset in that picture is read
        # directly when it is within fMarkReadMm of the centre (0.3 mm: scale and camera angle add
        # ~2 um there), else one more slow move and picture. About half the time per mark.
        read_mm = float(self.cfg.job("fMarkReadMm"))
        cam = self.cfg.head_cam_offset
        for it in range(3):
            img = self.grab(HEAD)
            if it == 0 and not m.exposure and cache.get(key):
                mid = V.histogram_mid_level(img)
                if not (120 <= mid <= 200):                   # the light changed: expose again
                    cache[key] = V.auto_exposure_mark(lambda: self.grab(HEAD), lambda x: d.exposure(HEAD, int(x)),
                                                      start=cache[key])
                    img = self.grab(HEAD)
            res = V.find_mark_best(img, m.dmin, m.dmax, k, min_dia_mm=float(self.cfg.job("fMarkMinDiaMm") or 1.0))
            if self.vlog:
                self.vlog.add("mark", img, camera="head", iteration=it + 1, design=[m.x, m.y], dmin=m.dmin, dmax=m.dmax,
                              led=m.led, px_per_mm=k, at=list(self.mb.pos or ()), found=res.precision > 0,
                              px=[res.x, res.y], r_px=res.r, precision=res.precision, threshold=res.threshold)
            if res.precision <= 0:
                self.log("mark recognition failed")
                return None
            if it == 0:
                self.log(f"mark: round {res.precision:.3f}, diameter {2 * res.r / k:.2f} mm")
            dx, dy = V.mark_offset_mm(res, 800, k)
            if it and math.hypot(dx, dy) <= read_mm:        # after a slow approach: read it off the picture
                return self.mb.pos[0] - cam[0] + dx, self.mb.pos[1] - cam[1] + dy
            self.mb.move_rel(dx, dy, timeout=10, slow=True)
        cam = self.cfg.head_cam_offset
        return self.mb.pos[0] - cam[0], self.mb.pos[1] - cam[1]

    # ---- pad alignment (neojob.padalign) ----
    def head_shot(self, x, y):
        """the head camera's settled picture with its centre on (x, y) (nozzle-1 frame), pad lighting"""
        from neocalib import grab_settled
        cam = self.cfg.head_cam_offset
        self.mb.move_xy(x + cam[0], y + cam[1], timeout=15)
        self.head_for_pads()
        return grab_settled(lambda: self.grab(HEAD), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame

    def pocket(self, x, y, runner, radius=1.5):
        """the tape pocket nearest the pick point (x, y): the head camera over it, neocam.tape.
        Returns (x, y) of its centre when it holds a part and is within `radius` mm; "empty" when the
        pocket there is empty (the runner feeds on); None when none is seen (the stored point is used)"""
        from neocam.tape import find_pockets
        from .padalign import px_to_machine
        cam = self.cfg.head_cam_offset
        self.mb.move_xy(x + cam[0], y + cam[1], timeout=15)
        self.head_for_pads()
        # the head shakes for a moment after the move: grab until the picture stops moving
        from neocalib import grab_settled
        sr = grab_settled(lambda: self.grab(HEAD), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15)
        img = sr.frame
        if not sr.settled:
            runner.log(f"feeder pocket: the picture did not settle in {sr.time_s:.1f} s")
        k = self.cfg.pxmm(HEAD)
        ang = float(self.cfg.padalign("fHeadCamAngle"))
        for f in find_pockets(img, k):
            if f["kind"] != "pocket":
                continue
            px, py = px_to_machine((x, y), f["px"], f["py"], k, img.shape[1], ang)
            if math.hypot(px - x, py - y) <= radius:
                if not f.get("full", True):
                    runner.log(f"feeder pocket at {px:.3f}, {py:.3f} is empty")
                    return "empty"
                runner.log(f"feeder pocket {f['w']:.2f} x {f['h']:.2f} mm at {px:.3f}, {py:.3f} (stored {x:.3f}, {y:.3f})")
                return px, py
        runner.log(f"feeder pocket not found near {x:.2f}, {y:.2f}: the stored pick point is used")
        return None

    def head_for_pads(self):
        """head lamp + 800 window + exposure for pad images"""
        self.mb.head_led(int(self.cfg.padalign("nLed")) or 1)
        self.setup(HEAD, 0, 800)
        e = int(self.cfg.padalign("nExposure"))
        if e:
            self.setup(HEAD, e, 800)
        else:
            with self.lock:
                d = self.dev() if callable(self.dev) else self.dev
            V.auto_exposure_median(lambda: self.grab(HEAD), lambda v: d.exposure(HEAD, int(v)),
                                   float(self.cfg.padalign("fPadMedian")))

    def pad_aligner(self):
        from .padalign import PadAligner
        cam = self.cfg.head_cam_offset
        pa = PadAligner(lambda: self.grab(HEAD), lambda x, y: self.mb.move_xy(x + cam[0], y + cam[1], timeout=10), self.cfg, self.log)
        if self.vlog:
            pa.on_view = lambda img, meta: self.vlog.add("pad", img, camera="head", px_per_mm=self.cfg.pxmm(HEAD), **meta)
        return pa

    def pad_align(self, pads, centre, tf, runner, images=False, hole=False):
        self.head_for_pads()
        return self.pad_aligner().align(pads, centre, tf, images, hole=hole)

    def bad_mark(self, exposure, runner):
        self.mb.head_led(3)                                # NeoDen4: 2 (outer), too dark here
        self.setup(HEAD, exposure or 200, 800)
        img = self.grab(HEAD)
        h, w = img.shape
        return float(img[h // 2 - 16:h // 2 + 16, w // 2 - 16:w // 2 + 16].mean())
