"""Live progress of build.py: reads build.log (the VM's console) and shows a percentage.

    python build/image/progress.py

Stages are weighted by how long they take under emulation; the package step (most of the
time) is measured by apt's own lines: downloaded, unpacked and set up out of N packages.
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "build.log")
OUT = os.path.join(HERE, "build.out")

# (marker in the log, percent when it is seen, label)
STAGES = [
    ("login:", 2, "Alpine started"),
    ("### tools", 3, "tools"),
    ("### partition", 4, "partitioning the disk"),
    ("### ubuntu base", 5, "Ubuntu base"),
    ("### files from the host", 6, "files from the host"),
    ("### apt sources", 7, "package lists"),
    ("### packages", 10, "packages"),
    ("### system", 82, "system settings"),
    ("### user neo", 83, "user"),
    ("### network", 84, "network"),
    ("### ssh + ftp", 85, "SSH + FTP"),
    ("### neoden software", 86, "NeoDen software"),
    ("### boot loader", 88, "boot loader (initramfs, grub)"),
    ("### chroot done", 96, "finishing"),
    ("### clean up", 97, "cleaning up"),
    ("### DONE", 99, "done, shutting down"),
]


def bar(p, w=40):
    n = int(w * p / 100)
    return "[" + "#" * n + "-" * (w - n) + "]"


def main():
    t0 = None
    while True:
        out = open(OUT, encoding="utf-8", errors="replace").read() if os.path.exists(OUT) else ""
        log = open(LOG, encoding="utf-8", errors="replace").read() if os.path.exists(LOG) else ""
        if t0 is None:
            t0 = os.path.getmtime(OUT) if os.path.exists(OUT) else time.time()
        p, label = 0, "starting QEMU"
        img = os.path.join(HERE, "neoden-ubuntu.img")
        if not log and os.path.exists(img):
            sz = os.path.getsize(img)
            p, label = 1.5 * sz / 15e9, f"preparing the 15 GB disk file: {sz / 1e9:.1f} GB"
        for marker, pct, lab in STAGES:
            if marker in log:
                p, label = pct, lab
        if "### packages" in log and "### system" not in log:
            m = re.search(r"(\d+) newly installed", log)
            n = int(m.group(1)) if m else 0
            got = len(re.findall(r"^Get:\d+", log, re.M))
            unp = len(re.findall(r"^Unpacking ", log, re.M))
            setup = len(re.findall(r"^Setting up ", log, re.M))
            if n:
                # download 10..30 %, unpack 30..60 %, set up 60..82 %
                frac = 0.25 * min(got / n, 1) + 0.40 * min(unp / n, 1) + 0.35 * min(setup / n, 1)
                p = 10 + 72 * frac
                label = f"packages: {n} total, downloaded {got}, unpacked {unp}, set up {setup}"
        done = "BUILD_OK_42" in log or "EXIT=0" in out
        failed = "BUILD_FAIL_42" in log or re.search(r"EXIT=[1-9]", out) or "Traceback" in out
        if done:
            p, label = 100, "FINISHED: " + (out.strip().splitlines()[-2] if out.strip().count("\n") else "image ready")
        el = time.time() - t0
        eta = f"~{int(el * (100 - p) / p / 60)} min left" if 3 < p < 100 else ""
        last = [l for l in log.splitlines() if l.strip()][-1:] or [""]
        os.system("cls" if os.name == "nt" else "clear")
        print("NeoDen disk image build\n")
        print(f"{bar(p)} {p:5.1f} %")
        print(f"\nstage:   {label}")
        print(f"elapsed: {int(el // 3600)}h {int(el % 3600 // 60):02d}m   {eta}")
        print(f"\nlast console line:\n  {last[0][:110]}")
        if failed:
            print("\nBUILD FAILED - see build/image/build.log and build.out")
            print(out[-800:])
            return 1
        if done:
            return 0
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
