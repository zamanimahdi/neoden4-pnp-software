"""Job runs from the web UI (neojob.Runner in a thread).

    GET  /api/job/status                    state, counters, the open question, log lines
    GET  /api/job/resume?path=              the resume point of that job file, or null
    POST /api/job/start?path=&resume=0|1&client=   needs the machine control lock
    POST /api/job/pause | continue | step | stop
    POST /api/job/answer?a=yes|no|ignore    answers the runner's question

While a job runs, manual machine commands are refused (Machine.job_running).
"""
import codecs
import os
import threading
import time

from neojob import CameraVision, Runner, load_job, load_packages, NO


class JobService:
    def __init__(self, st, mc, cam, log):
        self.st, self.mc, self.cam, self.log = st, mc, cam, log
        self.runner = None
        self.thread = None
        self.lines = []
        self.answer_ev = threading.Event()
        self.answer = None

    def _log(self, text):
        self.lines.append(text)
        self.lines = self.lines[-500:]
        self.log(text, "job")

    def _ask(self, text, buttons):
        self.answer_ev.clear()
        self.answer = None
        self.mc.jog_ok = text.startswith("Mark")      # the operator brings the camera onto the mark
        try:
            while not self.answer_ev.wait(0.5):
                if self.runner and self.runner.flags.get("stop"):
                    return NO
            return self.answer if self.answer in buttons else NO
        finally:
            self.mc.jog_ok = False

    # ---- the job list (Work): parts on the board, panels, when each job was last run ----
    def usage_path(self):
        return os.path.join(self.st.neoden_dir, "job_usage.json")

    def mark_used(self, path):
        import json
        try:
            d = json.load(open(self.usage_path(), encoding="utf-8")) if os.path.exists(self.usage_path()) else {}
        except ValueError:
            d = {}
        d[os.path.basename(path)] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            json.dump(d, open(self.usage_path(), "w", encoding="utf-8"), indent=1)
        except OSError:
            pass

    def list(self, folder):
        import json
        folder = folder or os.path.join(self.st.neoden_dir, "Proc")
        try:
            used = json.load(open(self.usage_path(), encoding="utf-8")) if os.path.exists(self.usage_path()) else {}
        except ValueError:
            used = {}
        out = []
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if not name.lower().endswith(".csv") or not os.path.isfile(p):
                continue
            parts = placed = panels = 0
            skip_st, comps = set(), []
            try:
                raw = open(p, "rb").read()
                text = raw.decode("utf-8") if not raw.startswith(codecs.BOM_UTF16_LE) else raw.decode("utf-16")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            for line in text.splitlines():
                t = line.split(",")
                tag = t[0].strip()
                if tag == "comp" and len(t) > 9:
                    parts += 1
                    if t[9].strip().lower() not in ("yes", "1", "true", "是") and not t[3].upper().startswith("FID"):
                        comps.append(t[1].strip())
                elif tag == "stack" and len(t) > 2:
                    k = 23 if t[2].strip() == "1" else 20                       # the skip field (tray / tape)
                    if len(t) > k and t[k].strip().lower() in ("yes", "1", "true", "是"):
                        skip_st.add(t[1].strip())
                elif tag == "mirror":
                    panels += 1
            placed = sum(1 for sid in comps if sid not in skip_st)            # not skipped, nor their feeder
            last = used.get(name)
            if not last and os.path.exists(p + ".resume.json"):
                last = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p + ".resume.json")))
            out.append({"name": name, "parts": parts, "to_place": placed, "panels": panels, "last_used": last or "",
                        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))})
        return out

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, path, resume, client, only=""):
        if self.running():
            raise RuntimeError("a job is running")
        if self.mc._owner() != client:
            raise PermissionError("take control first")
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        from neocam.config import NeoConfig
        cfg = NeoConfig.load(os.path.join(self.st.neoden_dir, "Config.ini"))
        job = load_job(path)
        if job.mode != "Lock":
            raise ValueError(f"pcb mode {job.mode}: this machine has the fixed table only")
        mb = self.mc.board()
        vis = CameraVision(self.cam.dev, mb, cfg, self.cam.lock, self._log)
        self.vis = vis
        pk = load_packages(os.path.join(self.st.neoden_dir, "Config", "pkg.csv"))
        from neoserver.feedersetup import apply_head_scale      # the job's own head camera scale (its board)
        if apply_head_scale(cfg, path):
            self._log(f"head camera scale of this job: {cfg.pxmm(1):.3f} px/mm")
        self.runner = Runner(mb, cfg, vis, self._ask, self._log, pk)
        res = Runner.resume_file(path) if resume else None
        if res and res.get("parts") and res["parts"] != Runner.fingerprint(job):
            raise ValueError("the job was changed since it stopped (parts or panels): it cannot go on where it stopped; "
                             "start a new board, or undo the change")
        idx = None
        if only:
            idx = next((i for i, c in enumerate(job.comps) if c.key == only), None)
            if idx is None:
                idx = next((i for i, c in enumerate(job.comps) if c.name == only), None)
            if idx is None:
                raise ValueError(f"no part {only} in the job")
            res = None
            if job.test:                     # a placement test is a real placement: with the feeder's vision
                job.test = False
                self._log("the job is set to Test run (no parts placed): this one part is placed for real, with its feeder's vision")
        from neojob.reach import problems, feeder_issues       # a feeder the head cannot get to: said now
        bad = problems(job, cfg, idx)
        if bad:
            raise ValueError(f"{len(bad)} pick point(s) outside the machine's travel: " + "; ".join(bad[:8]) +
                             (" ..." if len(bad) > 8 else "") + ". Move those feeders (Feeders), or skip them")
        from neojob import zmap                                # place depth = board + part height: every part's height first
        hz = zmap.load(path, self.st.neoden_dir)
        if hz and hz.board_z is not None and bool(cfg.job("bZMapDepth")) and (not job.test or idx is not None):
            miss = sorted(sid for sid, w in feeder_issues(job, cfg, pk, True, idx).items() if any("part height" in x for x in w))
            if miss:
                raise ValueError(f"no part height for feeder(s) {', '.join(map(str, miss))}: set it (Feeders: part size H, "
                                 "or the wizard's parts step) and save the job; the feeder list marks them")
        self.lines = []
        self._log(f"start {os.path.basename(path)}: {len(job.comps)} components, {len(job.panels)} panel(s), "
                  f"{len(job.marks)} mark(s){', resume' if res else ''}{f', test: {only} alone' if only else ''}")

        auto_map = (not res and idx is None and not job.test and bool(cfg.job("bAutoZMap"))
                    and getattr(self, "zmap_cal", None) is not None)

        def run():
            self.mc.job_running = True
            try:
                if auto_map:                 # the board's surface first, while the nozzles are empty (neojob.zmap)
                    self.zmap_cal.auto(path)
                self.runner.run(job, resume=res, only=idx)
            except Exception as e:
                self._log(f"job error: {type(e).__name__}: {e}")
            finally:
                self.mc.job_running = False
                try:                         # the marks / pads left their window and exposure: the live views
                    self.cam.restore_view()  # (Mount screen, Feeder settings) would stay small and dark
                except Exception:
                    pass
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        if not only:
            self.mark_used(path)

    def control(self, what):
        r = self.runner
        if not r or not self.running():
            raise RuntimeError("no job is running")
        if what == "stop":
            self.answer = NO
            self.answer_ev.set()
        {"pause": r.pause, "continue": r.resume, "step": r.step, "stop": r.stop}[what]()

    def give_answer(self, a):
        self.answer = a
        self.answer_ev.set()

    def image(self, cam, scale=2):
        """the last picture the job took with camera `cam` (0 up, 1 head) as BMP, half size; None if none"""
        from neojob.camvision import UP, HEAD
        last = getattr(getattr(self, "vis", None), "last", {}).get(UP if cam == 0 else HEAD)
        if not last:
            return None, 0
        from neoserver.hw import bmp8
        import numpy as np
        seq, img = last
        s = max(1, min(4, int(scale)))
        return bmp8(np.ascontiguousarray(img[::s, ::s])), seq

    def status(self, since=0):
        d = self.runner.status() if self.runner else {"state": "idle", "placed": 0, "question": None, "result": None, "job": ""}
        d["running"] = self.running()
        d["log"] = self.lines[since:] if since < len(self.lines) else []
        d["log_n"] = len(self.lines)
        lv = getattr(getattr(self, "vis", None), "last", {})
        d["img_seq"] = {str(k): v[0] for k, v in lv.items()} if lv else {}
        li = getattr(getattr(self, "vis", None), "last_info", {})
        d["img_info"] = {str(k): v for k, v in li.items()} if li else {}
        return d
