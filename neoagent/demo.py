"""Run the whole stack locally with fake hardware, to try the web UI without the machine.

    python neoagent/demo.py            then open http://127.0.0.1:8080  (login admin / demo)

Starts in a temp folder: the board simulator (neomach.sim), and neoagent.exe supervising the
real camserver.exe (fake NeodenCamera.dll) and serbridge.exe (connected to the simulator), with
a copy of the machine's Config.ini as the NeoDen4 folder. Ctrl+C stops everything.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_CFG = os.path.join(os.environ.get("NEODEN_DATA", ""), "Config.ini")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    d = tempfile.mkdtemp(prefix="neodemo_")
    agent, neo = os.path.join(d, "agent"), os.path.join(d, "NeoDen4")
    os.makedirs(agent); os.makedirs(os.path.join(neo, "Proc"))
    shutil.copy(os.path.join(ROOT, "neoagent", "neoagent.exe"), agent)
    shutil.copytree(os.path.join(ROOT, "neoagent", "www"), os.path.join(agent, "www"))
    for f in ("camserver/camserver.exe", "camserver/test/build/new/NeodenCamera.dll", "serbridge/serbridge.exe"):
        shutil.copy(os.path.join(ROOT, f), neo)
    if os.path.exists(REAL_CFG):
        shutil.copy(REAL_CFG, neo)
    open(os.path.join(agent, "neoagent.ini"), "w").write(
        f"[agent]\nhttp_port={port}\nftp_port=2121\nbind=127.0.0.1\nuser=admin\npassword=demo\nupdate_key=demo-key\n"
        f"neoden_dir={neo}\n\n[procs]\ncamserver={neo}|camserver.exe --bind 127.0.0.1\n"
        f"serbridge={neo}|serbridge.exe --bind 127.0.0.1 --serial-tcp 127.0.0.1:5700\n")
    sim = subprocess.Popen([sys.executable, "-m", "neomach.sim", "--port", "5700"], cwd=ROOT)
    ag = subprocess.Popen([os.path.join(agent, "neoagent.exe")], cwd=agent)
    print(f"\ndemo in {d}\nweb UI: http://127.0.0.1:{port}  (admin / demo), FTP 127.0.0.1:2121\nCtrl+C to stop\n")
    try:
        while ag.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        ag.kill(); sim.kill()
        for exe in ("camserver.exe", "serbridge.exe"):
            subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)


if __name__ == "__main__":
    main()
