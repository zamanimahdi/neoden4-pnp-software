"""The job wizard's server side: neoserver /api/setup/call driving neosetup over HTTP.

    python tests/test_setup_wizard.py

Uploads the GPS sample Pick & Place CSV and its machine file (from KFG_TOOL_DIR, as
neosetup's own tests) like the web wizard does, then runs the wizard's calls in order:
import, template, BOM, board turn, alignment plan/apply, build, feeders, order, speed and
the machine file written into Proc. Skipped when the fixtures are missing.
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
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOL_DIR = os.environ.get("KFG_TOOL_DIR", "")
GPS = os.path.join(TOOL_DIR, "Pick Place for BOARD-C Main(VA.1).csv")
GPS_MACHINE = os.path.join(TOOL_DIR, "BOARD-C main 2.csv")
HTTP = 18191
AUTH = "Basic " + base64.b64encode(b"admin:pw").decode()


def req(method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{HTTP}{path}", data=body, method=method, headers={"Authorization": AUTH})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def call(method, *args, expect=200, **kw):
    code, j = req("POST", "/api/setup/call", json.dumps({"sid": "t1", "method": method, "args": list(args), "kwargs": kw}).encode())
    assert code == expect, (method, code, j)
    return j["result"] if code == 200 else j


def main():
    if not (os.path.exists(GPS) and os.path.exists(GPS_MACHINE)):
        print("setup wizard: SKIPPED (no KFG_TOOL_DIR fixtures)")
        return
    d = tempfile.mkdtemp(prefix="wizard_")
    data = os.path.join(d, "data")
    os.makedirs(os.path.join(data, "Proc"))
    ini = os.path.join(d, "neoserver.ini")
    open(ini, "w").write(f"[server]\nhttp_port={HTTP}\nbind=127.0.0.1\nuser=admin\npassword=pw\nupdate_key=k\n"
                         f"backup={os.path.join(d, 'backup')}\nneoden_dir={data}\ncamera=none\nmachine=sim://\nservices=neoserver\n"
                         f"home={d}\nwww={os.path.join(ROOT, 'neoagent', 'www')}\n")
    srv = subprocess.Popen([sys.executable, "-m", "neoserver", "--config", ini], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if req("GET", "/api/ping")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        jobs = os.path.join(data, "Jobs")
        up = lambda src: (req("PUT", "/api/file?path=" + urllib.parse.quote(os.path.join(jobs, os.path.basename(src)), safe=""), open(src, "rb").read()),
                          os.path.join(jobs, os.path.basename(src)))[1]
        shutil.copy(GPS_MACHINE, os.path.join(data, "Proc", "BOARD-C main 2.csv"))
        # wizard step 1..3
        r = call("import_pnp", up(GPS))
        assert r["components"] > 50 and r["placeable"] > 0, r
        call("import_machine_file", os.path.join(data, "Proc", "BOARD-C main 2.csv"))
        assert call("stats")["components"] == r["components"]
        bom = call("bom")
        assert bom and {"part", "qty", "flag"} <= set(bom[0]), bom[0]
        assert call("edit_component", "NO-SUCH-REF", x=1.0, expect=404)["ok"] is False
        assert call("frobnicate", expect=400)["ok"] is False                 # not in the allowed list
        assert call("set_part_height", bom[0]["part"], -1, expect=422)["title"]   # the tool refuses it
        # step 4: board
        call("rotate_board_quarter", 90)
        # step 5: alignment, measured = design turned 0.5 deg and shifted (what the camera would read)
        pair = call("suggest_alignment", 6)[0]
        import math
        t = math.radians(0.5)
        m = lambda c: [100 + c["x"] * math.cos(t) - c["y"] * math.sin(t), 50 + c["x"] * math.sin(t) + c["y"] * math.cos(t)]
        plan = call("plan_alignment", pair["a"]["designator"], pair["b"]["designator"], m(pair["a"]), m(pair["b"]), side="top")
        assert abs(abs(plan["theta"]) - 0.5) < 0.01 and abs(plan["scale"] - 1) < 1e-3, plan
        res = call("apply_alignment", pair["a"]["designator"], pair["b"]["designator"], m(pair["a"]), m(pair["b"]), side="top")
        assert res["check_b"]["designator"] == pair["b"]["designator"], res
        # the same with board points (design X, Y typed in, not a part): a further turn of 0.5 deg
        cs = {c["designator"]: c for c in call("components")}
        ca, cb = cs[pair["a"]["designator"]], cs[pair["b"]["designator"]]
        pa, pb = [ca["x"] + 1.0, ca["y"]], [cb["x"], cb["y"] - 1.0]
        mp = lambda p: m({"x": p[0], "y": p[1]})
        plan = call("plan_alignment", pa, pb, mp(pa), mp(pb), side="top")
        assert abs(abs(plan["theta"]) - 0.5) < 0.01 and abs(plan["scale"] - 1) < 1e-3, plan
        res = call("apply_alignment", pa, pb, mp(pa), mp(pb), side="top")
        assert res["check_b"]["designator"].startswith("point"), res
        moved = {c["designator"]: c for c in call("components")}[ca["designator"]]
        want = m(ca)
        assert abs(moved["x"] - want[0]) < 0.01 and abs(moved["y"] - want[1]) < 0.01, (moved, want)
        # the marks taken with the camera (machine frame after the alignment)
        assert call("add_mark", 120.5, 60.25) == [[120.5, 60.25]]
        call("add_mark", 130.0, 70.0); call("add_mark", 1.0, 2.0)
        assert call("delete_mark", 2) == [[120.5, 60.25], [130.0, 70.0]]
        # step 6..8
        pb = call("prepare_build", top_only=True)
        assert pb["parts"] and pb["has_template"], pb.keys()
        b = call("build_machine", top_only=True)
        assert b["feeders"] > 0 if isinstance(b["feeders"], int) else b["feeders"], b
        fs = call("feeders")
        assert fs and "pick_height" in fs[0]
        pl = call("placements")
        assert len(pl) > 50 and all(p["fiducial"] for p in pl[:sum(p["fiducial"] for p in pl)]), pl[:3]   # fiducials first
        call("auto_sort")
        assert "summary" in call("speed_plan")
        call("apply_speed_plan", "both")
        # step 9
        out = os.path.join(data, "Proc", "gps-wizard.csv")
        call("write_machine_file", out)
        txt = open(out, "rb").read()
        # after the alignment the board rows fit the parts: one panel at the first part, no turn, the
        # marks taken here (not the template's, which were for another place of the board)
        L = [l.split(",") for l in txt.decode("latin-1").splitlines()]
        first = next(t for t in L if t[0] == "comp")
        mir = [t for t in L if t[0] == "mirror"]
        assert len(mir) == 1 and (float(mir[0][1]), float(mir[0][2])) == (float(first[6]), float(first[7])), (mir, first)
        mc = next(t for t in L if t[0] == "mirror_create")
        assert mc[1:3] == ["1", "1"] and float(mc[9]) == 0.0, mc
        mk = next(t for t in L if t[0] == "mark")
        assert mk[1] == "Whole" and [float(v) for v in mk[3:7]] == [120.5, 60.25, 130.0, 70.0], mk
        assert sum(1 for t in L if t[0] == "markext") == 2
        assert txt.count(b"\r\ncomp,") > 50 and txt.startswith(b"#Feeder") or b"stack," in txt, txt[:200]
        assert txt == call("machine_file_text").encode("latin-1", "replace") or len(txt) > 1000
        call("save_project", os.path.join(jobs, "gps.kfg"))
        assert os.path.getsize(os.path.join(jobs, "gps.kfg")) > 1000
        assert req("POST", "/api/setup/close?sid=t1")[0] == 200
        assert call("stats")["components"] == 0                              # a new, empty project
        print("setup wizard over HTTP: OK (import, template, BOM, refusals, board, alignment, build, order, speed, machine file, project)")
    finally:
        srv.kill()
        if "--verbose" in sys.argv:
            print(srv.stdout.read())


if __name__ == "__main__":
    main()
