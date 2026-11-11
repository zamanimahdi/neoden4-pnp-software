"""Placement run on a fixed table (pcb mode Lock), as NeoDen4.exe does it (JOB_SPEC.md section 2).

    r = Runner(mb, cfg, vision, ask=..., log=...)
    r.run(job)            # blocks; pause() / step() / resume() / stop() from other threads

Differences from NeoDen4.exe, all on purpose (JOB_SPEC.md section 4):
- a part that failed the pick is blown off from the nozzle that picked it (not slot[nozzle]);
- a part that failed vision is discarded before it is picked again (an empty nozzle is not: nothing to throw away);
- "No" always aborts (also in the discard / rotate dialogs);
- with 3+ marks the farthest pair is used; the transform keeps NeoDen's formula otherwise;
- after every cycle the progress goes to a resume file, so a stopped job continues where it was.
Rail modes (Trace / TraceSep) are refused: this machine has the fixed table only.
"""
import json
import math
import os
import threading
import time

import numpy as np

from .transform import MarkTransform

YES, NO, IGNORE = "yes", "no", "ignore"
RUN, PAUSE, STEP, IDLE = 0, 1, 2, 3
STATE_NAMES = {RUN: "running", PAUSE: "paused", STEP: "single step", IDLE: "idle"}


class Abort(Exception):
    pass


class Runner:
    def __init__(self, mb, cfg, vision, ask, log=print, packages=None, beep=None):
        self.mb, self.cfg, self.vision, self._ask, self.log = mb, cfg, vision, ask, log
        self.packages = packages or {}            # footprint -> (L, W, H) mm (Config/pkg.csv)
        self.state = IDLE
        self.req = threading.Condition()
        self.flags = {"start": False, "step": False, "stop": False}
        self.placed = 0
        self.panel = self.comp = 0
        self.job = None
        self.done = set()                         # (panel, comp) placed, for resume
        self.question = None
        self.speed_cache = 0
        self.t0 = 0.0
        self.t_end = None                         # the job ended: the elapsed time stops there
        self.paused_s = 0.0                       # time spent paused / between single steps (not counted)
        self.pause_from = None
        self.result = None

    # ---- state machine (CProcPaist::checkRunState) ----
    def _set(self, k):
        with self.req:
            self.flags[k] = True
            self.req.notify_all()

    def pause(self):
        if self.state == RUN:
            self._set("start")             # running: start = pause at the next checkpoint

    def resume(self):
        if self.state in (PAUSE, STEP):
            self._set("start")

    def step(self):
        self._set("step")

    def stop(self):
        self._set("stop")
        self.mb.cancel() if hasattr(self.mb, "cancel") else None

    def checkpoint(self):
        with self.req:
            waited = None
            while True:
                f = self.flags
                if self.state == IDLE:
                    raise Abort("not running")
                if f["stop"]:
                    f["stop"] = False
                    raise Abort("stopped by the operator")
                if self.state == RUN:
                    if f["step"]:
                        f["step"] = False
                        self.state = STEP
                    elif f["start"]:
                        f["start"] = False
                        self.state = PAUSE
                        self.log("paused")
                    else:
                        return
                else:
                    if waited is None:
                        waited = self.pause_from = time.monotonic()
                    if f["step"]:
                        f["step"] = False
                        self.state = STEP
                        break
                    if f["start"]:
                        f["start"] = False
                        self.state = RUN
                        self.log("continued")
                        break
                self.req.wait(0.2)
            if waited is not None:
                self.paused_s += time.monotonic() - waited
                self.pause_from = None
        self.reload_settings()          # left a pause: the operator may have edited the job / Config.ini

    # fields of the job file taken again when a paused job continues (JOB_SPEC 1.1 / 1.8);
    # marks, panels and the list of parts (count, order) are not: they are measured / planned
    STACK_FIELDS = ("type", "nozzles", "x", "y", "angle", "footprint", "value", "pick_height", "pick_delay",
                    "place_height", "place_delay", "vacuum_detect", "threshold", "vision", "speed", "pitch",
                    "feed_strength", "peel_strength", "cols", "rows", "end_x", "end_y", "skip", "size_check", "thr",
                    "pocket_check", "part_l", "part_w", "pad_align", "second_look", "part_h", "bulk", "place_check")
    COMP_FIELDS = ("stack", "nozzle", "value", "footprint", "x", "y", "rot", "skip", "pad_align", "size_check")

    def reload_settings(self):
        """read the job file and Config.ini again and apply them to what is not placed yet.
        Objects are updated in place, so the cycle in progress sees the new values too."""
        job = self.job
        if not job or not job.path:
            return
        try:
            from .jobfile import load
            new = load(job.path)
        except Exception as e:
            self.log(f"job file not read again ({e}): old settings kept")
            return
        changed = []
        before = {sid: {f: getattr(st, f) for f in self.STACK_FIELDS} for sid, st in job.stacks.items()}
        for sid, ns in new.stacks.items():
            st = job.stacks.get(sid)
            if st is None:
                job.stacks[sid] = ns
                changed.append(f"stack {sid} added")
                continue
            for f in self.STACK_FIELDS:
                setattr(st, f, getattr(ns, f))
        if len(new.comps) == len(job.comps) and all(a.name == b.name for a, b in zip(new.comps, job.comps)):
            for i, (c, nc) in enumerate(zip(job.comps, new.comps)):
                d = [f for f in self.COMP_FIELDS if getattr(c, f) != getattr(nc, f)]
                for f in d:
                    setattr(c, f, getattr(nc, f))
                if d:
                    changed.append(f"{c.name}: {', '.join(d)}")
        else:
            self.log("the list of parts changed (added, removed or moved): part edits are not taken mid-job; stop and start again")
        if (new.marks != job.marks) or (len(new.panels) != len(job.panels)):
            self.log("marks / panels changed: they are used from the next start")
        try:
            from neocam.config import NeoConfig
            if getattr(self.cfg, "path", None):
                fresh = NeoConfig.load(self.cfg.path)
                fresh.raw.update({k: v for k, v in self.cfg.raw.items() if k not in fresh.raw})
                self.cfg.raw = fresh.raw
        except Exception as e:
            self.log(f"Config.ini not read again ({e})")
        self.assign()                                   # the machine feeder table may have changed too
        for sid, old in before.items():                 # what really changed (after assign put the table back)
            st = job.stacks.get(sid)
            d = [f for f in self.STACK_FIELDS if st is not None and getattr(st, f) != old[f]]
            if d:
                changed.insert(0, f"stack {sid}: {', '.join(d)}")
        if changed:
            self.log("settings taken: " + "; ".join(changed[:20]) + (" ..." if len(changed) > 20 else ""))

    def assign(self):
        """neojob.assign: feeder data (feed amount / torque) from the machine's feeder table into the
        job's stacks, and each part's feeder / nozzle; at every start and after a pause"""
        try:
            from .assign import assign, summary
            for line in summary(assign(self.job, self.cfg)):
                self.log(line)
        except Exception as e:
            self.log(f"assign: {type(e).__name__}: {e}")

    # ---- operator questions (reportErrorAndAsk) ----
    def ask(self, msg, ignore=False):
        self.log("ASK: " + msg)
        if self.cfg.job("bSound") and self.cfg.job("bBeepError"):
            self._beep(1)
        self.question = {"text": msg + ", try again?", "buttons": [YES, NO] + ([IGNORE] if ignore else [])}
        try:
            a = self._ask(self.question["text"], self.question["buttons"])
        finally:
            self.question = None
        self.log(f"answer: {a}")
        return a

    def retry(self, what, f, ignore=False):
        """run f() until it works; ask on errors: yes again, no abort, ignore -> False.
        Steps lost on XY (the controller's own correction gave up) or XY not referenced any more:
        XY is homed again, the last target sent again with the one-sided approach, and f() run
        again, up to Job/nRehomeTries times before the operator is asked."""
        from neomach import NotHomed, StepLoss
        rehomes = 0
        while True:
            try:
                return f()
            except Abort:
                raise
            except (StepLoss, NotHomed) as e:
                if rehomes < int(self.cfg.job("nRehomeTries")) and not self.job.test:
                    rehomes += 1
                    target = getattr(self.mb, "_target", None)
                    self.log(f"{what}: {e}: XY homed again ({rehomes}) and back to work")
                    try:
                        self.mb.home_xy()               # the nozzles go up first; held parts stay on (vacuum)
                        if target:
                            self.mb.move_xy(*target, slow=True)
                    except Exception as e2:
                        self.log(f"homing again failed: {e2}")
                    continue
                a = self.ask(f"{what} ({e})", ignore)
                if a == NO:
                    raise Abort(what)
                if a == IGNORE:
                    return False
            except Exception as e:
                a = self.ask(f"{what} ({e})", ignore)
                if a == NO:
                    raise Abort(what)
                if a == IGNORE:
                    return False

    def _beep(self, n):
        try:
            if hasattr(self.mb, "beep_pulse"):
                self.mb.beep_pulse(n)
        except Exception:
            pass

    # ---- config helpers ----
    def feeder_of(self, sid):
        return int(self.cfg.get(f"Stack/lstStack{sid - 1}.nFeedId", sid)), int(self.cfg.get(f"Stack/lstStack{sid - 1}.nPeelId", sid))

    def nozzle_offset(self, n):
        return self.cfg.nozzle_offset(n)

    # ---- the job ----
    def run(self, job, resume=None, only=None):
        """run the job; resume = a dict from resume_file() to continue. Returns True when finished.
        only = a component index: place that part alone on the first panel to be placed (a placement
        test from the feeder settings), then a head camera picture of it; the resume record is kept."""
        self.only = only
        if job.mode != "Lock":
            raise ValueError(f"pcb mode {job.mode}: this machine has the fixed table only (Lock)")
        self.job, self.placed, self.done = job, 0, set()
        self.panel = self.comp = 0
        if resume:
            fp = resume.get("parts")
            if fp and fp != self.fingerprint(job):     # the indices would point at other parts now
                raise ValueError("the job was changed since it stopped (parts or panels): it cannot go on where it "
                                 "stopped; start a new board, or undo the change")
            self.done = {tuple(x) for x in resume.get("done", [])}
            self.panel, self.comp = resume.get("panel", 0), resume.get("comp", 0)
            for sid, (c, r) in resume.get("trays", {}).items():
                s = job.stack(int(sid))
                if s:
                    s.cur_col, s.cur_row = c, r
            self.log(f"resume at panel {self.panel + 1}, component {self.comp + 1} ({len(self.done)} placed before)")
        with self.req:
            self.flags = {"start": False, "step": False, "stop": False}
            self.state = RUN
        self.mb.sound = bool(self.cfg.job("bSound"))
        self.result = None
        ok = False
        try:
            # the server homes XY once after boot (neoserver.hw); a job only homes when XY is not
            # referenced or lost steps, then raises the nozzles and sets the run speed
            st = self.mb.xy_state()
            if st != 0:
                self.log(f"XY is {('not homed', 'busy', 'step loss X', 'step loss Y', 'step loss X+Y')[min(st, 5) - 1] if st else st}: homing first")
                self.mb.home_xy()
            self.mb.raise_all()
            self.mb.set_xy_speed(int(self.cfg.get("Default/nRunSpeed", 80)))
            self.speed_cache = 0
            from .visionlog import VisionLog
            root = os.path.dirname(self.cfg.path) if getattr(self.cfg, "path", None) else os.path.dirname(os.path.dirname(job.path))
            self.vlog = VisionLog(root, job.path, self.cfg, self.log)
            if self.vlog.on:
                self.log(f"vision log on: {self.vlog.dir}")
            if hasattr(self.vision, "vlog"):
                self.vision.vlog = self.vlog if self.vlog.on else None
            from .learn import PickLearner, Stats
            self.stats = Stats(root, on=bool(self.cfg.job("bStats")))
            self.picklearn = PickLearner(job.path + ".picklearn.json", self.cfg.job("nPickLearnSamples"),
                                         self.cfg.job("fPickLearnMaxMm")) if self.cfg.job("bPickLearn") and job.path else None
            self.start_panel = self.panel
            self.assign()                         # feeder data + feeder / nozzle of the parts, every start
            self.vision.start(job, self)          # backgrounds (acquireBackground)
            ok = self.placement_job()
        except Abort as e:
            self.log(f"Exit current mounting task, current panel [{self.panel + 1}], next component [{self.comp + 1}]: {e}")
        except Exception as e:
            self.log(f"job error: {type(e).__name__}: {e}")
        finally:
            self.t_end = time.monotonic()
            if getattr(self, "picklearn", None) is not None:
                try:
                    self.picklearn.save()
                except Exception as e:
                    self.log(f"learnt pick points not saved: {e}")
            if getattr(self, "stats", None) is not None and self.only is None:
                self.stats.add("job", job=os.path.basename(job.path or ""), seconds=round(self.elapsed()), placed=self.placed, ok=bool(ok))
            if getattr(self, "_vac", None) is not None and getattr(self, "_vac_path", None):
                try:
                    self._vac.save(self._vac_path)
                except Exception as e:
                    self.log(f"vacuum levels not saved: {e}")
            if self.pause_from:                     # stopped during a pause: that pause is not working time
                self.paused_s += self.t_end - self.pause_from
                self.pause_from = None
            if self.only is None:
                self.save_resume(finished=ok)
                trays = {s.id: (s.cur_col, s.cur_row) for s in self.job.stacks.values() if s.type == 1 and not getattr(s, "bulk", 0)}
                if trays and self.job.path:                 # the next run starts at the next cell, not cell 1
                    try:
                        from .jobfile import save_tray_counters
                        save_tray_counters(self.job.path, trays)
                    except Exception as e:
                        self.log(f"tray counters not saved: {e}")
            with self.req:
                self.state = IDLE
            try:
                if hasattr(self.mb, "_cancel"):
                    self.mb._cancel.clear()             # a Stop's cancel must not cancel the way home too
                if hasattr(self.mb, "drop_held"):       # parts still on nozzles: the waste box, not the board
                    self.mb.drop_held(self.cfg.pt("Position/posWasteBox"), self.log)
                self.mb.home_all(int(self.cfg.get("Default/nRunSpeed", 80)))
            except Exception as e:
                self.log(f"homeAll after the job: {e}")
            if self.cfg.job("bSound") and self.cfg.job("bBeepJobEnd"):
                self._beep(1 if ok else 3)
            self.result = "finished" if ok else "stopped"
            self.log(f"job {self.result}: {self.placed} parts placed")
        return ok

    def placement_job(self):
        job = self.job
        self.t0 = time.monotonic()
        self.t_end, self.paused_s, self.pause_from = None, 0.0, None
        if not job.panels:
            self.log("no panel (mirror) line: nothing to place")
            return True
        if not job.comps:
            return True
        tf = MarkTransform()
        tf.fit = str(self.cfg.job("sMarkFit") or "neoden")
        tf.set_axes(self.cfg.job("fAxisScaleX"), self.cfg.job("fAxisScaleY"), self.cfg.job("fAxisSkewDeg"))
        from .pads import load_pads
        self.pads = (load_pads(job.path) or {}).get("parts", {})
        if self.pads:
            self.log(f"pad alignment data: {len(self.pads)} parts")
        from . import zmap
        self.zmap = zmap.load(job.path, os.path.dirname(self.cfg.path) if getattr(self.cfg, "path", None) else os.path.dirname(os.path.dirname(job.path)))
        if self.zmap:
            m, b = self.zmap.machine, self.zmap.board_z
            self.log((f"machine height map: {len(m.points)} points, {float(m.Zv.min() - m.ref):+.3f} .. {float(m.Zv.max() - m.ref):+.3f} mm"
                      + ("" if m.on else " (switched off)") if m else "no machine height map")
                     + (f"; board height {b:.3f}: place depth = board + part height" if b is not None else "; no board height"))
            self.need_part_heights(job)
        R, C = (job.panels[0].x, job.panels[0].y), job.anchor()
        if job.origin is not None:
            self.log(f"board zero point (origin line): {C[0]:.3f}, {C[1]:.3f}")
        if job.mark_whole and job.marks:
            tf.set_points([(m.x, m.y) for m in job.marks], self.locate_marks(job.panels[0]))
            self.log_fit(tf)
        for p in range(self.panel, len(job.panels)):
            P = job.panels[p]
            self.panel = p
            if P.skip == 1:
                continue
            if P.skip == 2 and self.bad_panel(P):
                self.log(f"panel {p + 1}: bad mark, skipped")
                continue
            if not job.mark_whole and job.marks:
                tf.set_points([(m.x, m.y) for m in job.marks], self.locate_marks(P))
                self.log_fit(tf)
            tf.set_params(job.pcb_angle, R, (P.x, P.y), C, job.mark_whole, P.angle)
            c = self.comp
            look = max(0, int(self.cfg.job("nCycleLookahead") or 0))
            mix = bool(self.cfg.job("bCycleMixSpeeds"))
            tried = set()                                   # taken into a cycle on this panel (placed or not)
            while True:
                slots, i, first_left = [], c, None
                while i < len(job.comps) and len(slots) < 4:
                    comp = job.comps[i]
                    st = job.stack(comp.stack)
                    test = getattr(self, "only", None) is not None
                    if test and i != self.only:
                        i += 1
                        continue
                    if (p, i) in self.done or i in tried or (comp.skip and not test) or st is None or (st.skip and not test) or not 1 <= comp.nozzle <= 4:
                        i += 1
                        continue
                    if slots and (comp.nozzle in [s["nozzle"] for s in slots] or (not mix and st.speed != slots[0]["stack"].speed)):
                        # its nozzle is taken: left for the next cycle; a part a little further on may fill this one
                        if first_left is None:
                            first_left = i
                        if not look or i - first_left >= look:
                            break
                        i += 1
                        continue
                    slots.append({"index": i, "comp": comp, "stack": st, "nozzle": comp.nozzle, "ok": False, "held": False})
                    i += 1
                c = first_left if first_left is not None else i
                if not slots:
                    break
                tried.update(s["index"] for s in slots)
                self.comp = slots[0]["index"]
                self.cycle(slots, tf, p)
                self.comp = c
                if getattr(self, "only", None) is not None:
                    self.test_picture(slots[0])
                    return True
                self.save_resume()
            self.comp = 0
        self.panel = len(job.panels)
        self.log(f"it took {int(time.monotonic() - self.t0)} seconds to finish the board")
        return True

    def test_picture(self, s):
        """after a test placement: the head camera over the part (the mount / feeder screen shows it)"""
        if "target" not in s:
            self.log(f"test: {s['comp'].name} was not placed")
            return
        X, Y, _ = s["target"]
        cam = self.cfg.head_cam_offset
        self.mb.raise_all()
        self.mb.move_xy(X + cam[0], Y + cam[1], timeout=20)
        if hasattr(self.vision, "head_for_pads"):
            self.vision.head_for_pads()
            time.sleep(0.25)
            self.vision.grab(1)
        self.log(f"test: {s['comp'].name} placed at {X:.3f}, {Y:.3f}; head camera picture taken")

    def log_fit(self, tf):
        n = len(tf.design)
        used = tf.fit if (tf.fit == "affine" and n >= 3) or (tf.fit == "bilinear" and n >= 4) else ("affine" if tf.fit == "bilinear" and n == 3 else "neoden")
        res = tf.residuals() if used != "neoden" else []
        self.log(f"board transform: {used} on {n} marks" + (f", mark residuals {', '.join(f'{r:.3f}' for r in res)} mm" if res else ""))

    # ---- fiducials ----
    def locate_marks(self, P):
        job = self.job
        A = job.anchor()
        off = (P.x - A[0], P.y - A[1])
        cam = self.cfg.head_cam_offset
        out = []
        for i, m in enumerate(job.marks):
            # a panel turned 180 about the anchor A: P + (A - m), as the transform places it (was P - A - m)
            tx, ty = (off[0] + m.x, off[1] + m.y) if P.angle != 180 else (P.x + A[0] - m.x, P.y + A[1] - m.y)
            pos = None
            if not job.mark_manual:
                self.checkpoint()
                self.retry(f"Movement to mark {i + 1} failed", lambda: self.mb.move_xy(tx + cam[0], ty + cam[1], timeout=10))
                pos = self.find_mark(i, m, tx, ty)
                while pos is None:
                    self._beep(1)
                    r = self.ask_manual_mark(i, tx, ty)
                    if r == "again":
                        pos = self.find_mark(i, m, tx, ty)
                        continue
                    if r is None:
                        raise Abort("Mark positioning failed.")
                    pos = self.manual_pos
            else:
                if self.ask_manual_mark(i, tx, ty) is None:
                    raise Abort("Mark positioning failed.")
                pos = self.manual_pos
            out.append(pos)
            self.log(f"mark {i + 1}: design {m.x:.3f}, {m.y:.3f} -> machine {pos[0]:.3f}, {pos[1]:.3f}")
        self.checkpoint()
        bad = self.check_marks(job.marks, out)
        if bad:
            self._beep(1)
            a = self.ask(f"Marks do not match the board: {bad}. Yes = find them again, Ignore = use them anyway, No = stop", ignore=True)
            if a == YES:
                return self.locate_marks(P)
            if a != IGNORE:
                raise Abort("the marks found do not match the board")
            self.log(f"marks used although {bad}")
        return out

    def check_marks(self, marks, found):
        """the distance between every two marks found must be the design distance (a wrong spot taken
        for a mark, or a wrong board, shows here); returns what is wrong, or ''"""
        base = float(self.cfg.job("fMarkMaxErrMm") or 0.3)
        pct = float(self.cfg.job("fMarkMaxErrPct") or 0.25)
        worst = None
        for a in range(len(marks)):
            for b in range(a + 1, len(marks)):
                d0 = math.hypot(marks[a].x - marks[b].x, marks[a].y - marks[b].y)
                d1 = math.hypot(found[a][0] - found[b][0], found[a][1] - found[b][1])
                lim_ab = base + pct / 100.0 * d0          # the machine's own scale error grows with the distance
                if worst is None or abs(d1 - d0) - lim_ab > worst[0] - worst[5]:
                    worst = (abs(d1 - d0), a, b, d0, d1, lim_ab)
        if worst and worst[0] > worst[5]:
            e, a, b, d0, d1, lim = worst
            self.log(f"marks {a + 1}-{b + 1}: {d1:.3f} mm apart, the board says {d0:.3f} mm ({e:.3f} mm off, limit {lim:g})")
            return f"marks {a + 1} and {b + 1} are {d1:.2f} mm apart, the board says {d0:.2f} mm"
        return ""

    manual_pos = None

    def find_mark(self, i, m, tx, ty, step=1.5):
        """the mark where it should be, then at 4 points around it (a board laid a little off)"""
        cam = self.cfg.head_cam_offset
        for k, (dx, dy) in enumerate(((0, 0), (step, 0), (-step, 0), (0, step), (0, -step))):
            if k:
                self.checkpoint()
                try:
                    self.mb.move_xy(tx + dx + cam[0], ty + dy + cam[1], timeout=10)
                except Exception as e:
                    self.log(f"mark {i + 1}: move failed: {e}")
                    continue
            try:
                pos = self.vision.mark(m, self)
            except Exception as e:
                self.log(f"mark {i + 1}: {e}")
                pos = None
            if pos is not None:
                off = math.hypot(pos[0] - tx, pos[1] - ty)
                lim = float(self.cfg.job("fMarkMaxOffMm") or 5.0)
                if off > lim:
                    self.log(f"mark {i + 1}: a round spot {off:.2f} mm from where the mark should be (more than {lim:g} mm): not taken")
                    continue
                if k:
                    self.log(f"mark {i + 1}: found {dx:+.1f}, {dy:+.1f} mm from where it should be")
                return pos
        self.log(f"mark {i + 1}: not found (searched around {tx:.2f}, {ty:.2f})")
        return None

    def ask_manual_mark(self, i, tx, ty):
        """the operator centres the head camera on the mark (jog on the mount screen) and answers yes;
        the point = head - camera offset. Ignore = search again; returns "again", None (stop) or the point"""
        cam = self.cfg.head_cam_offset
        try:
            self.mb.move_xy(tx + cam[0], ty + cam[1], timeout=10)
        except Exception as e:
            self.log(f"mark {i + 1}: move failed: {e}")
        a = self.ask(f"Mark {i + 1} not found: bring the head camera's crosshair onto the mark (arrows, or click the head "
                     f"picture), then Yes. Ignore = search again, No = stop", ignore=True)
        if a == IGNORE:
            return "again"
        if a != YES or self.mb.pos is None:
            return None
        self.manual_pos = (self.mb.pos[0] - cam[0], self.mb.pos[1] - cam[1])
        return self.manual_pos

    def bad_panel(self, P):
        job = self.job
        R = job.panels[0]
        d = (job.bad_mark[0] - R.x, job.bad_mark[1] - R.y) if P.angle == 0 else (R.x - job.bad_mark[0], R.y - job.bad_mark[1])
        cam = self.cfg.head_cam_offset
        try:
            self.mb.move_xy(P.x + d[0] + cam[0], P.y + d[1] + cam[1], timeout=10)
            m = self.vision.bad_mark(job.bad_mark_exposure, self)
        except Exception as e:
            self.log(f"bad mark test failed ({e}): panel treated as good")
            return False
        return m is not None and not 63 <= m <= 191

    # ---- one cycle (runPickPlaceCycle) ----
    def cycle(self, slots, tf, p):
        speed = min(s["stack"].speed for s in slots)        # mixed feeders: the slowest of them
        if speed != self.speed_cache:
            self.speed_cache = speed
            self.mb.set_xy_speed(speed, 9)
        tries = 0
        left = slots
        route = bool(self.cfg.job("bCycleRoute")) and not getattr(self, "only", None)
        while True:
            if route and getattr(self.mb, "pos", None):        # the picks of a cycle: nearest first
                left = self.nearest_order(left, self.mb.pos, lambda s: (s["stack"].x + self.nozzle_offset(s["nozzle"])[0],
                                                                        s["stack"].y + self.nozzle_offset(s["nozzle"])[1]))
            for s in left:
                if not s["held"]:
                    self.pick(s)
            left = [s for s in left if s["held"]]
            if not left:
                return
            results = self.vision.parts(left, self) if not self.job.test else {}
            for s in left:
                self.check_picked(s, results.get(s["nozzle"]))
                if s.get("ok") and s["stack"].vision > 0:
                    self.learn_pick(s, results.get(s["nozzle"]))
            for w in (self.stats.warn(float(self.cfg.job("fStatsWarnPct"))) if getattr(self, "stats", None) else []):
                self.log(w)
            for s in left:
                if s["stack"].vision < 1 or s["ok"] or self.job.test:
                    s["target"] = self.prepare(s, tf, results.get(s["nozzle"]))
            see_turn = bool(self.cfg.job("bSeeTurnSee"))   # single-part vision: always see, turn, see
            again = [s for s in left if "target" in s and (getattr(s["stack"], "second_look", False) or (see_turn and not s.get("joint")))
                     and not self.job.test and s["stack"].vision > 0 and s.get("res") is not None and s["res"].valid]
            if again:
                self.second_look(again)
            first = True
            todo = [s for s in left if "target" in s]
            if route and len(todo) > 1 and getattr(self.mb, "pos", None) and self.same_height(todo):
                todo = self.nearest_order(todo, self.mb.pos, lambda s: s["target"][:2])     # from the camera, nearest first
            photo = [s for s in todo if self.check_mode(s, p) in ("photo", "both")]
            for s in photo:                        # the place point before: the photo check compares
                s["before"] = self.retry(f"{s['comp'].name}: head camera picture failed", lambda: self.vision.head_shot(*s["pre"]), ignore=True)
            for s in todo:
                if not self.place(s, first):       # the part came back on the nozzle: thrown away, picked again
                    continue
                first = False
                self.placed += 1
                self.done.add((p, s["index"]))
                s["placed"] = True
            for s in photo:
                if s.get("placed") and s.get("before") is not None:
                    self.photo_check(s)
            left = [s for s in left if not s.get("placed")]
            if not left:
                return
            for s in left:                         # vision failed: throw the part away first
                if s["held"]:
                    self.discard(s)
                s.pop("target", None)
            if tries == 2:
                names = ", ".join(f"[{s['nozzle']}]# Nozzle, [{s['comp'].stack}]# Feeder" for s in left)
                a = self.ask(names + " Multiple suction failures", ignore=True)
                if a == NO:
                    raise Abort("multiple suction failures")
                if a == IGNORE:
                    self.log("skip paist manual: " + ", ".join(s["comp"].name for s in left))
                    return
                tries = 1
            else:
                tries += 1

    def stat(self, kind, s, **f):
        st = getattr(self, "stats", None)
        if st is None or self.job.test:
            return
        c, sk = s["comp"], s["stack"]
        st.add(kind, feeder=sk.id, nozzle=s["nozzle"], part=f"{c.value}/{c.footprint}".strip("/"), ref=c.name, **f)

    def learn_pick(self, s, res):
        """the part's offset on the nozzle (up camera) -> the feeder's pick point (Job/bPickLearn)"""
        pl, st = getattr(self, "picklearn", None), s["stack"]
        if pl is None or self.job.test or getattr(st, "bulk", 0) or res is None or not res.valid:
            return
        new = pl.add(st.id, float(res.x), float(res.y))
        if new:
            self.log(f"feeder {st.id}: pick point corrected by {new[0]:+.3f}, {new[1]:+.3f} mm (learnt from its parts' offsets)")

    def vac_learner(self):
        """per-nozzle open / sealed vacuum levels learnt from good picks, kept next to Config.ini"""
        if getattr(self, "_vac", None) is None:
            from neomach import VacuumLearner
            root = os.path.dirname(self.cfg.path) if getattr(self.cfg, "path", None) else None
            self._vac_path = os.path.join(root, "vacuum_learn.json") if root else None
            try:
                self._vac = VacuumLearner.load(self._vac_path) if self._vac_path and os.path.exists(self._vac_path) else VacuumLearner()
            except Exception:
                self._vac = VacuumLearner()
        return self._vac

    def zspeed(self):
        return max(1, min(255, self.speed_cache * 150 // 130))

    def bulk_feeder(self):
        if getattr(self, "_bulk", None) is None:
            from .bulkfeed import BulkFeeder
            root = os.path.dirname(self.cfg.path) if getattr(self.cfg, "path", None) else os.path.dirname(os.path.dirname(self.job.path))
            self._bulk = BulkFeeder(root, self.vision, self.mb, self.cfg, self.log)
        return self._bulk

    def bulk_pick(self, s):
        """a part from a bulk feeder: the next one that is there, is the package and lies alone (looked
        at centred), picked by the vacuum; an empty container: scanned again, then the operator refills"""
        st, n = s["stack"], s["nozzle"]
        bf = self.bulk_feeder()
        cid = int(st.bulk)
        c = bf.container(cid) or {}
        scanned, failed = False, 0
        extra = float(self.cfg.get("Job/fBulkPressMm", 1.0) or 1.0)
        while True:
            self.checkpoint()
            r = bf.next_part(cid)
            if r is None and not scanned:
                bf.scan(cid)
                scanned = True
                continue
            if r is None:
                a = self.ask(f"Bulk feeder {cid} ({c.get('package', '?')}) is empty: refill it, then Yes (scan again). "
                             f"Ignore = skip this part, No = stop", ignore=True)
                if a == NO:
                    raise Abort(f"bulk feeder {cid} empty")
                if a == IGNORE:
                    self.log(f"bulk feeder {cid} empty: {s['comp'].name} skipped")
                    s["skipped"] = True
                    return
                bf.scan(cid)
                continue
            x, y, phi, p = r
            o = self.nozzle_offset(n)
            z = bf.pick_z(bf.container(cid))
            if z is None:                                   # no height on the feeder: measured once, kept
                z = bf.pick_by_feel(n, x + o[0], y + o[1], extra=extra)
                if z is not None:
                    bf.update(cid, z_pick=round(z, 2))
                    self.log(f"bulk feeder {cid}: pick height measured: {z:.2f} mm (kept on the feeder)")
                held = z is not None
            else:
                held = bf.pick_at(n, x + o[0], y + o[1], z, extra)
            if not held:
                failed += 1
                mode = (bf.container(cid) or {}).get("on_fail", "next")
                self.log(f"bulk feeder {cid}: not picked at {x:.2f}, {y:.2f} (the part did not come up): {mode}")
                if mode == "retry" and not s.get("bulk_retried"):        # the same part once more
                    s["bulk_retried"] = True
                    bf.queue.setdefault(cid, []).insert(0, {"x": x, "y": y})
                    continue
                s.pop("bulk_retried", None)
                if mode == "skip":
                    self.log(f"bulk feeder {cid}: {s['comp'].name} skipped (setting: skip when not picked)")
                    s["skipped"] = True
                    return
                if mode == "ask":
                    a = self.ask(f"Bulk feeder {cid}: the part at {x:.2f}, {y:.2f} was not picked. Yes = take the next part, "
                                 f"Ignore = skip {s['comp'].name}, No = stop", ignore=True)
                    if a == NO:
                        raise Abort(f"bulk feeder {cid}: part not picked")
                    if a == IGNORE:
                        s["skipped"] = True
                        return
                    failed = 0
                    continue
                if failed >= 3:
                    a = self.ask(f"Bulk feeder {cid}: 3 parts in a row not picked at {z or 0:.2f} mm. Check the pick "
                                 f"height (Bulk feeders: set it, or Measure). Yes = try again, No = stop")
                    if a == NO:
                        raise Abort(f"bulk feeder {cid}: parts not picked")
                    failed = 0
                continue
            s["held"], s["bulk_phi"], s["bulk_at"], s["bulk_seal"] = True, phi, (x, y), z
            s["pick_angle"] = st.angle + phi                 # how the part lay: its turn on the nozzle
            return

    def pick(self, s):
        """pickComponent (JOB_SPEC 2.5)"""
        st, n, mb, test = s["stack"], s["nozzle"], self.mb, self.job.test
        if getattr(st, "bulk", 0) and not test:
            return self.bulk_pick(s)
        fid, pid = self.feeder_of(st.id)
        if st.type == 1 and getattr(st, "used_up", False):     # every cell was taken: not again from cell 1
            a = self.ask(f"Feeder {st.id}: its tray is used up ({st.cols} x {st.rows}). Refill it, then Yes (from the first "
                         f"cell again); No stops")
            if a != YES:
                raise Abort(f"feeder {st.id}: tray used up")
            st.used_up = False
        x, y = st.next_array_pos()
        if getattr(st, "pocket_check", False) and st.type == 0 and not test and hasattr(self.vision, "pocket"):
            for tries in range(5):                          # the pocket in front now, by the head camera
                p = self.vision.pocket(x, y, self)
                if p != "empty":
                    break
                if tries == 4:
                    self.log(f"feeder {fid}: 5 empty pockets in a row: picking at the stored point")
                    p = None
                    break
                length = max(int(self.cfg.get(f"Peel/lstPeel{pid - 1}.nLoad", 0) or 0), round(3.0 * st.pitch))
                self.retry(f"Feeder [{fid}] / peeler [{pid}] failed", lambda: (mb.peel(pid, round(st.peel_strength), min(64, max(4, length))),
                                                                              mb.feed(fid, round(st.feed_strength), round(st.pitch))), ignore=True)
                self.retry(f"Feeder [{fid}] feeding failed", lambda: mb.wait_feed(fid, 2.0), ignore=True)
                self.log(f"feeder {fid}: empty pocket skipped (fed one more)")
            if p:
                x, y = p
        if getattr(self, "picklearn", None) is not None and not test:
            cx, cy = self.picklearn.correction(st.id)             # learnt from where its parts sat on the nozzle
            x, y = x + cx, y + cy
        o = self.nozzle_offset(n)
        tz = self.ztilt(n, 0.0, max(0.0, 12.0 - st.pick_height))     # the tip's shift at the pick depth
        pick = (x + o[0] - tz[0], y + o[1] - tz[1])
        zs = self.zspeed()
        self.retry(f"Movement to pick position [feeder {st.id}] failed", lambda: mb.move_xy(*pick, wait=False))
        self.checkpoint()
        attempt = 0
        while True:
            if not test and st.type == 0:
                self.retry(f"Feeder [{fid}] feeding failed", lambda: mb.wait_feed(fid, 2.0), ignore=True)
            self.retry(f"Movement to pick position [feeder {st.id}] failed", lambda: mb.wait_xy(20))
            vac = st.vacuum_detect and not test and hasattr(mb, "pick")
            if vac:
                # vacuum check without a threshold to set: suction on above the part (this nozzle's open-air
                # level), the seal edge on the way down, the seal INTEGRAL after lifting; the threshold is the
                # midpoint between this pick's open and sealed levels (neomach.vacuum)
                z = max(0.0, 12.0 - st.pick_height)
                pr = self.retry(f"Nozzle [{n}] suction failure",
                                lambda: mb.pick(n, z, zs, st.pick_delay, learner=self.vac_learner(), raise_on_fail=False))
            else:
                self.retry(f"Nozzle [{n}] down failure", lambda: mb.set_z(n, max(0.0, 12.0 - st.pick_height), zs, wait=True, timeout=1))
                if not test:
                    self.retry(f"Nozzle [{n}] suction failure", lambda: mb.valve(n, -1))
                time.sleep(st.pick_delay / 1000)
                self.retry(f"Nozzle [{n}] movement failure", lambda: mb.set_z(n, 0.0, zs, wait=False))
                time.sleep(0.02)
            if st.type == 0 and not test:
                length = max(int(self.cfg.get(f"Peel/lstPeel{pid - 1}.nLoad", 0) or 0), round(3.0 * st.pitch))

                def feed_peel():
                    mb.peel(pid, round(st.peel_strength), min(64, max(4, length)))
                    mb.feed(fid, round(st.feed_strength), round(st.pitch))
                self.retry(f"Feeder [{fid}] / peeler [{pid}] failed", feed_peel, ignore=True)
            if not vac:
                self.retry(f"Nozzle [{n}] lift failure", lambda: mb.wait_idle(n, 1))
            self.checkpoint()
            if not vac or (pr and pr.held):
                s["held"] = True
                self.stat("pick", s, ok=True)
                return
            self.stat("pick", s, ok=False, why=str(getattr(pr, "reason", "vacuum")))
            self.log(f"{s['comp'].name}: nozzle {n}: vacuum: {getattr(pr, 'reason', 'no reading')}")
            if attempt < 6:
                if attempt == 3:
                    mb.valve(n, 1); time.sleep(0.02); mb.valve(n, 0)
                    attempt = 4
                else:
                    attempt += 1
                continue
            s["held"] = True
            self.discard(s)                      # NeoDen blows slot[nozzle] here (bug 4.1)
            a = self.ask(f"Feeder [{fid}] Nozzle [{n}] picking failed!", ignore=True)
            if a == NO:
                raise Abort("picking failed")
            if a == IGNORE:
                self.log(f"manual skip feed[{fid}]: {s['comp'].name}")
                s["skipped"] = True
                return
            self.retry(f"Movement to pick position [feeder {st.id}] failed", lambda: mb.move_xy(*pick, wait=False))
            attempt = 1

    def check_picked(self, s, res):
        """checkPickedComponent (2.6)"""
        st = s["stack"]
        if self.job.test or st.vision == 0:
            s["ok"] = st.vision == 0 or self.job.test
            return
        vl = getattr(self, "vlog", None)
        vid = s.get("vlog_id")
        if res is not None and not res.valid and getattr(res, "empty", False):
            s["ok"] = False
            s["held"] = False                   # nothing on the nozzle: nothing to throw away, pick again
            self.stat("vision", s, ok=False, why="empty nozzle")
            if vl:
                vl.result(vid, ok=False, reject="empty nozzle")
            return
        if res is None or not res.valid:
            self.log(f"{s['comp'].name}: no vision result (nozzle {s['nozzle']})")
            s["ok"] = False
            self.stat("vision", s, ok=False, why="not recognised")
            if vl:
                vl.result(vid, ok=False, reject="not recognised")
            return
        pkg = (st.part_l, st.part_w) if getattr(st, "part_l", 0) > 0 and getattr(st, "part_w", 0) > 0 else self.packages.get(st.footprint)
        own = getattr(s["comp"], "size_check", None)
        if (st.size_check if own is None else own) and pkg and not getattr(res, "leads", False):   # leads matched to the pads: a stronger check
            from neocam.vision import size_check
            lo, hi = self.cfg.size_limits()
            chk = size_check(res, pkg[0], pkg[1], st.angle, lo, hi, self.cfg.vision("sSizeOrientation"))
            if vl:
                vl.result(vid, ratio=[getattr(chk, "rx", None), getattr(chk, "ry", None)], size_ok=chk.ok, limits=[lo, hi])
            if not chk.ok:
                if vl:
                    vl.result(vid, ok=False, reject="size")
                self.log(f"{s['comp'].name}: size {res.w:.2f} x {res.h:.2f} mm does not match {st.footprint} {pkg[0]} x {pkg[1]}")
                s["ok"] = False
                self.stat("vision", s, ok=False, why="size")
                bad = self.size_bad = getattr(self, "size_bad", {})
                bad[st.id] = bad.get(st.id, 0) + 1
                if bad[st.id] >= 2:                       # twice in a row: most likely the wrong reel was loaded
                    bad[st.id] = 0
                    fid = self.feeder_of(st.id)[0]
                    a = self.ask(f"Feeder {fid}: the part picked is {max(res.w, res.h):.2f} x {min(res.w, res.h):.2f} mm, but this feeder's part "
                                 f"({st.value} {st.footprint}) is {max(pkg[:2]):.2f} x {min(pkg[:2]):.2f} mm. Is the right reel loaded? "
                                 f"Yes = I fixed it, pick again; Ignore = this size is right (take it); No = stop", ignore=True)
                    if a == NO:
                        raise Abort(f"feeder {fid}: parts of another size")
                    if a == IGNORE:
                        st.part_l, st.part_w = max(res.w, res.h), min(res.w, res.h)
                        self.log(f"feeder {fid}: its part size taken as {st.part_l:.2f} x {st.part_w:.2f} mm for this run")
                return
        s["ok"] = True
        getattr(self, "size_bad", {}).pop(st.id, None)
        self.stat("vision", s, ok=True)

    def prepare(self, s, tf, res):
        """preparePlacement (2.7): target XY of the head and the nozzle angle"""
        comp, st, n = s["comp"], s["stack"], s["nozzle"]
        # with lead vision the part is measured at its lead pattern centre, so that point goes on
        # the pad pattern centre (the same point on the board)
        X, Y, corr = tf.point(*s["lead_centre"]) if s.get("lead_centre") and res is not None and getattr(res, "leads", False) else tf.point(comp.x, comp.y)
        X, Y, corr = self.local_align(s, tf, X, Y, corr)
        s["pre"], s["res"], s["turn_corr"] = (X, Y), res, corr      # for the second look / the photo check
        A = comp.rot - s.get("pick_angle", st.angle) + self.cfg.cam_angle + corr
        A = (A + 180.0) % 360.0 - 180.0
        if not self.job.test and st.vision > 0 and res is not None and res.valid:
            import math
            A = (A - res.angle + 180.0) % 360.0 - 180.0
            t = A * 3.1415926 / 180
            X -= res.x * math.cos(t) - res.y * math.sin(t)
            Y -= res.x * math.sin(t) + res.y * math.cos(t)
        o = self.nozzle_offset(n)
        X, Y = X + o[0], Y + o[1]
        self.retry("Angle correction failed", lambda: self.turn(n, A))
        self.log(f"{comp.name}: nozzle {n} -> {X:.3f}, {Y:.3f}, {A:.2f} deg")
        return X, Y, A

    def turn(self, n, A, wait=False):
        """nozzle n to angle A, always ending from the same side (Job/fTurnApproachDeg short of it first):
        the rotation's backlash then lands the same way every time"""
        d = float(self.cfg.job("fTurnApproachDeg") or 0.0)
        if d > 0 and abs(A - self.mb.angle[n]) > 1e-6:
            self.mb.rotate(n, A - d, self.zspeed(), wait=True)
        self.mb.rotate(n, A, self.zspeed(), wait=wait)

    def second_look(self, group):
        """see, turn, see: the parts already turned to their place angle are photographed again; what is
        left of the turn (against the first picture + the turn made) is turned out and they are looked
        at again, until the turn reads right (Job/fSeeTurnTolDeg) or Job/nSeeTurnLooks looks. Nothing
        turns after the last look: the place point comes from its offset (a part off the nozzle's axis
        moves when it turns, and a part can slip on the nozzle)."""
        wrap90 = lambda a: (a + 45.0) % 90.0 - 45.0
        tol = float(self.cfg.job("fSeeTurnTolDeg"))
        looks = max(1, int(self.cfg.job("nSeeTurnLooks")))
        pending = list(group)
        for look in range(1, looks + 1):
            for s in pending:
                self.mb.wait_idle(s["nozzle"], 2.0)
            r2 = self.vision.parts(pending, self)
            turned = []
            for s in pending:
                n, r1, r = s["nozzle"], s["res"], r2.get(s["nozzle"])
                name = s["comp"].name
                if r is None or not r.valid:
                    self.log(f"{name}: look {look + 1}: not recognised, the {'first picture' if look == 1 else 'look before'} is used")
                    continue
                X, Y, A = s["target"]
                e = wrap90(r.angle - (r1.angle + A))       # turn still wrong after turning to A
                if abs(e) > 15.0 or math.hypot(r.x, r.y) > 2.0:
                    self.log(f"{name}: look {look + 1}: {e:+.2f} deg, offset {r.x:+.3f}, {r.y:+.3f} mm: too large, the first picture is used")
                    continue
                o = self.nozzle_offset(n)
                px, py = s["pre"]
                X2, Y2 = px - r.x + o[0], py - r.y + o[1]      # the offset at the angle it is at now
                s["target"] = (X2, Y2, A)
                if abs(e) <= tol or look == looks:
                    self.log(f"{name}: second look {look}: turn {e:+.2f} deg left{'' if abs(e) <= tol else ' (looks used up)'}, "
                             f"place point moved {X2 - X:+.3f}, {Y2 - Y:+.3f} mm")
                    continue
                A2 = A - e
                self.retry("Angle correction failed", lambda: self.turn(n, A2))
                s["target"] = (X2, Y2, A2)
                self.log(f"{name}: second look {look}: turn {e:+.2f} deg, turned to {A2:+.2f}, looking again")
                turned.append(s)
            pending = turned
            if not pending:
                break

    def local_align(self, s, tf, X, Y, corr):
        """pad alignment (neojob.padalign) for the parts it is switched on for"""
        from .padalign import on_hole, wanted
        comp, st = s["comp"], s["stack"]
        part = getattr(self, "pads", {}).get(comp.key or comp.name)
        if self.job.test or not wanted(self.cfg, comp, st):
            return X, Y, corr
        if not part:
            self.log(f"{comp.name}: pad alignment wanted, but no pads of it (build <job>.pads.json)")
            return X, Y, corr
        r = self.retry(f"{comp.name}: pad alignment failed", lambda: self.vision.pad_align(part["pads"], (comp.x, comp.y), tf, self,
                                                                         hole=on_hole(self.cfg, comp.name, comp.footprint or "") or bool(part.get("hole"))), ignore=True)
        if not r:
            return X, Y, corr
        self.log(f"{comp.name}: pad alignment: {r.message}")
        if not r.ok:
            a = self.ask(f"{comp.name}: pad alignment: {r.message}. Yes = look again, Ignore = place without it", ignore=True)
            if a == NO:
                raise Abort("pad alignment")
            return self.local_align(s, tf, X, Y, corr) if a == YES else (X, Y, corr)
        if math.hypot(r.dx, r.dy) > float(self.cfg.padalign("fMaxShiftMm")) or abs(r.dtheta) > float(self.cfg.padalign("fMaxTurnDeg")):
            a = self.ask(f"{comp.name}: pads are {math.hypot(r.dx, r.dy):.3f} mm / {r.dtheta:+.2f} deg off. Yes = use it, Ignore = place without it", ignore=True)
            if a == NO:
                raise Abort("pad alignment too large")
            if a == IGNORE:
                return X, Y, corr
        return X + r.dx, Y + r.dy, corr + r.dtheta

    def ztilt(self, n, angle, z):
        """where nozzle n's tip is, sideways, at depth z mm with the nozzle at angle (neocalib.ztilt)"""
        if not hasattr(self, "_zt"):
            from neocalib.ztilt import from_config
            self._zt = {k: from_config(self.cfg, k) for k in range(1, 5)}
        t = self._zt.get(n)
        if not t or z <= 0:
            return 0.0, 0.0
        from neocalib.ztilt import shift
        return shift(t[0], t[1], t[2], angle, z)

    def approach(self, X, Y):
        """the place point from one side (+X, +Y: what NeoDen4's moveToSlow does), so the XY
        backlash lands the same way every time. That side, not the slowness, takes the backlash out:
        a head already below-left of the point goes straight there at the run speed; otherwise it
        goes to 1 mm before the point and does the last millimetre at Job/nPlaceApproachSpeed
        (NeoDen4: speed 10, 0.2 s; 0 = NeoDen4's slow move)"""
        mb = self.mb
        sp = int(self.cfg.job("nPlaceApproachSpeed"))
        pos = getattr(mb, "pos", None)
        if not sp:
            return mb.move_xy(X, Y, wait=False, slow=True)
        if pos and pos[0] <= X - 1.0 and pos[1] <= Y - 1.0:
            return mb.move_xy(X, Y, wait=False)
        mb.move_xy(max(X - 1.0, 0.0), max(Y - 1.0, 0.0))
        return mb.move_xy(X, Y, wait=False, speed=min(sp, self.speed_cache or sp))

    @staticmethod
    def nearest_order(items, start, point):
        """items in nearest-neighbour order from `start`: the head's travel through them is shorter
        than in the job's order (each pick / place of a cycle is independent of the others)"""
        left, out, here = list(items), [], start
        while left:
            nxt = min(left, key=lambda s: math.dist(here, point(s)))
            left.remove(nxt)
            out.append(nxt)
            here = point(nxt)
        return out

    def same_height(self, slots):
        """may these parts be placed in any order? Not when a tall one could be put down before a low
        neighbour (the job's order puts low parts first): heights within 1 mm, unknown = alike"""
        hs = []
        for s in slots:
            h = float(getattr(s["stack"], "part_h", 0) or 0)
            if not h:
                pk = self.packages.get(s["stack"].footprint) or self.packages.get(s["comp"].footprint)
                h = float(pk[2]) if pk and len(pk) > 2 and pk[2] else 0.0
            if h:
                hs.append(h)
        return not hs or max(hs) - min(hs) <= 1.0

    def part_height(self, st, comp):
        """the part's height (mm): the feeder's own, else the footprint library's (pkg.csv); 0 unknown"""
        pk = self.packages.get(st.footprint) or self.packages.get(comp.footprint)
        return getattr(st, "part_h", 0) or (pk[2] if pk and len(pk) > 2 and pk[2] else 0.0)

    def need_part_heights(self, job):
        """with a board height map the place depth is the board + the part's height: every feeder used
        needs its part's height. Asked until set (the job file read again), or Ignore: those feeders'
        place height (the board still the floor)"""
        from .jobfile import load as load_job
        while True:
            first = {}
            only = getattr(self, "only", None)              # a placement test: only that part's feeder
            for i, c in enumerate(job.comps):
                if (i == only) if only is not None else not c.skip:
                    first.setdefault(c.stack, c)
            miss = [st for sid, c in first.items() for st in [job.stack(sid)]
                    if st is not None and (only is not None or not st.skip) and not self.part_height(st, c)]
            if not miss or self.job.test or not self.cfg.job("bZMapDepth") or self.zmap.board_z is None:
                return
            names = ", ".join(f"{st.id} ({st.footprint or '?'} {st.value or ''})".strip() for st in miss[:12])
            a = self.ask(f"No part height for feeder(s) {names}{' ...' if len(miss) > 12 else ''}: set it "
                         "(Feeders: part size H, or the wizard's parts step) and save the job. Ignore = their place height", ignore=True)
            if a == NO:
                raise Abort("no part height")
            if a == IGNORE:
                return
            fresh = load_job(job.path).stacks
            for sid, st in job.stacks.items():
                if sid in fresh:
                    st.part_h = fresh[sid].part_h

    def place(self, s, first):
        """placeComponent (2.8)"""
        X, Y, A = s["target"]
        st, n, mb, zs = s["stack"], s["nozzle"], self.mb, self.zspeed()
        tz = self.ztilt(n, A, max(0.0, 12.0 - st.place_height))      # the tip's shift at the place depth
        X, Y = X - tz[0], Y - tz[1]
        self.retry(f"Placement position [{X:.2f}, {Y:.2f}] move failed", lambda: self.approach(X, Y))
        self.retry("Failure: angle correction timeout", lambda: mb.wait_idle(n, 2))
        self.retry(f"Placement position [{X:.2f}, {Y:.2f}] move failed", lambda: mb.wait_xy(20))
        self.checkpoint()
        z = max(0.0, 12.0 - st.place_height)
        zm = getattr(self, "zmap", None)
        if zm:                                                         # the machine's / board's heights here (neojob.zmap)
            c = s["comp"]
            px, py = s.get("pre") or (X, Y)                            # the point on the table (machine, nozzle-1 frame)
            ph = self.part_height(st, c)
            t = zm.touch(px, py, n)
            if t is not None and ph and self.cfg.job("bZMapDepth"):   # the board there + the part, a little press
                z = max(0.0, t - ph + float(self.cfg.job("fPlacePressMm")))
            else:
                z = max(0.0, z + zm.delta(px, py, n))
            fl = zm.floor(px, py, n, ph)                               # never into the board, whatever the place height
            if fl is not None and z > fl:
                self.log(f"{c.name}: place depth {z:.2f} is past the board ({fl:.2f} on nozzle {n}, part {ph:.2f} mm): held at the board")
                z = fl
        self.retry("Nozzle down failed", lambda: mb.set_z(n, z, zs, wait=True, timeout=1))
        # release with the nozzle still on the part: vacuum off + blow on, the place delay, a small lift
        # (Job/fPlaceLiftMm, 0.1 mm), blow off, then up. NeoDen4.exe kept blowing while the nozzle rose,
        # and that air moved the part.
        self.retry("Nozzle blow failed", lambda: mb.valve(n, 1))
        time.sleep(st.place_delay / 1000)
        lift = float(self.cfg.get("Job/fPlaceLiftMm", 0.1) or 0.0)
        if lift > 0:
            self.retry("Nozzle lift failed", lambda: mb.set_z(n, max(0.0, z - lift), zs, wait=True, timeout=1))
        self.retry("Blow shutoff failure", lambda: mb.valve(n, 0))
        self.retry("Nozzle rising failed", lambda: mb.set_z(n, 0.0, zs, wait=False))
        self.retry("Nozzle rise failed", lambda: mb.wait_idle(n, 1))
        self.retry("Angle reset failed", lambda: mb.rotate(n, 0.0, 150, wait=False))
        s["held"] = False
        if self.check_mode(s) in ("vacuum", "both") and self.came_back(n):
            s["held"] = True
            self.stat("placecheck", s, ok=False, why="came back on the nozzle")
            self.log(f"{s['comp'].name}: the part came back on nozzle {n} after placing: it is thrown away and another placed")
            return False
        self.stat("place", s)
        if getattr(self, "vlog", None):
            self.vlog.result(s.get("vlog_id"), placed=True, target=[X, Y, A])
        self.log(f"placed {s['comp'].name} ({s['comp'].value})")
        self.checkpoint()
        return True

    def check_mode(self, s, panel=None):
        """the after-place check of this part: its own setting, else its feeder's; "" = none"""
        if self.job.test or not self.cfg.job("bPlaceCheck"):
            return ""
        own = getattr(s["comp"], "place_check", None)
        m = getattr(s["stack"], "place_check", "") if own is None else ("" if own == "none" else own)
        if m in ("photo", "both") and self.cfg.job("bPlaceCheckFirstOnly") and (self.panel if panel is None else panel) != getattr(self, "start_panel", 0):
            m = "vacuum" if m == "both" else ""                  # the photo on the first panel only
        return m

    def came_back(self, n):
        """after placing, the nozzle up: suction on for a moment; a part still on it seals the tip"""
        mb = self.mb
        if not hasattr(mb, "vacuum"):
            return False
        mb.valve(n, -1)
        time.sleep(0.15)
        v = float(np.median([mb.vacuum(n) for _ in range(3)]))
        mb.valve(n, 0)
        lr = self.vac_learner()
        thr = lr.threshold(n) if lr.knows(n) else -20
        return v <= thr

    def photo_check(self, s):
        """the part on the board: the head camera's picture after placing against the one before"""
        from .learn import placed_part
        c, st = s["comp"], s["stack"]
        X, Y = s["pre"]
        after = self.retry(f"{c.name}: head camera picture failed", lambda: self.vision.head_shot(X, Y), ignore=True)
        if after is None or after is False:
            return
        pkg = (st.part_l, st.part_w) if getattr(st, "part_l", 0) > 0 and getattr(st, "part_w", 0) > 0 else self.packages.get(st.footprint)
        k = self.cfg.pxmm(1)
        r = placed_part(s["before"], after, k, *(pkg[:2] if pkg else (0.0, 0.0)))
        tol, tol_a = float(self.cfg.job("fPlaceCheckMm")), float(self.cfg.job("fPlaceCheckDeg"))
        if r["found"]:
            want = (c.rot + s.get("turn_corr", 0.0))
            da = (r["angle"] - want + 45.0) % 90.0 - 45.0
            off = math.hypot(r["dx"], r["dy"])
            ok = off <= tol and abs(da) <= tol_a
            msg = f"{c.name}: placed {r['dx']:+.3f}, {r['dy']:+.3f} mm off, turned {da:+.2f} deg ({r['L']:.2f} x {r['W']:.2f} mm)"
        else:
            ok, off, da, msg = False, None, None, f"{c.name}: placed part not found: {r['why']}"
        self.stat("placecheck", s, ok=ok, dx=r.get("dx"), dy=r.get("dy"), da=da, why=None if ok else msg)
        self.save_check_picture(s, s["before"], after, r, ok)
        if ok:
            self.log(msg + ": OK")
            return
        self.log(msg + f": out of the limits ({tol:.2f} mm, {tol_a:.1f} deg)")
        a = self.ask(msg + ". Yes = go on, No = stop the job")
        if a == NO:
            raise Abort(f"{c.name}: placement check failed")

    def save_check_picture(self, s, before, after, r, ok):
        """before | after with the found part drawn: <data>/PlaceCheck/<date>/<job>_<ref>.png"""
        try:
            import cv2
            root = os.path.dirname(self.cfg.path) if getattr(self.cfg, "path", None) else None
            if not root:
                return
            d = os.path.join(root, "PlaceCheck", time.strftime("%Y-%m-%d"))
            os.makedirs(d, exist_ok=True)
            b = cv2.cvtColor(before, cv2.COLOR_GRAY2BGR) if before.ndim == 2 else before.copy()
            a = cv2.cvtColor(after, cv2.COLOR_GRAY2BGR) if after.ndim == 2 else after.copy()
            h, w = a.shape[:2]
            cv2.drawMarker(a, (w // 2, h // 2), (0, 255, 0), cv2.MARKER_CROSS, 40, 2)
            if r.get("box"):
                box = cv2.boxPoints(r["box"]).astype(np.int32)
                cv2.polylines(a, [box], True, (0, 255, 0) if ok else (0, 0, 255), 2)
            name = f"{os.path.splitext(os.path.basename(self.job.path or 'job'))[0]}_{s['comp'].name}_{time.strftime('%H%M%S')}.png"
            cv2.imwrite(os.path.join(d, name), cv2.resize(np.hstack([b, a]), (2 * min(w, 600), min(h, 600))))
        except Exception as e:
            self.log(f"place check picture not saved: {e}")

    def discard(self, s):
        """discardComponent (2.6): blow the part off over the waste box (nozzle up)"""
        wx, wy = self.cfg.pt("Position/posWasteBox")
        self.retry(f"Movement to destination [{wx:.2f}, {wy:.2f}] failed", lambda: self.mb.move_xy(wx, wy, timeout=20))
        self.mb.valve(s["nozzle"], 1); time.sleep(0.1); self.mb.valve(s["nozzle"], 0)
        s["held"] = False
        self.stat("discard", s)
        self.log(f"discarded {s['comp'].name} (nozzle {s['nozzle']})")

    # ---- resume file (next to the job: <job>.resume.json) ----
    @staticmethod
    def resume_path(job_path):
        return job_path + ".resume.json"

    @classmethod
    def resume_file(cls, job_path):
        p = cls.resume_path(job_path)
        if not os.path.exists(p):
            return None
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            return None
        return None if d.get("finished") else d

    @staticmethod
    def fingerprint(job):
        """the job's parts as the resume record refers to them (by index): designator, feeder, place,
        skip; an edit (a part added, deleted, moved in the list) changes it"""
        import hashlib
        t = "|".join(f"{getattr(c, 'key', c.name)},{c.stack},{c.x:.3f},{c.y:.3f},{int(bool(c.skip))}" for c in job.comps)
        t += "#" + "|".join(f"{p.x:.3f},{p.y:.3f},{p.angle}" for p in job.panels)
        return hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16]

    def save_resume(self, finished=False):
        if not self.job or not self.job.path:
            return
        d = {"job": self.job.path, "finished": finished, "panel": self.panel, "comp": self.comp, "parts": self.fingerprint(self.job),
             "done": sorted(self.done), "placed": self.placed, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "trays": {str(s.id): [s.cur_col, s.cur_row] for s in self.job.stacks.values() if s.type == 1}}
        tmp = self.resume_path(self.job.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.resume_path(self.job.path))

    def status(self):
        job = self.job
        return {"state": STATE_NAMES[self.state], "placed": self.placed, "panel": self.panel + 1, "comp": self.comp + 1,
                "panels": len(job.panels) if job else 0, "comps": len(job.comps) if job else 0,
                "question": self.question, "result": self.result, "job": job.path if job else "",
                "elapsed_s": int(self.elapsed())}

    def elapsed(self):
        """working time of the job: pauses not counted, stopped at the end"""
        if not self.t0:
            return 0
        now = self.t_end or time.monotonic()
        return max(0.0, now - self.t0 - self.paused_s - ((now - self.pause_from) if self.pause_from else 0))
