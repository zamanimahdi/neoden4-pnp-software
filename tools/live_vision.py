"""Live view of the machine's cameras with the image processing drawn on top (runs on a PC).

    python tools/live_vision.py [--host neoden.local]

Window: left = camera image with the results, right = what the algorithm sees (threshold).
Keys:
  1 / 2     head camera / up camera
  m         fiducial finder (find_mark_best: 25 thresholds, best round blob, d 0.5..3 mm)
  p         pad finder (detect_pads: Otsu or fixed threshold, blobs of pad size)
  b         part on the nozzle (up camera: NeoDen's outline, analyze_nozzle; needs an empty-nozzle
            background: press g with the nozzle empty first)
  g         take the empty-nozzle background (up camera)
  + / -     exposure up / down          [ / ]   threshold down / up (0 = automatic)
  l         head lamp: off, inner, outer, both     u   up-camera light on / off
  c         centre the head camera on the found fiducial / pad group (moves the machine)
  s         save the current picture          q / Esc   quit
Lamps and moves need the machine control (taken while this runs, released on quit).
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neocam import vision as V           # noqa: E402
from neojob.padalign import detect_pads   # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="neoden.local")
a = ap.parse_args()
B = f"http://{a.host}"
C = "live-vision"


def req(method, path, q=None, raw=False):
    u = B + path + ("?" + urllib.parse.urlencode(q) if q else "")
    r = urllib.request.Request(u, data=b"" if method == "POST" else None, method=method)
    d = urllib.request.urlopen(r, timeout=30).read()
    return d if raw else json.loads(d)


state = {"cam": 1, "mode": "p", "exp": {0: 150, 1: 30}, "thr": 0, "led": 0, "light": False, "bg": None, "ctl": False,
         "msg": "", "last": None}
stop = False


def heartbeat():
    while not stop:
        try:
            state["ctl"] = bool(req("POST", "/api/mc/lock", {"client": C}).get("ok"))
        except Exception:
            state["ctl"] = False
        time.sleep(4)


def mc(cmd, **kw):
    try:
        r = req("POST", f"/api/mc/{cmd}", dict(client=C, **kw))
        if not r.get("ok", True):
            state["msg"] = f"{cmd}: {r.get('error')}"
        return r
    except Exception as e:
        state["msg"] = f"{cmd}: {e}"


def set_exp():
    req("POST", "/api/cam/set", {"cam": state["cam"], "exposure": state["exp"][state["cam"]]})


def grab():
    b = req("GET", "/api/cam/grab", {"cam": state["cam"], "scale": 1, "t": time.time()}, raw=True)
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)


cfg_k = {0: 34.5, 1: 112.0}
try:
    import tempfile
    from neocam.config import NeoConfig
    p = os.path.join(tempfile.gettempdir(), "live_vision_Config.ini")
    open(p, "wb").write(req("GET", "/api/file", {"path": "/opt/neoden/data/Config.ini"}, raw=True))
    cfg = NeoConfig.load(p)
    cfg_k = {0: cfg.pxmm(0), 1: cfg.pxmm(1)}
    state["exp"][0] = cfg.up_exposure()
except Exception as e:
    print("Config.ini not read:", e)

threading.Thread(target=heartbeat, daemon=True).start()
req("POST", "/api/cam/set", {"cam": 1, "w": 800, "h": 800})
set_exp()
cv2.namedWindow("NeoDen live vision", cv2.WINDOW_NORMAL)
cv2.resizeWindow("NeoDen live vision", 1400, 720)
t_last = time.time()
fps = 0.0
while True:
    try:
        img = grab()
    except Exception as e:
        state["msg"] = f"grab: {e}"
        img = np.zeros((800, 800), np.uint8)
    cam, k, mode = state["cam"], cfg_k[state["cam"]], state["mode"]
    h, w = img.shape
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    side = np.zeros_like(img)
    info = []
    state["last"] = None
    if mode == "m":
        res = V.find_mark_best(img, 0.5, 3.0, k)
        if res.threshold:
            _, side = cv2.threshold(img, res.threshold, 255, cv2.THRESH_BINARY)
        if res.precision > 0:
            cv2.circle(vis, (int(res.x), int(res.y)), int(res.r), (0, 255, 0), 2)
            cv2.drawMarker(vis, (int(res.x), int(res.y)), (0, 255, 0), cv2.MARKER_CROSS, 16, 1)
            dx, dy = (res.x - w / 2) / k, (h / 2 - res.y) / k
            info.append(f"FIDUCIAL d {2 * res.r / k:.2f} mm  round {res.precision:.2f}  thr {res.threshold}  off {dx:+.3f},{dy:+.3f} mm")
            state["last"] = (dx, dy)
        else:
            info.append("fiducial: not found")
    elif mode == "p":
        # pads: each one's own rotated rectangle (yellow) and a small cross at its centre; links
        # narrower than 0.12 mm (pins tied by copper) are cut so tied pads stay separate
        blobs, t = detect_pads(img, k, state["thr"], split_mm=0.12)
        _, side = cv2.threshold(cv2.GaussianBlur(img, (3, 3), 0), t, 255, cv2.THRESH_BINARY)
        # a fiducial in view (round, possibly inside a copper ring): drawn as its circle (magenta)
        fid = V.find_mark_best(img, 0.5, 3.0, k)
        if fid.precision >= 0.85:
            cv2.circle(vis, (int(fid.x), int(fid.y)), int(fid.r), (255, 0, 255), 2, cv2.LINE_AA)
            cv2.drawMarker(vis, (int(fid.x), int(fid.y)), (255, 0, 255), cv2.MARKER_CROSS, 14, 1)
            info.append(f"FIDUCIAL d {2 * fid.r / k:.2f} mm  round {fid.precision:.2f}")
        for bb in blobs:
            if bb.rect is None:
                continue
            if fid.precision >= 0.85 and cv2.pointPolygonTest(cv2.boxPoints(bb.rect), (float(fid.x), float(fid.y)), False) >= 0:
                continue                                     # the ring around the fiducial: not a pad
            (rw, rh) = bb.rect[1]
            fill = bb.area_mm2 * k * k / max(rw * rh, 1.0)   # 1.0 rectangle, ~0.785 circle
            if 0.70 <= fill <= 0.86 and 0.8 <= rw / max(rh, 1.0) <= 1.25:
                cv2.circle(vis, (int(bb.px), int(bb.py)), int((rw + rh) / 4), (0, 220, 255), 2, cv2.LINE_AA)   # round pad
            else:
                cv2.polylines(vis, [np.round(cv2.boxPoints(bb.rect)).astype(np.int32)], True, (0, 220, 255), 2, cv2.LINE_AA)
            cv2.drawMarker(vis, (int(bb.px), int(bb.py)), (0, 255, 0), cv2.MARKER_CROSS, 8, 1)
        if blobs:
            cx = sum(bb.px for bb in blobs) / len(blobs)
            cy = sum(bb.py for bb in blobs) / len(blobs)
            state["last"] = ((cx - w / 2) / k, (h / 2 - cy) / k)
        info.append(f"PADS {len(blobs)}  threshold {t:.0f}{' (auto)' if not state['thr'] else ''}  "
                    f"(best: inner lamp, mask median ~45: press a)")
    elif mode == "b":
        if state["bg"] is None or state["bg"].shape != img.shape:
            info.append("press g with the nozzle EMPTY to take the background")
        else:
            r = V.analyze_nozzle(img, state["bg"], 300, k)
            side = cv2.subtract(img, state["bg"])
            _, side = cv2.threshold(side, 50, 255, cv2.THRESH_BINARY)
            if r.valid and r.rect_px:
                cx, cy, rw, rh, ang = r.rect_px
                box = cv2.boxPoints(((cx, cy), (rw, rh), -ang))
                cv2.polylines(vis, [np.int32(box)], True, (0, 255, 0), 2)
                info.append(f"PART {r.w:.2f} x {r.h:.2f} mm  off {r.x:+.3f},{r.y:+.3f} mm  {r.angle:+.2f} deg")
            else:
                info.append("part: not found")
    cv2.drawMarker(vis, (w // 2, h // 2), (0, 0, 255), cv2.MARKER_CROSS, 40, 1)
    now = time.time()
    fps = 0.8 * fps + 0.2 / max(now - t_last, 1e-3)
    t_last = now
    head = [f"{'HEAD' if cam == 1 else 'UP'} camera  {w}x{h}  {k:g} px/mm  exp {state['exp'][cam]}  mean {img.mean():.0f}  "
            f"lamp {['off', 'inner', 'outer', 'both'][state['led']]}  light {'on' if state['light'] else 'off'}  "
            f"control {'yes' if state['ctl'] else 'NO'}  {fps:.1f} fps"] + info + ([state["msg"]] if state["msg"] else [])
    for i, t in enumerate(head):
        cv2.putText(vis, t, (8, 22 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(vis, t, (8, 22 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(vis, "keys: 1/2 cam  m mark  p pads  a auto-exp  b part  g bg  +/- exp  [/] thr  l lamp  u light  c centre  s save  q quit",
                (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)
    side_bgr = cv2.cvtColor(cv2.resize(side, (w, h)), cv2.COLOR_GRAY2BGR)
    cv2.imshow("NeoDen live vision", np.hstack([vis, np.full((h, 6, 3), 80, np.uint8), side_bgr]))
    key = cv2.waitKey(30) & 0xFF
    if key in (ord("q"), 27):
        break
    if key == ord("1") or key == ord("2"):
        state["cam"] = 1 if key == ord("1") else 0
        req("POST", "/api/cam/set", {"cam": state["cam"], "w": 800 if state["cam"] == 1 else 1024, "h": 800 if state["cam"] == 1 else 1024})
        set_exp()
    elif key in (ord("m"), ord("p"), ord("b")):
        state["mode"] = chr(key)
        if key == ord("p") and cam == 1:          # pads: inner lamp, board median ~45 (BOARD-A test)
            state["led"] = 1
            mc("led", v=1)
            key = ord("a")
    if key == ord("a") and cam == 1:
        def _set(v):
            state["exp"][1] = int(v)
            set_exp()
        V.auto_exposure_median(grab, _set, 45, start=state["exp"][1])
        state["msg"] = f"auto exposure {state['exp'][1]} (board median ~45)"
    elif key in (ord("+"), ord("=")):
        state["exp"][cam] = int(state["exp"][cam] * 1.3) + 1
        set_exp()
    elif key == ord("-"):
        state["exp"][cam] = max(1, int(state["exp"][cam] / 1.3))
        set_exp()
    elif key == ord("["):
        state["thr"] = max(0, state["thr"] - 8)
    elif key == ord("]"):
        state["thr"] = min(250, (state["thr"] or 120) + 8)
    elif key == ord("l"):
        state["led"] = (state["led"] + 1) % 4
        mc("led", v=state["led"])
    elif key == ord("u"):
        state["light"] = not state["light"]
        mc("light", on=1 if state["light"] else 0)
    elif key == ord("g"):
        state["bg"] = img.copy()
        state["msg"] = "background taken"
    elif key == ord("c") and state["last"] and cam == 1:
        dx, dy = state["last"]
        mc("rel", x=f"{dx:.4f}", y=f"{dy:.4f}", slow=1)
    elif key == ord("s"):
        fn = time.strftime("live_%H%M%S.png")
        cv2.imwrite(fn, vis)
        state["msg"] = f"saved {os.path.abspath(fn)}"
stop = True
try:
    mc("led", v=0)
    mc("light", on=0)
    req("POST", "/api/mc/unlock", {"client": C})
except Exception:
    pass
cv2.destroyAllWindows()
