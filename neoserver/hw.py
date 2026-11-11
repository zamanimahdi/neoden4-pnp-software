"""neoserver hardware: one Mainboard (neomach) shared by all clients, with the control lock,
and the cameras (native USB on the machine, or camserver over TCP for development).

The /api/mc/* commands and the status JSON are the same as neoagent's (docs/NEOAGENT.md), so the
web UI works unchanged; the machine rules (nozzles up before XY moves, no lowering while XY moves,
homing, travel limits) are those of neomach.Mainboard.
"""
import os
import struct
import threading
import time
from contextlib import contextmanager

import numpy as np

LOCK_S = 15.0
IDLE_S = 10.0          # a holder idle this long hands control to the next client that asks (screen + LAN)


def where(addr):
    """who a client is, for people: the PC's own screen (kiosk) or the LAN address"""
    return "the machine screen" if addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1") else (addr or "")


class Machine:
    def __init__(self, url, log):
        self.url, self.log = url, log
        self.mb = None
        self.err = ""
        self.lock = threading.RLock()               # one command at a time
        self.owner, self.until = "", 0.0
        self.owner_from = ""                        # where(): the machine screen or an IP
        self.last_cmd = 0.0                         # last command / hold renewal of the owner
        self.idle_off = None                        # (head lamp, up light) as they were when switched off idle
        self.led_user = None                        # the head lamp level the operator last chose (led command)
        self.busy = 0                               # align calls running (minutes): no hand-over meanwhile
        self.olock = threading.Lock()
        self.job_running = False                    # neoserver.job: manual commands refused meanwhile
        self.jog_ok = False                         # ... except jogging while the job asks for a mark by hand
        self.last_xy = None                         # last XY state seen: homed -> not homed = controller restarted
        self.home_at_start = False                  # full home after connecting: only neoserver turns it on
                                                    # (neoserver.ini home_at_start), not tools / tests
        self.run_speed = lambda: 80                 # Config.ini Default/nRunSpeed (set by __main__)
        self.xy_file = ""                           # <data>/xy_correction.json (set by __main__)
        self.homing = False

    def board(self):
        if self.mb is None:
            from neomach import Mainboard
            self.mb = Mainboard.connect(self.url)
            self.mb.on_step_loss = lambda st, target, retry: self.log(
                f"STEP LOSS (XY state {st}) on the way to {target}: correction {retry + 1}; repeated losses mean a crash", "machine")
            self.log(f"machine connected: {self.url}")
            self.load_xy_correction()
            if self.home_at_start:
                self._home_after_connect()
        return self.mb

    def load_xy_correction(self):
        """the machine's linear XY error (neocalib.XYCorrection, measured on a precise board): every move
        is corrected by it; no file = no correction"""
        if self.mb is None:
            return
        self.mb.xy_map, self.xy_corr = None, None
        if self.xy_file and os.path.exists(self.xy_file):
            try:
                from neocalib import XYCorrection
                c = XYCorrection.load(self.xy_file)
                self.mb.xy_map, self.xy_corr = c.correct, c
                self.log(f"XY correction on: {c.summary()}", "machine")
            except Exception as e:
                self.log(f"XY correction not loaded: {type(e).__name__}: {e}", "machine")

    def _home_after_connect(self):
        """Like NeoDen4.exe at start-up: a full XY home and homeAll (nozzles up, 0 deg, park at 0, 400)
        whenever the mainboard is (re)connected: after boot, or when the machine is switched on after
        the PC. Commands wait for it (self.lock)."""
        if self.homing:
            return
        self.homing = True

        def go():
            try:
                time.sleep(0.5)
                with self.lock:
                    before = self.mb.startup(int(self.run_speed()))
                self.log(f"machine homed after connecting (XY state before: {before})", "machine")
            except Exception as e:
                self.log(f"homing after connecting failed: {type(e).__name__}: {e}", "machine")
            finally:
                self.homing = False
        threading.Thread(target=go, daemon=True).start()

    def connect_loop(self):
        """at server start: connect (and so home) as soon as the mainboard answers; retried every 5 s"""
        while self.mb is None:
            try:
                self.board()
            except Exception:
                time.sleep(5)

    LIGHTS_IDLE_S = 30.0

    def lights_watch(self):
        """head lamp and up-camera light off when nobody has used the machine for LIGHTS_IDLE_S (no command,
        no job, no calibration / align call running), so they do not stay on for nothing"""
        while True:
            time.sleep(5)
            mb = self.mb
            try:
                last = max(self.last_cmd, getattr(getattr(self, "cams", None), "last_grab", 0.0))
                if (mb is None or self.job_running or self.busy or self.homing
                        or time.monotonic() - last < self.LIGHTS_IDLE_S):
                    continue
                if getattr(mb, "led", 0) or (mb.sw[4] & 1):
                    with self.lock:
                        self.idle_off = (int(getattr(mb, "led", 0) or 0), bool(mb.sw[4] & 1))
                        mb.head_led(0)
                        mb.up_light(False)
                    self.log("lights off (idle)", "machine")
            except Exception:
                pass

    def relight(self):
        """the lights as they were before the idle switch-off: on the next picture or command, so a page
        left open does not come back to a dark camera"""
        off, self.idle_off = self.idle_off, None
        mb = self.mb
        if not off or mb is None:
            return
        try:
            with self.lock:
                if off[0] and not getattr(mb, "led", 0):
                    mb.head_led(off[0])
                if off[1] and not (mb.sw[4] & 1):
                    mb.up_light(True)
            self.log("lights on again", "machine")
        except Exception:
            pass

    # ---- control lock ----
    def _owner(self):
        return self.owner if self.owner and time.monotonic() < self.until else ""

    def owner_label(self):
        return self.owner_from if self._owner() else ""

    def take(self, client, release=False, hold=False, addr=""):
        """lock / renew / release. Another client gets it when the owner has been idle IDLE_S
        (no command, no hold=1 renewal), no job and no align call runs: the machine's own screen
        and a LAN browser can both work without pressing release."""
        with self.olock:
            cur = self._owner()
            now = time.monotonic()
            if release:
                if cur == client:
                    self.owner = ""
                return True, self._owner()
            if cur and cur != client:
                if self.job_running or self.busy or now - self.last_cmd < IDLE_S:
                    return False, cur
                self.log(f"machine control handed from {cur} ({self.owner_from}, idle) to {client} ({where(addr)})")
                cur = ""
            if cur != client:
                self.log(f"machine control taken by {client} ({where(addr)})")
                self.last_cmd = now
                self.owner_from = where(addr)
            elif addr:
                self.owner_from = where(addr)
            if hold:
                self.last_cmd = now
            self.owner, self.until = client, now + LOCK_S
            return True, client

    @contextmanager
    def busy_call(self):
        """a long call (align) by the owner: counts as busy for its whole duration"""
        if self.idle_off:
            self.relight()
        with self.olock:
            self.busy += 1
            self.last_cmd = time.monotonic()
        try:
            yield
        finally:
            with self.olock:
                self.busy -= 1
                self.last_cmd = time.monotonic()

    def cancel(self):
        if self.mb:
            self.mb.cancel()

    def stop_home(self, wait_s=15.0):
        """after the red STOP: once what was running has seen the cancel, valves off, nozzles up and at
        0 deg, the head to its home corner (Board.home_all). A job does that itself when it ends."""
        t0 = time.monotonic()
        while (self.job_running or self.busy) and time.monotonic() - t0 < wait_s:
            time.sleep(0.1)
        if self.job_running:
            return
        mb = self.board()
        with self.lock:
            if hasattr(mb, "_cancel"):
                mb._cancel.clear()                  # the STOP's cancel must not cancel this move too
            try:                                    # a part on a nozzle (a camera tool stopped): the waste box
                wb = getattr(self, "waste_box", None)
                mb.drop_held(wb() if callable(wb) else wb, self.log)
            except Exception as e:
                self.log(f"STOP: parts to the waste box: {type(e).__name__}: {e}")
            for n in range(1, 5):
                try:
                    mb.valve(n, 0)
                except Exception:
                    pass
            rs = getattr(self, "run_speed", None)
            mb.home_all(int((rs() if callable(rs) else rs) or 80))

    # ---- status (no lock needed) ----
    def status(self):
        st = {"ok": False, "bridge": self.url, "error": ""}
        # every screen asks every 0.7 s; the two serial queries wait on the job's commands (one lock): the
        # mainboard's answer is shared for 0.3 s (1 s while a job runs), position and owner always fresh
        c = getattr(self, "_st_cache", None)
        if c and time.monotonic() - c[0] < (1.0 if self.job_running else 0.3):
            st.update(c[1])
            s = None
        else:
            s = True
        try:
            if s is None:
                raise _Cached()
            mb = self.board()
            with self.lock:
                s = mb.status()
                idle, vac = mb.head_status()
            st.update(ok=True, xy_state=s["xy_state"], xy_state_name=s["xy_state_name"], rail_done=s["rail_done"],
                      input0=s["input0"], input1=s["input1"], idle=idle, vacuum=vac)
            # the machine was switched off and on while the PC stayed on: home again, like after boot
            if (self.home_at_start and s["xy_state"] == 1 and self.last_xy not in (None, 1)
                    and not self.homing and not self.job_running):
                self.log("machine reports XY not homed (switched off and on?): homing", "machine")
                self._home_after_connect()
            self.last_xy = s["xy_state"]
            self._st_cache = (time.monotonic(), {k: st[k] for k in ("ok", "xy_state", "xy_state_name", "rail_done", "input0", "input1", "idle", "vacuum")})
        except _Cached:
            pass
        except Exception as e:
            st["error"] = str(e)
            self._st_cache = None
            self.mb = None if "LinkError" in type(e).__name__ else self.mb
        mb = self.mb
        if mb is not None:
            st.update(pos=list(mb.pos) if mb.pos else None, z=mb.z[1:5], angle=mb.angle[1:5],
                      light=bool(mb.sw[4] & 1), led=int(getattr(mb, "led", 0) or 0), led_user_off=self.led_user == 0,
                      beep=bool(mb.sw[5] & 1), board_ready=bool(mb.sw[5] & 2),
                      xy_speed=mb.cfg[0], rail_speed=mb.cfg[3],
                      step_losses=len(getattr(mb, "step_losses", [])),
                      last_step_loss=(lambda l: {"t": l[0], "state": l[1], "target": l[2]} if l else None)(getattr(mb, "step_losses", [])[-1:] and mb.step_losses[-1]))
        st["owner"] = self._owner()
        st["owner_from"] = self.owner_label()
        st["homing"] = self.homing
        return st

    # ---- commands ----
    def cmd(self, name, q, client):
        with self.olock:
            if self._owner() != client:
                raise PermissionError("take control first (another client has it, or the lock expired)")
            if self.job_running and not (self.jog_ok and name in ("rel", "move", "led", "light")):
                raise PermissionError("a job is running: pause or stop it on the Job page first")
            self.until = self.last_cmd = time.monotonic()
            if name in ("led", "light"):
                self.idle_off = None                 # the operator sets the lights now
                if name == "led":
                    try:
                        self.led_user = int(float(q.get("v", 0) or 0))
                    except ValueError:
                        pass
            elif self.idle_off:
                threading.Thread(target=self.relight, daemon=True).start()
            self.until += LOCK_S
        f = lambda k, d=0.0: float(q.get(k, d))
        i = lambda k, d=0: int(float(q.get(k, d)))
        w = bool(i("wait", 1))
        mb = self.board()
        with self.lock:
            if name == "home": mb.home_xy()
            elif name == "correct": mb.xy_correct()
            elif name == "move": mb.move_xy(f("x"), f("y"), slow=bool(i("slow")), speed=i("speed") or None)
            elif name == "rel": mb.move_rel(f("x"), f("y"), slow=bool(i("slow")))
            elif name == "z":                    # just after a move XY may still report busy: wait (3 s), not refuse
                t0 = time.monotonic()
                while True:
                    try:
                        mb.set_z(i("n"), f("mm"), speed=i("speed", 0x96), wait=w)
                        break
                    except Exception as e:
                        if "busy" not in str(e) or time.monotonic() - t0 > 3.0:
                            raise
                        time.sleep(0.1)
            elif name == "rotate": mb.rotate(i("n"), f("deg"), speed=i("speed", 0x96), wait=w)
            elif name == "raise": mb.raise_all()
            elif name == "valve": mb.valve(i("ch"), i("state"))
            elif name == "led": mb.head_led(i("v"))
            elif name == "light": mb.up_light(bool(i("on")))
            elif name == "beep": mb.beep(bool(i("on")))
            elif name == "board_ready": mb.board_in_ready(bool(i("on")))
            elif name == "xy_speed": mb.set_xy_speed(i("v", 80), i("force", 9))
            elif name == "rail_speed": mb.set_rail_speed(i("v", 200))
            elif name == "rail_move": mb.rail_move(f("mm"), wait=w)
            elif name == "rail_stop": mb.rail_stop()
            elif name == "feed": mb.feed(i("n"), i("p0"), i("p1"), i("mode"))
            elif name == "feeder":
                done, failed, left = mb.feeder_status(i("n"))
                return {"done": done, "failed": failed, "remaining": left}
            elif name == "peel": mb.peel(i("n"), i("a"), i("b"))
            elif name == "vib": mb.vibration(i("a"), i("b"))
            elif name == "vib_feeder": mb.vibration_feeder(bool(i("on")), i("v"), i("p"))
            elif name == "reg_read": return {"data": bytes(mb.read(i("addr"))).hex()}
            elif name == "reg_write":
                d = bytes.fromhex(q.get("data", ""))
                if len(d) != 8:
                    raise ValueError("data = 16 hex digits")
                a = i("addr")
                if a == 6: mb.cfg[:] = d
                if a == 7: mb.sw[:] = d
                mb.write(a, d)
            else:
                raise KeyError(f"unknown machine command {name}")
        return {}


class Cameras:
    """camera backend + BMP output turned like NeoDen4.exe (up: transpose + flip, head: flip)"""

    VIEW = {0: {"exp": 150, "gain": -1, "win": 1024}, 1: {"exp": 25, "gain": 8, "win": 1024}}   # NeoDen4 at start

    def __init__(self, spec, log):
        self.spec, self.log, self.cam, self.lock = spec, log, None, threading.Lock()
        self.view_file = ""                     # <data>/camera.json: the viewing settings chosen on the Cameras page
        self.view = {c: dict(v) for c, v in self.VIEW.items()}
        self.cur_exp = {}                       # the exposure set now (auto_exposure), per camera

    def load_view(self):
        import json
        try:
            d = json.load(open(self.view_file, encoding="utf-8"))
            for c in (0, 1):
                self.view[c].update({k: int(v) for k, v in d.get(str(c), {}).items() if k in ("exp", "gain", "win")})
        except (OSError, ValueError, TypeError):
            pass

    def restore_view(self, cams=(0, 1)):
        """the viewing settings back (a vision task leaves its own exposure / window: a dark picture)"""
        with self.lock:
            c = self.dev()
            for n in cams:
                v = self.view[n]
                try:
                    c.window(n, v["win"], v["win"])
                    c.exposure(n, v["exp"])
                    self.cur_exp[n] = v["exp"]
                    if v["gain"] >= 0:
                        c.gain(n, v["gain"])
                except Exception:
                    pass

    def dev(self):
        if self.cam is None:
            if self.spec == "none":
                raise RuntimeError("no camera configured")
            if self.spec.startswith("tcp://"):
                from neocam.client import CamClient
                host, port = self.spec[6:].rsplit(":", 1)
                self.cam = CamClient(host, int(port))
            else:
                from neocam.usbcam import UsbCameras
                self.cam = UsbCameras.open()
            self.load_view()
            for c in (0, 1):                         # the viewing settings (NeoDen4 at start: 1024, head 25 / gain 8)
                v = self.view[c]
                try:
                    self.cam.window(c, v["win"], v["win"])
                    self.cam.exposure(c, v["exp"])
                    if v["gain"] >= 0:
                        self.cam.gain(c, v["gain"])
                except Exception:
                    pass
            self.log("camera: " + self.cam.info())
        return self.cam

    def info(self):
        try:
            with self.lock:
                return self.dev().info()
        except Exception as e:
            self.cam = None
            return f"camera: {e}"

    def auto_exposure(self, cam, target=200, tol=30, steps=7, lo=1, hi=400):
        """the exposure for what is under the camera now (white tape or dark board): the bright end of the
        picture (99th percentile) brought near `target` grey, not burnt out. Brightness follows the
        exposure about linearly; a burnt-out picture halves it. For this view only (not camera.json:
        a vision task or restore_view brings the viewing setting back). Returns exp, p99, median, steps"""
        import numpy as np
        with self.lock:
            c = self.dev()
            exp = int(self.cur_exp.get(cam, self.view.get(cam, {}).get("exp", 25)))
            n = p99 = med = 0
            for n in range(1, steps + 1):
                c.exposure(cam, exp)
                c.grab(cam)                                  # the frame after the change
                img = np.asarray(c.grab(cam))
                small = img[::4, ::4]
                p99, med = float(np.percentile(small, 99)), float(np.median(small))
                if abs(p99 - target) <= tol and p99 < 250:
                    break
                f = 0.5 if p99 >= 250 else max(0.35, min(3.0, target / max(p99, 1.0)))
                new = int(max(lo, min(hi, round(exp * f))))
                if new == exp:
                    break
                exp = new
            self.cur_exp[cam] = exp
            self.last_grab = __import__("time").monotonic()
        return {"exp": exp, "p99": round(p99, 1), "median": round(med, 1), "steps": n}

    def set(self, cam, exposure=0, gain=-1, w=0, h=0, led=-1, save=False):
        """the camera's window / exposure / gain / LED now; save: also its viewing settings (self.view,
        camera.json; the Cameras page's own controls only). A tool's, a calibration's or a page's own
        exposure is for that moment: restore_view goes back to the saved ones"""
        with self.lock:
            c = self.dev()
            if w > 0 and h > 0: c.window(cam, w, h)
            if exposure > 0:
                c.exposure(cam, exposure)
                self.cur_exp[cam] = int(exposure)
            if gain >= 0: c.gain(cam, gain)
            if led >= 0: c.led(cam, led)
        if save and cam in self.view and (exposure > 0 or gain >= 0 or w > 0):    # kept: the viewing settings (camera.json)
            v = self.view[cam]
            if exposure > 0: v["exp"] = int(exposure)
            if gain >= 0: v["gain"] = int(gain)
            if w > 0: v["win"] = int(w)
            if self.view_file:
                try:
                    import json
                    json.dump({str(k): x for k, x in self.view.items()}, open(self.view_file, "w", encoding="utf-8"))
                except OSError:
                    pass

    def grab_bmp(self, cam, scale=1, settle=False):
        """BMP of one frame; settle: grab until the image stops moving (neocalib.grab_settled,
        instead of NeoDen's fixed 250 ms after a move). self.last_settle = its stats or None."""
        self.last_settle = None
        self.last_grab = time.monotonic()                   # a picture watched: the lights are in use
        with self.lock:
            try:
                if settle:
                    from neocalib import grab_settled
                    d = self.dev()
                    r = grab_settled(lambda: d.grab(cam))
                    raw, self.last_settle = r.frame, r
                else:
                    raw = self.dev().grab(cam)
            except Exception:
                self.cam = None                     # reconnect next time
                raise
        img = np.flip(raw.T, (0, 1)) if (cam & 0x7F) == 0 and raw.shape[0] == raw.shape[1] else np.flip(raw, (0, 1))
        if self.view_file:                                   # the camera's pixel map, when it is on
            from neocalib import cammap
            img = cammap.apply(img, cam, os.path.dirname(self.view_file))
        if not hasattr(self, "last_img"):
            self.last_img = {}
        self.last_img[cam & 0x7F] = img                     # the last picture (oriented, full size): /api/cam/pockets
        scale = max(1, min(8, int(scale)))
        img = np.ascontiguousarray(img[::scale, ::scale])
        return bmp8(img)


class _Cached(Exception):
    """Machine.status: the mainboard's last answer is still fresh"""


_PAL8 = bytes(b for i in range(256) for b in (i, i, i, 0))


def bmp8(img):
    """8-bit gray BMP (bottom-up rows, 4-byte aligned)"""
    h, w = img.shape
    stride = (w + 3) & ~3
    pal = _PAL8
    rows = np.zeros((h, stride), np.uint8)
    rows[:, :w] = img[::-1]
    size = 54 + 1024 + stride * h
    head = b"BM" + struct.pack("<IHHI", size, 0, 0, 54 + 1024) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 8, 0, stride * h, 2835, 2835, 256, 0)
    return head + pal + rows.tobytes()
