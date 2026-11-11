"""Z height map on the machine (neojob.zmap): the machine's own heights (a calibration, every job) and each
job's board height.

    POST /api/zmap/calibrate?client=&job=[&nx=4&ny=3&nozzles=1,2,3,4&clear=1.5&gerber=]
        the machine map, on the job's board (it lies on the table): a grid of nx x ny points over it, each
        moved to the nearest bare spot (clear mm from every pad, part, mask opening and hole; without a
        pads file, a 5 mm box round each part; no board under a spot: the next one 2 mm on, up to 4); at
        each nozzle 1 comes down slowly with the suction on until the pressure falls (it touches the
        board): the first point searched from 6 mm down, the next ones from 0.8 mm above the first touch,
        each touch found coarse (0.1 mm) then fine (0.02 mm, from 0.6 mm above the coarse one). Then the
        other nozzles at two points far apart: each one's difference from nozzle 1. Saved as
        <data>/zmap.json (machine coordinates), and this board's height with the job. gerber=: the mask /
        holes added to an older pads file first.
    POST /api/zmap/board?client=&job=         this job's board height alone: nozzle 1 at 3 bare spots,
        the machine's own shape there taken off (<job>.zmap.json). At a job's start when it has none.
    POST /api/zmap/on?client=&on=1|0          the machine map used when placing (or not)
    GET  /api/zmap/get?job=                    the machine map (points, spread, odd points, triangles) and
        the job's board height
"""
import math
import os
import time

import numpy as np

from neojob import zmap as Z
from neojob.jobfile import load as load_job
from neojob.pads import load_pads
from neoserver.align import _busy


def bare_spots(pads, nx, ny, clear=1.5, inset=3.0, keepout=(), alts=4):
    """nx x ny points over the pads' extent (inset mm inside it); for each the nearest spots at least
    `clear` mm from every pad, each part's pad box (its body) and every keep-out box (mask openings and
    holes, neojob.pads.keepout_of): up to `alts` of them, 2 mm or more apart, nearest first (the next
    one tried when the nozzle finds no board on one). [] for a point with none within 6 mm"""
    boxes, flat = [], []
    for part in pads.values():
        P = np.array([[x, y, w, h] for x, y, w, h in part["pads"]], float)
        if not len(P):
            continue
        flat.append(P)
        boxes.append((P[:, 0].min() - P[:, 2].max() / 2, P[:, 1].min() - P[:, 3].max() / 2,
                      P[:, 0].max() + P[:, 2].max() / 2, P[:, 1].max() + P[:, 3].max() / 2))
    A = np.vstack(flat)
    x0, y0 = A[:, 0].min() + inset, A[:, 1].min() + inset
    x1, y1 = A[:, 0].max() - inset, A[:, 1].max() - inset
    if len(keepout):
        A = np.vstack([A, np.array(keepout, float)[:, :4]])
    B = np.array(boxes)
    # each board of a panel (its copy: C10, C10#2 ...): a point stays inside one board's pads, not in the
    # gap or routed slot between two boards
    regions = {}
    for key, part in pads.items():
        c = int(key.split("#")[1]) if "#" in key else 1
        for x, y, w, h in part["pads"]:
            r = regions.setdefault(c, [1e9, 1e9, -1e9, -1e9])
            r[0], r[1], r[2], r[3] = min(r[0], x), min(r[1], y), max(r[2], x), max(r[3], y)
    R = [r for r in regions.values()]

    def on_board(x, y):
        return any(r[0] + 0.5 <= x <= r[2] - 0.5 and r[1] + 0.5 <= y <= r[3] - 0.5 for r in R)

    def free(x, y):
        if not on_board(x, y):
            return False
        dx = np.maximum(0, np.maximum(B[:, 0] - x, x - B[:, 2]))       # distance to each part's box
        dy = np.maximum(0, np.maximum(B[:, 1] - y, y - B[:, 3]))
        if (np.hypot(dx, dy) < clear).any():
            return False
        ex = np.maximum(0, np.abs(A[:, 0] - x) - A[:, 2] / 2)           # and to every pad / opening / hole
        ey = np.maximum(0, np.abs(A[:, 1] - y) - A[:, 3] / 2)
        return not (np.hypot(ex, ey) < clear).any()

    out = []
    for gy in np.linspace(y0, y1, ny):
        for gx in np.linspace(x0, x1, nx):
            got = []
            for r in np.arange(0, 6.01, 0.5):                           # rings around the grid point
                for a in np.linspace(0, 2 * math.pi, max(1, int(8 * r)), endpoint=False):
                    x, y = gx + r * math.cos(a), gy + r * math.sin(a)
                    if free(x, y) and all(math.hypot(x - u, y - v) >= 2.0 for u, v in got):
                        got.append((round(float(x), 3), round(float(y), 3)))
                if len(got) >= alts:
                    break
            out.append(got[:alts])
    return out


def bare_points(pads, nx, ny, clear=1.5, inset=3.0, keepout=()):
    """the nearest bare spot of each grid point (bare_spots), None where there is none"""
    return [g[0] if g else None for g in bare_spots(pads, nx, ny, clear, inset, keepout, alts=1)]


class ZMapCal:
    def __init__(self, align, feedsetup, log, data_dir):
        self.align, self.fs, self.log, self.data = align, feedsetup, log, data_dir
        self.mc = align.mc

    def _need(self, client):
        self.align._need(client)

    def get(self, q):
        d = Z.read_machine(self.data)
        out = {"machine": None, "board": Z.read(q.get("job", "")) if q.get("job") else None}
        if d and len(d.get("points") or []) >= 3:
            m = Z.ZMap(d["points"], d.get("on", True), d.get("nozzles"))
            zs = [p["z"] for p in d["points"]]
            out["machine"] = dict(d, spread=round(max(zs) - min(zs), 3), tri=m.tri, ref=round(m.ref, 3),
                                  tilt_rms=round(m.tilt_rms, 4), odd=m.odd())
        return out

    def on(self, q):
        self.align._need(q.get("client", ""))
        d = Z.read_machine(self.data)
        if not d:
            raise KeyError("no machine height map yet: calibrate it first")
        d["on"] = q.get("on", "1") in ("1", "true")
        Z.save_machine(self.data, d)
        return {"on": d["on"]}

    def _machine(self, path, x, y):
        """design point -> machine (nozzle-1 frame): the board's marks when read, else the job's own place"""
        r = self.align.point({"job": path, "x": str(x), "y": str(y), "nominal": "1"})
        return r["x"], r["y"]

    def _touch(self, cfg, n, mx, my, z_from, zmax):
        """where nozzle n meets the board: coarse (0.1 mm), then fine (0.02 mm, slower, longer settle)
        from 0.6 mm above the coarse touch: the pressure lags the move, so the coarse one is a few steps
        late. A fine search that seals on its very first step started on the board already: again from
        higher up. None: no board down to zmax"""
        coarse, _ = self.fs.seal_depth(cfg, n, mx, my, z_from=z_from, step=0.1, zmax=zmax, speed=20)
        if coarse is None:
            return None
        start = max(0.0, coarse - 0.6)
        fine = None
        for _ in range(3):
            fine, _ = self.fs.seal_depth(cfg, n, mx, my, z_from=start, step=0.02, zmax=coarse + 0.1, speed=10, settle=0.2)
            if fine is None:
                return coarse
            if fine > start + 1e-6 or start <= 0.0:
                return fine
            start = max(0.0, start - 0.6)
        return fine

    def _spots(self, q, nx, ny):
        """the bare spots of the job's board (bare_spots), with their fallbacks"""
        path = q.get("job", "")
        if q.get("gerber"):               # a pads file from before the keep-out: the mask / holes added
            from neojob.pads import add_keepout
            self.log(f"height map: {add_keepout(path, q['gerber'])} mask openings and holes kept clear", "align")
        pd = load_pads(path) or {}
        parts, keepout = pd.get("parts"), pd.get("keepout") or []
        if not parts:                     # no pads file (the wizard's new job): each part a 5 mm box
            job = load_job(path)
            parts = {c.key or c.name: {"pads": [[c.x, c.y, 5.0, 5.0]]} for c in job.comps}
            if not parts:
                raise KeyError("the job has no parts: nothing tells where the board is")
            self.log("height map: no pads file, the parts' places used (5 mm round each)", "align")
        elif not keepout:
            self.log("height map: the pads file has no keep-out (mask openings, holes): build it again (Job file -> Board), "
                     "or pass gerber=, or a point may land on a hole", "align")
        pts = [g for g in bare_spots(parts, nx, ny, float(q.get("clear", 1.5)), keepout=keepout) if g]
        if len(pts) < 3:
            raise RuntimeError("fewer than 3 bare spots on the board: smaller grid or less clearance")
        return pts

    def _touches(self, cfg, path, pts, what):
        """nozzle 1 on each spot (the next one of a spot where it finds no board) -> [{x, y, mx, my, z}]"""
        out, first = [], None
        for i, spots in enumerate(pts):
            rec = None
            for x, y in spots:
                mx, my = self._machine(path, x, y)
                z = self._touch(cfg, 1, mx, my, 6.0 if first is None else max(0.0, first - 0.8),
                                13.0 if first is None else first + 1.5)
                if z is not None:
                    rec = {"x": x, "y": y, "mx": round(mx, 3), "my": round(my, 3), "z": round(float(z), 3)}
                    break
                self.log(f"{what} {i + 1}/{len(pts)}: no board at {x:.2f}, {y:.2f}" + (" (a hole?), the next spot" if (x, y) != spots[-1] else ""), "align")
            if rec is None:
                out.append({"x": spots[0][0], "y": spots[0][1], "z": None, "why": "no touch"})
                continue
            first = rec["z"] if first is None else first
            out.append(rec)
            self.log(f"{what} {i + 1}/{len(pts)}: {rec['x']:.2f}, {rec['y']:.2f} touched at {rec['z']:.3f} mm", "align")
        return out

    @_busy
    def calibrate(self, q):
        return self._calibrate(q)

    def _calibrate(self, q):
        path = q.get("job", "")
        nx, ny = int(q.get("nx", 4)), int(q.get("ny", 3))
        others = [int(k) for k in str(q.get("nozzles", "1,2,3,4")).split(",") if k.strip() and int(k) != 1 and 1 <= int(k) <= 4]
        cfg = self.align.cfg(q)
        t0 = time.monotonic()
        out = self._touches(cfg, path, self._spots(q, nx, ny), "height map")
        good = [p for p in out if p.get("z") is not None]
        if len(good) < 3:
            self.mc.board().raise_all()
            raise RuntimeError(f"only {len(good)} touches: the nozzle must be empty and seal (no part on it)")
        # the other nozzles: at the first point and the one farthest from it, each touch less nozzle 1's there
        far = max(good, key=lambda p: math.hypot(p["x"] - good[0]["x"], p["y"] - good[0]["y"]))
        noz = {}
        for k in others:
            diffs = []
            for p in (good[0], far):
                z = self._touch(cfg, k, p["mx"], p["my"], max(0.0, p["z"] - 1.5), p["z"] + 1.5)
                if z is not None:
                    diffs.append(z - p["z"])
            if diffs:
                noz[str(k)] = round(float(np.mean(diffs)), 3)
                self.log(f"height map: nozzle {k} touches {noz[str(k)]:+.3f} mm from nozzle 1" + (f" (the two points {diffs[0]:+.3f} / {diffs[1]:+.3f})" if len(diffs) == 2 else ""), "align")
            else:
                self.log(f"height map: nozzle {k} found no board within 1.5 mm of nozzle 1's touch (no nozzle on it, or a part)", "align")
        self.mc.board().raise_all()
        # machine coordinates: the map is the machine's, every job uses it
        pts = [{"x": p["mx"], "y": p["my"], "z": p["z"], "bx": p["x"], "by": p["y"]} for p in good]
        m = Z.ZMap(pts, True, noz)
        rec = {"points": pts, "missed": [p for p in out if p.get("z") is None], "nozzles": noz, "on": True,
               "board": os.path.basename(path), "time": time.strftime("%Y-%m-%d %H:%M"), "seconds": int(time.monotonic() - t0)}
        Z.save_machine(self.data, rec)
        Z.save(path, {"board_z": round(m.ref, 3), "points": good, "how": "machine calibration", "time": rec["time"]})
        zs = [p["z"] for p in pts]
        self.log(f"machine height map (on {os.path.basename(path)}): {len(pts)} points, {min(zs):.3f} .. {max(zs):.3f} mm, "
                 f"off a plane {m.tilt_rms:.3f}; this board at {m.ref:.3f}", "align")
        return dict(rec, tri=m.tri, spread=round(max(zs) - min(zs), 3), ref=round(m.ref, 3), tilt_rms=round(m.tilt_rms, 4),
                    odd=m.odd(), board_z=round(m.ref, 3))

    @_busy
    def board(self, q):
        return self._board(q)

    def _board(self, q):
        """this job's board height: nozzle 1 at 3 bare spots far apart, the machine's shape there taken off"""
        path = q.get("job", "")
        cfg = self.align.cfg(q)
        spots = self._spots(q, 2, 2)
        spots = [spots[0], spots[-1], spots[1] if len(spots) > 2 else spots[0]][:3]
        out = [p for p in self._touches(cfg, path, spots, "board height") if p.get("z") is not None]
        self.mc.board().raise_all()
        if not out:
            raise RuntimeError("no touch: the nozzle must be empty and seal (no part on it)")
        m = Z.load_machine(self.data)
        bz = [p["z"] - (m.shape(p["mx"], p["my"], 1) if m else 0.0) for p in out]
        rec = {"board_z": round(float(np.median(bz)), 3), "points": out, "how": "board" + (" less the machine map" if m else ""),
               "time": time.strftime("%Y-%m-%d %H:%M"), "spread": round(max(bz) - min(bz), 3)}
        Z.save(path, rec)
        self.log(f"board height of {os.path.basename(path)}: {rec['board_z']:.3f} mm ({len(out)} touches, they differ {rec['spread']:.3f})", "align")
        return rec

    def auto(self, path):
        """at a job's start (neoserver.job): the board height measured when the job has none (the next boards
        of the job go straight on); False when it could not"""
        if (Z.read(path) or {}).get("board_z") is not None:
            return True
        self.log("no board height for this job: measuring it first (nozzle 1 must be empty)", "align")
        try:
            self._board({"job": path})
            return True
        except Exception as e:
            self.log(f"board height not measured: {type(e).__name__}: {e}", "align")
            return False
