"""Feeder set-up with the machine's own measurements (the steps an operator does by hand for every new
feeder), for the "Set up feeders" page; nothing is written to the job here, the page applies what the
operator ticks.

    POST /api/feedsetup/pick_height?client=&job=&stack=[&nozzle=][&x=&y=]
        the tape's pick height by the vacuum: the nozzle over the pick point, suction on, down in 0.1 mm
        steps until the pressure falls (the tip on the part, at most 13 mm), the part left where it is;
        pick height = 12 - (that depth + Job/fTapePressMm)
    POST /api/feedsetup/feeder?client=&job=&stack=&steps=pocket,pitch,height,part
        one feeder, in order: the pocket at the pick point (auto exposure, the nearest full pocket centred:
        the pick point), the pitch (pocket to pocket in the picture -> feed rate), the pick height (as
        above, on the new pick point), one part picked at that height and measured on the bottom camera
        (its size; the picture kept for the Setup view)
"""
import math
import os
import time

import cv2
import numpy as np

from neojob.jobfile import load as load_job
from neojob.camvision import HEAD
from neojob.padalign import px_to_machine
from neoserver.align import _busy

STD_PITCH = (2, 4, 8, 12, 16, 20, 24, 28, 32)


class FeederSetup:
    def __init__(self, align, cams, log):
        self.align, self.cams, self.log = align, cams, log
        self.mc = align.mc                                    # for _busy (control, busy_call)

    def _need(self, client):
        self.align._need(client)

    def _stack(self, q):
        job = load_job(q.get("job", ""))
        st = job.stack(int(q["stack"]))
        if st is None:
            raise KeyError(f"feeder {q['stack']} is not in the job")
        return job, st

    @staticmethod
    def _nozzle(st, q):
        if q.get("nozzle", "") != "":
            return int(q["nozzle"])
        d = [int(c) for c in str(st.nozzles or "") if c in "1234"]
        return d[0] if d else 1

    # ---- 1. the pick height by the vacuum ----
    def seal_depth(self, cfg, n, x, y, z_from=None, step=0.1, zmax=13.0, speed=20, settle=0.08):
        """nozzle n over machine (x, y) (nozzle-1 frame), suction on, down in steps from z_from until the
        pressure falls; then suction off and up (the part stays). The depth it sealed at, or None"""
        mb = self.mc.board()
        o = cfg.nozzle_offset(n)
        mb.raise_all()
        mb.rotate(n, 0.0, wait=True)
        mb.move_xy(x + o[0], y + o[1], timeout=20, slow=True)
        mb.valve(n, -1)
        time.sleep(0.3)
        open_air = [mb.vacuum(n) for _ in range(9)]
        base = float(np.median(open_air))
        # a touch: the pressure well below the open-air noise (8 or more, 4 x its spread), on two readings in
        # a row (feeder 7, 0603 on nozzle 1: -3 open, -30 ... -40 on the part)
        drop = max(8.0, 4.0 * float(np.std(open_air)))
        z = max(0.0, z_from if z_from is not None else 9.0)
        t0 = time.monotonic()
        while True:                                          # XY may still report busy just after the move
            try:
                mb.set_z(n, z, 40, wait=True)
                break
            except Exception as e:
                if "busy" not in str(e) or time.monotonic() - t0 > 3.0:
                    raise
                time.sleep(0.1)
        seal = None
        try:
            while z <= zmax:
                mb.set_z(n, z, speed, wait=True)
                time.sleep(settle)
                if float(np.median([mb.vacuum(n) for _ in range(3)])) <= base - drop:
                    time.sleep(0.05)
                    if float(np.median([mb.vacuum(n) for _ in range(3)])) <= base - drop:
                        seal = z
                        break
                z = round(z + step, 3)
        finally:
            if seal is not None:                             # the part let go where it is: a puff, then up
                mb.valve(n, 1)                               # (suction off alone: it stuck to the tip, was
                time.sleep(0.1)                              # carried up and dropped elsewhere)
            mb.valve(n, 0)
            mb.set_z(n, 0.0, 30, wait=True)
        return seal, base

    def _height(self, cfg, st, n, x, y):
        press = float(cfg.raw.get("Job/fTapePressMm", 0.2) or 0.2)   # into the part past the touch
        cur = max(0.0, 12.0 - st.pick_height) if st.pick_height else None
        seal, base = self.seal_depth(cfg, n, x, y, z_from=(cur - 1.5) if cur else None)
        if seal is None:
            return {"ok": False, "text": "the pressure never fell down to 13 mm (the lowest the nozzle goes): no part at the pick point, or the nozzle leaks"}
        z = seal + press
        return {"ok": True, "seal": round(seal, 2), "z": round(z, 2), "pick_height": round(12.0 - z, 2),
                "base": round(base, 1), "nozzle": n, "was": round(st.pick_height, 2)}

    @_busy
    def pick_height(self, q):
        cfg = self.align.cfg(q)
        job, st = self._stack(q)
        n = self._nozzle(st, q)
        x = float(q["x"]) if q.get("x", "") != "" else st.x
        y = float(q["y"]) if q.get("y", "") != "" else st.y
        r = self._height(cfg, st, n, x, y)
        self.log(f"feeder {st.id}: pick height by the vacuum: {r}", "align")
        return {"stack": st.id, **r}

    # ---- the pocket and the pitch, from one picture at the pick point ----
    def _pocket_pitch(self, cfg, st):
        from neocam.tape import find_pockets
        from neocalib import grab_settled
        mb = self.mc.board()
        v = self.align.vis(cfg)
        cam = cfg.head_cam_offset
        k = cfg.pxmm(1)
        ang = float(cfg.padalign("fHeadCamAngle"))
        mb.raise_all()
        mb.head_led(3)
        mb.move_xy(st.x + cam[0], st.y + cam[1], timeout=20)
        try:
            self.cams.auto_exposure(HEAD)
        except Exception:
            pass
        img = grab_settled(lambda: v.grab(HEAD), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
        found = [f for f in find_pockets(img, k) if f["kind"] == "pocket" and not f.get("round")]
        out = {}
        best = None
        for f in found:
            px, py = px_to_machine((st.x, st.y), f["px"], f["py"], k, img.shape[1], ang)
            d = math.hypot(px - st.x, py - st.y)
            if f.get("full", True) and d <= 2.0 and (best is None or d < best[0]):
                best = (d, px, py, f)
        near = min(((math.hypot(*(lambda q: (q[0] - st.x, q[1] - st.y))(px_to_machine((st.x, st.y), f["px"], f["py"], k, img.shape[1], ang))), f)
                    for f in found), default=None, key=lambda t: t[0])
        if near and near[0] <= 2.0 and not near[1].get("full", True) and (not best or best[0] > near[0] + 1.0):
            out["empty"] = True                               # the pocket at the pick point has no part
        if best:
            out["pocket"] = {"x": round(best[1], 3), "y": round(best[2], 3), "off": round(best[0], 3),
                             "w": best[3]["w"], "h": best[3]["h"]}
        d = []
        for a in found:                                       # each pocket's nearest neighbour of its size
            nb = [math.hypot(a["px"] - b["px"], a["py"] - b["py"]) for b in found if b is not a
                  and abs(b["w"] - a["w"]) < 0.35 * a["w"] + 0.2 and abs(b["h"] - a["h"]) < 0.35 * a["h"] + 0.2]
            if nb:
                d.append(min(nb) / k)
        if d:
            m = float(np.median(d))
            std = min(STD_PITCH, key=lambda p: abs(p - m))
            out["pitch"] = {"mm": round(m, 2), "feed": std if abs(std - m) < 0.15 * std else None, "pockets": len(found)}
        return out

    @_busy
    def feeder(self, q):
        cfg = self.align.cfg(q)
        job, st = self._stack(q)
        steps = set((q.get("steps") or "pocket,pitch,height,part").split(","))
        n = self._nozzle(st, q)
        out = {"stack": st.id, "value": st.value, "nozzle": n, "was": {"x": st.x, "y": st.y, "feed": st.pitch,
                                                                      "pick_height": st.pick_height}}
        x, y = st.x, st.y
        if steps & {"pocket", "pitch"} and st.type == 0 and not getattr(st, "bulk", 0):
            try:
                out.update(self._pocket_pitch(cfg, st))
            except ValueError as e:                       # the head camera cannot get over this pick point
                out["pocket_err"] = f"the head camera cannot reach the pick point ({e})"
            if "pocket" in steps and out.get("pocket"):
                x, y = out["pocket"]["x"], out["pocket"]["y"]
        if out.get("empty"):
            out["height"] = {"ok": False, "text": "the pocket at the pick point is empty: feed the tape (Feed test), then set it up again"}
            steps = steps - {"height", "part"}
            out["part"] = None
        if "height" in steps:
            out["height"] = self._height(cfg, st, n, x, y)
        if "part" in steps:
            z = out["height"]["z"] if out.get("height", {}).get("ok") else max(0.0, 12.0 - st.pick_height)
            img, bg, r = self.align.part_on_nozzle(cfg, n, x, y, z, delay=max(0.05, st.pick_delay / 1000))
            seal = getattr(self.align, "last_seal_s", None)
            out["seal_s"] = seal                              # how long the suction took to seal on the part
            if seal is not None:                              # a Pick delay with some margin
                out["pick_delay"] = int(round(max(100, 1000 * seal * 1.5 + 50), -1))
            if r.valid:
                self.align.save_partpic(q.get("job", ""), st.id, img, r, cfg.pxmm(0))
                out["part"] = {"L": round(max(r.w, r.h), 3), "W": round(min(r.w, r.h), 3), "angle": round(float(r.angle), 2)}
            else:
                out["part"] = None
        self.mc.board().raise_all()
        self.log(f"feeder {st.id}: set-up: { {k: v for k, v in out.items() if k != 'was'} }", "align")
        return out

    # ---- 5. the head camera's offset from placements ----
    def camoffset_job(self, q):
        """a copy of the job for the camera-offset placements (<data>/Source/_calib/): every part with the
        after-place photo (comp field 11 = photo), its Gerber pads file beside it; the job itself is not
        touched. Returns the copy's path"""
        import shutil
        src = q.get("job", "")
        d = os.path.join(os.path.dirname(os.path.dirname(src)), "Source", "_calib")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "camoffset-" + os.path.basename(src))
        raw = open(src, "rb").read()
        nl = b"\r\n" if b"\r\n" in raw else b"\n"
        out = []
        for line in raw.split(nl):
            t = line.split(b",")
            if t and t[0].strip() == b"comp" and len(t) > 9:
                while len(t) < 13:
                    t.append(b"")
                t[12] = b"photo"                              # k[11]: the after-place check of this part
                line = b",".join(t)
            out.append(line)
        open(dst, "wb").write(nl.join(out))
        if os.path.exists(src + ".pads.json"):
            shutil.copyfile(src + ".pads.json", dst + ".pads.json")
        for ext in (".resume.json",):
            try:
                os.remove(dst + ext)
            except OSError:
                pass
        return {"path": dst}


# ---- the head camera's scale for one board (the camera is not telecentric: ~115.2 px/mm on the BOARD-A,
# ~116.3 on the BOARD-B top face). Kept in the job as a `headscale,<px/mm>,<how>` line (ours; NeoDen4.exe
# ignores it); when set it replaces Vision/fHeadPxPerMm for that job (the run, the align calls) ----
def job_head_scale(path):
    """the job's own head camera px/mm, or None"""
    try:
        for line in open(path, "rb").read().decode("latin-1").splitlines():
            t = line.split(",")
            if t and t[0].strip() == "headscale" and len(t) > 1:
                v = float(t[1])
                return v if 60.0 < v < 200.0 else None
    except (OSError, ValueError):
        return None
    return None


def apply_head_scale(cfg, path):
    """cfg (NeoConfig) with the job's head scale when it has one; returns the value used or None"""
    v = job_head_scale(path) if path else None
    if v:
        cfg.raw["Vision/fHeadPxPerMm"] = v
    return v


def _row_scale(img, us, v, half_h, pitch_px):
    """px per mm along a row of pads: each pad's bright centre found in the brightness profile of the row
    (a window of +-0.4 pitch around where it should be), fitted against its Gerber position (a line through
    all of them, so a footprint whose spacing alternates on the Gerber grid, 0.660 / 0.635, reads right).
    us: [(Gerber position mm, expected px)]. Returns (px/mm, rms px) or None"""
    h, w = img.shape
    a, b = int(max(0, v - half_h)), int(min(h, v + half_h))
    if b - a < 3:
        return None
    prof = cv2.GaussianBlur(img, (5, 5), 0)[a:b].astype(float).mean(0)
    xs, ps = [], []
    for g, u in us:
        lo, hi = int(u - 0.4 * pitch_px), int(u + 0.4 * pitch_px)
        if lo < 0 or hi > w:
            continue
        seg = prof[lo:hi] - prof[lo:hi].min()
        if seg.sum() <= 0:
            continue
        xs.append(g); ps.append(lo + float((seg * np.arange(len(seg))).sum() / seg.sum()))
    if len(xs) < 4:
        return None
    A = np.vstack([xs, np.ones(len(xs))]).T
    (slope, c), *_ = np.linalg.lstsq(A, np.array(ps), rcond=None)
    e = np.array(ps) - A @ [slope, c]
    return abs(float(slope)), float(np.sqrt((e ** 2).mean()))


def _rows(pads, along):
    """rows of 4+ (nearly) evenly spaced pads along X (along=0) or Y (1): [(cross position, [positions], pitch mm,
    pad size across)]; pads of one row: within 0.3 mm of each other across (a turned board is not exact)"""
    items = sorted(((y, x)[along], (x, y)[along], (w, h)[1 - along]) for x, y, w, h in pads)
    groups, cur = [], []
    for it in items:
        if cur and it[0] - cur[-1][0] > 0.3:
            groups.append(cur)
            cur = []
        cur.append(it)
    if cur:
        groups.append(cur)
    rows = []
    for g in groups:
        if len(g) < 4:
            continue
        pos = sorted(p for _, p, _ in g)
        d = np.diff(pos)
        if not len(d) or np.ptp(d) > 0.06 * np.median(d) + 0.002:
            continue                                          # not a regular row (0.660 / 0.635 alternating is)
        rows.append((float(np.mean([c for c, _, _ in g])), pos, float(np.median(d)), float(np.median([s for _, _, s in g]))))
    return rows


def best_scale_part(pads):
    """the part whose pads give the best scale: the longest evenly spaced row (most pads x pitch)"""
    best = None
    for key, part in pads.items():
        for along in (0, 1):
            for cross, pos, pitch, size in _rows(part["pads"], along):
                span = pitch * (len(pos) - 1)
                if 0.3 <= pitch <= 2.6 and span < 7.0 and (best is None or span > best[0]):
                    best = (span, key)
    return best[1] if best else None


@_busy
def _measure_head_scale(self, q):
    import cv2
    from neojob.pads import load_pads
    path = q.get("job", "")
    d = load_pads(path)
    if not d or not d.get("parts"):
        raise KeyError("no pads file for this job: build it first (Job file -> Board)")
    if q.get("ref"):
        keys = [q["ref"]]
    else:                                                     # every board copy of the best part: a copy with a
        b = best_scale_part(d["parts"])                       # part already on it covers its pads (U3 on BOARD-B
        if not b:                                             # board 1: 114.3, 3 px off); the first clean one wins
            raise KeyError("no part with an evenly spaced row of 4+ pads (pass ref=)")
        base = b.split("#")[0]
        keys = sorted((k for k in d["parts"] if k.split("#")[0] == base), key=lambda k: (k != base, k))
    tried = []
    for key in keys:
        if key not in d["parts"]:
            raise KeyError(f"{key}: no pads of it in the pads file")
        r = self._head_scale_at(path, key, d["parts"][key])
        tried.append(r)
        if max(x["rms_px"] for x in r["rows"]) < 2.0:
            return dict(r, tried=[t["ref"] for t in tried])
    r = min(tried, key=lambda t: max(x["rms_px"] for x in t["rows"]))
    return dict(r, tried=[t["ref"] for t in tried], warning="no copy read cleanly (a part on its pads?): the best one is given")


def _head_scale_at(self, path, key, part):
    cfg = self.align.cfg({})                                  # Config's own scale, not the job's
    k0 = cfg.pxmm(1)
    cam = cfg.head_cam_offset
    mb = self.mc.board()
    mb.raise_all()
    mb.head_led(3)
    cx, cy = part["x"], part["y"]
    mb.move_xy(cx + cam[0] - 0.3, cy + cam[1] - 0.3, timeout=20, slow=True)
    mb.move_xy(cx + cam[0], cy + cam[1], timeout=20, slow=True)
    with self.cams.lock:
        self.cams.dev().window(1, 1024, 1024)
    try:
        self.cams.auto_exposure(1)
        from neocalib import grab_settled
        from neocam import vision as V
        with self.cams.lock:
            dev = self.cams.dev()
            raw = grab_settled(lambda: np.asarray(dev.grab(1)), tol_px=0.3, stable=2, max_s=1.5, min_s=0.15).frame
        img = V.orient_head(raw)                              # raw pixels: not through a camera map
    finally:
        try:
            self.cams.restore_view()
        except Exception:
            pass
    res = []
    for along in (0, 1):
        for cross, pos, pitch, size in _rows(part["pads"], along):
            if along == 0:                                    # a row along machine X: the picture's X (Y up)
                r = _row_scale(img, [(g, 512 + (g - cx) * k0) for g in pos], 512 - (cross - cy) * k0, 0.35 * size * k0, pitch * k0)
            else:                                             # along machine Y: the picture's rows, upwards
                r = _row_scale(np.ascontiguousarray(img.T), [(g, 512 - (g - cy) * k0) for g in pos], 512 + (cross - cx) * k0, 0.35 * size * k0, pitch * k0)
            if r and r[1] < 0.15 * pitch * k0:
                res.append({"along": "XY"[along], "pads": len(pos), "pitch_mm": round(pitch, 4), "px_per_mm": round(r[0], 3), "rms_px": round(r[1], 2)})
    if not res:
        raise RuntimeError(f"{key}: the pad rows were not readable in the picture")
    v = float(np.median([r["px_per_mm"] for r in res]))
    self.log(f"head camera scale on {os.path.basename(path)} from {key}: {v:.3f} px/mm ({res}); Config {k0}", "align")
    return {"ref": key, "rows": res, "px_per_mm": round(v, 3), "config": k0, "job": job_head_scale(path)}


FeederSetup.measure_head_scale = _measure_head_scale
FeederSetup._head_scale_at = _head_scale_at
