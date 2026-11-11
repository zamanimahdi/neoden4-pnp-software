"""Live tests of pad alignment on a real board, for tuning its image processing (neojob.padalign).

    POST /api/align/detect?client=&x=&y=&led=&exp=&thr=&invert=&min=&max=
         camera crosshair to (x, y) if given; find the pads; -> blobs + annotated PNG (base64)
    POST /api/align/camcal?client=&x=&y=&step=1
         head camera scale and angle: pads seen before / after moving the head by `step` mm in
         X and in Y -> px per mm and the camera angle (to put into [Vision] fHeadPxPerMm and
         [PadAlign] fHeadCamAngle)
    POST /api/align/automarks?client=&job=   the Gerber's round pads of no part, looked for with the camera
    POST /api/align/pads?job=&pnp=&gerber=      build <job>.pads.json (neojob.pads)
    GET  /api/align/parts?job=                  the parts in it: designator, footprint, pads
    POST /api/align/pocket?client=&x=&y=        a feeder's tape under the head camera: pockets + holes
    POST /api/align/marks?job=&client=          find the job's fiducials (head camera, auto)
                                                -> marks, transform, residuals (kept for /part)
    POST /api/align/part?job=&ref=&client=      pad alignment of one part -> aligned, correction,
                                                matched pads, annotated views (nothing placed)
    POST /api/align/leads?client=&nozzle=&bg=1  empty nozzle over the up camera: keep the background
    POST /api/align/leads?client=&nozzle=&job=&ref=[&stack_angle=]
                                                the part held by that nozzle: lead vision against
                                                the part's pads, plus NeoDen's body outline, image
Every call that moves needs the machine control lock (client=). Settings come from Config.ini
([PadAlign], [Vision], [Job] sMarkFit) on each call, so a change there is used at once; the
parameters given in the query override them for that call only.
"""
import contextlib
import functools
import math
import os

from neocam.config import NeoConfig
from neojob import load_job
from neojob.camvision import CameraVision, HEAD
from neojob.padalign import PadAligner, detect_pads, px_to_machine, SIZE
from neojob.pads import build, load_pads, pads_path
from neojob.transform import MarkTransform
from neocam import vision as V


def _busy(f):
    """a call that moves the machine: checked like _need, then the owner keeps control for the
    whole call (some take minutes); no idle hand-over to another client meanwhile (hw.Machine.take)"""
    @functools.wraps(f)
    def w(self, q):
        self._need(q.get("client", ""))
        busy = getattr(self.mc, "busy_call", None)
        with busy() if busy else contextlib.nullcontext():
            return f(self, q)
    return w



class _Shifted:
    """a board transform moved by a panel's shift"""
    def __init__(self, tf, sh):
        self.tf, self.sh = tf, sh

    def point(self, x, y):
        X, Y, a = self.tf.point(x, y)
        return X + self.sh[0], Y + self.sh[1], a

    def __getattr__(self, k):
        return getattr(self.tf, k)


def pads_axis(pads):
    """the direction (deg, mod 180) the part's long side must lie along on these pads. On nearly every
    SMD part the pads are long ACROSS the body's long side (0603, SOT, SOIC, TSSOP): the long side runs
    across the pads' long dimension. Square / round pads: the line through 2 pads. Pads long both ways
    (QFN, QFP) or a square pattern: None"""
    import numpy as np
    if len(pads) < 2:
        return None
    hor = sum(1 for p in pads if p[2] > 1.2 * p[3])          # long along X
    ver = sum(1 for p in pads if p[3] > 1.2 * p[2])          # long along Y
    if hor + ver >= 0.6 * len(pads):
        if hor >= 0.75 * (hor + ver):
            return 90.0
        if ver >= 0.75 * (hor + ver):
            return 0.0
        return None
    if len(pads) == 2:
        d = np.array(pads[1][:2], float) - np.array(pads[0][:2], float)
        return math.degrees(math.atan2(d[1], d[0])) % 180.0
    return None


def d180(a, b):
    return (a - b + 90.0) % 180.0 - 90.0

class AlignService:
    def __init__(self, st, mc, cam, log):
        self.st, self.mc, self.cam, self.log = st, mc, cam, log
        self.tf = {}                     # job path -> MarkTransform from /marks
        self.bg = {}                     # nozzle -> empty-nozzle up-camera image (/leads?bg=1)

    def cfg(self, q=None):
        c = NeoConfig.load(os.path.join(self.st.neoden_dir, "Config.ini"))
        if q and q.get("job"):                               # the job's own head camera scale (its board)
            from neoserver.feedersetup import apply_head_scale
            apply_head_scale(c, q["job"])
        for k, key in (("led", "nLed"), ("exp", "nExposure"), ("thr", "nThreshold"), ("invert", "bInvert"),
                       ("min", "fMinPadMm2"), ("max", "fMaxPadMm2"), ("radius", "fMatchRadiusMm")):
            if q and q.get(k, "") != "":
                v = q[k]
                c.raw["PadAlign/" + key] = (v in ("1", "true")) if key.startswith("b") else float(v)
        return c

    def _need(self, client):
        if self.mc._owner() != client:
            raise PermissionError("take control first")
        if getattr(self.mc, "job_running", False):
            raise PermissionError("a job is running")

    def vis(self, cfg):
        return CameraVision(self.cam.dev, self.mc.board(), cfg, self.cam.lock, lambda t: self.log(t, "align"))

    @_busy
    def detect(self, q):
        self._need(q.get("client", ""))
        cfg = self.cfg(q)
        v = self.vis(cfg)
        mb = self.mc.board()
        cam = cfg.head_cam_offset
        if q.get("x", "") != "" and q.get("y", "") != "":
            at = (float(q["x"]), float(q["y"]))
            mb.move_xy(at[0] + cam[0], at[1] + cam[1], timeout=15)
        else:
            if mb.pos is None:
                raise RuntimeError("position unknown: home first")
            at = (mb.pos[0] - cam[0], mb.pos[1] - cam[1])
        v.head_for_pads()
        img = v.grab(HEAD)
        k = cfg.pxmm(1)
        blobs, t = detect_pads(img, k, int(cfg.padalign("nThreshold")), bool(cfg.padalign("bInvert")),
                               float(cfg.padalign("fMinPadMm2")), float(cfg.padalign("fMaxPadMm2")))
        out = []
        for b in blobs:
            b.x, b.y = px_to_machine(at, b.px, b.py, k, SIZE, float(cfg.padalign("fHeadCamAngle")))
            out.append({"x": round(b.x, 4), "y": round(b.y, 4), "px": round(b.px, 2), "py": round(b.py, 2),
                        "area_mm2": round(b.area_mm2, 4), "w": round(b.w_mm, 3), "h": round(b.h_mm, 3)})
        png = PadAligner.annotate({"at": at, "_img": img, "_blobs": blobs}, [], [], blobs, at, None, k)
        mean = float(img.mean())
        return {"at": at, "threshold": t, "mean": round(mean, 1), "blobs": out, "png": png,
                "hint": "too dark: raise exposure" if mean < 40 else "too bright: lower exposure" if mean > 200 else ""}

    @_busy
    def camcal(self, q):
        """move the head by +step in X, then in Y; follow the pads; fit px/mm and the angle"""
        self._need(q.get("client", ""))
        cfg = self.cfg(q)
        v = self.vis(cfg)
        mb = self.mc.board()
        cam = cfg.head_cam_offset
        step = float(q.get("step", 1.0) or 1.0)
        x0 = float(q["x"]) if q.get("x") else mb.pos[0] - cam[0]
        y0 = float(q["y"]) if q.get("y") else mb.pos[1] - cam[1]
        v.head_for_pads()
        k = cfg.pxmm(1)

        def look(x, y):
            mb.move_xy(x + cam[0], y + cam[1], timeout=15, slow=True)
            bl, _ = detect_pads(v.grab(HEAD), k, int(cfg.padalign("nThreshold")), bool(cfg.padalign("bInvert")),
                                float(cfg.padalign("fMinPadMm2")), float(cfg.padalign("fMaxPadMm2")))
            return [(b.px, b.py) for b in bl]
        b0 = look(x0, y0)
        bx = look(x0 + step, y0)
        by = look(x0, y0 + step)
        look(x0, y0)

        def shift(a, b, expect):
            """median pixel shift of blobs between two views (nearest neighbour around the expected shift)"""
            ds = []
            for p in a:
                q2 = min(b, key=lambda r: (r[0] - p[0] - expect[0]) ** 2 + (r[1] - p[1] - expect[1]) ** 2, default=None)
                if q2 and math.dist((q2[0] - p[0], q2[1] - p[1]), expect) < 0.3 * k * step:
                    ds.append((q2[0] - p[0], q2[1] - p[1]))
            if len(ds) < 3:
                raise RuntimeError(f"only {len(ds)} pads followed: put the camera over a group of pads")
            ds.sort(key=lambda d: d[0]); dx = ds[len(ds) // 2][0]
            ds.sort(key=lambda d: d[1]); dy = ds[len(ds) // 2][1]
            return dx, dy, len(ds)
        # head +X: the board moves -X in the image (pixels left); head +Y: the board moves down in mm = +py
        sx = shift(b0, bx, (-k * step, 0.0))
        sy = shift(b0, by, (0.0, k * step))
        kx, ky = math.hypot(sx[0], sx[1]) / step, math.hypot(sy[0], sy[1]) / step
        ax = math.degrees(math.atan2(sx[1], -sx[0]))          # image X axis against machine X
        ay = math.degrees(math.atan2(sy[0], sy[1]))
        return {"px_per_mm_x": round(kx, 3), "px_per_mm_y": round(ky, 3), "px_per_mm": round((kx + ky) / 2, 3),
                "cam_angle_deg": round(-(ax + ay) / 2, 3), "skew_deg": round(ax - ay, 3), "pads_followed": [sx[2], sy[2]],
                "now": {"fHeadPxPerMm": k, "fHeadCamAngle": float(cfg.padalign("fHeadCamAngle"))},
                "note": "write px_per_mm into [Vision] fHeadPxPerMm and cam_angle_deg into [PadAlign] fHeadCamAngle"}

    def pads(self, q):
        job, pnp, gerber = q.get("job", ""), q.get("pnp", ""), q.get("gerber", "")
        d = build(job, pnp, gerber)
        import json
        json.dump(d, open(pads_path(job), "w", encoding="utf-8"), indent=1)
        f = d["fit"]
        return {"path": pads_path(job), "parts": len(d["parts"]), "pads": sum(len(p["pads"]) for p in d["parts"].values()),
                "layer": d["gerber"], "fit": f}

    def repnp(self, q):
        """a new pick-and-place file for an existing job: fitted onto the job's part coordinates (rotation,
        shift, mirror, as the job was made with the setup wizard), then each part's new X / Y / rotation
        in the job's frame and how far it is from what the job has now"""
        from neojob.pads import read_pnp, fit_rigid, apply_fit
        job = load_job(q.get("job", ""))
        pnp = read_pnp(q.get("pnp", ""))
        raw = q.get("raw", "") == "1"                     # the file is already in the job's frame
        pairs = [(pnp[c.name][:2], (c.x, c.y)) for c in job.comps if c.name in pnp]
        if len(pairs) < 3 and not raw:
            raise ValueError(f"only {len(pairs)} designators of the job are in the file")
        f = {"theta": 0.0, "tx": 0.0, "ty": 0.0, "mirror": False, "scale": 1.0, "rms": 0.0, "n": len(pairs)}
        if not raw:
            f = fit_rigid([p[0] for p in pairs], [p[1] for p in pairs])
            for _ in range(4):                            # parts that really moved do not pull the fit
                res = [math.dist(apply_fit(f, *a), b) for a, b in pairs]
                med = sorted(res)[len(res) // 2]
                keep = [p for p, r in zip(pairs, res) if r <= max(3 * med, 0.1)]
                if len(keep) == len(pairs) or len(keep) < 3:
                    break
                pairs = keep
                f = fit_rigid([p[0] for p in pairs], [p[1] for p in pairs])
        out = []
        for c in job.comps:
            if c.name not in pnp:
                continue
            x, y, rot = pnp[c.name][:3]
            nx, ny = apply_fit(f, x, y) if not raw else (x, y)
            nr = ((-rot if f["mirror"] else rot) + f["theta"]) % 360.0
            dr = (nr - c.rot + 180.0) % 360.0 - 180.0
            out.append({"name": c.name, "x": round(nx, 3), "y": round(ny, 3), "rot": round((nr + 180.0) % 360.0 - 180.0, 2),
                        "dx": round(nx - c.x, 3), "dy": round(ny - c.y, 3), "drot": round(dr, 2)})
        missing = [c.name for c in job.comps if c.name not in pnp]
        new = [d for d in pnp if d not in {c.name for c in job.comps}]
        return {"fit": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}, "parts": out,
                "missing": missing[:50], "new": new[:50]}

    def parts(self, q):
        job = q.get("job", "")
        d = load_pads(job)
        if not d:
            return {"parts": [], "note": "no pads file: build it first (/api/align/pads)"}
        j = load_job(job)
        fp = {c.key: c.footprint or (j.stack(c.stack).footprint if j.stack(c.stack) else "") for c in j.comps}
        return {"parts": [{"ref": n, "footprint": fp.get(n, ""), "pads": len(p["pads"]), "x": p["x"], "y": p["y"],
                           "in_job": n in fp} for n, p in sorted(d["parts"].items())]}

    @_busy
    def automarks(self, q):
        """fiducials found from the Gerber: the pads file's round copper pads of no part
        (neojob.pads.fiducial_candidates) are looked for with the head camera, each where the board
        puts it (through the job's marks if they were read, else the job's frame). Returns every
        candidate with what the camera found, and `suggest`: the found ones farthest apart (2, or 3
        spanning the board), to become the job's `mark` line."""
        from neojob.jobfile import Mark
        self._need(q.get("client", ""))
        path = q.get("job", "")
        cfg = self.cfg(q)
        d = load_pads(path)
        if not d or "fiducials" not in d:
            raise KeyError("no fiducial candidates: build the pads file again (New Gerber + P&P)")
        job = load_job(path)
        mb = self.mc.board()
        v = self.vis(cfg)
        P = job.panels[0] if job.panels else None
        C = job.anchor()
        ref = (P.x, P.y) if P else C
        off = (ref[0] - C[0], ref[1] - C[1])
        tf = self.tf.get(path)
        cam = cfg.head_cam_offset
        out = []
        for x, y, dia in d["fiducials"]:
            tx, ty = tf.point(x, y)[:2] if tf else (off[0] + x, off[1] + y)
            mb.move_xy(tx + cam[0], ty + cam[1], timeout=15)
            pos = v.mark(Mark(x, y, dmin=round(0.7 * dia, 2), dmax=round(1.3 * dia, 2), led=3), None)
            out.append({"design": [x, y], "d": dia, "expected": [round(tx, 4), round(ty, 4)], "found": pos is not None,
                        "measured": [round(pos[0], 4), round(pos[1], 4)] if pos else None,
                        "off_mm": round(math.dist(pos, (tx, ty)), 3) if pos else None})
        found = [i for i, c in enumerate(out) if c["found"]]
        suggest = []
        if len(found) >= 2:
            pairs = [(math.dist(out[i]["design"], out[j]["design"]), i, j) for i in found for j in found if i < j]
            _, i, j = max(pairs)
            suggest = [i, j]
            # a third mark off the line of the first two: the one farthest from that line
            (x1, y1), (x2, y2) = out[i]["design"], out[j]["design"]
            L = math.dist((x1, y1), (x2, y2))
            far = max(((abs((x2 - x1) * (y1 - out[k]["design"][1]) - (x1 - out[k]["design"][0]) * (y2 - y1)) / L, k)
                       for k in found if k not in (i, j)), default=None)
            if far and far[0] > 0.25 * L:
                suggest.append(far[1])
        return {"candidates": out, "suggest": suggest,
                "note": "" if out else "the Gerber has no round pad without a part (the marks may be on the panel frame)"}

    @_busy
    def marks(self, q):
        self._need(q.get("client", ""))
        path = q.get("job", "")
        cfg = self.cfg(q)
        job = load_job(path)
        mb = self.mc.board()
        v = self.vis(cfg)
        tf = MarkTransform()
        tf.fit = str(cfg.job("sMarkFit") or "neoden")
        tf.set_axes(cfg.job("fAxisScaleX"), cfg.job("fAxisScaleY"), cfg.job("fAxisSkewDeg"))
        P = job.panels[0] if job.panels else None
        C = job.anchor()
        ref = (P.x, P.y) if P else C
        cam = cfg.head_cam_offset
        found = []
        if job.marks:
            off = (ref[0] - C[0], ref[1] - C[1])
            for i, m in enumerate(job.marks):
                tx, ty = off[0] + m.x, off[1] + m.y
                mb.move_xy(tx + cam[0], ty + cam[1], timeout=15)
                pos = v.mark(m, None)
                if pos is None:
                    raise RuntimeError(f"mark {i + 1} not found at {tx:.3f}, {ty:.3f}")
                found.append(pos)
            tf.set_points([(m.x, m.y) for m in job.marks], found)
        tf.set_params(job.pcb_angle, ref, ref, C, job.mark_whole, P.angle if P else 0)
        self.tf[path] = tf
        res = tf.residuals() if len(found) >= 3 and tf.fit != "neoden" else []
        return {"marks": [{"design": [m.x, m.y], "measured": [round(a, 4), round(b, 4)]} for m, (a, b) in zip(job.marks, found)],
                "fit": tf.fit, "residuals_mm": [round(r, 4) for r in res]}

    @_busy
    def pocket(self, q):
        """a feeder's pick point: the head camera at (x, y) (or where it is), the tape pockets and sprocket
        holes in its picture (neocam.tape) -> their machine X / Y (nearest pocket first) + annotated PNG"""
        import base64
        import cv2
        from neocam.tape import find_pockets, annotate
        cfg = self.cfg(q)
        v = self.vis(cfg)
        mb = self.mc.board()
        cam = cfg.head_cam_offset
        if q.get("x", "") != "" and q.get("y", "") != "":
            at = (float(q["x"]), float(q["y"]))
            mb.move_xy(at[0] + cam[0], at[1] + cam[1], timeout=15)
        else:
            if mb.pos is None:
                raise RuntimeError("position unknown: home first")
            at = (mb.pos[0] - cam[0], mb.pos[1] - cam[1])
        v.head_for_pads()
        img = v.grab(HEAD)
        k = cfg.pxmm(1)
        found = find_pockets(img, k)
        ang = float(cfg.padalign("fHeadCamAngle"))
        for f in found:
            f["x"], f["y"] = (round(c, 4) for c in px_to_machine(at, f["px"], f["py"], k, img.shape[1], ang))
        ok, png = cv2.imencode(".png", annotate(img, found, k)[::2, ::2])
        return {"at": at, "size": img.shape[1], "found": found, "png": base64.b64encode(png.tobytes()).decode()}

    # ---- where a part really lands (mount screen "Vision Align"): through the marks found by /marks ----
    def _tf_for(self, path, panel, nominal=False):
        """the marks' transform for panel `panel` (0-based) and a shift (mm) to add: with marks for the whole
        board the panel is placed through the transform; with marks per panel (Mirror; only panel 1's
        were measured) the other panels are panel 1's result moved by the panel spacing. nominal: no
        marks read yet -> the job's own panel place"""
        tf = self.tf.get(path)
        if tf is None and not nominal:
            raise PermissionError("find the board marks first (/api/align/marks)")
        job = load_job(path)
        if tf is None:
            P = job.panels[panel] if job.panels else None
            C = job.anchor()
            ref = (P.x, P.y) if P else C
            tf = MarkTransform()
            tf.set_params(job.pcb_angle, ref, ref, C, job.mark_whole, P.angle if P else 0)
            return tf, job, (0.0, 0.0)
        shift = (0.0, 0.0)
        if job.panels and 0 < panel < len(job.panels):
            P, P0 = job.panels[panel], job.panels[0]
            if tf.whole:
                import copy
                tf = copy.copy(tf)
                tf.cur = (P.x, P.y)
            else:
                shift = (P.x - P0.x, P.y - P0.y)
        return tf, job, shift

    def feedercheck_path(self, path):
        return path + ".feedercheck.json"

    @_busy
    def check_feeder(self, q):
        """one feeder of the job, with the cameras: the pocket at its pick point (the stored point off?),
        and (pick=1) one part picked, measured on the bottom camera and put back: its size against the
        expected one (a wrong reel?), the way it lies on the nozzle -> along or across its pads on the board.
        Suggestions to apply in the job editor. The result is kept in <job>.feedercheck.json"""
        import base64
        import json
        import time
        import cv2
        import numpy as np
        from neocam import vision as V
        from neocam.tape import find_pockets
        from neocalib import grab_settled
        from neojob.camvision import load_packages
        self._need(q.get("client", ""))
        path = q.get("job", "")
        cfg = self.cfg(q)
        job = load_job(path)
        sid = int(q["stack"])
        st = job.stack(sid)
        if st is None:
            raise KeyError(f"feeder {sid} is not in the job")
        mb = self.mc.board()
        v = self.vis(cfg)
        cam = cfg.head_cam_offset
        k = cfg.pxmm(1)
        x, y = st.x, st.y
        out = {"stack": sid, "value": st.value, "footprint": st.footprint, "suggest": []}
        # 1. the pocket at the pick point (tapes)
        if st.type == 0 and not getattr(st, "bulk", 0):
            mb.raise_all()
            mb.move_xy(x + cam[0], y + cam[1], timeout=20)
            v.head_for_pads()
            img = grab_settled(lambda: v.grab(HEAD), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
            ang = float(cfg.padalign("fHeadCamAngle"))
            best = None
            for f in find_pockets(img, k):
                if f["kind"] != "pocket" or f.get("round"):
                    continue
                px, py = px_to_machine((x, y), f["px"], f["py"], k, img.shape[1], ang)
                dd = math.hypot(px - x, py - y)
                if dd <= 1.5 and (best is None or dd < best[0]):
                    best = (dd, px, py, f)
            vv = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
            cv2.drawMarker(vv, (img.shape[1] // 2, img.shape[0] // 2), (0, 0, 255), cv2.MARKER_CROSS, 60, 2)
            if best:
                f = best[3]
                box = cv2.boxPoints(((f["px"], f["py"]), (f["w"] * k, f["h"] * k), -f["angle"])).astype(np.int32)
                cv2.polylines(vv, [box], True, (0, 255, 0) if f.get("full", True) else (0, 160, 255), 3)
                out["pocket"] = {"x": round(best[1], 3), "y": round(best[2], 3), "off": round(best[0], 3), "full": bool(f.get("full", True)),
                                 "w": f["w"], "h": f["h"]}
                if best[0] > 0.15:
                    out["suggest"].append({"kind": "pick_point", "x": round(best[1], 3), "y": round(best[2], 3),
                                           "text": f"the pocket is {best[0]:.2f} mm from the pick point: set the pick point on it"})
                if not f.get("full", True):
                    out["suggest"].append({"kind": "info", "text": "the pocket at the pick point is empty: feed the tape on (Feed test)"})
                x, y = best[1], best[2]
            else:
                out["suggest"].append({"kind": "info", "text": "no pocket found within 1.5 mm of the pick point: check it with the camera"})
            ok, png = cv2.imencode(".png", cv2.resize(vv, (300, 300)))
            out["pocket_png"] = base64.b64encode(png.tobytes()).decode()
        # 2. one part on the bottom camera
        if q.get("pick") == "1":
            n = int((str(st.nozzles or "1").strip() or "1")[0]) if str(st.nozzles or "").strip()[:1].isdigit() else 1
            o = cfg.nozzle_offset(n)
            z = max(0.0, 12.0 - st.pick_height)
            up, _bg, r = self.part_on_nozzle(cfg, n, x, y, z, delay=max(0.05, st.pick_delay / 1000))
            if r.valid:
                self.save_partpic(path, sid, up, r, cfg.pxmm(0))
            if not r.valid:
                out["suggest"].append({"kind": "info", "text": "nothing on the nozzle after the pick: check the pick height / the pick point"})
            else:
                L, W = max(r.w, r.h), min(r.w, r.h)
                axis0 = 0.0 if r.w >= r.h else 90.0                  # the long side at the nozzle's 0 deg
                pk = load_packages(os.path.join(self.st.neoden_dir, "Config", "pkg.csv"))
                exp = (st.part_l, st.part_w) if getattr(st, "part_l", 0) > 0 and getattr(st, "part_w", 0) > 0 else (pk.get(st.footprint) or (0, 0))[:2]
                eL, eW = (max(exp), min(exp)) if exp and exp[0] else (0.0, 0.0)
                size_ok = not eL or (abs(L - eL) <= 0.2 * eL + 0.1 and abs(W - eW) <= 0.25 * eW + 0.1)
                out["part"] = {"L": round(L, 3), "W": round(W, 3), "axis0": axis0, "expected": [eL, eW] if eL else None, "size_ok": size_ok}
                if not size_ok:
                    out["suggest"].append({"kind": "size", "L": round(L, 3), "W": round(W, 3),
                                           "text": f"the part is {L:.2f} x {W:.2f} mm, the feeder's part {eL:.2f} x {eW:.2f} ({st.footprint}): a wrong reel? "
                                                   "Load the right one, or take this size"})
                elif not eL:
                    out["suggest"].append({"kind": "size", "L": round(L, 3), "W": round(W, 3), "text": f"no size known for {st.footprint or 'this part'}: take the measured {L:.2f} x {W:.2f} mm"})
                vv = cv2.cvtColor(up, cv2.COLOR_GRAY2BGR)
                if r.rect_px:
                    box = cv2.boxPoints(((r.rect_px[0], r.rect_px[1]), (r.rect_px[2], r.rect_px[3]), r.rect_px[4])).astype(np.int32)
                    cv2.polylines(vv, [box], True, (0, 255, 0), 2)
                ok, png = cv2.imencode(".png", cv2.resize(vv[212:812, 212:812], (300, 300)))
                out["part_png"] = base64.b64encode(png.tobytes()).decode()
                # along its pads?
                orient = self._orient_stack(path, job, st, axis0)
                out["orient"] = orient
                if orient and orient["across"] and len(orient["across"]) == orient["checked"]:
                    out["suggest"].append({"kind": "pick_angle", "angle": round((st.angle + 90.0 + 180.0) % 360.0 - 180.0, 2),
                                           "text": f"all its parts would sit across their pads: Pick angle {st.angle:+.0f} -> {(st.angle + 90 + 180) % 360 - 180:+.0f}"})
                elif orient and orient["across"]:
                    out["suggest"].append({"kind": "info", "text": f"{len(orient['across'])} of {orient['checked']} parts would sit across their pads "
                                           f"({', '.join(orient['across'][:8])}): check their rotation (Check board)"})
                try:
                    p = self.feedercheck_path(path)
                    d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
                    d[str(sid)] = {"axis0": axis0, "L": round(L, 3), "W": round(W, 3), "time": time.strftime("%Y-%m-%d %H:%M")}
                    json.dump(d, open(p, "w", encoding="utf-8"), indent=1)
                except OSError:
                    pass
        mb.raise_all()
        return out

    def _orient_stack(self, path, job, st, axis0):
        """the parts of a feeder: their long side on the board (the way it lies on the nozzle + its rotation
        - the pick angle) against the line of their pads"""
        d = load_pads(path)
        if not d:
            return None
        across, checked = [], 0
        for c in job.comps:
            if c.stack != st.id or c.skip or c.key not in d["parts"]:
                continue
            pa = pads_axis(d["parts"][c.key]["pads"])
            if pa is None:
                continue
            checked += 1
            on_board = (axis0 + c.rot - st.angle) % 180.0
            if abs(d180(on_board, pa)) > 45.0:
                across.append(c.key)
        return {"checked": checked, "across": across}

    def orient(self, q):
        """every part of the job whose feeder was checked (Check feeders: the way its parts lie on the
        nozzle): along or across its pads"""
        import json
        path = q.get("job", "")
        job = load_job(path)
        p = self.feedercheck_path(path)
        fc = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        d = load_pads(path) or {"parts": {}}
        out = []
        for c in job.comps:
            st = job.stack(c.stack)
            e = fc.get(str(c.stack))
            pads = d["parts"].get(c.key, {}).get("pads")
            pa = pads_axis(pads) if pads else None
            row = {"ref": c.key, "stack": c.stack, "checked": bool(e and pa is not None)}
            if e and pa is not None and st:
                on_board = (e["axis0"] + c.rot - st.angle) % 180.0
                row.update(pads_axis=round(pa, 1), part_axis=round(on_board, 1), across=abs(d180(on_board, pa)) > 45.0)
            out.append(row)
        return {"parts": out, "feeders_checked": sorted(int(s) for s in fc)}

    @_busy
    def circle(self, q):
        """the head camera centred on the round mark (fiducial) nearest the picture's middle: found,
        the camera moved onto its centre, found again (up to 3 times). Returns the centre (nozzle-1
        frame, as the marks), its diameter and the picture"""
        import base64
        import cv2
        from neocam.vision import find_mark_best
        from neocalib import grab_settled
        self._need(q.get("client", ""))
        cfg = self.cfg(q)
        v = self.vis(cfg)
        mb = self.mc.board()
        cam = cfg.head_cam_offset
        if mb.pos is None:
            raise RuntimeError("position unknown: home first")
        at = (mb.pos[0] - cam[0], mb.pos[1] - cam[1])
        dmin, dmax = float(q.get("dmin", 0.5) or 0.5), float(q.get("dmax", 3.5) or 3.5)
        k = cfg.pxmm(1)
        ang = float(cfg.padalign("fHeadCamAngle"))
        mb.head_led(3)
        r = img = None
        for _ in range(3):
            img = grab_settled(lambda: v.grab(HEAD), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
            r = find_mark_best(img, dmin, dmax, k, min_dia_mm=dmin)
            if r.precision <= 0:
                raise RuntimeError(f"no round mark ({dmin:g} .. {dmax:g} mm) in the picture: bring the camera nearer to it")
            x, y = px_to_machine(at, r.x, r.y, k, img.shape[1], ang)
            moved = math.hypot(x - at[0], y - at[1])
            at = (x, y)
            mb.move_xy(x + cam[0], y + cam[1], timeout=15)
            if moved < 0.01:
                break
        v2 = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
        cv2.circle(v2, (int(r.x), int(r.y)), int(r.r), (0, 255, 0), 2)
        ok, png = cv2.imencode(".png", cv2.resize(v2, (300, 300)))
        return {"x": round(at[0], 4), "y": round(at[1], 4), "d": round(2 * r.r / k, 3), "q": round(r.precision, 3),
                "png": base64.b64encode(png.tobytes()).decode()}

    def part_on_nozzle(self, cfg, n, x, y, z, delay=0.15, reach=5.0):
        """nozzle n picks the part at machine (x, y) (pick depth z), shows it to the bottom camera at nozzle
        angle 0 and puts it back. Bottom light on, head lamp off (it dazzles); the empty nozzle first as the
        background; only `reach` mm around the nozzle (a light change or dirt further out reads as a big
        part); lowering waits while XY still reports busy. Returns (picture, background, PartResult)"""
        import time
        from neocam import vision as V
        from neocalib import grab_settled
        mb = self.mc.board()
        o = cfg.nozzle_offset(n)
        v = self.vis(cfg)

        def down(depth):
            t0 = time.monotonic()
            while True:
                try:
                    return mb.set_z(n, depth, 40, wait=True)
                except Exception as e:
                    if "busy" not in str(e) or time.monotonic() - t0 > 3.0:
                        raise
                    time.sleep(0.1)

        def shot():
            mb.head_led(0)
            mb.up_light(True)
            v.setup(0, cfg.up_exposure(), 1024)
            time.sleep(0.3)                          # the light and the exposure settled
            return grab_settled(lambda: v.grab(0), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame

        mb.raise_all()
        mb.rotate(n, 0.0, wait=True)
        mb.move_xy(*cfg.nozzle_positions[n - 1], timeout=20)
        bg = shot()
        mb.move_xy(x + o[0], y + o[1], timeout=20)
        mb.valve(n, -1)
        time.sleep(0.2)
        import numpy as np
        open_air = [mb.vacuum(n) for _ in range(5)] if hasattr(mb, "vacuum") else []
        down(z)
        # held at the bottom until the suction has really sealed on the part (two readings well below the open
        # air), at least `delay`, at most 1 s: a fixed short wait lifted parts on their side
        t0, self.last_seal_s = time.monotonic(), None
        if open_air:
            base, drop = float(np.median(open_air)), max(8.0, 4.0 * float(np.std(open_air)))
            ok = 0
            while time.monotonic() - t0 < 1.0:
                ok = ok + 1 if float(np.median([mb.vacuum(n) for _ in range(3)])) <= base - drop else 0
                if ok >= 2 and time.monotonic() - t0 >= delay:
                    break
                time.sleep(0.03)
            if ok >= 2:
                self.last_seal_s = round(time.monotonic() - t0, 2)
        else:
            time.sleep(delay)
        mb.set_z(n, 0.0, 20, wait=True)                      # up slowly: the part settles on the tip
        try:
            mb.move_xy(*cfg.nozzle_positions[n - 1], timeout=20)
            img = shot()
            k = cfg.pxmm(0)
            p = int(min(500, max(40, reach * k)))
            r = V.analyze_nozzle(img, bg, p, k)
            if r.valid and r.rect_px is not None:
                cx, cy, w, h = r.rect_px[:4]
                half = max(w, h) / 2
                if abs(cx - 512) + half > p - 2 or abs(cy - 512) + half > p - 2:
                    r = V.PartResult(False)              # touches the edge of the search: not a part
        finally:
            mb.up_light(False)
            mb.move_xy(x + o[0], y + o[1], timeout=20)          # back where it came from
            down(max(0.0, z - 0.2))
            mb.valve(n, 1)
            time.sleep(0.1)
            mb.valve(n, 0)
            mb.set_z(n, 0.0, 40, wait=True)
        return img, bg, r

    def partpic_path(self, path, sid):
        return os.path.join(path + ".parts", f"{int(sid)}.png")

    def save_partpic(self, path, sid, img, r, k, half_mm=6.0):
        """a feeder's part as the bottom camera saw it on the nozzle (nozzle angle 0, the nozzle at the
        centre, +X right / +Y up), +-half_mm around the nozzle, for the Setup view; its size beside it"""
        import json
        import time
        import cv2
        try:
            os.makedirs(path + ".parts", exist_ok=True)
            hp = int(half_mm * k)
            c = img[512 - hp:512 + hp, 512 - hp:512 + hp]
            cv2.imwrite(self.partpic_path(path, sid), c)
            meta = {"k": k, "half_mm": half_mm, "L": round(max(r.w, r.h), 3), "W": round(min(r.w, r.h), 3),
                    "angle": round(float(r.angle), 2), "dx": round(r.x, 3), "dy": round(r.y, 3), "time": time.strftime("%Y-%m-%d %H:%M")}
            json.dump(meta, open(self.partpic_path(path, sid)[:-4] + ".json", "w", encoding="utf-8"))
        except OSError:
            pass

    def setupview(self, q):
        """the parts of one board (the first copy of each designator: one board of a panel) with all that
        decides how they land: rotation, the feeder's Pick angle, the bottom camera's angle -> the turn the
        nozzle makes; the part's Gerber pads around its centre; the feeder's part picture if one was taken"""
        import json
        from neojob.camvision import load_packages
        path = q.get("job", "")
        job = load_job(path)
        cfg = self.cfg(q)
        d = load_pads(path) or {"parts": {}}
        pk = load_packages(os.path.join(self.st.neoden_dir, "Config", "pkg.csv"))
        feeders, parts = {}, []
        for c in job.comps:
            if "#" in c.key or c.skip:
                continue
            st = job.stack(c.stack)
            if st is None:
                continue
            if c.stack not in feeders:
                meta = None
                mp = self.partpic_path(path, c.stack)[:-4] + ".json"
                if os.path.exists(mp):
                    try:
                        meta = json.load(open(mp, encoding="utf-8"))
                    except ValueError:
                        meta = None
                fp = st.footprint or c.footprint or (st.value.split("/")[0] if "/" in (st.value or "") else "")
                size = (st.part_l, st.part_w) if getattr(st, "part_l", 0) > 0 and getattr(st, "part_w", 0) > 0 else tuple((pk.get(fp) or (0, 0))[:2])
                feeders[c.stack] = {"id": c.stack, "value": st.value, "footprint": st.footprint, "pick_angle": st.angle,
                                    "bulk": int(getattr(st, "bulk", 0) or 0), "nozzles": st.nozzles, "x": st.x, "y": st.y,
                                    "pick_z": round(max(0.0, 12.0 - st.pick_height), 2),
                                    "size": [round(float(v), 3) for v in size] if size and size[0] else None, "pic": meta}
            pd = d["parts"].get(c.key) or {}
            pads = pd.get("pads") or []
            shapes = pd.get("shapes") or []
            turn = (c.rot - st.angle + cfg.cam_angle + 180.0) % 360.0 - 180.0
            parts.append({"key": c.key, "value": c.value, "footprint": c.footprint or st.footprint, "stack": c.stack,
                          "x": c.x, "y": c.y, "rot": c.rot, "turn": round(turn, 2),
                          "pads": [[round(px - c.x, 4), round(py - c.y, 4), w, h] for px, py, w, h in pads],
                          "shapes": shapes if len(shapes) == len(pads) else []})
        return {"cam_angle": cfg.cam_angle, "feeders": feeders, "parts": parts, "pads_file": bool(d["parts"])}

    @_busy
    def measure_part(self, q):
        """a part's size from the part itself: picked from the feeder (x, y = its pick point, z = the
        pick depth), held over the bottom camera, measured (its outline), put back in its pocket.
        Returns L / W (mm) and the picture; the job editor keeps the size in that feeder (this job only)"""
        import base64
        import time
        import cv2
        import numpy as np
        from neocam import vision as V
        from neocalib import grab_settled
        cfg = self.cfg(q)
        n = int(q.get("nozzle", 1) or 1)
        img, bg, r = self.part_on_nozzle(cfg, n, float(q["x"]), float(q["y"]), float(q["z"]), reach=float(q.get("reach", 5.0) or 5.0))
        if r.valid and q.get("job") and q.get("stack", "") != "":
            self.save_partpic(q["job"], int(q["stack"]), img, r, cfg.pxmm(0))
        if not r.valid:
            diff = cv2.subtract(img, bg)
            raise RuntimeError(f"nothing measured on nozzle {n}: not picked (pick height?), or too dark "
                               f"(picture grey {float(np.mean(img)):.0f}, part brighter than the empty nozzle "
                               f"by up to {float(np.percentile(diff, 99.9)):.0f}; 50 needed)")
        L, W = max(r.w, r.h), min(r.w, r.h)
        vv = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        rect = getattr(r, "rect_px", None)
        if rect is not None and len(rect) == 5:
            box = cv2.boxPoints(((rect[0], rect[1]), (rect[2], rect[3]), rect[4])).astype(np.int32)
            cv2.polylines(vv, [box], True, (0, 255, 0), 2)
        ok, png = cv2.imencode(".png", cv2.resize(vv[212:812, 212:812], (300, 300)))
        return {"L": round(L, 3), "W": round(W, 3), "angle": round(float(r.angle), 2), "png": base64.b64encode(png.tobytes()).decode()}

    def board_view(self, path, x, y, size, k, margin=2.0):
        """the job's Gerber pads seen by the head camera centred on machine (x, y) (nozzle-1 frame), as
        polygons in picture px (+X right, +Y up). The board's transform: the measured marks, else the
        job's own panel positions. None when the picture is not over the board (or no pads file)"""
        from neojob.transform import MarkTransform
        if not path:
            return None
        # asked twice a second by Feeder settings: the pads file (~160 kB of JSON) and the job read again
        # only when one of them changed
        try:
            key = (path, os.path.getmtime(path), os.path.getmtime(path + ".pads.json"))
        except OSError:
            return None
        c = getattr(self, "_bv_cache", None)
        if not c or c[0] != key:
            d = load_pads(path)
            if not d or not d.get("parts"):
                return None
            pads = [p for part in d["parts"].values() for p in part["pads"]]
            xs, ys = [p[0] for p in pads], [p[1] for p in pads]
            self._bv_cache = c = (key, d, load_job(path), pads, (min(xs), min(ys), max(xs), max(ys)))
        _, d, job, pads, ext = c
        half = size / 2.0 / k
        box = (ext[0] - margin, ext[1] - margin, ext[2] + margin, ext[3] + margin)
        for panel in range(max(1, len(job.panels))):
            try:
                tf, _, sh = self._tf_for(path, panel)
            except PermissionError:                         # no marks measured: the job's nominal place
                P = job.panels[panel] if job.panels else None
                C = job.anchor()
                ref = (P.x, P.y) if P else C
                tf, sh = MarkTransform(), (0.0, 0.0)
                tf.set_params(job.pcb_angle, ref, ref, C, job.mark_whole, P.angle if P else 0)
            pt = lambda a, b: (lambda r: (r[0] + sh[0], r[1] + sh[1]))(tf.point(a, b))
            # the design point under the camera (Newton steps on the transform)
            dx, dy = x - sh[0], y - sh[1]
            u, v = dx, dy
            for _ in range(8):
                X, Y = pt(u, v)
                ex, ey = x - X, y - Y
                if math.hypot(ex, ey) < 1e-5:
                    break
                a11 = pt(u + 0.01, v)[0] - X; a21 = pt(u + 0.01, v)[1] - Y
                a12 = pt(u, v + 0.01)[0] - X; a22 = pt(u, v + 0.01)[1] - Y
                det = (a11 * a22 - a12 * a21) / 1e-4
                u += (a22 * ex - a12 * ey) / 0.01 / det
                v += (-a21 * ex + a11 * ey) / 0.01 / det
            if not (box[0] <= u <= box[2] and box[1] <= v <= box[3]):
                continue
            out = []
            for px, py, w, h in pads:
                if abs(px - u) > half + 3 or abs(py - v) > half + 3:
                    continue
                c4 = [(px - w / 2, py - h / 2), (px + w / 2, py - h / 2), (px + w / 2, py + h / 2), (px - w / 2, py + h / 2)]
                poly = []
                for cx, cy in c4:
                    X, Y = pt(cx, cy)
                    poly.append([round(size / 2 + (X - x) * k, 1), round(size / 2 - (Y - y) * k, 1)])
                out.append(poly)
            return {"panel": panel + 1, "pads": out, "design": [round(u, 3), round(v, 3)]}
        return None

    def point(self, q):
        """design X, Y (or ref=) -> machine X, Y of nozzle 1 where it places, the angle correction, and the
        part's Gerber pads + their outline in machine coordinates (to draw over the head camera)"""
        path = q.get("job", "")
        tf, job, sh = self._tf_for(path, int(q.get("panel", 0) or 0), q.get("nominal") == "1")
        pt = lambda x_, y_: (lambda r: (r[0] + sh[0], r[1] + sh[1], r[2]))(tf.point(x_, y_))
        ref = q.get("ref", "")
        comp = (next((c for c in job.comps if c.key == ref), None) or next((c for c in job.comps if c.name == ref), None)) if ref else None
        x = float(q["x"]) if q.get("x", "") != "" else comp.x
        y = float(q["y"]) if q.get("y", "") != "" else comp.y
        X, Y, A = pt(x, y)
        out = {"x": round(X, 4), "y": round(Y, 4), "angle": round(A, 4), "design": [x, y], "fit": tf.fit, "pads": [], "outline": None,
               "panel": int(q.get("panel", 0) or 0)}
        d = load_pads(path) if ref else None
        # drawn at the solder-mask opening (copper + 0.1 mm per side on this board): the camera sees the
        # tinned copper, which spreads up to the mask edge
        g = float(q.get("grow", 0.1) or 0.0)
        if d and ref in d["parts"]:
            corners = []
            for px, py, w, h in d["parts"][ref]["pads"]:
                w, h = w + 2 * g, h + 2 * g
                c4 = [(px - w / 2, py - h / 2), (px + w / 2, py - h / 2), (px + w / 2, py + h / 2), (px - w / 2, py + h / 2)]
                corners += c4
                out["pads"].append([[round(v, 4) for v in pt(cx, cy)[:2]] for cx, cy in c4])
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            box = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
            out["outline"] = [[round(v, 4) for v in pt(cx, cy)[:2]] for cx, cy in box]
        return out

    def design(self, q):
        """machine X, Y of nozzle 1 -> design X, Y (the inverse of point(), by Newton steps): to write a
        part's corrected position back into the job"""
        measured = q.get("job", "") in self.tf
        tf, _, sh = self._tf_for(q.get("job", ""), int(q.get("panel", 0) or 0), q.get("nominal") == "1")
        mx, my = float(q["x"]) - sh[0], float(q["y"]) - sh[1]
        x, y = float(q.get("x0", mx) or mx), float(q.get("y0", my) or my)
        for _ in range(8):
            X, Y, _a = tf.point(x, y)
            ex, ey = mx - X, my - Y
            if math.hypot(ex, ey) < 1e-6:
                break
            h = 0.01
            a11 = (tf.point(x + h, y)[0] - X) / h; a21 = (tf.point(x + h, y)[1] - Y) / h
            a12 = (tf.point(x, y + h)[0] - X) / h; a22 = (tf.point(x, y + h)[1] - Y) / h
            det = a11 * a22 - a12 * a21
            x += (a22 * ex - a12 * ey) / det
            y += (-a21 * ex + a11 * ey) / det
        return {"x": round(float(x), 4), "y": round(float(y), 4), "measured": measured}

    @_busy
    def part(self, q):
        self._need(q.get("client", ""))
        path, ref = q.get("job", ""), q.get("ref", "")
        cfg = self.cfg(q)
        d = load_pads(path)
        if not d or ref not in d["parts"]:
            raise KeyError(f"{ref}: no pads (build the pads file first)")
        if self.tf.get(path) is None:
            raise RuntimeError("find the marks first (/api/align/marks)")
        tf0, job, sh = self._tf_for(path, int(q.get("panel", 0) or 0))
        tf = _Shifted(tf0, sh)                                   # that panel
        comp = (next((c for c in job.comps if c.key == ref), None) or next((c for c in job.comps if c.name == ref), None))
        centre = (comp.x, comp.y) if comp else (d["parts"][ref]["x"], d["parts"][ref]["y"])
        v = self.vis(cfg)
        from neojob.padalign import on_hole
        r = v.pad_align(d["parts"][ref]["pads"], centre, tf, None, images=True,
                        hole=on_hole(cfg, ref, (comp.footprint if comp else "") or "") or bool(d["parts"][ref].get("hole")))
        out = r.as_dict()
        out["aligned"] = out.pop("ok")          # "ok" is the HTTP reply's own field
        out["placement_without"] = [round(c, 4) for c in tf.point(*centre)]
        out["limits"] = {"fMaxShiftMm": cfg.padalign("fMaxShiftMm"), "fMaxTurnDeg": cfg.padalign("fMaxTurnDeg")}
        out["within_limits"] = math.hypot(r.dx, r.dy) <= float(cfg.padalign("fMaxShiftMm")) and abs(r.dtheta) <= float(cfg.padalign("fMaxTurnDeg"))
        return out

    @_busy
    def leads(self, q):
        """lead vision on a part held by the nozzle (pick it on the Manual page first)"""
        from neojob import leadvision as LV
        from neojob.camvision import UP
        self._need(q.get("client", ""))
        cfg = self.cfg(q)
        for k, key in (("thr", "nThreshold"), ("radius", "fMatchRadiusMm")):
            if q.get(k, "") != "":
                cfg.raw["LeadVision/" + key] = float(q[k])
        n = int(q.get("nozzle", 1) or 1)
        v = self.vis(cfg)
        mb = self.mc.board()
        exp = int(q.get("exp", 0) or 0) or int(cfg.leadvision("nExposure")) or cfg.up_exposure()
        v.setup(UP, exp, 1024)
        mb.up_light(True)
        mb.move_xy(*cfg.nozzle_positions[n - 1], timeout=20)
        img = v.grab(UP)
        k = cfg.pxmm(0)
        if q.get("bg") == "1":
            self.bg[n] = img
            return {"nozzle": n, "background": "kept", "mean": round(float(img.mean()), 1)}
        bg = self.bg.get(n)
        if bg is None:
            raise RuntimeError(f"take the empty-nozzle background first (bg=1, nozzle {n})")
        path, ref = q.get("job", ""), q.get("ref", "")
        d = load_pads(path)
        if not d or ref not in d["parts"]:
            raise KeyError(f"{ref}: no pads (build the pads file first)")
        job = load_job(path)
        comp = (next((c for c in job.comps if c.key == ref), None) or next((c for c in job.comps if c.name == ref), None))
        st = job.stack(comp.stack) if comp else None
        stack_angle = float(q["stack_angle"]) if q.get("stack_angle", "") != "" else (st.angle if st else 0.0)
        leads, _ = LV.local_leads(d["parts"][ref]["pads"], comp.rot if comp else 0.0)
        ext = max(max(abs(x), abs(y)) for x, y, _ in leads) + 1.0
        roi = max(100, min(500, int(ext * k)))
        r = LV.analyze_leads(img, bg, roi, k, leads, stack_angle, cfg)
        body = V.analyze_nozzle(img, bg, roi, k)
        out = r.as_dict()
        out.update(png=LV.annotate(img, r, k), stack_angle=stack_angle, exposure=exp,
                   body={"valid": body.valid, "x": body.x, "y": body.y, "angle": body.angle, "w": body.w, "h": body.h},
                   leads_vs_body_mm=round(math.hypot(r.x - body.x, r.y - body.y), 4) if r.valid and body.valid else None)
        return out

