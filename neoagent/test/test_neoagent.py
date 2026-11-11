"""End-to-end test of the real neoagent.exe (no machine needed).

    sh neoagent/build.sh && sh camserver/build.sh && python neoagent/test/test_neoagent.py

Runs neoagent.exe in a temp folder (HTTP 18080, FTP 12121) supervising the real camserver.exe
with the fake camera DLL, then checks: login, status, file read/write with backup + restore,
camera BMP, supervisor restart, FTP (list, upload with backup, download, rename, delete),
update packages (good, bad signature, rollback), and a self update of neoagent.exe.
"""
import base64
import ftplib
import io
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
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mkpkg  # noqa: E402

HTTP, FTP, CAM = 18080, 12121, 15600
USER, PW, KEY = "admin", "s3cret", "test-key"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()


def call(method, path, body=None, auth=True, raw=False):
    req = urllib.request.Request(f"http://127.0.0.1:{HTTP}{path}", data=body, method=method)
    if auth:
        req.add_header("Authorization", AUTH)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


def wait_http(t=15):
    for _ in range(t * 10):
        try:
            if call("GET", "/api/ping")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise SystemExit("neoagent did not answer")


def main():
    d = tempfile.mkdtemp(prefix="neoagent_")
    agent, machine = os.path.join(d, "agent"), os.path.join(d, "NeoDen4")
    os.makedirs(os.path.join(agent, "www")); os.makedirs(machine)
    shutil.copy(os.path.join(ROOT, "neoagent", "neoagent.exe"), agent)
    shutil.copy(os.path.join(ROOT, "camserver", "camserver.exe"), machine)
    shutil.copy(os.path.join(ROOT, "camserver", "test", "build", "new", "NeodenCamera.dll"), machine)
    open(os.path.join(agent, "www", "index.html"), "w").write("<h1>neo</h1>")
    cfg = os.path.join(machine, "Config.ini")
    open(cfg, "wb").write(b"[Default]\nnRunSpeed=80\n")
    open(os.path.join(agent, "neoagent.ini"), "w").write(
        f"[agent]\nhttp_port={HTTP}\nftp_port={FTP}\nbind=127.0.0.1\nuser={USER}\npassword={PW}\n"
        f"update_key={KEY}\nwww=www\nbackup=backup\nftp_root=\ncam_port={CAM}\n\n"
        f"[procs]\ncamserver={machine}|camserver.exe --bind 127.0.0.1 --port {CAM}\n")
    p = subprocess.Popen([os.path.join(agent, "neoagent.exe")], cwd=agent,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_http()
        # auth
        assert call("GET", "/api/status", auth=False)[0] == 401
        assert call("GET", "/", raw=True)[1] == b"<h1>neo</h1>"
        # supervisor started camserver
        for _ in range(50):
            st = call("GET", "/api/status")[1]
            if st["procs"][0]["running"] and "backend=new" in str(st["camera"]):
                break
            time.sleep(0.2)
        assert st["procs"][0]["running"] and "backend=new" in st["camera"], st
        # files: read, write (backup), restore
        assert call("GET", f"/api/file?path={q(cfg)}", raw=True)[1] == b"[Default]\nnRunSpeed=80\n"
        s, r = call("PUT", f"/api/file?path={q(cfg)}", b"[Default]\nnRunSpeed=55\n")
        assert s == 200 and r["backup"], r
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=55\n"
        lst = call("GET", f"/api/files?path={q(machine)}")[1]
        assert {"Config.ini", "camserver.exe"} <= {e["name"] for e in lst["entries"]}
        s, r = call("POST", f"/api/restore?id={r['backup']}")
        assert s == 200 and open(cfg, "rb").read() == b"[Default]\nnRunSpeed=80\n", r
        big = os.urandom(300000)                     # bodies larger than the header buffer
        assert call("PUT", f"/api/file?path={q(machine + chr(92) + 'big.bin')}", big)[0] == 200
        assert call("GET", f"/api/file?path={q(machine + chr(92) + 'big.bin')}", raw=True)[1] == big
        drives = call("GET", "/api/files?path=")[1]["entries"]
        assert any(e["name"] == "C:" for e in drives)
        # camera through the agent: BMP, turned like NeoDen4 (head: 180 deg)
        assert call("POST", "/api/cam/set?cam=1&w=800&h=800&exposure=30")[0] == 200
        s, bmp = call("GET", "/api/cam/grab?cam=1&scale=2", raw=True)
        assert s == 200 and bmp[:2] == b"BM", bmp[:100]
        w, h = int.from_bytes(bmp[18:22], "little"), int.from_bytes(bmp[22:26], "little")
        assert (w, h) == (400, 400)
        # supervisor: kill camserver, it comes back
        pid = call("GET", "/api/procs")[1][0]["pid"]
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        for _ in range(80):
            pr = call("GET", "/api/procs")[1][0]
            if pr["running"] and pr["pid"] != pid:
                break
            time.sleep(0.2)
        assert pr["running"] and pr["restarts"] >= 1, pr
        # FTP
        ftp = ftplib.FTP()
        ftp.connect("127.0.0.1", FTP, timeout=20)
        ftp.login(USER, PW)
        assert "C" in ftp.nlst("/")
        vdir = "/" + machine[0] + "/" + machine[3:].replace("\\", "/")
        ftp.cwd(vdir)
        assert "Config.ini" in ftp.nlst()
        ftp.storbinary("STOR Config.ini", io.BytesIO(b"[Default]\nnRunSpeed=99\n"))
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=99\n"
        buf = io.BytesIO(); ftp.retrbinary("RETR Config.ini", buf.write)
        assert buf.getvalue() == b"[Default]\nnRunSpeed=99\n"
        ftp.storbinary("STOR new.txt", io.BytesIO(b"x" * 100000))
        assert ftp.size("new.txt") == 100000
        ftp.rename("new.txt", "new2.txt"); ftp.delete("new2.txt")
        assert "new2.txt" not in ftp.nlst()
        ftp.mkd("sub"); ftp.rmd("sub")
        ftp.quit()
        backups = call("GET", "/api/backups")[1]
        assert any(b["why"] == "ftp upload" and cfg in b["files"] for b in backups), backups
        # update: good package (stops + restarts camserver, writes a new file)
        src = os.path.join(d, "pkgsrc"); os.makedirs(src)
        open(os.path.join(src, "job.csv"), "wb").write(b"a,b\n")
        open(os.path.join(src, "Config.ini"), "wb").write(b"[Default]\nnRunSpeed=70\n")
        pkg = mkpkg.build(KEY, [(os.path.join(src, "job.csv"), machine + "\\Proc\\job.csv"),
                                (os.path.join(src, "Config.ini"), cfg)], "test1")
        s, r = call("POST", "/api/update", pkg)
        assert s == 200 and r["ok"], r
        assert "stopped camserver" in r["report"] and "started camserver" in r["report"], r["report"]
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n" and os.path.exists(machine + "\\Proc\\job.csv")
        # bad signature: nothing changes
        s, r = call("POST", "/api/update", mkpkg.build("wrong", [(os.path.join(src, "job.csv"), cfg)], "bad"))
        assert s == 500 and "signature" in r["report"] and open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n"
        # a destination that is a folder: refused before anything is changed
        os.makedirs(machine + "\\blocker.ini")
        pkg = mkpkg.build(KEY, [(os.path.join(src, "job.csv"), cfg), (os.path.join(src, "job.csv"), machine + "\\blocker.ini")], "rb")
        s, r = call("POST", "/api/update", pkg)
        assert s == 500 and "cannot back up" in r["report"], r
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n"
        # rollback: third file cannot be replaced (read-only) -> the first one is restored,
        # the new file the package created is removed
        ro = machine + "\\readonly.ini"
        open(ro, "wb").write(b"keep")
        os.chmod(ro, 0o444)
        newf = machine + "\\Proc\\created.csv"
        pkg = mkpkg.build(KEY, [(os.path.join(src, "job.csv"), cfg), (os.path.join(src, "job.csv"), newf),
                                (os.path.join(src, "job.csv"), ro)], "rb2")
        s, r = call("POST", "/api/update", pkg)
        os.chmod(ro, 0o666)
        assert s == 500 and "rolling back" in r["report"], r
        assert open(cfg, "rb").read() == b"[Default]\nnRunSpeed=70\n", open(cfg, "rb").read()
        assert not os.path.exists(newf) and open(ro, "rb").read() == b"keep"
        assert "started camserver" in r["report"]
        # self update: package with neoagent.exe -> new agent answers, old exits
        old_pid = p.pid
        pkg = mkpkg.build(KEY, [(os.path.join(ROOT, "neoagent", "neoagent.exe"), "@agent\\neoagent.exe")], "self")
        s, r = call("POST", "/api/update", pkg)
        assert s == 200 and "staged new neoagent.exe" in r["report"], r
        p.wait(30)                                   # the old agent exits after the hand-over
        time.sleep(1)
        wait_http(20)
        st = call("GET", "/api/status")[1]
        assert os.path.exists(os.path.join(agent, "neoagent.exe.old"))
        for _ in range(50):
            st = call("GET", "/api/status")[1]
            if st["procs"][0]["running"]:
                break
            time.sleep(0.2)
        assert st["procs"][0]["running"], st
        print("neoagent end-to-end: OK (auth, files+backup+restore, camera, supervisor, FTP, updates, rollback, self update)")
        print("old agent pid", old_pid, "exited; new agent running")
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "neoagent.exe"], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "camserver.exe"], capture_output=True)
        try:
            out = p.stdout.read().decode(errors="replace") if p.poll() is not None else ""
            if "--verbose" in sys.argv:
                print(out)
        except Exception:
            pass


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
