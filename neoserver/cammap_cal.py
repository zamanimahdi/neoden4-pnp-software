"""Camera pixel-map calibration (neocalib.cammap) on the machine:

    POST /api/cammap/calibrate?client=&cam=1[&n=7&span=3.2&dmin=0.7&dmax=1.5]
        head camera: centred near a round mark on the board; the head moves over an n x n grid (+-span mm,
        each point approached from the same side), the mark is found in every (raw) picture
    POST /api/cammap/calibrate?client=&cam=0[&n=9&span=12&nozzle=1&exp=600]
        up camera: nozzle n empty over the camera, at its tip plane (Z 0); the head moves over the grid and
        the tip's hole is followed
    POST /api/cammap/on?client=&cam=&on=1|0     the map used (or not) for that camera's pictures
    GET  /api/cammap/status                      both cameras' maps (fit, scales, on / off)

The pictures for the fit are raw (oriented, not through a map), 1024 x 1024. The map is stored off;
switch it on, then measure again what was measured in raw pixels (see neocalib.cammap).
"""
import math
import os
import time

import numpy as np

from neocalib import cammap
from neoserver.align import _busy


class CamMapCal:
    def __init__(self, align, cams, data_dir, log):
        self.align, self.cams, self.data_dir, self.log = align, cams, data_dir, log
        self.mc = align.mc

    def _need(self, client):                             # for _busy: the caller must have control
        self.align._need(client)

    def status(self, q=None):
        d = cammap.load(self.data_dir)
        return {"maps": {c: {k: v for k, v in (d.get(c) or {}).items() if k not in ("coef",)} for c in ("0", "1")}}

    def on(self, q):
        self.align._need(q.get("client", ""))
        cam = int(q.get("cam", 1))
        e = cammap.save(self.data_dir, cam, on=q.get("on", "1") in ("1", "true"))
        self.log(f"camera {cam}: pixel map {'on' if e['on'] else 'off'}", "align")
        return {"cam": cam, "on": e["on"]}

    def _raw(self, cam):
        """one raw picture of camera cam, oriented as the job sees it (not through the map), settled"""
        from neocam import vision as V
        from neocalib import grab_settled
        with self.cams.lock:
            d = self.cams.dev()
            fr = grab_settled(lambda: np.asarray(d.grab(cam)), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
        return V.orient_up(fr) if cam == 0 else V.orient_head(fr)

    def _exposure(self, cam, e):
        with self.cams.lock:
            self.cams.dev().exposure(cam, int(e))

    def _grid(self, n, span):
        g = np.linspace(-span, span, n)
        pts = []
        for j, y in enumerate(g):                           # rows, back and forth
            xs = g if j % 2 == 0 else g[::-1]
            pts += [(float(x), float(y)) for x in xs]
        return pts

    def _move(self, mb, x, y, pre=0.3):
        mb.move_xy(x - pre, y - pre, timeout=20, slow=True)    # every point from the same side (backlash)
        mb.move_xy(x, y, timeout=20, slow=True)

    @_busy
    def calibrate(self, q):
        cam = int(q.get("cam", 1))
        cfg = self.align.cfg(q)
        k = cfg.pxmm(cam)
        mb = self.mc.board()
        if mb.pos is None:
            raise RuntimeError("position unknown: home first")
        D, P, miss = [], [], 0
        t0 = time.monotonic()
        with self.cams.lock:
            dev = self.cams.dev()
            dev.window(cam, 1024, 1024)
        try:
            if cam == 1:
                from neocam.vision import find_mark_best
                n, span = int(q.get("n", 7)), float(q.get("span", 3.2))
                dmin, dmax = float(q.get("dmin", 0.7)), float(q.get("dmax", 1.5))
                off = cfg.head_cam_offset
                x0 = float(q["x"]) if q.get("x", "") != "" else mb.pos[0] - off[0]
                y0 = float(q["y"]) if q.get("y", "") != "" else mb.pos[1] - off[1]
                import cv2
                from neocam.vision import auto_exposure_mark
                mb.raise_all()
                mb.head_led(3)
                self._move(mb, x0 + off[0], y0 + off[1])
                # the job's mark exposure (the general one burns the white mark into the light board around it)
                auto_exposure_mark(lambda: self._raw(1), lambda e: self._exposure(1, e), start=150)
                guess, saved = None, False
                half = int(max(90, 1.3 * dmax * k))           # the search window around where the mark should be
                for dx, dy in self._grid(n, span):
                    self._move(mb, x0 + dx + off[0], y0 + dy + off[1])
                    img = self._raw(1)
                    if guess is None:                         # the camera moves +dx: the mark goes -dx in the picture
                        u, v = 512 - dx * k, 512 + dy * k
                    else:
                        u, v = cammap.raw_of(guess, -dx, -dy).ravel()
                    ux, vy = int(max(0, u - half)), int(max(0, v - half))
                    roi = img[vy:int(v + half), ux:int(u + half)]
                    r = None
                    if roi.size and min(roi.shape) > 2 * dmin * k:
                        # low contrast (a white mark on a light board): stretched within the window first
                        st = cv2.normalize(cv2.GaussianBlur(roi, (5, 5), 0), None, 0, 255, cv2.NORM_MINMAX)
                        r = find_mark_best(np.ascontiguousarray(st), dmin, dmax, k, min_dia_mm=dmin)
                    if r is None or r.precision <= 0:
                        miss += 1
                        if not saved:
                            cv2.imwrite(os.path.join(self.data_dir, "cammap_miss.png"), img)
                            saved = True
                        continue
                    D.append((-dx, -dy))                      # the mark against the camera's centre (mm)
                    P.append((float(r.x + ux), float(r.y + vy)))
                    if len(D) >= 8:
                        guess = cammap.fit(D, P, k, order=1, reject_px=0)
                self._move(mb, x0 + off[0], y0 + off[1])
            else:
                from neocam.vision import tip_centre
                n, span = int(q.get("n", 9)), float(q.get("span", 12.0))
                nz = int(q.get("nozzle", 1))
                px0, py0 = cfg.nozzle_positions[nz - 1]
                mb.raise_all()                                # the tip plane: Z 0
                mb.rotate(nz, 0.0, wait=True)
                mb.head_led(0)
                mb.up_light(True)
                with self.cams.lock:
                    dev.exposure(0, int(q.get("exp", 600)))
                # the axes first: the tip at the centre and 3 mm along X and Y, searched widely; then each grid
                # point is looked for where that (and the fit so far) puts it
                probe = {}
                for key, (dx, dy) in (("c", (0.0, 0.0)), ("x", (3.0, 0.0)), ("y", (0.0, 3.0))):
                    self._move(mb, px0 + dx, py0 + dy)
                    img = self._raw(0)
                    # the tip's hole, searched narrowly (+-60 px): a wider search takes the dark ring around the
                    # nozzle's face; 3 mm out it is in one of the four directions
                    t = None
                    if key == "c":
                        t = tip_centre(img, 512, 512)
                    else:
                        for gu, gv in ((3 * k, 0), (-3 * k, 0), (0, 3 * k), (0, -3 * k)):
                            gx, gy = probe["c"][0] + gu, probe["c"][1] + gv
                            t = tip_centre(img, gx, gy)
                            if t is not None and math.hypot(t[0] - gx, t[1] - gy) < 0.6 * k:
                                break
                            t = None
                    if t is None:
                        import cv2
                        cv2.imwrite(os.path.join(self.data_dir, "cammap_miss.png"), img)
                        raise RuntimeError(f"the nozzle tip is not found at the {'centre' if key == 'c' else key + ' + 3 mm'} "
                                           "(picture in <data>/cammap_miss.png): an empty nozzle, the up light, exposure")
                    probe[key] = (float(t[0]), float(t[1]))
                    D.append((dx, dy)); P.append(probe[key])
                ax = np.array([(probe["x"][0] - probe["c"][0]) / 3.0, (probe["x"][1] - probe["c"][1]) / 3.0])
                ay = np.array([(probe["y"][0] - probe["c"][0]) / 3.0, (probe["y"][1] - probe["c"][1]) / 3.0])
                guess = None
                saved = False
                for dx, dy in self._grid(n, span):
                    self._move(mb, px0 + dx, py0 + dy)
                    img = self._raw(0)
                    if guess is None:                         # where the tip should be: the probe, or the fit so far
                        u, v = np.array(probe["c"]) + ax * dx + ay * dy
                    else:
                        u, v = (cammap.raw_of(guess, dx, dy)).ravel()
                    t = tip_centre(img, u, v)
                    if t is None:
                        miss += 1
                        if not saved:
                            import cv2
                            cv2.imwrite(os.path.join(self.data_dir, "cammap_miss.png"), img)
                            saved = True
                        continue
                    D.append((dx, dy))                        # the tip moves with the head
                    P.append((float(t[0]), float(t[1])))
                    if len(D) >= 8:
                        guess = cammap.fit(D, P, k, order=1, reject_px=0)
                mb.up_light(False)
                self._move(mb, px0, py0)
        finally:
            try:
                self.cams.restore_view()
            except Exception:
                pass
        if len(D) < 12:
            raise RuntimeError(f"only {len(D)} of {len(D) + miss} points found: camera {cam} "
                               + ("must be centred near a round mark (Centre on the circle)" if cam == 1 else "needs an empty nozzle, the up light and a brighter exposure"))
        # the head camera: at 117 px/mm the machine's own repeatability (~0.02 mm, up to 0.036) is 2 .. 4 px, more
        # than its lens: a line (scale, skew, angle) by default; points judged in mm, not px
        order = int(q.get("order", 1 if cam == 1 else 3))
        m = cammap.fit(D, P, k, order=order, reject_px=max(2.0, 0.03 * k))
        m.update(cam=cam, missed=miss, time=time.strftime("%Y-%m-%d %H:%M"), seconds=int(time.monotonic() - t0))
        e = cammap.save(self.data_dir, cam, m)
        self.log(f"camera {cam}: pixel map from {len(D)} points: rms {m['rms_px']} px (one matrix {m['rms_linear_px']} px), "
                 f"scale centre {m['scale_centre']} corner {m['scale_corner']} px/mm", "align")
        return {k2: v for k2, v in e.items() if k2 != "coef"}
