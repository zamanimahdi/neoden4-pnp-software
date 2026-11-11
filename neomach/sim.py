"""NeoDen4 mainboard simulator: the byte protocol of PROTOCOL.md plus a small machine model.

Used by the tests, by open_link("sim://"), and, served over TCP, as the "serial
line" for serbridge.exe --serial-tcp and pyserial's socket:// URLs:

    python -m neomach.sim --port 5700

What the model does (enough to exercise every command; timings are short):
  XY: not homed (state 1) until the switch register's home bit; then moves take
      time (state 2) and end idle (state 0). Position kept in steps.
  head: four nozzles with angle and Z; each move clears that nozzle's idle bit for a while.
  valves + pressure: reg 0 b0..b3 follow a first-order lag (tau 20 ms) with noise toward
      0 (off), +25 (blow), the nozzle's open level (sim.open_level[n], about -15) with suction
      on, or the sealed level (sim.sealed_level[n], about -60) while a part is held.
      Z moves take time; a part is picked when suction is on and the descending nozzle passes
      sim.contact_z[n] while sim.pick_here[n] is True. sim.drop_on_lift[n] loses it again on
      the way up. Set sim.parts[n] = True to put a part on a nozzle directly.
  XY errors (off by default): the head's TRUE position (xy_true_mm) differs from the
      commanded one by err_scale (per axis), err_skew_deg (X/Y not square), err_periodic
      (amplitude mm, period mm: pulley runout) and backlash (mm per axis, depends on the
      direction of the last move). Calibration tests compare against xy_true_mm().
  lights: up-camera light (reg 7 b4 bit0) and head LED (reg 4 b0).
  feeders / peelers: commands counted; a feed is busy, then done; sim.fail_feeds[n] = k
  makes the first k feeds of feeder n report "failed".
"""
import math
import random
import socket
import struct
import threading
import time

from . import proto as P

BUSY_S = 0.03           # how long a nozzle / peeler / feeder action keeps "busy"
XY_MM_PER_S = 800.0     # simulated XY speed
Z_MM_PER_S = 200.0      # simulated nozzle Z speed
VAC_TAU_S = 0.02        # pressure time constant
VAC_NOISE = 1.5         # pressure noise (standard deviation, raw units)
BLOW_LEVEL = 25


class Reg:
    __slots__ = ("updated", "written", "data")

    def __init__(self):
        self.updated, self.written, self.data = False, False, bytearray(8)


class MainboardSim:
    def __init__(self):
        self.lock = threading.RLock()
        self.regs = [Reg() for _ in range(128)]
        self.state = ("cmd",)
        self.writes = []                    # (addr, bytes) of every accepted write
        self.bad_frames = 0
        # machine model
        self.xy_state = 1                   # 1 = not homed
        self.xy_steps = [0, 0]
        self.xy_done_at = 0.0
        self.home_s = 0.05                  # homing duration
        self.switch_lag_s = 0.0             # status shows the old XY state this long after a pulse
        self.home_fails = False             # homing ends in state 1 (switch not found)
        self.xy_lag = None                  # (until, state) shown instead of the real state
        self._homing_fails = False
        self.err_scale = [1.0, 1.0]         # true / commanded per axis
        self.err_skew_deg = 0.0             # Y axis tilt: true y += x * tan(skew)
        self.err_periodic = (0.0, 30.48)    # (amplitude mm, period mm) on both axes
        self.backlash = [0.0, 0.0]          # mm per axis
        self.last_dir = [1, 1]              # sign of the last move per axis
        self.angle = [0.0] * 5              # index 1..4
        self.z = [0.0] * 5
        self.nozzle_busy_until = [0.0] * 5
        self.valve = {}
        self.parts = [False] * 5            # part held on nozzle 1..4
        self.z_from = [0.0] * 5             # Z motion: from, start time, end time
        self.z_t0 = [0.0] * 5
        self.z_t1 = [0.0] * 5
        self.pick_here = [False] * 5        # a part waits under nozzle n
        self.contact_z = [2.0] * 5          # depth (mm) at which the tip touches it
        self.drop_on_lift = [False] * 5
        self.open_level = [0, -15, -15, -15, -15]
        self.sealed_level = [0, -60, -60, -60, -60]
        self.pressure = [0.0] * 5
        self.p_time = [time.monotonic()] * 5
        self.rng = random.Random(1)
        self.up_light = False
        self.head_led = 0
        self.beep = False
        self.board_ready = False
        self.config = bytearray(8)
        self.rail_steps = 0
        self.rail_done_at = 0.0
        self.input0 = False
        self.peels = []                     # (feeder, a, b)
        self.peel_busy_until = {P.R_PEEL_R: 0.0, P.R_PEEL_L: 0.0}
        self.feeds = []                     # (feeder, p0, p1, mode)
        self.feed_busy_until = {}
        self.feed_failed = {}
        self.fail_feeds = {}                # feeder -> number of feeds that fail
        self.vib = []

    # ---- byte protocol ------------------------------------------------------

    def feed(self, data):
        """Bytes in from the PC -> bytes out to the PC."""
        out = bytearray()
        with self.lock:
            for b in bytes(data):
                out += self._byte(b)
        return bytes(out)

    def _byte(self, b):
        st = self.state
        if st[0] == "data":
            st[2].append(b)
            if len(st[2]) == 9:
                self.state = ("cmd",)
                if P.data_ok(st[2]):
                    self._write(st[1], bytes(st[2][:8]))
                else:
                    self.bad_frames += 1
            return b""
        if st[0] == "long":
            self.state = ("cmd",)
            return self._command(st[1], b, b)
        if b & 0x3F == P.LONG:
            self.state = ("long", b >> 6)
            return bytes([P.answer_for(b, 0, prefix=True)])
        return self._command(b >> 6, b & 0x3F, b)

    def _flags(self, addr):
        r = self.regs[addr]
        return (P.F_UPDATED if r.updated else 0) | (P.F_WRITTEN if r.written else 0)

    def _command(self, typ, addr, sent):
        r = self.regs[addr]
        if typ == P.T_CLEAR:
            r.updated = r.written = False
            self._refresh(addr)             # the board refreshes readable data after a clear
            return bytes([P.answer_for(sent, 0)])
        if typ == P.T_STATUS:
            if addr >= P.LONG and not r.updated:
                # long "clear" is the same two bytes as a long status poll (CIoSerial::clearLong)
                self._refresh(addr)
            return bytes([P.answer_for(sent, self._flags(addr))])
        if typ == P.T_READ:
            ans = bytes([P.answer_for(sent, self._flags(addr))])
            return ans + P.data_frame(self._read(addr))
        # write: the 9 data bytes follow
        r.written = False
        self.state = ("data", addr, bytearray())
        return bytes([P.answer_for(sent, self._flags(addr))])

    def _refresh(self, addr):
        self.regs[addr].updated = True

    # ---- machine model: reads -------------------------------------------------

    def _tick(self):
        now = time.monotonic()
        if self.xy_state == 2 and now >= self.xy_done_at:
            self.xy_state = 1 if self._homing_fails else 0
            self._homing_fails = False
        return now

    def _read(self, addr):
        now = self._tick()
        if addr == P.R_HEAD_STATUS:
            vac, idle = [], 0
            for n in range(1, 5):
                vac.append(self._pressure(n, now))
                if now >= self.nozzle_busy_until[n]:
                    idle |= 1 << (n - 1)
            return struct.pack("<4bB3x", *vac, idle)
        if addr == P.R_STATUS:
            rail_done = 2 if now >= self.rail_done_at else 0
            # b3 bit0 start button; bit1 rail / board-out sensor, inverted (0 = rail idle)
            inputs = (1 if self.input0 else 0) | (0 if now >= self.rail_done_at else 2)
            xy = self.xy_lag[1] if self.xy_lag and now < self.xy_lag[0] else self.xy_state
            return bytes([xy, rail_done, 0, inputs, 0, 0, 0, 0])
        if addr in (P.R_PEEL_R_STATUS, P.R_PEEL_L_STATUS):
            peeler = P.R_PEEL_R if addr == P.R_PEEL_R_STATUS else P.R_PEEL_L
            return bytes([1 if now >= self.peel_busy_until[peeler] else 0]) + bytes(7)
        if 0x15 <= addr <= 0x46:
            n = addr - 0x14
            if now < self.feed_busy_until.get(n, 0):
                return bytes(8)
            done = 0 if self.feed_failed.get(n) else 0x20
            fail = 0x40 if self.feed_failed.get(n) else 0
            return bytes([done | fail]) + bytes(7)
        return bytes(self.regs[addr].data)

    # ---- machine model: writes ------------------------------------------------

    def _write(self, addr, d):
        now = self._tick()
        r = self.regs[addr]
        r.data[:] = d
        self.writes.append((addr, d))
        r.written = True
        if addr in (P.R_HEAD_ROTATE, P.R_HEAD_DEPTH):
            value, speed, n = struct.unpack("<hBB", d[:4])
            if 1 <= n <= 4:
                if addr == P.R_HEAD_ROTATE:
                    self.angle[n] = -value / 10
                    self.nozzle_busy_until[n] = now + BUSY_S
                else:
                    self._update_part(n, now)
                    self.z_from[n] = self.z_at(n, now)
                    self.z[n] = value / 1000
                    self.z_t0[n] = now
                    self.z_t1[n] = now + abs(self.z[n] - self.z_from[n]) / Z_MM_PER_S
                    self.nozzle_busy_until[n] = max(self.z_t1[n], now + 0.005)
        elif addr == P.R_HEAD_VALVE:
            for n in (range(1, 5) if d[1] == 0 else [d[1]]):
                self._update_part(n, now)
                self.valve[n] = d[0]
                if d[0] != 0xFF:
                    self.parts[n] = False         # suction off or blow: the part is released
        elif addr == P.R_HEAD_LED:
            self.head_led = d[0]
        elif addr == P.R_CONFIG:
            self.config[:] = d
        elif addr == P.R_SWITCH:
            if d[0] & 3 and self.switch_lag_s:
                self.xy_lag = (now + self.switch_lag_s, self.xy_state)
            if d[0] & 1:                                    # XY home
                self.xy_steps = [0, P.Y_MAX_STEPS]
                self.xy_state, self.xy_done_at = 2, now + self.home_s
                self._homing_fails = self.home_fails
            if d[0] & 2 and self.xy_state in (3, 4, 5):     # XY position correction
                self.xy_state, self.xy_done_at = 2, now + 0.05
            if d[1] & 2:                                    # rail stop
                self.rail_done_at = now
            self.up_light = bool(d[4] & 1)
            self.beep = bool(d[5] & 1)
            self.board_ready = bool(d[5] & 2)
        elif addr == P.R_XY:
            if self.xy_state in (0, 2):
                xr, yr = struct.unpack("<ii", d)
                sx, sy = xr + P.X_OFFSET, yr
                if 0 <= sx <= P.X_MAX_STEPS and 0 <= sy <= P.Y_MAX_STEPS:
                    dist = max(abs(sx - self.xy_steps[0]), abs(sy - self.xy_steps[1])) * P.XY_STEP_MM
                    for i, new in enumerate((sx, sy)):
                        if new != self.xy_steps[i]:
                            self.last_dir[i] = 1 if new > self.xy_steps[i] else -1
                    self.xy_steps = [sx, sy]
                    self.xy_state, self.xy_done_at = 2, now + 0.01 + dist / XY_MM_PER_S
        elif addr == P.R_RAIL:
            self.rail_steps += struct.unpack("<i", d[4:])[0]
            self.rail_done_at = now + BUSY_S
        elif addr in (P.R_PEEL_R, P.R_PEEL_L):
            feeder = d[0] if addr == P.R_PEEL_R else d[0] + 0x13
            self.peels.append((feeder, d[2], d[1]))
            self.peel_busy_until[addr] = now + BUSY_S
        elif addr == P.R_VIB:
            self.vib.append((d[0], d[1]))
        elif 0x47 <= addr <= 0x78:
            n = addr - 0x46
            self.feeds.append((n, d[0], d[1], d[7]))
            left = self.fail_feeds.get(n, 0)
            self.feed_failed[n] = left > 0
            if left:
                self.fail_feeds[n] = left - 1
            self.feed_busy_until[n] = now + BUSY_S

    # ---- nozzle Z and pressure model ------------------------------------------

    def z_at(self, n, now):
        """Actual depth of nozzle n now (linear move from z_from to z over z_t0..z_t1)."""
        if now >= self.z_t1[n] or self.z_t1[n] <= self.z_t0[n]:
            return self.z[n]
        f = (now - self.z_t0[n]) / (self.z_t1[n] - self.z_t0[n])
        return self.z_from[n] + (self.z[n] - self.z_from[n]) * f

    def _update_part(self, n, now):
        z = self.z_at(n, now)
        sucking = self.valve.get(n, 0) == 0xFF
        if sucking and not self.parts[n] and self.pick_here[n] and z >= self.contact_z[n]:
            self.parts[n] = True
            self.pick_here[n] = False
        if self.parts[n] and self.drop_on_lift[n] and z < self.contact_z[n] - 0.5:
            self.parts[n] = False

    def _pressure(self, n, now):
        self._update_part(n, now)
        v = self.valve.get(n, 0)
        if v == 0xFF:
            target = self.sealed_level[n] if self.parts[n] else self.open_level[n]
        elif v == 1:
            target = BLOW_LEVEL
        else:
            target = 0
        dt = max(0.0, now - self.p_time[n])
        self.p_time[n] = now
        self.pressure[n] += (target - self.pressure[n]) * (1 - math.exp(-dt / VAC_TAU_S))
        val = self.pressure[n] + self.rng.gauss(0, VAC_NOISE)
        return int(max(-128, min(127, round(val))))

    # ---- convenience ----------------------------------------------------------

    def xy_mm(self):
        """Commanded XY (mm)."""
        return self.xy_steps[0] * P.XY_STEP_MM, self.xy_steps[1] * P.XY_STEP_MM

    def xy_true_mm(self):
        """Where the head really is, with the error model (scale, skew, periodic, backlash)."""
        cx, cy = self.xy_mm()
        amp, per = self.err_periodic
        x = cx * self.err_scale[0] + amp * math.sin(2 * math.pi * cx / per)
        y = (cy * self.err_scale[1] + cx * math.tan(math.radians(self.err_skew_deg))
             + amp * math.sin(2 * math.pi * cy / per))
        x -= self.last_dir[0] * self.backlash[0] / 2     # the carriage lags behind its motion
        y -= self.last_dir[1] * self.backlash[1] / 2
        return x, y


class SimPort:
    """In-process port for SerialLink: bytes go straight into the simulator."""

    def __init__(self, sim):
        self.sim, self.name, self.buf = sim, "sim", bytearray()

    def write(self, b):
        self.buf += self.sim.feed(b)

    def read(self, n, timeout):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def close(self):
        pass


def serve_tcp(sim, port=5700, bind="127.0.0.1", ready=None, stop=None):
    """Serve the simulator's byte stream over TCP (one client at a time), like a serial line."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(1)
    srv.settimeout(0.2)
    if ready:
        ready.set()
    while not (stop and stop.is_set()):
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        c.settimeout(0.2)
        with c:
            while not (stop and stop.is_set()):
                try:
                    d = c.recv(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not d:
                    break
                out = sim.feed(d)
                if out:
                    c.sendall(out)
    srv.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="NeoDen4 mainboard simulator (byte stream over TCP)")
    ap.add_argument("--port", type=int, default=5700)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args()
    print(f"mainboard simulator on {a.bind}:{a.port}")
    serve_tcp(MainboardSim(), a.port, a.bind)


if __name__ == "__main__":
    main()
