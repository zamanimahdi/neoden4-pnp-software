"""Live pad-alignment tests on the machine from a PC on the LAN (until the web page exists).

    python tools/padalign_live.py --host neoden.local --user admin --password ... COMMAND ...

  detect [X Y] [--led 2 --exp 0 --thr 0 --invert --min 0.05 --max 25]
                       find pads where the camera is (or at X Y): count, threshold, image
  camcal [X Y] [--step 1]
                       head camera px/mm and angle (put the camera over a group of pads)
  pads JOB PNP GERBER  build JOB.pads.json on the machine (paths on the machine)
  parts JOB            parts that have pads
  marks JOB            find the job's fiducials (keeps the board transform for `part`)
  part JOB REF         pad alignment of one part: the correction it would make
  leads N bg           empty nozzle N over the up camera: keep it as the background
  leads N JOB REF      the part held by nozzle N: its leads matched to its pads (and NeoDen's
                       body outline for comparison)

Images go to ./padalign_out/<command>-<n>.png (blue + expected pad, yellow found pad,
green + fitted pad, red + the camera centre). The command takes the machine control lock.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="neoden.local")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="padalign_out")
    for k in ("led", "exp", "thr", "min", "max", "radius", "step"):
        ap.add_argument("--" + k)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("cmd", choices=["detect", "camcal", "pads", "parts", "marks", "part", "leads"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    base = f"http://{a.host}:{a.port}"
    auth = "Basic " + base64.b64encode(f"{a.user}:{a.password}".encode()).decode()
    client = f"cli-{os.getpid()}"

    def call(method, path, q=None):
        url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
        r = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method, headers={"Authorization": auth})
        try:
            with urllib.request.urlopen(r, timeout=300) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            sys.exit(f"{e.code}: {body}")

    q = {"client": client}
    for k in ("led", "exp", "thr", "min", "max", "radius", "step"):
        if getattr(a, k) is not None:
            q[k] = getattr(a, k)
    if a.invert:
        q["invert"] = "1"
    x = a.args
    if a.cmd in ("detect", "camcal") and len(x) >= 2:
        q.update(x=x[0], y=x[1])
    elif a.cmd == "pads":
        q.update(job=x[0], pnp=x[1], gerber=x[2])
    elif a.cmd in ("parts", "marks"):
        q.update(job=x[0])
    elif a.cmd == "part":
        q.update(job=x[0], ref=x[1])
    elif a.cmd == "leads":
        q.update(nozzle=x[0], **({"bg": "1"} if x[1:2] == ["bg"] else {"job": x[1], "ref": x[2]}))
    moving = a.cmd in ("detect", "camcal", "marks", "part", "leads")
    if moving and not call("POST", "/api/mc/lock", {"client": client, "hold": 1}).get("ok"):
        sys.exit("the machine is controlled by another client")
    try:
        r = call("GET" if a.cmd == "parts" else "POST", f"/api/align/{a.cmd}", q)
    finally:
        if moving:
            call("POST", "/api/mc/unlock", {"client": client})
    os.makedirs(a.out, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    pngs = ([r.pop("png")] if r.get("png") else []) + [v.pop("png") for v in r.get("views", []) if v.get("png")]
    for i, p in enumerate(pngs):
        fn = os.path.join(a.out, f"{a.cmd}-{stamp}-{i + 1}.png")
        open(fn, "wb").write(base64.b64decode(p))
        print("image:", fn)
    if "blobs" in r:
        r["blobs"] = f"{len(r['blobs'])} pads (first: {r['blobs'][:3]})"
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
