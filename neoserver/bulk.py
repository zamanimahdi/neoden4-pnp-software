"""Bulk feeders on the web UI (neojob.bulkfeed): the containers, their scans, teaching a package from one
part, looking at one part.

    GET  /api/bulk/list                         containers + template names
    POST /api/bulk/save?client=  {container}    add / change one (id 1 .. 30)
    POST /api/bulk/delete?client=&id=
    POST /api/bulk/scan?client=&id=             the container's area scanned: parts found, the mosaic
    GET  /api/bulk/mosaic?id=                   the last scan's picture (PNG)
    POST /api/bulk/look?client=&id=&x=&y=       one part, centred: what it is, which way round (+ PNG)
    GET  /api/bulk/grid                         the containers' layout: a matrix of cols x rows blocks
    POST /api/bulk/grid?client=  {grid}         saved, and the containers 1 .. cols x rows made from it
                                                (row by row from block 1; their other settings kept)
    POST /api/bulk/confirm?client=&id=&count=   the operator saw the scan: that many parts are right
    POST /api/bulk/measure?client=&id=&x=&y=      the pick height read once on the part there (the nozzle
                                                down by the pressure sensor), kept on the container
    POST /api/bulk/trial?client=&id=&max=        every part of the last scan picked, its underside, back
                                                (the times of each step, the pictures in bulk_trial/)
    POST /api/bulk/teach?client=&id=&x=&y=&name=&nozzle=1
                                                the part there taught: top, picked by the vacuum,
                                                its underside, put back; the container uses it
Every call that moves needs the machine control lock (client=), and no job running.
"""
import base64
import contextlib
import functools
import math
import os

import cv2

from neocam.config import NeoConfig
from neocam import bulk as BK
from neojob.bulkfeed import BulkFeeder, bottom_result, MAX
from neojob.camvision import CameraVision


def _busy(f):
    @functools.wraps(f)
    def w(self, q):
        self._need(q.get("client", ""))
        busy = getattr(self.mc, "busy_call", None)
        with busy() if busy else contextlib.nullcontext():
            return f(self, q)
    return w


def _png(img):
    ok, b = cv2.imencode(".png", img)
    return base64.b64encode(b.tobytes()).decode()


class BulkService:
    def __init__(self, st, mc, cam, log):
        self.st, self.mc, self.cam, self.log = st, mc, cam, log

    def cfg(self):
        return NeoConfig.load(os.path.join(self.st.neoden_dir, "Config.ini"))

    def _need(self, client):
        if self.mc._owner() != client:
            raise PermissionError("take control first")
        if getattr(self.mc, "job_running", False):
            raise PermissionError("a job is running")

    def feeder(self):
        cfg = self.cfg()
        mb = self.mc.board()
        vis = CameraVision(self.cam.dev, mb, cfg, self.cam.lock, lambda t: self.log(t, "bulk"))
        return BulkFeeder(self.st.neoden_dir, vis, mb, cfg, lambda t: self.log(t, "bulk"))

    def _quiet(self):
        """a BulkFeeder for reading only (no camera, no machine)"""
        return BulkFeeder(self.st.neoden_dir, None, None, None, lambda t: None)

    # ---- the layout: one matrix of blocks ----
    def grid_path(self):
        return os.path.join(self.st.neoden_dir, "bulk_grid.json")

    def grid(self, q=None):
        import json
        p = self.grid_path()
        g = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
        return {"grid": g}

    def set_grid(self, q, body):
        """grid: cols, rows, x0 / y0 (block 1's lower-left corner, head camera), w / h (a block's inside),
        px / py (from one block to the next); blocks are numbered row by row from block 1"""
        import json
        self._need(q.get("client", ""))
        g = {k: float((body or {}).get(k, 0) or 0) for k in ("x0", "y0", "w", "h", "px", "py")}
        g["cols"], g["rows"] = int((body or {}).get("cols", 1) or 1), int((body or {}).get("rows", 1) or 1)
        if g["cols"] < 1 or g["rows"] < 1 or g["cols"] * g["rows"] > MAX:
            raise ValueError(f"1 .. {MAX} blocks")
        if g["w"] <= 0 or g["h"] <= 0:
            raise ValueError("a block's size")
        if (g["cols"] > 1 and g["px"] < g["w"]) or (g["rows"] > 1 and g["py"] < g["h"]):
            raise ValueError("the step from one block to the next must be at least the block's size")
        json.dump(g, open(self.grid_path(), "w", encoding="utf-8"), indent=1)
        bf = self._quiet()
        old = {int(c["id"]): c for c in bf.containers()}
        cs = []
        for r in range(g["rows"]):
            for c in range(g["cols"]):
                cid = r * g["cols"] + c + 1
                x0, y0 = g["x0"] + c * g["px"], g["y0"] + r * g["py"]
                d = dict(old.get(cid, {"id": cid, "name": f"Bulk {cid}", "pin1": True, "exp_bottom": 300, "nozzle": 1}))
                moved = any(abs(float(d.get(k, -1e9)) - v) > 0.05 for k, v in (("x0", x0), ("y0", y0), ("x1", x0 + g["w"]), ("y1", y0 + g["h"])))
                d.update(id=cid, row=r + 1, col=c + 1, x0=round(x0, 3), y0=round(y0, 3), x1=round(x0 + g["w"], 3), y1=round(y0 + g["h"], 3))
                if moved:                                    # a new place: the last scan is not there any more
                    d.pop("parts", None); d.pop("count", None); d.pop("scanned", None); d.pop("confirmed", None)
                cs.append(d)
        json.dump(cs, open(bf.path, "w", encoding="utf-8"), indent=1)
        return {"grid": g, "containers": len(cs)}

    def confirm(self, q):
        """the operator checked the scan: the count seen is right"""
        import time
        self._need(q.get("client", ""))
        bf = self._quiet()
        cid = int(q.get("id", 0))
        bf.update(cid, confirmed={"count": int(q.get("count", 0)), "time": time.strftime("%Y-%m-%d %H:%M")})
        return {}

    # ---- reading ----
    def list(self, q):
        bf = self._quiet()
        cs = bf.containers()
        import json
        for c in cs:                                   # where the last scan's picture lies (to click a part)
            p = os.path.join(self.st.neoden_dir, f"bulk_{int(c['id'])}.json")
            if os.path.exists(p):
                try:
                    c["mosaic"] = json.load(open(p, encoding="utf-8"))
                except ValueError:
                    pass
        return {"containers": cs, "templates": bf.templates(), "max": MAX, **self.grid()}

    def mosaic(self, cid):
        p = os.path.join(self.st.neoden_dir, f"bulk_{int(cid)}.png")
        return open(p, "rb").read() if os.path.exists(p) else None

    # ---- changing ----
    def save(self, q, body):
        self._need(q.get("client", ""))
        c = dict(body or {})
        cid = int(c.get("id", 0))
        bf = self._quiet()
        old = bf.container(cid) or {}
        old.update({k: v for k, v in c.items() if k in ("id", "name", "x0", "y0", "x1", "y1", "package", "pin1", "exp_bottom", "nozzle", "z_pick", "dir_source", "on_fail")})
        bf.save_container(old)
        return {"container": old}

    def delete(self, q):
        self._need(q.get("client", ""))
        bf = self._quiet()
        cs = [c for c in bf.containers() if int(c["id"]) != int(q.get("id", 0))]
        import json
        json.dump(cs, open(bf.path, "w", encoding="utf-8"), indent=1)
        return {}

    @_busy
    def scan(self, q):
        bf = self.feeder()
        found = bf.scan(int(q["id"]))
        bf.mb.head_led(0)
        return {"parts": found, "count": len(found)}

    @_busy
    def look(self, q):
        """one part, centred: the analysis, the match against the container's package, a drawn picture"""
        bf = self.feeder()
        cid = int(q.get("id", 0))
        x, y = float(q["x"]), float(q["y"])
        k = bf.cfg.pxmm(1)
        p, (px, py), img = bf.look(x, y)
        c = bf.container(cid) or {}
        top, _ = bf.template(c.get("package"))
        out = {"x": round(px, 3), "y": round(py, 3), "ok": bool(p.ok and p.cx), "why": p.why, "signature": BK.signature(p) if p.cx else None}
        cap = None
        if p.cx and top:
            t, dist, turn, conf = BK.match(p, [top], use_pin1=bool(c.get("pin1", True)))
            phi = (p.angle - turn - float(top.get("angle", 0.0)) + 180) % 360 - 180
            out.update(package=c.get("package"), distance=dist, is_it=dist <= 1.3, turn=phi, sure=conf, by=getattr(p, "how", ""))
            cap = f"{c.get('package')}: {'yes' if dist <= 1.3 else 'NO'} ({dist:.2f}), turned {phi:+.1f} deg by {getattr(p, 'how', '')}"
        out["png"] = _png(cv2.resize(BK.annotate_top(img, p, k, cap), (640, 640), interpolation=cv2.INTER_AREA))
        bf.mb.head_led(0)
        return out

    @_busy
    def trial(self, q):
        """every part of the last scan: looked at (top), picked by the vacuum, its underside, put back;
        the time of each step, the machine's own processing time apart from the moves, and the drawn
        pictures in <data>/bulk_trial/ (for checking)"""
        import json
        import time
        bf = self.feeder()
        cid = int(q.get("id", 0))
        c = bf.container(cid) or {}
        n = int(c.get("nozzle", 1) or 1)
        exp = int(c.get("exp_bottom", 300) or 300)
        k, ku = bf.cfg.pxmm(1), bf.cfg.pxmm(0)
        top_t, bot_t = bf.template(c.get("package"))
        out_dir = os.path.join(self.st.neoden_dir, "bulk_trial")
        os.makedirs(out_dir, exist_ok=True)
        o = bf.cfg.nozzle_offset(n)
        res = []
        limit = int(q.get("max", 0) or 0)
        for i, f in enumerate(c.get("parts") or []):
            if limit and i >= limit:
                break
            r = {"i": i, "x": f["x"], "y": f["y"]}
            t0 = time.monotonic()
            p, (px, py), img = bf.look(f["x"], f["y"])
            r["look_s"] = round(time.monotonic() - t0, 2)
            t1 = time.monotonic()
            p2 = BK.analyze(img, k)
            if top_t is not None and p2.cx:
                BK.match(p2, [top_t], use_pin1=bool(c.get("pin1", True)))
            r["top_cpu_ms"] = round((time.monotonic() - t1) * 1000)
            r["top"] = BK.signature(p) if p.cx else None
            cv2.imwrite(os.path.join(out_dir, f"{i:02d}_top.png"), cv2.resize(BK.annotate_top(img, p, k, f"#{i}"), (512, 512), interpolation=cv2.INTER_AREA))
            if not p.cx or p.touching or not p.ok:
                r["skip"] = p.why or "no part"
                res.append(r)
                continue
            t2 = time.monotonic()
            seal = bf.pick_z(c)
            if seal is None:
                raise ValueError("no pick height on the feeder: set it, or Measure first")
            if not bf.pick_at(n, px + o[0], py + o[1], seal):
                seal = None
            r["pick_s"] = round(time.monotonic() - t2, 2)
            if seal is None:
                r["skip"] = "not picked"
                res.append(r)
                continue
            r["seal_z"] = seal
            t3 = time.monotonic()
            try:
                up = bf.bottom_picture(n, exp)
                r["to_camera_s"] = round(time.monotonic() - t3, 2)
                t4 = time.monotonic()
                b = BK.analyze_bottom(up, ku)
                m = BK.match_bottom(b, bot_t) if (bot_t and b.ok) else None
                r["bottom_cpu_ms"] = round((time.monotonic() - t4) * 1000)
                r["bottom_metal"] = len(b.blobs)
                if m:
                    r["bottom_missing"] = m["missing"]
                cv2.imwrite(os.path.join(out_dir, f"{i:02d}_bottom.png"), cv2.resize(BK.annotate_bottom(up, b, ku, f"#{i}: {len(b.blobs)} metal")[262:762, 262:762], (400, 400)))
            finally:
                bf.mb.up_light(False)
                t5 = time.monotonic()
                bf.put_back(n, px + o[0], py + o[1], seal)
                r["put_back_s"] = round(time.monotonic() - t5, 2)
            r["total_s"] = round(time.monotonic() - t0, 2)
            res.append(r)
            json.dump(res, open(os.path.join(out_dir, "trial.json"), "w"), indent=1)
        bf.mb.head_led(0)
        bf.mb.raise_all()
        return {"parts": res}

    @_busy
    def measure(self, q):
        """the pick height read once on the part at x, y: the nozzle down in 0.1 mm steps until the
        pressure falls, the part put back; kept on the container (the job picks there directly)"""
        bf = self.feeder()
        cid = int(q.get("id", 0))
        c = bf.container(cid) or {"id": cid}
        n = int(c.get("nozzle", 1) or 1)
        p, (px, py), _ = bf.look(float(q["x"]), float(q["y"]))
        if not p.cx:
            raise ValueError("no part there")
        o = bf.cfg.nozzle_offset(n)
        z = bf.pick_by_feel(n, px + o[0], py + o[1])
        if z is None:
            raise RuntimeError("the nozzle did not meet the part (no pressure drop down to 13 mm)")
        bf.put_back(n, px + o[0], py + o[1], z)
        bf.update(cid, z_pick=round(z, 2))
        bf.mb.head_led(0)
        return {"z_pick": round(z, 2)}

    @_busy
    def teach(self, q):
        """the part at x, y (it lies the right way round): its top, then picked by the vacuum, its
        underside at the container's exposure, put back; saved as the package `name`, the container
        set to it and to the depth the nozzle met it"""
        bf = self.feeder()
        cid = int(q.get("id", 0))
        name = (q.get("name") or "").strip().replace("/", "-")
        if not name:
            raise ValueError("a name for the package")
        n = int(q.get("nozzle", 0) or 0) or int((bf.container(cid) or {}).get("nozzle", 1) or 1)
        c = bf.container(cid) or {"id": cid}
        exp = int(c.get("exp_bottom", 300) or 300)
        k, ku = bf.cfg.pxmm(1), bf.cfg.pxmm(0)
        p, (px, py), img = bf.look(float(q["x"]), float(q["y"]))
        if not p.cx:
            raise ValueError("no part there")
        top = BK.teach(p, name)
        o = bf.cfg.nozzle_offset(n) if hasattr(bf.cfg, "nozzle_offset") else (0.0, 0.0)
        seal = bf.pick_z(c)
        if seal is None:
            seal = bf.pick_by_feel(n, px + o[0], py + o[1])
            if seal is None:
                raise RuntimeError("the nozzle did not get the part (no pressure drop down to 13 mm)")
        elif not bf.pick_at(n, px + o[0], py + o[1], seal):
            raise RuntimeError(f"the nozzle did not get the part at {seal:.2f} mm (check the pick height)")
        try:
            up = bf.bottom_picture(n, exp)
            b = BK.analyze_bottom(up, ku)
            if not b.ok:
                raise RuntimeError(f"the underside: {b.why}")
            bottom = BK.teach_bottom(b, name)
        finally:
            bf.mb.up_light(False)
            bf.put_back(n, px + o[0], py + o[1], seal)
        bf.save_template(name, top, bottom)
        bf.update(cid, package=name, z_pick=round(seal, 2))
        bf.mb.head_led(0)
        return {"name": name, "seal_z": seal, "top": BK.signature(p), "bottom_metal": len(b.blobs),
                "png_top": _png(cv2.resize(BK.annotate_top(img, p, k, f"{name} (taught)"), (520, 520), interpolation=cv2.INTER_AREA)),
                "png_bottom": _png(cv2.resize(BK.annotate_bottom(up, b, ku, f"{name}: {len(b.blobs)} metal"), (520, 520), interpolation=cv2.INTER_AREA))}
