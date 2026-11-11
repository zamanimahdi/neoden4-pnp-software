"""End-to-end test of camserver.exe with the fake camera DLLs (both backends).

    python camserver/test/test_camserver.py     (after camserver/build.sh)

Starts the real 32-bit camserver.exe on a copy of the fake DLLs, talks to it with
neocam.CamClient, and checks frames and the DLL calls it made (fakecam.log).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
from neocam import CamClient, UP, HEAD  # noqa: E402


def run(backend, port):
    d = tempfile.mkdtemp(prefix="camserver_")
    shutil.copy(os.path.join(ROOT, "camserver", "camserver.exe"), d)
    dll = "NeodenCamera.dll" if backend == "new" else "imgdll.dll"
    shutil.copy(os.path.join(HERE, "build", backend, dll), d)
    p = subprocess.Popen([os.path.join(d, "camserver.exe"), "--port", str(port), "--bind", "127.0.0.1"],
                         cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(50):
            try:
                c = CamClient("127.0.0.1", port)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("camserver did not start: " + p.stdout.read())
        with c:
            info = c.info()
            assert f"backend={backend}" in info and "up=1024x1024" in info, info
            c.exposure(HEAD, 30)
            c.window(HEAD, 800, 800)
            img = c.grab(HEAD)
            assert img.shape == (800, 800), img.shape
            assert img[400 - 21, 400 + 37] == 180, img[379, 437]     # disc = 6 * exposure
            up = c.grab(UP, 500)
            assert up.shape == (1024, 1024)
            c.gain(UP, 4)
            if backend == "new":
                c.led(HEAD, 2)
                c.reset(UP)
        log = open(os.path.join(d, "fakecam.log")).read().split("\n")
        return info, log
    finally:
        p.terminate()
        p.wait(5)


if __name__ == "__main__":
    info, log = run("new", 5621)
    print("new:", info)
    want = ["lt 5 128 0", "wh 5 1024 1024", "exp 5 150", "exp 1 25", "gain 1 8", "exp 1 30",
            "lt 1 240 112", "wh 1 800 800", "readAsy 1 640000 1000 1", "readAsy 5 1048576 500 1",
            "gain 5 4", "led 1 2", "reset 5"]
    miss = [w for w in want if w not in log]
    assert not miss, (miss, log)
    assert "gain 5 8" not in log, "up camera gain must stay at the DLL default at start"
    info, log = run("old", 5622)
    print("old:", info)
    want = ["setSize 0 1024 1024", "setSize 1 1024 1024", "set 0 1 150 8", "set 1 2 25 8",
            "set 1 2 30 8", "setSize 1 800 800", "capture 1", "capture 0", "set 0 1 150 4"]
    miss = [w for w in want if w not in log]
    assert not miss, (miss, log)
    print("camserver end-to-end: OK (new and old backend)")
