"""Build the NeoDen machine disk image (Ubuntu 24.04 + neoserver) with QEMU, on Windows.

    python build/image/build.py            -> build/image/neoden-ubuntu.img (raw, write it to the SSD)
    python build/image/build.py --test     -> boot the finished image in QEMU, check neoserver

Inputs in build/image: ubuntu-base-24.04.5-base-amd64.tar.gz, alpine-virt-3.24.3-x86_64.iso,
site.env (WIFI_SSID / WIFI_PASS). Ubuntu packages come from archive.ubuntu.com.
Passwords are generated once into credentials.txt (kept between builds).
"""
import argparse
import functools
import http.server
import io
import os
import secrets
import socket
import subprocess
import sys
import tarfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
QEMU = r"C:\Program Files\qemu\qemu-system-x86_64.exe"
ISO = os.path.join(HERE, "alpine-virt-3.24.3-x86_64.iso")
BASE = "ubuntu-base-24.04.5-base-amd64.tar.gz"
DISK = os.path.join(HERE, "neoden-ubuntu.img")
DISK_BYTES = 15_000_000_000            # the SSD has 16,013,942,784 bytes
NEODEN_D = os.environ.get("NEODEN_DATA", "")
HTTP_PORT, SER_PORT = 8765, 4555
VM_CPUS = str(max(2, (os.cpu_count() or 4) - 4))     # TCG: one host thread per vCPU; 4 left for Windows
VM_MEM = "14336"


# ---------------------------------------------------------------- tiny ISO 9660 reader
def iso_read(path, want):
    """read file `want` (e.g. /boot/vmlinuz-virt) from an ISO 9660 image"""
    f = open(path, "rb")
    f.seek(16 * 2048)
    pvd = f.read(2048)
    root = pvd[156:156 + 34]

    def entries(rec):
        lba, size = int.from_bytes(rec[2:6], "little"), int.from_bytes(rec[10:14], "little")
        f.seek(lba * 2048)
        data, i = f.read(size), 0
        while i < len(data):
            n = data[i]
            if n == 0:
                i = (i // 2048 + 1) * 2048
                continue
            r = data[i:i + n]
            name = r[33:33 + r[32]].decode("latin-1").split(";")[0].rstrip(".").lower()
            yield name, r
            i += n
    rec = root
    for part in [p for p in want.lower().split("/") if p]:
        rec = next((r for nm, r in entries(rec) if nm == part.replace("-", "_")), None)
        if rec is None:
            raise FileNotFoundError(want)
    f.seek(int.from_bytes(rec[2:6], "little") * 2048)
    return f.read(int.from_bytes(rec[10:14], "little"))


# ---------------------------------------------------------------- settings and payload
def settings():
    cred = os.path.join(HERE, "credentials.txt")
    site = dict(l.strip().split("=", 1) for l in open(os.path.join(HERE, "site.env"), encoding="utf-8") if "=" in l)
    have = open(cred, encoding="utf-8").read() if os.path.exists(cred) else ""
    if "NEO_PASSWORD=" not in have or "UPDATE_KEY=" not in have:   # missing or emptied: new passwords
        c = {"NEO_PASSWORD": secrets.token_urlsafe(9), "WEB_PASSWORD": secrets.token_urlsafe(9), "UPDATE_KEY": secrets.token_hex(16)}
        with open(cred, "w", encoding="utf-8") as f:
            f.write("# NeoDen machine (Ubuntu) - keep this file private\n")
            f.write(f"ssh / ftp:   user neo, password {c['NEO_PASSWORD']}\n")
            f.write("web UI:      http://neoden.local  (no login: LAN only)\n")
            f.write(f"update key:  {c['UPDATE_KEY']}   (tools/mkpkg.py -k ...)\n")
            for k, v in c.items():
                f.write(f"{k}={v}\n")
    c = dict(l.strip().split("=", 1) for l in open(cred, encoding="utf-8") if "=" in l and not l.startswith("#") and " " not in l.split("=")[0])
    return {**site, **c}


def app_tgz():
    """the software + machine data, packed when the VM asks for it (late in the build, so the
    newest code goes into the image)"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        def add(src, arc):
            for base, dirs, files in os.walk(src) if os.path.isdir(src) else [(os.path.dirname(src), [], [os.path.basename(src)])]:
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for fn in files:
                    if fn.endswith((".exe", ".dll", ".o", ".pyc")):
                        continue
                    full = os.path.join(base, fn)
                    name = arc if not os.path.isdir(src) else arc + "/" + os.path.relpath(full, src).replace(os.sep, "/")
                    data = open(full, "rb").read()
                    if fn.endswith(".sh"):                 # scripts run on Linux: LF, executable
                        data = data.replace(b"\r\n", b"\n")
                    ti = tarfile.TarInfo(name); ti.size = len(data)
                    ti.mode = 0o755 if fn.endswith(".sh") else 0o644
                    t.addfile(ti, io.BytesIO(data))
        pkgs = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "__init__.py")))
        for d in pkgs + ["tools", "docs", "boot.d"]:          # every Python package of the project
            if os.path.isdir(os.path.join(ROOT, d)):
                add(os.path.join(ROOT, d), f"app/{d}")
        add(os.path.join(ROOT, "neoagent", "www"), "app/neoagent/www")
        add(os.path.join(ROOT, "README.md"), "app/README.md")
        for n in ("Config.ini", "Limited.ini", "new.csv"):
            p = os.path.join(NEODEN_D, n)
            if os.path.exists(p):
                t.add(p, f"data/{n}")
        for d in ("Proc", "Config"):
            if os.path.isdir(os.path.join(NEODEN_D, d)):
                t.add(os.path.join(NEODEN_D, d), f"data/{d}")
    return buf.getvalue()


def payload(env):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        sh = open(os.path.join(HERE, "setup-chroot.sh"), "rb").read().replace(b"\r\n", b"\n")
        ti = tarfile.TarInfo("setup-chroot.sh"); ti.size = len(sh); ti.mode = 0o755
        t.addfile(ti, io.BytesIO(sh))
        s = "".join(f"{k}='{v}'\n" for k, v in env.items() if k in ("WIFI_SSID", "WIFI_PASS", "NEO_PASSWORD", "WEB_PASSWORD", "UPDATE_KEY"))
        ti = tarfile.TarInfo("settings.env"); ti.size = len(s.encode()); ti.mode = 0o600
        t.addfile(ti, io.BytesIO(s.encode()))
    open(os.path.join(HERE, "payload.tgz"), "wb").write(buf.getvalue())


# ---------------------------------------------------------------- serial console driver
class Console:
    def __init__(self, port, logf):
        for _ in range(100):
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        self.s.settimeout(1)
        self.buf, self.log = "", open(logf, "a", encoding="utf-8", errors="replace")

    def read(self):
        try:
            d = self.s.recv(65536).decode("utf-8", "replace")
        except socket.timeout:
            return ""
        if not d:
            raise EOFError("console closed (VM stopped)")
        self.buf = (self.buf + d)[-20000:]
        self.log.write(d); self.log.flush()
        return d

    def wait(self, text, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if text in self.buf:
                self.buf = self.buf.split(text, 1)[1]
                return True
            self.read()
        raise TimeoutError(f"no '{text}' within {timeout} s")

    def send(self, line):
        self.s.sendall(line.encode() + b"\n")


def accel():
    out = subprocess.run([QEMU, "-accel", "help"], capture_output=True, text=True).stdout
    return "whpx" if "whpx" in out else "tcg,thread=multi"


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith(".sh"):                      # shell scripts always with LF (a CRLF copy breaks `set -eu`)
            fn = os.path.join(HERE, os.path.basename(self.path))
            if os.path.isfile(fn):
                body = open(fn, "rb").read().replace(b"\r\n", b"\n")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        if self.path == "/app.tgz":
            body = app_tgz()
            print(f"app.tgz sent ({len(body)} bytes)")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def serve():
    h = functools.partial(Handler, directory=HERE)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def build():
    env = settings()
    payload(env)
    serve()
    if os.path.exists(DISK):
        os.remove(DISK)
    open(DISK, "wb").close()                    # sparse raw disk (NTFS writes zeros without the flag)
    subprocess.run(["fsutil", "sparse", "setflag", DISK], capture_output=True)
    with open(DISK, "r+b") as f:
        f.truncate(DISK_BYTES)
    kern, initrd = os.path.join(HERE, "vmlinuz-virt"), os.path.join(HERE, "initramfs-virt")
    open(kern, "wb").write(iso_read(ISO, "/boot/vmlinuz-virt"))
    open(initrd, "wb").write(iso_read(ISO, "/boot/initramfs-virt"))
    logf = os.path.join(HERE, "build.log")
    open(logf, "w").close()
    acc = accel()
    print(f"building with QEMU ({acc}); console log: {logf}")
    vm = subprocess.Popen([QEMU, "-accel", acc, "-m", VM_MEM, "-smp", VM_CPUS, "-display", "none",
                           "-kernel", kern, "-initrd", initrd,
                           "-append", "modules=loop,squashfs,sd-mod,usb-storage console=ttyS0,115200",
                           "-drive", f"file={ISO},media=cdrom,readonly=on",
                           "-drive", f"file={DISK},format=raw,if=virtio,cache=unsafe,aio=threads",   # build only: flushed at poweroff
                           "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0",
                           "-serial", f"tcp:127.0.0.1:{SER_PORT},server=on,wait=on"])
    con = Console(SER_PORT, logf)
    try:
        con.wait("login:", 900)
        con.send("root")
        con.wait("#", 60)
        con.send("ip link set eth0 up; udhcpc -q -i eth0; "
                 "echo https://dl-cdn.alpinelinux.org/alpine/v3.24/main > /etc/apk/repositories; "
                 f"wget -q -O /tmp/p.sh http://10.0.2.2:{HTTP_PORT}/provision.sh && "
                 f"sh /tmp/p.sh http://10.0.2.2:{HTTP_PORT} && echo BUILD_OK_$((6*7)) || echo BUILD_FAIL_$((6*7))")
        con.buf = ""          # the echoed command holds "$((6*7))", the shell prints 42
        t0 = time.time()
        while True:
            con.read()
            if "BUILD_OK_42" in con.buf or "BUILD_FAIL_42" in con.buf:
                break
            if time.time() - t0 > 6 * 3600:
                raise TimeoutError("build took more than 6 h")
        ok = "BUILD_OK_42" in con.buf
        con.send("poweroff")
        vm.wait(300)
    finally:
        if vm.poll() is None:
            vm.kill()
    for p in (kern, initrd, os.path.join(HERE, "payload.tgz")):
        os.remove(p)
    print("image:", DISK if ok else "FAILED (see build.log)")
    return ok


def test():
    """boot the image, wait for neoserver on the forwarded port 18080"""
    acc = accel()
    vm = subprocess.Popen([QEMU, "-accel", acc, "-m", VM_MEM, "-smp", VM_CPUS, "-display", "none",
                           "-drive", f"file={DISK},format=raw,if=virtio,snapshot=on",
                           "-netdev", "user,id=n0,hostfwd=tcp:127.0.0.1:18080-:80,hostfwd=tcp:127.0.0.1:12222-:22",
                           "-device", "virtio-net-pci,netdev=n0"])
    import base64
    import urllib.request
    env = settings()
    auth = "Basic " + base64.b64encode(f"admin:{env['WEB_PASSWORD']}".encode()).decode()
    try:
        for i in range(600):
            try:
                req = urllib.request.Request("http://127.0.0.1:18080/api/status", headers={"Authorization": auth})
                print("neoserver answers:", urllib.request.urlopen(req, timeout=5).read()[:300].decode())
                return True
            except Exception:
                time.sleep(2)
        print("no answer from neoserver within 20 min")
        return False
    finally:
        vm.kill()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    sys.exit(0 if (test() if a.test else build()) else 1)
