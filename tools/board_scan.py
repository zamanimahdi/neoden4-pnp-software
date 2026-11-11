"""Scan a whole board on the machine: fiducials, then pad alignment of every part with pads.

    python tools/board_scan.py --host neoden.local --job /opt/neoden/data/Proc/BOARD-A-1.csv

Needs <job>.pads.json on the machine (neojob.pads). Writes to ./board_scan_<time>/:
  map.png       the board: every part at its expected centre (blue), its centre read from the
                pads (green, the shift drawn x20 as a line), parts that failed (red cross)
  parts.csv     designator, footprint, aligned, dx, dy, turn, rms, matched/expected, message
  <REF>_<n>.png each part's views (Gerber pads blue, found pads yellow, corrected pads green)
The machine moves; it uses client id "live-vision", so the live window can watch.
"""
import argparse
import base64
import csv
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="neoden.local")
ap.add_argument("--job", required=True)
ap.add_argument("--refs", default="", help="only these designators (comma separated)")
a = ap.parse_args()
B = f"http://{a.host}"
C = "live-vision"
OUT = time.strftime("board_scan_%H%M%S")
os.makedirs(OUT, exist_ok=True)


def req(m, p, q=None, timeout=300):
    r = urllib.request.Request(B + p + ("?" + urllib.parse.urlencode(q) if q else ""), data=b"" if m == "POST" else None, method=m)
    try:
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


stop = False


def hb():
    while not stop:
        try:
            req("POST", "/api/mc/lock", {"client": C, "hold": 1}, 20)
        except Exception:
            pass
        time.sleep(4)


assert req("POST", "/api/mc/lock", {"client": C, "hold": 1}).get("ok"), "the machine is controlled by another client"
threading.Thread(target=hb, daemon=True).start()
parts = req("GET", "/api/align/parts", {"job": a.job})["parts"]
parts = [p for p in parts if p["in_job"] and p["pads"] >= 2]
if a.refs:
    want = {r.strip() for r in a.refs.split(",")}
    parts = [p for p in parts if p["ref"] in want]
print(f"{len(parts)} parts with pads")
rows = []
try:
    st = req("GET", "/api/mc/status")
    if st.get("xy_state_name") != "idle" or not st.get("pos"):
        print("home", req("POST", "/api/mc/home", {"client": C}).get("ok"))
    m = req("POST", "/api/align/marks", {"client": C, "job": a.job})
    print("marks:", m.get("error") or [mk["measured"] for mk in m.get("marks", [])])
    t0 = time.time()
    for n, p in enumerate(parts, 1):
        r = req("POST", "/api/align/part", {"client": C, "job": a.job, "ref": p["ref"]})
        ok = r.get("ok") and r.get("aligned")
        row = {"ref": p["ref"], "footprint": p["footprint"], "x": p["x"], "y": p["y"], "aligned": bool(ok),
               "dx": r.get("dx", 0.0), "dy": r.get("dy", 0.0), "turn": r.get("dtheta", 0.0), "rms": r.get("rms", 0.0),
               "matched": f"{r.get('matched', 0)}/{r.get('expected', 0)}", "message": r.get("message") or r.get("error", "")}
        rows.append(row)
        for i, v in enumerate(r.get("views", []), 1):
            if v.get("png"):
                open(os.path.join(OUT, f"{p['ref']}_{i}.png"), "wb").write(base64.b64decode(v["png"]))
        el = time.time() - t0
        print(f"[{n:3d}/{len(parts)}] {p['ref']:6s} {p['footprint']:22s} {'OK ' if ok else 'NO '} "
              f"{row['dx']:+.3f} {row['dy']:+.3f} mm {row['turn']:+.2f} deg  {row['message'][:60]}   "
              f"(~{int(el / n * (len(parts) - n) / 60)} min left)", flush=True)
finally:
    stop = True
with open(os.path.join(OUT, "parts.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ref"])
    w.writeheader()
    w.writerows(rows)

# the map: job frame, 8 px per mm, shifts drawn x20
if rows:
    xs, ys = [r["x"] for r in rows], [r["y"] for r in rows]
    x0, x1, y0, y1 = min(xs) - 8, max(xs) + 8, min(ys) - 8, max(ys) + 8
    S = 8.0
    img = np.full((int((y1 - y0) * S), int((x1 - x0) * S), 3), 30, np.uint8)
    P = lambda x, y: (int((x - x0) * S), int((y1 - y) * S))
    for r in rows:
        c = P(r["x"], r["y"])
        if r["aligned"]:
            e = P(r["x"] + 20 * r["dx"], r["y"] + 20 * r["dy"])
            cv2.line(img, c, e, (0, 200, 0), 1, cv2.LINE_AA)
            cv2.circle(img, c, 2, (255, 140, 0), -1)
            cv2.drawMarker(img, e, (0, 255, 0), cv2.MARKER_CROSS, 8, 1)
        else:
            cv2.drawMarker(img, c, (0, 0, 255), cv2.MARKER_TILTED_CROSS, 10, 2)
        cv2.putText(img, r["ref"], (c[0] + 4, c[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    ok = sum(r["aligned"] for r in rows)
    cv2.putText(img, f"{ok}/{len(rows)} parts read from their pads; blue = expected centre, green = read centre "
                     f"(shift x20), red = not read", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.imwrite(os.path.join(OUT, "map.png"), img)
    print(f"\n{ok}/{len(rows)} parts aligned. Results: {os.path.abspath(OUT)}")
