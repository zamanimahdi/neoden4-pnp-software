"""neoserver core: settings, log, files with backups, signed updates with rollback, services.

Same behaviour as neoagent (docs/NEOAGENT.md), for the Ubuntu machine PC.
"""
import configparser
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque

VERSION = "1.0"


# ---------------------------------------------------------------- settings
class Settings:
    DEFAULTS = {
        "http_port": "8080", "bind": "0.0.0.0", "user": "admin", "password": "neoden",
        "login": "no",
        "home_at_start": "yes",          # full XY home as soon as the mainboard answers (after boot)                   # yes: the web UI asks for user / password (Basic); no: open
        "update_key": "change-me", "www": "www", "backup": "/opt/neoden/backup",
        "neoden_dir": "/opt/neoden/data",
        "camera": "usb",                 # usb | tcp://host:5600 (camserver, development) | none
        "machine": "serial:///dev/ttyS1",  # neomach URL: serial://..., tcp://host:5602, sim://
        "services": "neoserver,ssh,vsftpd",   # systemd units shown on the dashboard
        "home": "",                      # folder of this program (@agent in update packages)
    }

    def __init__(self, path=None):
        self.path = path
        cp = configparser.ConfigParser()
        cp["server"] = dict(self.DEFAULTS)
        if path and os.path.exists(path):
            cp.read(path, encoding="utf-8")
        self.s = cp["server"]
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.home = self.s.get("home") or here
        www = self.s.get("www")
        self.www = www if os.path.isabs(www) else os.path.join(self.home, "neoagent", "www") if www == "www" else os.path.join(self.home, www)

    def __getattr__(self, k):
        if k in Settings.DEFAULTS:
            v = self.s.get(k)
            return int(v) if k == "http_port" else v
        raise AttributeError(k)

    @staticmethod
    def write_default(path):
        cp = configparser.ConfigParser()
        cp["server"] = dict(Settings.DEFAULTS)
        with open(path, "w", encoding="utf-8") as f:
            f.write("; neoserver settings (restart neoserver after editing)\n")
            cp.write(f)


# ---------------------------------------------------------------- log
class Log:
    def __init__(self, n=2000, path=None):
        self.lines, self.seq, self.lock, self.path = deque(maxlen=n), 0, threading.Lock(), path

    events = None                               # an Events: error lines go to every screen too

    def __call__(self, text, src="server"):
        line = time.strftime("%H:%M:%S ") + f"{src}: {text}"
        if self.events is not None:
            try:
                self.events.from_log(src, str(text))
            except Exception:
                pass
        with self.lock:
            self.lines.append((self.seq, line))
            self.seq += 1
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(time.strftime("%Y-%m-%d ") + line + "\n")
                except OSError:
                    pass
        print(line, flush=True)

    def since(self, n):
        with self.lock:
            return {"next": self.seq, "lines": [t for s, t in self.lines if s >= n]}


class Events:
    """what every screen must see (the web UI on any PC and the machine's monitor are one UI): errors
    of any client (their error toasts, POST /api/event), machine / job / calibration faults from the
    log. Clients get the ones after their last seq with every /api/mc/status poll."""
    WORDS = ("error", "failed", "failure", "step loss", "lost", "stop", "not found", "cannot", "timeout", "do not match")

    def __init__(self, n=200):
        self.items, self.seq, self.lock = deque(maxlen=n), 0, threading.Lock()

    def add(self, level, text, frm="server"):
        with self.lock:
            self.seq += 1
            self.items.append({"seq": self.seq, "t": time.strftime("%H:%M:%S"), "level": level, "text": str(text)[:400], "from": frm})

    def since(self, n):
        with self.lock:
            return self.seq, [e for e in self.items if e["seq"] > n]

    def from_log(self, src, text):
        """a log line worth showing everywhere: from the machine / job / calibration, with an error word"""
        if src not in ("machine", "job", "calib", "align", "server") or "BrokenPipe" in text or "ConnectionReset" in text:
            return
        low = text.lower()
        if src == "server" and not ("stop" in low or "step loss" in low or "homing" in low and "fail" in low):
            return
        # the job's everyday lines (a part not recognised and picked again, an empty pocket, ...) are not
        # errors for every screen; its questions are shown everywhere anyway (job status)
        if src == "job" and (low.startswith(("job stopped", "job finished", "paused", "continued", "ask:")) or any(w in low for w in (
                "recognition failed", "no vision result", "not recognised", "nozzle empty", "empty pocket", "discarded",
                "second look", "feeder pocket", "vacuum:", "homeall after the job"))):
            return
        if any(w in low for w in self.WORDS):
            self.add("bad", f"{src}: {text}", src)


# ---------------------------------------------------------------- files and backups
class Files:
    def __init__(self, backup_dir, log):
        self.root, self.log, self.n = backup_dir, log, 0
        os.makedirs(backup_dir, exist_ok=True)
        self.lock = threading.Lock()

    @staticmethod
    def ok(path):
        return bool(path) and os.path.isabs(path) and ".." not in path.replace("\\", "/").split("/")

    def new_set(self, why):
        with self.lock:
            self.n += 1
            d = os.path.join(self.root, time.strftime("%Y%m%d-%H%M%S") + f"-{self.n}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "manifest.txt"), "a", encoding="utf-8") as f:
            f.write(f"why {why}\n")
        return d

    def backup(self, path, setdir):
        if not os.path.isfile(path):
            return
        rel = os.path.splitdrive(os.path.abspath(path))[1].lstrip("/\\")
        dst = os.path.join(setdir, "files", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)
        with open(os.path.join(setdir, "manifest.txt"), "a", encoding="utf-8") as f:
            f.write(f"file {os.path.abspath(path)}\n")

    @staticmethod
    def write_atomic(path, data):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".neo-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def write(self, path, data, why="write"):
        setdir = ""
        if os.path.isfile(path):
            setdir = self.new_set(why)
            self.backup(path, setdir)
        self.write_atomic(path, data)
        return os.path.basename(setdir)

    def delete(self, path):
        if os.path.isdir(path):
            os.rmdir(path)                       # empty folders only
            return ""
        setdir = self.new_set("delete")
        self.backup(path, setdir)
        os.remove(path)
        return os.path.basename(setdir)

    def listing(self, path):
        if not path:                             # like the agent's drive list: the roots to start from
            roots = ["/", "/opt/neoden"] if os.name != "nt" else [f"{c}:" for c in "CDEFGHIJ" if os.path.exists(f"{c}:\\")]
            return {"path": "", "entries": [{"name": r, "dir": True, "size": 0, "mtime": ""} for r in roots]}
        out = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat()
                    out.append({"name": e.name, "dir": e.is_dir(), "size": 0 if e.is_dir() else st.st_size,
                                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
                except OSError:
                    pass
        return {"path": path, "entries": out}

    def sets(self):
        res = []
        for d in sorted(os.listdir(self.root), reverse=True)[:500]:
            mf = os.path.join(self.root, d, "manifest.txt")
            if not os.path.isfile(mf):
                continue
            why, files = "", []
            for line in open(mf, encoding="utf-8", errors="replace"):
                line = line.rstrip("\n")
                if line.startswith("why ") and not why:
                    why = line[4:]
                elif line.startswith("file "):
                    files.append(line[5:])
            res.append({"id": d, "why": why, "files": files})
        return res

    def restore(self, sid, before="before restore"):
        if "/" in sid or "\\" in sid or ".." in sid:
            raise ValueError("bad backup id")
        mf = os.path.join(self.root, sid, "manifest.txt")
        if not os.path.isfile(mf):
            raise FileNotFoundError(f"no backup {sid}")
        cur = self.new_set(before)
        report = []
        for line in open(mf, encoding="utf-8"):
            if not line.startswith("file "):
                continue
            path = line[5:].rstrip("\n")
            self.backup(path, cur)
            rel = os.path.splitdrive(path)[1].lstrip("/\\")
            shutil.copy2(os.path.join(self.root, sid, "files", rel), path)
            report.append(f"restored {path}")
        return "\n".join(report)



# ---------------------------------------------------------------- services (systemd)
class Services:
    def __init__(self, names, log):
        self.names = [n.strip() for n in names.split(",") if n.strip()]
        self.log = log
        self.systemd = os.name != "nt" and shutil.which("systemctl") is not None

    def status(self, max_age=10.0):
        """the services' state; kept max_age s: each page load asks (/api/status), and one systemctl
        call per service costs ~0.1 s on the machine's PC"""
        c = getattr(self, "_cache", None)
        if c and time.monotonic() - c[0] < max_age:
            return c[1]
        out = self._status()
        self._cache = (time.monotonic(), out)
        return out

    def _status(self):
        out = []
        for n in self.names:
            if not self.systemd:
                out.append({"name": n, "running": n == "neoserver", "wanted": True, "pid": os.getpid() if n == "neoserver" else 0,
                            "restarts": 0, "uptime_s": 0})
                continue
            r = subprocess.run(["systemctl", "show", n, "-p", "ActiveState,MainPID,NRestarts,ActiveEnterTimestampMonotonic,UnitFileState"],
                               capture_output=True, text=True, timeout=5)
            p = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
            up = 0
            try:
                up = max(0, int(time.monotonic() - int(p.get("ActiveEnterTimestampMonotonic", "0")) / 1e6))
            except ValueError:
                pass
            out.append({"name": n, "running": p.get("ActiveState") == "active", "wanted": p.get("UnitFileState") == "enabled",
                        "pid": int(p.get("MainPID", "0") or 0), "restarts": int(p.get("NRestarts", "0") or 0), "uptime_s": up})
        return out

    def action(self, name, act):
        if name not in self.names or act not in ("start", "stop", "restart"):
            raise ValueError("unknown service or action")
        self._cache = None
        if not self.systemd:
            raise RuntimeError("no systemd on this computer")
        if name == "neoserver":                   # restart ourselves after the reply
            threading.Timer(1.0, lambda: subprocess.run(["systemctl", act, name])).start()
            return
        r = subprocess.run(["systemctl", act, name], capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or f"systemctl {act} {name} failed")
        self.log(f"{act} {name}")


# ---------------------------------------------------------------- updates
class Updates:
    """NEOPKG1 packages (tools/mkpkg.py): HMAC-SHA256 signed, SHA-256 per file, backup, rollback.
    A package that changes this program's own files restarts the server after the reply; if the new
    version does not come up, systemd's OnFailure unit runs `python3 -m neoserver.rollback`."""

    def __init__(self, settings, files, services, log):
        self.st, self.files, self.svc, self.log = settings, files, services, log
        self.state = os.path.join(files.root, "last-update.json")

    def apply(self, pkg):
        rep = []
        if len(pkg) < 40 or not pkg.startswith(b"NEOPKG1\n"):
            return False, "not a neoagent package\n"
        mac = hmac.new(self.st.update_key.encode(), pkg[:-32], hashlib.sha256).digest()
        if not hmac.compare_digest(mac, pkg[-32:]):
            return False, "signature does not match (wrong update_key, or the package was changed)\n"
        end = pkg.find(b"\nend\n")
        if end < 0:
            return False, "bad header\n"
        name, files, stops = "", [], []
        for line in pkg[8:end + 1].decode("utf-8").splitlines():
            if line.startswith("name "):
                name = line[5:]
            elif line.startswith("stop "):
                stops.append(line[5:])
            elif line.startswith("file "):
                size, sha, dest = line[5:].split(" ", 2)
                if dest.startswith("@agent\\") or dest.startswith("@agent/"):
                    dest = os.path.join(self.st.home, dest[7:].replace("\\", os.sep))
                if not os.path.isabs(dest):
                    return False, f"destination must be absolute: {dest}\n"
                files.append([dest, int(size), sha])
        data = end + 5
        for f in files:
            blob = pkg[data:data + f[1]]
            if len(blob) != f[1] or hashlib.sha256(blob).hexdigest() != f[2].lower():
                return False, f"SHA-256 mismatch for {f[0]}\n"
            f.append(blob)
            data += f[1]
        if data != len(pkg) - 32:
            return False, "package has extra bytes\n"
        rep.append(f'package "{name}": {len(files)} file(s), signature and hashes ok')
        setdir = self.files.new_set(f"update {name}")
        existed = {}
        try:
            for dest, *_ in files:
                if os.path.isdir(dest):
                    raise IsADirectoryError(f"cannot back up {dest} (a folder)")
                existed[dest] = os.path.isfile(dest)
                self.files.backup(dest, setdir)
        except Exception as e:
            return False, "\n".join(rep + [str(e)]) + "\n"
        rep.append(f"backup {os.path.basename(setdir)}")
        own = any(os.path.abspath(d).startswith(os.path.abspath(self.st.home)) for d, *_ in files)
        written = []
        try:
            for dest, size, sha, blob in files:
                Files.write_atomic(dest, blob)
                written.append(dest)
                rep.append(f"wrote {dest} ({size} bytes)")
        except Exception as e:
            rep.append(f"FAILED writing {dest}: {e}; rolling back")
            for d in written:
                if not existed.get(d):
                    try:
                        os.remove(d)
                    except OSError:
                        pass
            try:
                rep.append(self.files.restore(os.path.basename(setdir), "rollback"))
            except Exception as e2:
                rep.append(f"rollback problem: {e2}")
            return False, "\n".join(rep) + "\n"
        for s in stops:
            try:
                self.svc.action(s, "restart")
                rep.append(f"restarted {s}")
            except Exception as e:
                rep.append(f"restart {s}: {e}")
        if own:
            json.dump({"backup": os.path.basename(setdir), "time": time.time()}, open(self.state, "w"))
            rep.append("server files changed: neoserver restarts after this reply (rolls back if it does not start)")
            if self.svc.systemd:
                threading.Timer(1.5, lambda: subprocess.run(["systemctl", "restart", "neoserver"])).start()
        return True, "\n".join(rep) + "\n"


def hostname():
    return socket.gethostname()
