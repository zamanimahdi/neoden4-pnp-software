#!/usr/bin/env python3
"""Build (and optionally send) a neoagent update package.

    python tools/mkpkg.py -k KEY -o update.neopkg  SRC=DEST [SRC=DEST ...] [--stop NAME] [--name TEXT]
    python tools/mkpkg.py -k KEY --send http://admin:pw@192.168.1.50:8080  SRC=DEST ...

DEST is an absolute path on the machine PC (D:\\NeoDen4\\camserver.exe) or @agent\\<path>
(relative to neoagent.exe's folder, e.g. @agent\\www\\index.html or @agent\\neoagent.exe).
SRC may be a folder: SRC_DIR=DEST_DIR packs every file below it.
KEY must equal update_key in neoagent.ini. Format: see neoagent/update.c.
"""
import argparse
import base64
import hashlib
import hmac
import os
import sys
import urllib.parse
import urllib.request


def expand(pairs):
    out = []
    for p in pairs:
        src, dest = p.split("=", 1)
        if os.path.isdir(src):
            for root, _, files in os.walk(src):
                for f in sorted(files):
                    s = os.path.join(root, f)
                    rel = os.path.relpath(s, src).replace("/", "\\")
                    out.append((s, dest.rstrip("\\") + "\\" + rel))
        else:
            out.append((src, dest))
    return out


def build(key, files, name="update", stop=()):
    head = [b"NEOPKG1", b"name " + name.encode()]
    body = []
    for src, dest in files:
        data = open(src, "rb").read()
        head.append(f"file {len(data)} {hashlib.sha256(data).hexdigest()} {dest}".encode("utf-8"))
        body.append(data)
    head += [b"stop " + s.encode() for s in stop] + [b"end", b""]
    pkg = b"\n".join(head) + b"".join(body)
    return pkg + hmac.new(key.encode(), pkg, hashlib.sha256).digest()


def send(url, pkg):
    u = urllib.parse.urlsplit(url)
    host = f"{u.hostname}:{u.port or 80}"
    req = urllib.request.Request(f"http://{host}/api/update", data=pkg, method="POST")
    if u.username:
        tok = base64.b64encode(f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", "--key", required=True)
    ap.add_argument("-o", "--out")
    ap.add_argument("--send", help="http://user:password@host:port")
    ap.add_argument("--name", default="update")
    ap.add_argument("--stop", action="append", default=[], help="program to stop while updating")
    ap.add_argument("pairs", nargs="+", help="SRC=DEST")
    a = ap.parse_args()
    pkg = build(a.key, expand(a.pairs), a.name, a.stop)
    if a.out:
        open(a.out, "wb").write(pkg)
        print(f"wrote {a.out} ({len(pkg)} bytes)")
    if a.send:
        print(send(a.send, pkg))
    if not a.out and not a.send:
        sys.exit("give -o and/or --send")


if __name__ == "__main__":
    main()
