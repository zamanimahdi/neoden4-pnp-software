"""End-to-end test of neoserver (the Ubuntu machine server) with simulators.

    python tests/test_neoserver.py

Runs `python -m neoserver` with machine = sim:// (neomach simulator) and camera = the neocam
camera simulator over TCP, then checks the API the web UI uses: login, status, files with
backup and restore, updates (good, bad signature, rollback), camera BMP, machine control.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mkpkg  # noqa: E402
from neocam import simserver  # noqa: E402

HTTP, CAM = 18190, 15690
AUTH = "Basic " + base64.b64encode(b"admin:pw").decode()


def call(method, path, body=None, auth=True, raw=False):
    req = urllib.request.Request(f"http://127.0.0.1:{HTTP}{path}", data=body if body is not None else (b"" if method in ("POST", "PUT") else None), method=method)
    if auth:
        req.add_header("Authorization", AUTH)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = r.read()
            return r.status, (d if raw else json.loads(d))
    except urllib.error.HTTPError as e:
        d = e.read()
        try:
            return e.code, json.loads(d)
        except ValueError:
            return e.code, d


def q(p):
    return urllib.parse.quote(p, safe="")


def mc(cmd, **kw):
    return call("POST", f"/api/mc/{cmd}?client=t1&" + "&".join(f"{k}={v}" for k, v in kw.items()))


def main():
    d = tempfile.mkdtemp(prefix="neoserver_")
    data, backup = os.path.join(d, "data"), os.path.join(d, "backup")
    os.makedirs(data)
    cfg = os.path.join(data, "Config.ini")
    open(cfg, "wb").write(b"[Default]\nnRunSpeed=80\n")
    ini = os.path.join(d, "neoserver.ini")
    open(ini, "w").write(f"[server]\nhttp_port={HTTP}\nbind=127.0.0.1\nhome_at_start=no\nlogin=yes\nuser=admin\npassword=pw\nupdate_key=k\n"
                         f"backup={backup}\nneoden_dir={data}\ncamera=tcp://127.0.0.1:{CAM}\nmachine=sim://\nservices=neoserver\n"
                         f"home={d}\nwww={os.path.join(ROOT, 'neoagent', 'www')}\n")
    ev = threading.Event()
    threading.Thread(target=simserver.serve, kwargs=dict(port=CAM, ready=ev), daemon=True).start()
    ev.wait(5)
    srv = subprocess.Popen([sys.executable, "-m", "neoserver", "--config", ini], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if call("GET", "/api/ping")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        assert call("GET", "/api/status", auth=False)[0] == 401
        s, st = call("GET", "/api/status")
        assert s == 200 and st["neoden_dir"] == data and "camserver sim" in st["camera"], st
        assert b"<title>NeoDen4 control</title>" in call("GET", "/", raw=True)[1]
        # files, backup, restore
        assert call("GET", f"/api/file?path={q(cfg)}", raw=True)[1] == b"[Default]\nnRunSpeed=80\n"
        s, r = call("PUT", f"/api/file?path={q(cfg)}", b"[Default]\nnRunSpeed=55\n")
        assert s == 200 and r["backup"], r
        assert {e["name"] for e in call("GET", f"/api/files?path={q(data)}")[1]["entries"]} >= {"Config.ini"}
        assert call("POST", f"/api/restore?id={r['backup']}")[0] == 200
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=80\n"
        big = os.urandom(300000)
        assert call("PUT", f"/api/file?path={q(os.path.join(data, 'big.bin'))}", big)[0] == 200
        assert call("GET", f"/api/file?path={q(os.path.join(data, 'big.bin'))}", raw=True)[1] == big
        # updates
        src = os.path.join(d, "src"); os.makedirs(src)
        open(os.path.join(src, "a.csv"), "wb").write(b"a,b\n")
        open(os.path.join(src, "c.ini"), "wb").write(b"[Default]\nnRunSpeed=70\n")
        s, r = call("POST", "/api/update", mkpkg.build("k", [(os.path.join(src, "a.csv"), os.path.join(data, "Proc", "a.csv")),
                                                              (os.path.join(src, "c.ini"), cfg)], "u1"))
        assert s == 200 and r["ok"] and open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n", r
        s, r = call("POST", "/api/update", mkpkg.build("wrong", [(os.path.join(src, "a.csv"), cfg)], "bad"))
        assert s == 500 and "signature" in r["report"]
        os.makedirs(os.path.join(data, "blocker"))
        s, r = call("POST", "/api/update", mkpkg.build("k", [(os.path.join(src, "a.csv"), cfg), (os.path.join(src, "a.csv"), os.path.join(data, "blocker"))], "rb"))
        assert s == 500 and open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n", r
        # camera: BMP, head camera turned 180 deg, scale 2
        assert call("POST", "/api/cam/set?cam=1&w=800&h=800&exposure=30")[0] == 200
        s, bmp = call("GET", "/api/cam/grab?cam=1&scale=2", raw=True)
        assert s == 200 and bmp[:2] == b"BM" and int.from_bytes(bmp[18:22], "little") == 400, bmp[:60]
        req = urllib.request.Request(f"http://127.0.0.1:{HTTP}/api/cam/grab?cam=1&settle=1", headers={"Authorization": AUTH})
        with urllib.request.urlopen(req, timeout=60) as r:            # still sim camera: settles at once
            assert r.read()[:2] == b"BM" and r.headers["X-Settle"].startswith("ok"), r.headers["X-Settle"]
        # machine through neomach (sim://)
        assert call("GET", "/api/mc/status")[1]["xy_state_name"] == "not homed"
        assert mc("home")[0] == 409                                   # no control yet
        assert call("POST", "/api/mc/lock?client=t1")[1]["ok"]
        r = call("POST", "/api/mc/lock?client=t2")[1]
        assert not r["ok"] and r["owner_from"] == "the machine screen", r
        assert mc("home")[0] == 200
        st = call("GET", "/api/mc/status")[1]
        assert st["xy_state"] == 0 and abs(st["pos"][1] - 400.05) < 0.01, st
        assert mc("move", x=120.5, y=80.25, slow=1)[0] == 200
        assert call("GET", "/api/mc/status")[1]["pos"] == [120.5, 80.25]
        assert mc("move", x=500, y=1)[0] == 409
        assert mc("rail_speed", v=200)[0] == 200
        assert mc("reg_read", addr=6)[1]["data"][:4] == "5009"          # XY speed 80 kept
        assert mc("z", n=1, mm=5)[0] == 200 and mc("rotate", n=2, deg=90)[0] == 200
        assert mc("move", x=100, y=100)[0] == 200 and call("GET", "/api/mc/status")[1]["z"] == [0.0, 0.0, 0.0, 0.0]
        assert mc("valve", ch=2, state=-1)[0] == 200
        assert mc("light", on=1)[0] == 200 and call("GET", "/api/mc/status")[1]["light"] is True
        assert mc("feed", n=12, p0=50, p1=4)[0] == 200 and "done" in mc("feeder", n=12)[1]
        print("neoserver end-to-end: OK (auth, files+backup+restore, updates+rollback, camera, machine via neomach)")
    finally:
        srv.kill()
        out = srv.stdout.read()
        if "--verbose" in sys.argv:
            print(out)


def handover():
    """control lock: an idle owner hands over (machine screen + LAN browser), a busy one does not"""
    from neoserver import hw
    now = [1000.0]
    hw.time.monotonic = lambda: now[0]
    try:
        m = hw.Machine("sim://", lambda *a: None)

        def wait(sec, who):                                          # the owner's page renews every 5 s
            for _ in range(int(sec // 5)):
                now[0] += 5
                assert m.take(who)[0]
        assert m.take("screen", addr="127.0.0.1") == (True, "screen") and m.owner_label() == "the machine screen"
        now[0] += 5
        assert m.take("screen") == (True, "screen")                  # plain renewal (api.js heartbeat)
        assert m.take("lan") == (False, "screen")                    # owner active 5 s ago
        now[0] += 6
        assert m.take("screen") == (True, "screen")                  # renewal does not count as activity
        assert m.take("lan", addr="192.168.1.20") == (True, "lan")   # 11 s idle: handed over
        assert m.owner_label() == "192.168.1.20"
        assert m.take("screen") == (False, "lan")                    # no ping-pong right after
        now[0] += 3
        assert m.take("lan", hold=True)[0]                           # hold=1 (scripts) keeps it active
        now[0] += 9
        assert m.take("screen") == (False, "lan")
        with m.busy_call():                                          # an align call of 1 min
            wait(60, "lan")
            assert m.take("screen") == (False, "lan")
        wait(5, "lan")
        assert m.take("screen") == (False, "lan")                    # idle counts from the end of the call
        m.job_running = True
        wait(60, "lan")
        assert m.take("screen") == (False, "lan")                    # never during a job
        m.job_running = False
        assert m.take("screen") == (True, "screen")
        assert m.take("lan", release=True) == (True, "screen")       # only the owner releases
    finally:
        hw.time.monotonic = time.monotonic
    print("control hand-over: OK")


if __name__ == "__main__":
    handover()
    main()


def test_lights_back_after_idle_off():
    """the lamps switched off idle come back as they were on the next picture / command / align call"""
    from neoserver.hw import Machine

    class MB:
        led, sw = 2, [0, 0, 0, 0, 1]
        def head_led(self, v): self.led = v
        def up_light(self, on): self.sw[4] = 1 if on else 0

    m = Machine("none", lambda *a: None)
    m.mb = MB()
    m.idle_off = (m.mb.led, True)
    m.mb.head_led(0); m.mb.up_light(False)
    m.relight()
    assert m.mb.led == 2 and m.mb.sw[4] & 1 and m.idle_off is None
    m.relight()                                   # nothing to do twice
    assert m.mb.led == 2
    m.idle_off = (3, False); m.mb.head_led(0)
    with m.busy_call():
        assert m.mb.led == 3


def test_auto_exposure():
    """white tape burns out at the page's exposure: brought down until its bright end is near 200 grey;
    a dark board is brought up the same way"""
    import numpy as np
    from neoserver.hw import Cameras

    class Cam:
        def __init__(self, k): self.k, self.exp = k, 25
        def exposure(self, cam, v): self.exp = v
        def grab(self, cam):
            g = np.linspace(0.2, 1.0, 64 * 64).reshape(64, 64) * self.k * self.exp
            return np.clip(g, 0, 255).astype(np.uint8)

    for k, up in ((40.0, False), (2.0, True)):              # tape: 25 * 40 = 1000 (burnt); board: 50
        c = Cameras("none", lambda *a: None)
        c.cam = Cam(k)
        r = c.auto_exposure(1)
        assert abs(r["p99"] - 200) <= 30 and r["steps"] <= 7, r
        assert (r["exp"] > 25) == up and c.cur_exp[1] == r["exp"]
