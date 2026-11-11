"""neoserver: the NeoDen4 machine server for the Ubuntu machine PC (replaces neoagent.exe on XP).

    python3 -m neoserver [--config /etc/neoden/neoserver.ini]

Serves the web UI (neoagent/www) and the same JSON API as neoagent (docs/NEOAGENT.md):
files with backups, signed updates with rollback, services (systemd), log, cameras
(native USB), machine control (neomach over the serial port) with the control lock.
Standard library only (plus numpy, pyusb, pyserial through neocam / neomach).
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neoserver.core import VERSION, Files, Log, Services, Settings, Updates, hostname  # noqa: E402
from neoserver.hw import Cameras, Machine  # noqa: E402
from neoserver.setup import Setup  # noqa: E402
from neoserver.job import JobService  # noqa: E402
from neoserver.align import AlignService  # noqa: E402
from neoserver.wifi import Wifi  # noqa: E402

S = None          # settings, log, files, services, updates, machine, cameras (set in main)



def _keep_view(f, q, moved):
    """an align / bulk call that used the cameras: their viewing settings back afterwards (the task set
    its own exposure / window, and the live pictures went dark)"""
    try:
        return f(q)
    finally:
        if moved:
            try:
                S.cam.restore_view()
            except Exception:
                pass

class Handler(BaseHTTPRequestHandler):
    server_version = "neoserver/" + VERSION
    protocol_version = "HTTP/1.1"
    # the headers and the body go out in two writes: with Nagle the body waited ~45 ms for the client's
    # delayed ACK on every API call (the screens poll several times a second)
    disable_nagle_algorithm = True

    def log_message(self, fmt, *a):                 # quiet; errors go to the log
        pass

    # ---- replies ----
    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    KIOSK_INI = "/etc/neoden/kiosk.ini"
    KIOSK_KEYS = {"mouse_speed": (0.2, 5.0), "zoom": (0.4, 2.0)}

    def kiosk(self, m, q):
        """GET: the kiosk's mouse_speed and zoom (kiosk.ini); POST key=value...: written into kiosk.ini
        (its comments kept), then the kiosk restarted to take them"""
        import re
        import subprocess
        p = self.KIOSK_INI
        text = open(p, encoding="utf-8").read() if os.path.exists(p) else "[kiosk]\n"
        def get(k, d):
            mm = re.search(r"(?m)^\s*%s\s*=\s*([^;\n]*)" % k, text)
            return mm.group(1).strip() if mm else d
        if m != "POST":
            return self.ok(installed=os.path.exists(p), mouse_speed=float(get("mouse_speed", "1.0") or 1.0),
                           zoom=float(get("zoom", "1.0") or 1.0), theme=get("theme", "auto") or "auto")
        for k, rng in list(self.KIOSK_KEYS.items()) + [("theme", None)]:
            if q.get(k, "") == "":
                continue
            if rng is None:                         # a choice, not a number
                v = q[k].strip().lower()
                if v not in ("dark", "light", "auto"):
                    continue
                line = "%s = %s" % (k, v)
            else:
                line = "%s = %g" % (k, min(rng[1], max(rng[0], float(q[k]))))
            if re.search(r"(?m)^\s*%s\s*=" % k, text):
                text = re.sub(r"(?m)^\s*%s\s*=[^\n]*" % k, line, text)
            else:
                text = re.sub(r"(?m)^\[kiosk\]\s*$", "[kiosk]\n" + line, text, count=1)
        S.files.write(p, text.encode("utf-8"))
        S.log(f"kiosk settings: {', '.join(k + '=' + q[k] for k in list(self.KIOSK_KEYS) + ['theme'] if q.get(k))}")
        if q.get("restart", "1") == "1":
            threading.Timer(0.5, lambda: subprocess.run(["systemctl", "restart", "neoden-kiosk"], check=False)).start()
        return self.ok(restarted=q.get("restart", "1") == "1")

    _gz = {}                                         # path -> (mtime, size, gzipped bytes)

    def static(self, full):
        """a UI file: asked again each time (no-cache) but answered 304 while it is unchanged (ETag from
        its time and size), and gzipped for a browser that takes it: the kiosk loads ~400 kB of script
        on every page load otherwise"""
        st = os.stat(full)
        tag = f'"{int(st.st_mtime_ns):x}-{st.st_size:x}"'
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = open(full, "rb").read()
        extra = {"ETag": tag, "Cache-Control": "no-cache"}
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(data) > 1024 and \
                ctype.startswith(("text/", "application/javascript", "application/json", "image/svg")):
            c = self._gz.get(full)
            if not c or c[0] != st.st_mtime_ns or c[1] != st.st_size:
                import gzip
                c = self._gz[full] = (st.st_mtime_ns, st.st_size, gzip.compress(data, 6))
            data = c[2]
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def ok(self, **kw):
        self.send(200, dict(ok=True, **kw))

    def err(self, code, msg):
        self.send(code, {"ok": False, "error": str(msg)})

    # ---- request ----
    def authed(self):
        if S.st.login.strip().lower() not in ("yes", "1", "true", "on"):
            return True                             # login off (default): the machine's own network only
        want = base64.b64encode(f"{S.st.user}:{S.st.password}".encode()).decode()
        got = (self.headers.get("Authorization") or "").split(" ", 1)
        return len(got) == 2 and got[1].strip() == want

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(u.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        if not self.authed():
            self.body()
            return self.send(401, "login required", "text/plain", {"WWW-Authenticate": 'Basic realm="NeoDen4"'})
        try:
            if path.startswith("/api/"):
                return self.api(path[5:], q)
            if self.command not in ("GET", "HEAD"):
                return self.err(400, "bad request")
            rel = path.lstrip("/") or "index.html"
            full = os.path.normpath(os.path.join(S.st.www, rel))
            if not full.startswith(os.path.normpath(S.st.www)) or not os.path.isfile(full):
                return self.err(404, "not found")
            return self.static(full)
        except Exception as e:                       # never kill the connection silently
            S.log(f"{self.command} {path}: {type(e).__name__}: {e}")
            try:
                self.err(500, f"{type(e).__name__}: {e}")
            except Exception:
                pass

    def api(self, ep, q):
        peer = self.client_address[0]
        m = self.command
        if ep == "ping":
            return self.ok(agent="neoserver " + VERSION)
        if ep == "status":
            return self.ok(agent=VERSION, host=hostname(), uptime_s=int(time.monotonic() - S.t0),
                           neoden_dir=S.st.neoden_dir, procs=S.svc.status(), camera=S.cam.info())
        if ep == "kiosk":                            # this PC's screen (neokiosk): mouse speed, zoom
            return self.kiosk(m, q)
        if ep == "procs":
            return self.send(200, S.svc.status())
        if ep == "proc":
            S.svc.action(q.get("name", ""), q.get("action", ""))
            return self.ok()
        if ep == "event":                            # a client's error, to show on every screen
            try:
                d = json.loads(self.body() or b"{}")
            except ValueError:
                d = {}
            S.events.add(str(d.get("level", "bad"))[:10], str(d.get("text", ""))[:400], str(q.get("client", "") or d.get("from", ""))[:40])
            return self.ok()
        if ep == "log":
            return self.send(200, S.log.since(int(q.get("since", 0) or 0)))
        if ep == "files":
            p = q.get("path", "")
            if p and not Files.ok(p):
                return self.err(400, "path must be absolute")
            try:
                return self.send(200, S.files.listing(p))
            except OSError as e:
                return self.err(404, f"cannot list folder: {e}")
        if ep == "file":
            p = q.get("path", "")
            if not Files.ok(p):
                return self.err(400, "path must be absolute, e.g. /opt/neoden/data/Config.ini")
            if m == "GET":
                if not os.path.isfile(p):
                    return self.err(404, "cannot read file")
                return self.send(200, open(p, "rb").read(), mimetypes.guess_type(p)[0] or "application/octet-stream")
            if m in ("PUT", "POST"):
                b = self.body()
                setid = S.files.write(p, b)
                S.log(f"{peer}: wrote {p} ({len(b)} bytes)" + (f", backup {setid}" if setid else ""))
                return self.ok(backup=setid)
            return self.err(400, "use GET or PUT")
        if ep == "delete":
            S.files.delete(q.get("path", ""))
            S.log(f"{peer}: deleted {q.get('path')}")
            return self.ok()
        if ep == "mkdir":
            os.makedirs(q.get("path", ""), exist_ok=True)
            return self.ok()
        if ep == "rename":
            os.rename(q.get("from", ""), q.get("to", ""))
            S.log(f"{peer}: renamed {q.get('from')} -> {q.get('to')}")
            return self.ok()
        if ep == "backups":
            return self.send(200, S.files.sets())
        if ep == "restore":
            rep = S.files.restore(q.get("id", ""))
            S.log(f"{peer}: restore {q.get('id')}")
            return self.ok(report=rep)
        if ep == "update":
            if m != "POST":
                return self.err(400, "POST the package as the body")
            ok, rep = S.upd.apply(self.body())
            S.log(f"{peer}: update {'applied' if ok else 'rejected'}")
            return self.send(200 if ok else 500, {"ok": ok, "report": rep})
        if ep.startswith("cam/"):
            cam = int(q.get("cam", 0) or 0)
            if ep == "cam/grab":
                if S.mc.idle_off:                 # lamps switched off idle: back on for this picture
                    S.mc.relight()
                    time.sleep(0.15)
                try:
                    bmp = S.cam.grab_bmp(cam, int(q.get("scale", 1) or 1), q.get("settle", "") == "1")
                    r = S.cam.last_settle
                    hdr = {"X-Settle": f"{'ok' if r.settled else 'timeout'} {r.time_s * 1000:.0f}ms {r.frames}f"} if r else None
                    return self.send(200, bmp, "image/bmp", hdr)
                except Exception as e:
                    return self.err(502, f"camera: {e}")
            if ep == "cam/pockets":               # tape pockets in the last picture of that camera (feeder settings)
                img = getattr(S.cam, "last_img", {}).get(cam)
                if img is None:
                    return self.err(404, "no picture yet")
                from neocam.config import NeoConfig
                from neocam.tape import find_pockets
                c = NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini"))
                k = c.pxmm(cam)
                found = find_pockets(img, k)
                board = None
                if q.get("job") and q.get("x", "") != "" and cam == 1:
                    try:
                        board = S.align.board_view(q["job"], float(q["x"]), float(q["y"]), int(img.shape[1]), k)
                    except Exception as e:
                        S.log(f"cam/pockets: board view: {type(e).__name__}: {e}", "align")
                if board:                          # the board, not a tape: no pockets, only round things and its pads
                    found = [dict(f, kind="round") for f in found if f.get("round")]
                return self.send(200, {"ok": True, "size": int(img.shape[1]), "found": found, "board": board})
            if ep == "cam/bulkparts":             # loose parts in the last picture (feeder settings of a bulk feeder)
                img = getattr(S.cam, "last_img", {}).get(cam)
                if img is None:
                    return self.err(404, "no picture yet")
                from neocam.config import NeoConfig
                from neojob.bulkfeed import find_bodies
                k = NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini")).pxmm(cam)
                g = img if img.ndim == 2 else img[:, :, 0]
                found = [{"kind": "part", "px": round(x, 1), "py": round(y, 1), "w": L, "h": W, "angle": -a} for x, y, L, W, a in find_bodies(g, k)]
                return self.send(200, {"ok": True, "size": int(img.shape[1]), "found": found})
            if ep == "cam/auto" and m == "POST":     # exposure for what is under the camera (feeder settings)
                try:
                    return self.send(200, {"ok": True, **S.cam.auto_exposure(cam, int(q.get("target", 200) or 200))})
                except Exception as e:
                    return self.err(502, f"camera: {e}")
            if ep == "cam/set":
                g = lambda k, d: int(q[k]) if q.get(k, "") != "" else d
                try:
                    S.cam.set(cam, g("exposure", 0), g("gain", -1), g("w", 0), g("h", 0), g("led", -1),
                              save=q.get("save", "") == "1")   # save=1: the Cameras page's viewing settings
                except Exception as e:
                    return self.err(502, f"camera: {e}")
                return self.ok()
        if ep == "stats":                          # production counts: per feeder / nozzle / part, warnings
            from neocam.config import NeoConfig
            from neojob.learn import read_stats, summary
            c = NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini"))
            days = max(1, min(366, int(q.get("days", 7) or 7)))
            return self.send(200, {"ok": True, "days": days, **summary(read_stats(S.st.neoden_dir, days), float(c.job("fStatsWarnPct")))})
        if ep == "visionlog/summary":
            from neojob import visionlog as VL
            return self.send(200, {"ok": True, "days": VL.summary(S.st.neoden_dir)})
        if ep == "visionlog/clear" and m == "POST":
            import shutil
            d = os.path.join(S.st.neoden_dir, "VisionLog")
            shutil.rmtree(d, ignore_errors=True)
            S.log("vision log deleted", "job")
            return self.ok()
        if ep.startswith("calib/"):                # Calibration page: auto-calibration (neocalib/autocal.py)
            what = ep[6:]
            try:
                if what == "status":
                    return self.send(200, {**S.calib.status(), "ok": True})
                if m != "POST":
                    return self.err(400, "use POST")
                if what == "start":
                    opts = json.loads(self.body() or b"{}")
                    return self.send(200, {**S.calib.start(q.get("client", ""), opts), "ok": True})
                if what == "cancel":
                    return self.send(200, {**S.calib.cancel(), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except Exception as e:
                return self.err(422, f"{type(e).__name__}: {e}")
            return self.err(404, "unknown calib call")
        if ep.startswith("wifi/"):                 # Network page (neoserver/wifi.py)
            what = ep[5:]
            try:
                if what == "status":
                    return self.send(200, {**S.wifi.status(), "ok": True})
                if m != "POST":
                    return self.err(400, "use POST")
                if what == "scan":
                    return self.send(200, {**S.wifi.scan(), "ok": True})
                if what == "connect":
                    req = json.loads(self.body() or b"{}")
                    return self.send(200, {**S.wifi.connect(req.get("ssid", ""), req.get("password", ""), bool(req.get("hidden"))), "ok": True})
                if what == "forget":
                    return self.send(200, {**S.wifi.forget(q.get("ssid", "")), "ok": True})
            except ValueError as e:
                return self.err(400, e)
            except Exception as e:
                return self.err(500, f"{type(e).__name__}: {e}")
            return self.err(404, "unknown wifi call")
        if ep.startswith("bulk/"):                   # bulk feeders (neoserver.bulk)
            what = ep[5:]
            try:
                if what == "list":
                    return self.send(200, {**S.bulk.list(q), "ok": True})
                if what == "mosaic":
                    data = S.bulk.mosaic(q.get("id", 0))
                    if data is None:
                        return self.err(404, "not scanned yet")
                    return self.send(200, data, "image/png", {"Cache-Control": "no-store"})
                if what == "grid" and m != "POST":
                    return self.send(200, {**S.bulk.grid(q), "ok": True})
                if m != "POST":
                    return self.err(400, "use POST")
                if what == "save":
                    return self.send(200, {**S.bulk.save(q, json.loads(self.body() or b"{}")), "ok": True})
                if what == "grid":
                    return self.send(200, {**S.bulk.set_grid(q, json.loads(self.body() or b"{}")), "ok": True})
                f = {"delete": S.bulk.delete, "scan": S.bulk.scan, "look": S.bulk.look, "teach": S.bulk.teach, "trial": S.bulk.trial,
                     "measure": S.bulk.measure, "confirm": S.bulk.confirm}.get(what)
                if not f:
                    return self.err(404, "unknown bulk call")
                return self.send(200, {**_keep_view(f, q, m == "POST"), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except Exception as e:
                S.log(f"bulk {what}: {type(e).__name__}: {e}", "bulk")
                return self.err(422, f"{type(e).__name__}: {e}")
        if ep.startswith("cammap/"):                      # the cameras' pixel maps (neocalib.cammap)
            what = ep[7:]
            try:
                if what == "status":
                    return self.send(200, {**S.cammap.status(q), "ok": True})
                if m != "POST":
                    return self.err(400, "use POST")
                f = {"calibrate": S.cammap.calibrate, "on": S.cammap.on}.get(what)
                if not f:
                    return self.err(404, "unknown cammap call")
                return self.send(200, {**f(q), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except Exception as e:
                S.log(f"cammap {what}: {type(e).__name__}: {e}", "align")
                return self.err(422, f"{type(e).__name__}: {e}")
        if ep.startswith("zmap/"):                        # the board's height map (neojob.zmap)
            what = ep[5:]
            try:
                if what == "get":
                    return self.send(200, {**S.zmap.get(q), "ok": True})
                if m != "POST":
                    return self.err(400, "use POST")
                f = {"calibrate": lambda q: _keep_view(S.zmap.calibrate, q, True), "measure": lambda q: _keep_view(S.zmap.calibrate, q, True),
                     "board": lambda q: _keep_view(S.zmap.board, q, True), "on": S.zmap.on}.get(what)
                if not f:
                    return self.err(404, "unknown zmap call")
                return self.send(200, {**f(q), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except KeyError as e:
                return self.err(404, e)
            except Exception as e:
                S.log(f"zmap {what}: {type(e).__name__}: {e}", "align")
                return self.err(422, f"{type(e).__name__}: {e}")
        if ep.startswith("feedsetup/") and m == "POST":     # feeder set-up by the machine's measurements
            f = {"pick_height": S.feedsetup.pick_height, "feeder": S.feedsetup.feeder,
                 "camoffset_job": S.feedsetup.camoffset_job, "headscale": S.feedsetup.measure_head_scale}.get(ep[10:])
            if not f:
                return self.err(404, "unknown feedsetup call")
            try:
                return self.send(200, {**_keep_view(f, q, True), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except Exception as e:
                S.log(f"feedsetup {ep[10:]}: {type(e).__name__}: {e}", "align")
                return self.err(422, f"{type(e).__name__}: {e}")
        if ep.startswith("align/"):
            what = ep[6:]
            f = {"detect": S.align.detect, "camcal": S.align.camcal, "pads": S.align.pads, "parts": S.align.parts,
                 "marks": S.align.marks, "automarks": S.align.automarks, "part": S.align.part, "leads": S.align.leads,
                 "point": S.align.point, "design": S.align.design, "pocket": S.align.pocket,
                 "repnp": S.align.repnp, "measure_part": S.align.measure_part, "circle": S.align.circle,
                 "check_feeder": S.align.check_feeder, "orient": S.align.orient, "setupview": S.align.setupview}.get(what)
            if not f and what != "partpic":
                return self.err(404, "unknown align call")
            if what == "partpic":                    # a feeder's part on the bottom camera (Setup view)
                pp = S.align.partpic_path(q.get("job", ""), int(q.get("stack", 0) or 0))
                if not os.path.isfile(pp):
                    return self.err(404, "no picture of this feeder's part yet")
                return self.send(200, open(pp, "rb").read(), "image/png")
            if m != "POST" and what not in ("parts", "point", "design", "orient", "setupview"):
                return self.err(400, "use POST")
            try:
                return self.send(200, {**_keep_view(f, q, m == "POST"), "ok": True})
            except PermissionError as e:
                return self.err(409, e)
            except Exception as e:
                S.log(f"align {what}: {type(e).__name__}: {e}", "align")
                return self.err(422, f"{type(e).__name__}: {e}")
        if ep.startswith("job/"):
            what = ep[4:]
            try:
                if what == "status":
                    return self.send(200, S.job.status(int(q.get("since", 0) or 0)))
                if what == "image":                # the last vision picture of the job (mount screen)
                    data, seq = S.job.image(int(q.get("cam", 0) or 0), int(q.get("scale", 2) or 2))
                    if data is None:
                        return self.err(404, "no picture yet")
                    return self.send(200, data, "image/bmp", {"X-Seq": str(seq), "Cache-Control": "no-store"})
                if what == "list":                 # the job list: parts, panels, last used
                    return self.send(200, {"ok": True, "jobs": S.job.list(q.get("path", ""))})
                if what == "check":                # pick points the head cannot reach (neojob.reach)
                    from neocam.config import NeoConfig
                    from neojob.jobfile import load as load_job
                    from neojob.reach import problems
                    from neojob.reach import feeder_issues
                    from neojob import load_packages
                    cfg = NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini"))
                    j = load_job(q.get("path", ""))
                    pk = load_packages(os.path.join(S.st.neoden_dir, "Config", "pkg.csv"))
                    return self.send(200, {"ok": True, "problems": problems(j, cfg),
                                           "feeders": {str(k): v for k, v in feeder_issues(j, cfg, pk).items()}})
                if what == "resume":
                    from neojob import Runner
                    return self.send(200, {"ok": True, "resume": Runner.resume_file(q.get("path", ""))})
                if m != "POST":
                    return self.err(400, "use POST")
                if what == "start":
                    S.job.start(q.get("path", ""), q.get("resume", "") == "1", q.get("client", ""), q.get("only", ""))
                elif what == "answer":
                    S.job.give_answer(q.get("a", ""))
                elif what in ("pause", "continue", "step", "stop"):
                    S.job.control(what)
                else:
                    return self.err(404, "unknown job call")
            except Exception as e:
                return self.err(409, f"{type(e).__name__}: {e}")
            return self.ok()
        if ep == "setup/call" and m == "POST":
            try:
                req = json.loads(self.body() or b"{}")
            except ValueError:
                return self.err(400, "JSON body needed")
            code, reply = S.setup.call(req)
            return self.send(code, reply)
        if ep == "setup/close" and m == "POST":
            S.setup.close(q.get("sid", ""))
            return self.ok()
        if ep.startswith("mc/"):
            cmd, client = ep[3:], q.get("client", "")
            if cmd == "status":
                st = S.mc.status()
                ev = int(q.get("ev", -1) or -1)               # the shared events after the client's last one
                nxt, items = S.events.since(ev if ev >= 0 else 10 ** 9)
                st["ev_next"], st["events"] = nxt, items[-20:]
                return self.send(200, st)
            if cmd in ("lock", "unlock"):
                if not client:
                    return self.err(400, "client=<id> needed")
                ok, owner = S.mc.take(client, cmd == "unlock", hold=q.get("hold") == "1", addr=peer)
                return self.send(200, {"ok": ok, "owner": owner, "owner_from": S.mc.owner_label()})
            if cmd == "cancel":
                S.mc.cancel()
                return self.ok()
            if cmd == "stop":                    # the red STOP, from any client: everything stops, then home
                S.log(f"{peer}: STOP", "server")
                try:
                    if S.job.running():
                        S.job.control("stop")
                except Exception:
                    pass
                try:
                    S.calib.cancel()
                except Exception:
                    pass
                S.mc.cancel()

                def home():
                    try:
                        S.mc.stop_home()
                    except Exception as e:
                        S.log(f"STOP: home: {type(e).__name__}: {e}", "server")
                threading.Thread(target=home, daemon=True).start()
                return self.ok()
            if m != "POST":
                return self.err(400, "use POST")
            try:
                return self.ok(**S.mc.cmd(cmd, q, client))
            except Exception as e:                   # refused / machine error -> 409 like the agent
                if not isinstance(e, PermissionError):
                    S.log(f"machine {cmd}: {e}")
                return self.err(409, e)
        return self.err(404, "unknown API call")


class _S:
    pass


def main(argv=None):
    global S
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/etc/neoden/neoserver.ini")
    ap.add_argument("--write-default-config", action="store_true")
    a = ap.parse_args(argv)
    if a.write_default_config:
        os.makedirs(os.path.dirname(a.config), exist_ok=True)
        Settings.write_default(a.config)
        print("wrote", a.config)
        return
    S = _S()
    S.st = Settings(a.config)
    S.t0 = time.monotonic()
    os.makedirs(S.st.backup, exist_ok=True)
    S.log = Log(path=os.path.join(S.st.backup, "neoserver.log"))
    from neoserver.core import Events
    S.events = Events()
    S.log.events = S.events
    S.files = Files(S.st.backup, S.log)
    S.svc = Services(S.st.services, S.log)
    S.upd = Updates(S.st, S.files, S.svc, S.log)
    S.mc = Machine(S.st.machine, S.log)
    S.mc.home_at_start = S.st.home_at_start.strip().lower() in ("yes", "1", "true", "on")

    def run_speed():
        from neocam.config import NeoConfig
        try:
            return int(float(NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini")).raw.get("Default/nRunSpeed", 80)))
        except Exception:
            return 80
    S.mc.run_speed = run_speed

    def waste_box():                              # where a STOP drops parts left on the nozzles
        from neocam.config import NeoConfig
        try:
            return NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini")).pt("Position/posWasteBox")
        except Exception:
            return None
    S.mc.waste_box = waste_box
    S.mc.xy_file = os.path.join(S.st.neoden_dir, "xy_correction.json")
    threading.Thread(target=S.mc.connect_loop, daemon=True).start()
    threading.Thread(target=S.mc.lights_watch, daemon=True).start()
    S.cam = Cameras(S.st.camera, S.log)
    S.cam.view_file = os.path.join(S.st.neoden_dir, "camera.json")
    S.mc.cams = S.cam                                     # camera use counts as activity (lights_watch)
    S.setup = Setup(S.st.backup, S.log)
    S.job = JobService(S.st, S.mc, S.cam, S.log)
    S.align = AlignService(S.st, S.mc, S.cam, S.log)
    from neoserver.feedersetup import FeederSetup
    S.feedsetup = FeederSetup(S.align, S.cam, S.log)
    from neoserver.cammap_cal import CamMapCal
    S.cammap = CamMapCal(S.align, S.cam, S.st.neoden_dir, S.log)
    from neoserver.zmap_cal import ZMapCal
    S.zmap = ZMapCal(S.align, S.feedsetup, S.log, S.st.neoden_dir)
    S.job.zmap_cal = S.zmap                           # a job without a height map: measured at its start
    from neoserver.bulk import BulkService
    S.bulk = BulkService(S.st, S.mc, S.cam, S.log)
    S.wifi = Wifi(lambda t: S.log(t, "wifi"))

    def machine_cfg():
        from neocam.config import NeoConfig
        return NeoConfig.load(os.path.join(S.st.neoden_dir, "Config.ini"))
    from neocalib.autocal import AutoCal
    S.calib = AutoCal(S.mc, S.cam, machine_cfg, lambda t: S.log(t, "calib"))
    threading.Thread(target=S.wifi.ensure, daemon=True).start()   # a dongle plugged in while it was off
    if S.st.login.strip().lower() in ("yes", "1", "true", "on") and S.st.password == "neoden" or S.st.update_key == "change-me":
        S.log(f"WARNING: default password or update key in {a.config}; change them")
    httpd = ThreadingHTTPServer((S.st.bind, S.st.http_port), Handler)
    httpd.daemon_threads = True
    S.log(f"neoserver {VERSION} on {S.st.bind}:{S.st.http_port}, web UI from {S.st.www}, machine {S.st.machine}, camera {S.st.camera}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
