"""Nozzle Z tilt: how far a nozzle's tip moves sideways as it goes down.

The axis is calibrated with the nozzle up (Z = 0), but parts are picked and placed about 10 mm down.
A tilted Z axis moves the tip by the same amount whatever the nozzle's turn ("fixed" part); a bent
nozzle moves it in a direction that turns with the nozzle ("turn" part). Measured over the up camera
(neocalib.autocal): the tip at a few depths, at 0 / 90 / 180 / -90 deg.

    fit = fit_ztilt([(theta_deg, z_mm, ex_mm, ey_mm), ...])      # tip offsets in machine mm
    fit -> {"fix": [fx, fy], "turn": [tx, ty], "sign": +1 | -1, "rms": ..., "slopes": {theta: [sx, sy]}}
    dx, dy = shift(fix, turn, sign, theta_deg, z_mm)             # where the tip is at depth z, mm

The runner moves the head by -shift() so the tip lands where it should at the pick / place depth.
Slopes are mm sideways per mm down; Config.ini Calib/ptZTiltFix<n>, Calib/ptZTiltTurn<n> (.x/.y),
Calib/fZTiltSign.
"""
import math

import numpy as np


def _rot(v, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def fit_ztilt(meas):
    by = {}
    for th, z, ex, ey in meas:
        by.setdefault(round(float(th), 3), []).append((z, ex, ey))
    slopes = {}
    for th, pts in by.items():
        if len(pts) < 2:
            continue
        P = np.asarray(pts, float)
        A = np.c_[P[:, 0], np.ones(len(P))]
        sx = np.linalg.lstsq(A, P[:, 1], rcond=None)[0][0]
        sy = np.linalg.lstsq(A, P[:, 2], rcond=None)[0][0]
        slopes[th] = np.array([sx, sy])
    if not slopes:
        raise ValueError("no angle with two or more depths")
    fix = np.mean(list(slopes.values()), axis=0)
    best = None
    for sign in (1, -1):                       # which way the nozzle's turn rotates a bend, as the machine turns
        turn = np.mean([_rot(s - fix, -sign * th) for th, s in slopes.items()], axis=0) if len(slopes) > 1 else np.zeros(2)
        rms = math.sqrt(np.mean([np.sum((fix + _rot(turn, sign * th) - s) ** 2) for th, s in slopes.items()]))
        if best is None or rms < best[0]:
            best = (rms, sign, turn)
    rms, sign, turn = best
    return {"fix": [float(fix[0]), float(fix[1])], "turn": [float(turn[0]), float(turn[1])], "sign": sign, "rms": rms,
            "slopes": {th: [float(s[0]), float(s[1])] for th, s in slopes.items()}}


def shift(fix, turn, sign, theta_deg, z_mm):
    """the tip's sideways offset (mm) at depth z_mm with the nozzle turned theta_deg"""
    v = np.asarray(fix, float) + _rot(np.asarray(turn, float), sign * theta_deg)
    return float(v[0] * z_mm), float(v[1] * z_mm)


def from_config(cfg, n):
    """(fix, turn, sign) of nozzle n from Config.ini, or None when not calibrated"""
    raw = getattr(cfg, "raw", {})
    keys = [f"Calib/ptZTiltFix{n}.x", f"Calib/ptZTiltFix{n}.y", f"Calib/ptZTiltTurn{n}.x", f"Calib/ptZTiltTurn{n}.y"]
    if not any(k in raw for k in keys):
        return None
    v = [float(raw.get(k, 0.0) or 0.0) for k in keys]
    return (v[0], v[1]), (v[2], v[3]), (1 if float(raw.get("Calib/fZTiltSign", 1) or 1) >= 0 else -1)
