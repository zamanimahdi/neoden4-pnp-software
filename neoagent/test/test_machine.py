"""Machine control through the agent: browser API -> neoagent.exe -> serbridge.exe -> simulator.

    sh neoagent/build.sh && sh serbridge/build.sh && python neoagent/test/test_machine.py

neomach.sim (the mainboard simulator) serves the serial byte stream over TCP; serbridge.exe
uses it instead of COM2 (--serial-tcp); neoagent.exe supervises serbridge and serves /api/mc/*.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HTTP, SIM, SB = 18090, 15700, 15602
AUTH = "Basic " + base64.b64encode(b"admin:pw").decode()


def call(method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{HTTP}{path}", data=b"" if method == "POST" else None, method=method)
    req.add_header("Authorization", AUTH)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def mc(cmd, client="ui1", **kw):
    q = "&".join([f"client={client}"] + [f"{k}={v}" for k, v in kw.items()])
    return call("POST", f"/api/mc/{cmd}?{q}")


def main():
    d = tempfile.mkdtemp(prefix="neoagent_mc_")
    shutil.copy(os.path.join(ROOT, "neoagent", "neoagent.exe"), d)
    shutil.copy(os.path.join(ROOT, "serbridge", "serbridge.exe"), d)
    os.makedirs(os.path.join(d, "www"))
    open(os.path.join(d, "neoagent.ini"), "w").write(
        f"[agent]\nhttp_port={HTTP}\nftp_port=0\nbind=127.0.0.1\nuser=admin\npassword=pw\nupdate_key=k\nserbridge_port={SB}\n"
        f"[procs]\nserbridge=|serbridge.exe --bind 127.0.0.1 --port {SB} --serial-tcp 127.0.0.1:{SIM}\n")
    sim = subprocess.Popen([sys.executable, "-m", "neomach.sim", "--port", str(SIM)], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    agent = subprocess.Popen([os.path.join(d, "neoagent.exe")], cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                s, st = call("GET", "/api/mc/status")
                if st.get("ok"):
                    break
            except OSError:
                pass
            time.sleep(0.2)
        assert st["ok"] and "serbridge" in st["bridge"], st
        assert st["xy_state_name"] == "not homed" and st["pos"] is None, st
        # control lock
        s, r = mc("home")
        assert s == 409 and "take control" in r["error"], r
        assert call("POST", "/api/mc/lock?client=ui1")[1]["ok"]
        assert not call("POST", "/api/mc/lock?client=ui2")[1]["ok"]            # someone else has it
        s, r = mc("move", x=10, y=10)
        assert s == 409 and "home first" in r["error"], r
        # home, moves, limits
        assert mc("home")[0] == 200
        st = call("GET", "/api/mc/status")[1]
        assert st["xy_state"] == 0 and st["pos"] == [0.0, 400.05], st
        assert mc("move", x=120.5, y=80.25)[0] == 200
        assert call("GET", "/api/mc/status")[1]["pos"] == [120.5, 80.25]
        assert mc("rel", x=1.5, y=-0.25, slow=1)[0] == 200
        assert call("GET", "/api/mc/status")[1]["pos"] == [122.0, 80.0]
        s, r = mc("move", x=500, y=10)
        assert s == 409 and "outside" in r["error"], r
        # nozzles
        assert mc("rotate", n=2, deg=90)[0] == 200
        assert mc("z", n=1, mm=5)[0] == 200
        st = call("GET", "/api/mc/status")[1]
        assert st["z"][0] == 5.0 and st["angle"][1] == 90.0, st
        assert mc("move", x=100, y=100)[0] == 200                               # raises nozzles first
        assert call("GET", "/api/mc/status")[1]["z"] == [0.0, 0.0, 0.0, 0.0]
        assert mc("valve", ch=1, state=1)[0] == 200
        # lights, outputs, rail, feeder, raw registers
        assert mc("light", on=1)[0] == 200 and call("GET", "/api/mc/status")[1]["light"] is True
        assert mc("led", v=1)[0] == 200
        assert mc("rail_speed", v=200)[0] == 200
        s, r = mc("reg_read", addr=6)                                          # reg 6 written whole:
        assert r["data"][:4] == "5009" and r["data"][6:8] == "c8", r          # XY speed 80, force 9 kept
        assert mc("valve", ch=2, state=-1)[0] == 200                          # suck = -1 -> 0xff
        assert mc("reg_read", addr=3)[1]["data"][:4] == "ff02"
        assert mc("z", n=1, mm=12.5)[0] == 200 and mc("z", n=1, mm=0)[0] == 200
        assert mc("z", n=1, mm=14)[0] == 409                                  # beyond 13 mm refused
        assert mc("rail_move", mm=25.4)[0] == 200
        assert mc("feed", n=12, p0=4)[0] == 200
        s, r = mc("feeder", n=12)
        assert s == 200 and "done" in r, r
        s, r = mc("reg_read", addr=5)
        assert s == 200 and len(r["data"]) == 16, r
        assert call("POST", "/api/mc/unlock?client=ui1")[1]["ok"]
        assert call("POST", "/api/mc/lock?client=ui2")[1]["ok"]                 # free again
        print("machine control through neoagent + serbridge + simulator: OK")
    finally:
        agent.kill()
        subprocess.run(["taskkill", "/F", "/IM", "serbridge.exe"], capture_output=True)
        sim.kill()


if __name__ == "__main__":
    main()
