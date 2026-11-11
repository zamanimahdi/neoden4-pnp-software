"""High-level NeoDen4 mainboard control, modelled on NeoDen4.exe's CFunNode* classes.

    from neomach import Mainboard
    mb = Mainboard.connect("tcp://192.168.1.20:5602")    # or "serial://COM2", "sim://"
    mb.home_xy()
    mb.move_xy(120.0, 80.0, slow=True)
    mb.rotate(1, 90); mb.set_z(1, 5.2); mb.valve(1, 1); mb.set_z(1, 0)
    mb.up_light(True)

Safety rules taken from NeoDen4.exe:
  * before any XY or rail move every nozzle is raised to Z <= 1 mm (moveXY -> allNozzlesUp / homeZ);
  * a nozzle is not lowered below 1 mm while XY is busy (depthSet);
  * XY targets are checked against the travel (0..416.24 x 0..400.05 mm);
  * XY must be homed first (status state 1 = not initialised).
The board does not report the XY position; like NeoDen4.exe we keep the last target.

Registers 6 (config) and 7 (switch) are shared by several functions, so, as in
NeoDen4.exe, a shadow copy of each is kept and written whole. Use one Mainboard
object per session.

Long waits (XY, nozzles, rail, feeders) call `progress(what, elapsed_s, timeout_s)`
if set, and stop with Cancelled when cancel() is called from another thread.
"""
import threading
import time

from . import proto as P
from .link import open_link
from .vacuum import VacuumParams, VacuumWatch, analyze_pick

Z_SAFE_MM = 1.0             # CFunNodeHead::allNozzlesUp
SPEED_DEFAULT = 0x96        # rotate / depth speed byte used by NeoDen4.exe for moves and homing
SLOW_SPEED = 10             # CFunNodeMainBoard::moveToSlow final approach speed
FORCE_DEFAULT = 9           # setXySpeed(..., 9) everywhere in NeoDen4.exe

XY_IDLE, XY_NOT_HOMED, XY_BUSY, XY_LOSS_X, XY_LOSS_Y, XY_LOSS_XY = 0, 1, 2, 3, 4, 5
XY_STATES = {XY_IDLE: "idle", XY_NOT_HOMED: "not homed", XY_BUSY: "busy",
             XY_LOSS_X: "step loss X", XY_LOSS_Y: "step loss Y", XY_LOSS_XY: "step loss X and Y"}
XY_LOSS = (XY_LOSS_X, XY_LOSS_Y, XY_LOSS_XY)

# speeds used by NeoDen4.exe (SETTINGS_MANUAL_SPEC.md)
SPEED_MANUAL_ROTATE = 50    # Turn left / right buttons
SPEED_MANUAL_Z = 100        # Nozzle Down button, keyboard jog
SPEED_HOME = 150            # homing, pickPart
MANUAL_Z_MM = 12.0          # "Nozzle Down" depth


def xy_state_name(st):
    return XY_STATES.get(st, f"state {st}")


class MachineError(Exception):
    """Machine refused or failed an action (base class)."""


class NotHomed(MachineError):
    pass


class StepLoss(MachineError):
    pass


class SafetyRefused(MachineError):
    pass


class MachineTimeout(MachineError):
    pass


class Cancelled(MachineError):
    pass


class PickError(MachineError):
    """A pick or hold check failed; .result is the PickResult (reason, levels, samples)."""

    def __init__(self, msg, result=None):
        super().__init__(msg)
        self.result = result


class Mainboard:
    def __init__(self, link):
        self.link = link
        self.lock = threading.RLock()
        self.cfg = bytearray(8)             # reg 6 shadow; constructor defaults b0 = 80, b1 = 9
        self.cfg[0], self.cfg[1] = 80, FORCE_DEFAULT
        self.sw = bytearray(8)              # reg 7 shadow
        self._start_prev = False
        self._start_key = False             # latched rising edge of the start button
        self.pos = None                     # last XY target in mm (None until homed)
        self.z = [0.0] * 5                  # last commanded Z per nozzle 1..4
        self.angle = [0.0] * 5
        self.progress = None                # callback(what, elapsed_s, timeout_s)
        self.sound = True                   # False: beep_pulse() stays silent (beep() still works)
        self._beep_timer = None
        self.xy_map = None                  # optional XY correction: f(x, y) -> commanded (x, y)
                                            # (neocalib.ErrorMap.correct); pos keeps the target
        self._cancel = threading.Event()
        self.step_loss_retries = 3          # automatic XY corrections per move, like NeoDen4.exe
        self.step_losses = []               # (time, state name, target mm) of every step loss seen
        self.on_step_loss = None            # callback(state, target, retry) before each correction
        self._loss_count = 0
        self._target = None                 # last target sent (mm), re-sent after a correction

    @classmethod
    def connect(cls, url, **kw):
        return cls(open_link(url, **kw))

    def close(self):
        self.link.close()

    def info(self):
        return self.link.info()

    # ---- raw access -----------------------------------------------------------

    def read(self, addr):
        with self.lock:
            return self.link.read_reg(addr)

    def write(self, addr, data8):
        with self.lock:
            self.link.write_reg(addr, data8)

    # ---- waiting, progress, cancel ------------------------------------------------

    def cancel(self):
        """Stop the current wait (from another thread); it raises Cancelled."""
        self._cancel.set()

    def _wait(self, what, timeout, done):
        """Poll done() until true; progress callback, cancel and timeout handled here."""
        t0 = time.monotonic()
        while True:
            if done():
                return
            el = time.monotonic() - t0
            if self._cancel.is_set():
                self._cancel.clear()
                raise Cancelled(f"{what}: cancelled")
            if el > timeout:
                raise MachineTimeout(f"{what}: not finished after {timeout:g} s")
            if self.progress:
                self.progress(what, el, timeout)
            time.sleep(0.01)

    # ---- status -----------------------------------------------------------------

    def status(self):
        """Controller status (reg 5): xy_state (+ name), rail_done, input0 = start button,
        input1 = rail idle (sensor, inverted), flag3. Latches start-button presses."""
        s = P.d_status(self.read(P.R_STATUS))
        s["xy_state_name"] = xy_state_name(s["xy_state"])
        if s["input0"] and not self._start_prev:
            self._start_key = True
        self._start_prev = s["input0"]
        return s

    def xy_state(self):
        return self.status()["xy_state"]

    def head_status(self):
        """Head status (reg 0): (idle[4], vacuum[4]) for nozzles 1..4."""
        return P.d_head_status(self.read(P.R_HEAD_STATUS))

    def position(self):
        """Last commanded XY in mm (the board does not report it); None before homing."""
        return self.pos

    # ---- XY -----------------------------------------------------------------------

    def _check_xy(self, st):
        if st == XY_NOT_HOMED:
            raise NotHomed("XY is not homed: home first")
        if st in XY_LOSS:
            raise StepLoss(f"XY {xy_state_name(st)}: run XY correction, then move again")

    def wait_xy(self, timeout=30.0):
        """Wait until XY is idle (state 0). A step loss is corrected and the target re-sent up
        to step_loss_retries times per move (waitXyIdle in NeoDen4.exe); then StepLoss.
        Raises NotHomed / StepLoss / MachineTimeout / Cancelled."""
        def done():
            st = self.xy_state()
            if st in XY_LOSS and self._recover(st):
                self.write(P.R_XY, P.p_xy(*self._commanded(*self._target)))
                return False
            self._check_xy(st)
            return st == XY_IDLE
        self._wait("XY move", timeout, done)

    def _recover(self, st):
        """Step loss: run the XY correction if retries are left. Returns True if it did."""
        if self._target is None or self._loss_count >= self.step_loss_retries:
            return False
        self._loss_count += 1
        self.step_losses.append((time.time(), xy_state_name(st), self._target))
        if self.on_step_loss:
            self.on_step_loss(st, self._target, self._loss_count)
        self.xy_correct()
        return True

    def _wait_switch(self, what, timeout, start_s=1.0):
        """After a home / correction pulse: wait for the controller to finish. The state may
        still show the old value for a moment after the pulse, so idle counts as done only once
        the run was seen (busy, or a change from a non-idle state) or after start_s. Ending
        in "not homed" or a step loss after the run started is a failure, not a timeout."""
        t0 = time.monotonic()
        st0 = last = self.xy_state()
        started = st0 == XY_BUSY
        trace = [(0.0, st0)]                                   # state changes, for the error message

        def done():
            nonlocal started, last
            st = last = self.xy_state()
            if st != trace[-1][1]:
                trace.append((time.monotonic() - t0, st))
            if st == XY_BUSY:
                started = True
            elif st == XY_IDLE:
                return started or st0 != XY_IDLE or time.monotonic() - t0 > start_s
            elif started and st == XY_NOT_HOMED:
                raise NotHomed(f"{what} failed: XY is not homed again - check the home switches and motors "
                               f"(states: {self._trace(trace)})")
            elif started and st in XY_LOSS:
                raise StepLoss(f"{what} failed: {xy_state_name(st)} (states: {self._trace(trace)})")
            return False
        try:
            self._wait(what, timeout, done)
        except MachineTimeout as ex:
            raise MachineTimeout(f"{ex} (XY state: {xy_state_name(last)}; states: {self._trace(trace)})") from None
        self.last_switch_trace = self._trace(trace)

    @staticmethod
    def _trace(trace):
        return ", ".join(f"{xy_state_name(st)} at {t:.1f} s" for t, st in trace)

    def _switch_pulse(self, byte, bit):
        """Set a momentary bit in reg 7, write, clear it again in the shadow (like xyInit)."""
        with self.lock:
            self.sw[byte] |= bit
            try:
                self.write(P.R_SWITCH, self.sw)
            finally:
                self.sw[byte] &= ~bit & 0xFF

    def home_xy(self, timeout=60.0):
        """Home XY (reg 7 b0 bit0). Afterwards the head is at X 0, Y 400.05 mm."""
        self.pos = None
        self.raise_all()
        with self.lock:                                         # speed / force first: after a power-up the
            self.write(P.R_CONFIG, self.cfg)                    # controller's reg 6 may hold 0 (a home at speed 0
        self._switch_pulse(0, 0x01)                             # runs into its 50 s timeout)
        self._wait_switch("XY homing", timeout)
        self.pos = (0.0, P.Y_MAX_MM)

    def xy_correct(self, timeout=30.0):
        """XY position correction after step loss (reg 7 b0 bit1); raises the nozzles first.
        Move to the target again afterwards (NeoDen4.exe re-sends it)."""
        self.raise_all()
        self._switch_pulse(0, 0x02)
        self._wait_switch("XY correction", timeout)

    def _commanded(self, x_mm, y_mm):
        return self.xy_map(x_mm, y_mm) if self.xy_map else (x_mm, y_mm)

    def _move(self, x_mm, y_mm, wait, timeout):
        data = P.p_xy(*self._commanded(x_mm, y_mm))     # range check before anything moves
        with self.lock:
            st = self.xy_state()
            if st in XY_LOSS and self._recover(st):
                st = self.xy_state()
            self._check_xy(st)
            if not self.nozzles_up():
                self.raise_all()
            self.write(P.R_XY, data)
            self.pos = self._target = (x_mm, y_mm)
        if wait:
            self.wait_xy(timeout)

    def move_xy(self, x_mm, y_mm, wait=True, timeout=30.0, slow=False, speed=None):
        """Absolute XY move in mm (reg 8). Raises all nozzles first.

        slow=True: like CFunNodeMainBoard::moveToSlow - go to (x-1, y-1), then the
        last millimetre at speed 10, then the previous speed back. (NeoDen4.exe may
        send both moves without waiting; here the approach is always waited for.)
        speed: XY speed byte for this move only (reg 6 b0), restored afterwards.
        Like NeoDen4.exe, the speed is restored right after the final move is sent.
        """
        P.xy_steps(x_mm, y_mm)
        self._loss_count = 0
        old = self.cfg[0]
        try:
            if speed is not None:
                self.set_xy_speed(speed)
            if slow:
                ax, ay = max(x_mm - 1.0, 0.0), max(y_mm - 1.0, 0.0)
                self._move(ax, ay, True, timeout)
                self.set_xy_speed(SLOW_SPEED)
            self._move(x_mm, y_mm, False, timeout)
        finally:
            if self.cfg[0] != old:
                self.set_xy_speed(old)
        if wait:
            self.wait_xy(timeout)

    def move_rel(self, dx_mm, dy_mm, wait=True, timeout=30.0, slow=False):
        if self.pos is None:
            raise NotHomed("position unknown: home first")
        self.move_xy(self.pos[0] + dx_mm, self.pos[1] + dy_mm, wait, timeout, slow)

    def set_xy_speed(self, speed, force=FORCE_DEFAULT):
        """XY speed and force/acceleration (reg 6 b0, b1). NeoDen4.exe uses force 9."""
        with self.lock:
            self.cfg[0], self.cfg[1] = speed & 0xFF, force & 0xFF
            self.write(P.R_CONFIG, self.cfg)

    xy_speed = set_xy_speed

    # ---- nozzles ------------------------------------------------------------------

    def nozzles_up(self):
        return all(self.z[n] <= Z_SAFE_MM for n in range(1, 5))

    def wait_idle(self, nozzle, timeout=5.0):
        """Wait for a nozzle's idle bit (reg 0 b4); nozzle 0 = all four."""
        def done():
            idle, _ = self.head_status()
            return all(idle) if nozzle == 0 else idle[nozzle - 1]
        self._wait(f"nozzle {nozzle or 'all'}", timeout, done)

    wait_nozzle = wait_idle

    def rotate(self, nozzle, angle_deg, speed=SPEED_DEFAULT, wait=True, timeout=5.0):
        """Nozzle angle in degrees (reg 1, 0.1 degree units)."""
        self._check_nozzle(nozzle)
        self.write(P.R_HEAD_ROTATE, P.p_rotate(nozzle, angle_deg, speed))
        self.angle[nozzle] = angle_deg
        if wait:
            self.wait_idle(nozzle, timeout)

    def set_z(self, nozzle, z_mm, speed=SPEED_DEFAULT, wait=True, timeout=5.0):
        """Nozzle depth in mm, 0 = up .. 13 (reg 2, micrometres; 12 is the manual "Nozzle Down",
        13 the head-camera correction stamp in frmCamDownCorrect). Not lowered while XY
        is busy. Speeds in NeoDen4.exe: 50 slider, 100 buttons, 150 homing, job speed*150/130."""
        self._check_nozzle(nozzle)
        if not 0 <= z_mm <= P.Z_MAX_MM:
            raise ValueError(f"Z must be 0..{P.Z_MAX_MM:g} mm")
        with self.lock:
            if z_mm > Z_SAFE_MM:
                st = self.xy_state()
                if st != XY_IDLE:
                    raise SafetyRefused(f"nozzle {nozzle} not lowered: XY is {xy_state_name(st)}")
            self.write(P.R_HEAD_DEPTH, P.p_depth(nozzle, z_mm, speed))
            self.z[nozzle] = z_mm
        if wait:
            self.wait_idle(nozzle, timeout)

    depth = set_z

    def raise_all(self):
        """Z = 0 on nozzles 1..4 in turn (CFunNodeHead::homeZ)."""
        for n in range(1, 5):
            self.write(P.R_HEAD_DEPTH, P.p_depth(n, 0.0, SPEED_DEFAULT))
            self.z[n] = 0.0
            self.wait_idle(n)

    def valve(self, channel, state):
        """Valve of nozzle `channel` 1..4 (reg 3: b0 state, b1 channel):
        -1 vacuum (suck), +1 blow, 0 off."""
        self.write(P.R_HEAD_VALVE, P.p_valve(channel, state))
        self.__dict__.setdefault("valve_state", {})[int(channel)] = int(state)

    def held(self):
        """the nozzles whose suction is on: they hold a part (or tried to)"""
        return sorted(n for n, v in getattr(self, "valve_state", {}).items() if v == P.VALVE_VACUUM)

    def drop_held(self, waste, log=None):
        """parts still on the nozzles (a STOP, a stopped job, an aborted camera tool) go to the waste box,
        not onto whatever is under the head: nozzles up, the head over the waste box, blow those nozzles.
        waste = (x, y) of the box (Position/posWasteBox); none set: they are left on, sucked, for the
        operator. Returns the nozzles dropped"""
        n = self.held()
        if not n:
            return []
        if not waste or not (waste[0] or waste[1]):
            if log:
                log(f"nozzle(s) {n} still hold a part and no waste box is set: left on the nozzle(s)")
            return []
        try:
            self.raise_all()
            self.move_xy(float(waste[0]), float(waste[1]), wait=True, timeout=30)
        except Exception as e:                      # not homed, a step loss: not dropped anywhere else
            if log:
                log(f"nozzle(s) {n} still hold a part, the waste box not reached ({type(e).__name__}: {e}): left on")
            return []
        for k in n:
            self.valve(k, P.VALVE_BLOW)
        time.sleep(0.1)
        for k in n:
            self.valve(k, P.VALVE_OFF)
        if log:
            log(f"part(s) on nozzle(s) {n} dropped in the waste box")
        return n

    def suck(self, nozzle, on=True):
        self.valve(nozzle, P.VALVE_VACUUM if on else P.VALVE_OFF)

    def blow(self, nozzle, on=True):
        self.valve(nozzle, P.VALVE_BLOW if on else P.VALVE_OFF)

    def vacuum(self, nozzle):
        self._check_nozzle(nozzle)
        return self.head_status()[1][nozzle - 1]

    # ---- vacuum: pick with edge / integral detection (neomach.vacuum) -------------------

    def pressure_sample(self, nozzle):
        """(t, pressure, idle) of one nozzle from one reg 0 read."""
        idle, vac = self.head_status()
        return time.monotonic(), vac[nozzle - 1], idle[nozzle - 1]

    def pick(self, nozzle, z_mm, speed=SPEED_DEFAULT, delay_ms=0, params=None, learner=None,
             raise_on_fail=True, timeout=5.0):
        """Pick a part with nozzle `nozzle` at depth `z_mm` (NeoDen: 12 - pick height).

        Sequence: suction on with the nozzle up (open-air baseline), down to z_mm (the
        falling pressure edge = the seal), wait delay_ms, back up, sample settle_ms. The
        decision is the seal integral after lifting (neomach.vacuum.analyze_pick).
        Returns the PickResult; raises PickError (with .result) when not held, unless
        raise_on_fail=False. Good picks update `learner` (VacuumLearner) if given.
        """
        p = params or VacuumParams()
        self._check_nozzle(nozzle)
        samples = []

        def run(phase, until):
            t_end = time.monotonic() + timeout
            while True:
                t, v, idle = self.pressure_sample(nozzle)
                samples.append((t, v, phase))
                if until(t, idle):
                    return
                if time.monotonic() > t_end:
                    raise MachineTimeout(f"pick nozzle {nozzle}: {phase} phase not finished")
                if self._cancel.is_set():
                    self._cancel.clear()
                    self.suck(nozzle, False)
                    raise Cancelled(f"pick nozzle {nozzle}: cancelled")

        self.suck(nozzle)
        t0 = time.monotonic()
        run("open", lambda t, idle: t - t0 >= p.open_ms / 1000)
        self.set_z(nozzle, z_mm, speed, wait=False)
        t_bottom = [None]

        def down_done(t, idle):
            if idle and t_bottom[0] is None:
                t_bottom[0] = t
            return t_bottom[0] is not None and t - t_bottom[0] >= delay_ms / 1000
        run("down", down_done)
        self.set_z(nozzle, 0.0, speed, wait=False)
        t_up = [None]

        def up_done(t, idle):
            if idle and t_up[0] is None:
                t_up[0] = t
            return t_up[0] is not None and t - t_up[0] >= p.settle_ms / 1000
        run("up", up_done)
        r = analyze_pick(samples, p, nozzle, learner)
        if learner is not None:
            learner.update(r)
        if not r.held and raise_on_fail:
            raise PickError(f"nozzle {nozzle}: {r.reason}", r)
        return r

    def watch_held(self, nozzle, result, duration_ms, params=None, raise_on_fail=True):
        """Integrate the leak of a held part for duration_ms (e.g. while it waits or after a
        move). Returns the VacuumWatch; raises PickError if the part slipped or fell."""
        w = VacuumWatch(result, params)
        t_end = time.monotonic() + duration_ms / 1000
        while True:
            t, v, _ = self.pressure_sample(nozzle)
            w.feed(t, v)
            if not w.ok:
                if raise_on_fail:
                    raise PickError(f"nozzle {nozzle}: part lost or slipping (leak integral "
                                    f"{w.leak * 1000:.1f} ms > {w.p.leak_limit_s * 1000:.1f} ms)", result)
                return w
            if t >= t_end:
                return w

    def part_present(self, nozzle, threshold=-40, reads=10):
        """Average of `reads` vacuum readings <= threshold (CFunNodeHead::vacuumBelow)."""
        total = sum(self.vacuum(nozzle) for _ in range(reads))
        return int(total / reads) <= threshold

    @staticmethod
    def _check_nozzle(n):
        if n not in (1, 2, 3, 4):
            raise ValueError("nozzle must be 1..4")

    # ---- lights, outputs, inputs -------------------------------------------------------

    def head_led(self, value):
        """Head (down camera) lamp (reg 4 b0): 0 off, 1 inner ring, 2 outer ring, 3 both
        (3 unverified). NeoDen4.exe's lamp button cycles 0, 1, 2, 3."""
        self.write(P.R_HEAD_LED, P.p_led(value))
        self.led = int(value)

    def _switch_bit(self, byte, bit, on):
        with self.lock:
            if on:
                self.sw[byte] |= bit
            else:
                self.sw[byte] &= ~bit & 0xFF
            self.write(P.R_SWITCH, self.sw)

    def up_light(self, on):
        """Up camera's light (reg 7 b4 bit0, CFunNodeMainBoard::setOutput)."""
        self._switch_bit(4, 0x01, on)

    output = up_light

    def beep(self, on):
        self._switch_bit(5, 0x01, on)

    def beep_pulse(self, count=1, on_s=0.3, off_s=0.2, wait=False):
        """Beep `count` times (on_s on, off_s off) unless self.sound is False. Runs on a timer
        thread so the caller is not held up (wait=True blocks until it is done). NeoDen4.exe
        beeps at the end of a job (runJob), when a finished board leaves (unloadPcb), when a
        fiducial needs the user (locateMarks) and when vision rejects a part; whether each of
        these beeps is a job-runner setting."""
        if not self.sound or count <= 0:
            return
        if self._beep_timer is not None:
            self._beep_timer.cancel()
        steps = []
        for i in range(count):
            steps += [(True, on_s), (False, off_s if i < count - 1 else 0.0)]

        def run(k=0):
            if k >= len(steps):
                return
            on, dur = steps[k]
            try:
                self.beep(on)
            except Exception:
                return
            if k + 1 < len(steps):
                self._beep_timer = threading.Timer(dur, run, (k + 1,))
                self._beep_timer.daemon = True
                self._beep_timer.start()
        if wait:
            for on, dur in steps:
                self.beep(on)
                time.sleep(dur)
        else:
            run()

    def board_in_ready(self, on):
        """'Board-in ready' output (reg 7 b5 bit1)."""
        self._switch_bit(5, 0x02, on)

    board_ready = board_in_ready

    def inputs(self):
        """(start button pressed, rail idle) from reg 5 b3."""
        s = self.status()
        return s["input0"], s["input1"]

    def start_pressed(self):
        return self.inputs()[0]

    def take_start_key(self):
        """True once per start-button press seen by status() (CFunNodeMainBoard::takeStartKey)."""
        self.status()
        k, self._start_key = self._start_key, False
        return k

    # ---- rail ---------------------------------------------------------------------------

    def set_rail_speed(self, speed):
        """Rail speed (reg 6 b3), 20..200 (= 2 x percent); NeoDen4.exe sets 200 at start-up."""
        with self.lock:
            self.cfg[3] = speed & 0xFF
            self.write(P.R_CONFIG, self.cfg)

    rail_speed = set_rail_speed

    def rail_done(self):
        return self.status()["rail_done"]

    def rail_move(self, mm, wait=True, timeout=30.0):
        """Move the board rail by `mm` (reg 9, 0.0127 mm steps; + = loading direction).
        Raises all nozzles first (NeoDen4.exe refuses instead). The manual buttons run
        +-10000 mm without waiting and call rail_stop() on release."""
        if not self.nozzles_up():
            self.raise_all()
        self.write(P.R_RAIL, P.p_rail(mm))
        if wait:
            self._wait("rail move", timeout, self.rail_done)

    def rail_stop(self):
        self._switch_pulse(1, 0x02)

    # ---- feeders, peelers, vibration --------------------------------------------------

    def feed(self, n, strength, length=0, mode=P.FEED_RUN):
        """Feeder n (1..50) command (reg 0x46+n): strength 10..100 (b0), length mm 2..32 (b1)."""
        if not 1 <= n <= P.FEEDERS:
            raise ValueError("feeder must be 1..50")
        self.write(P.r_feed_cmd(n), P.p_feed(strength, length, mode))

    def feeder_set_id(self, n, new_id):
        """Give feeder n a new ID 1..50 (mode 1, frmFeedIdCfg)."""
        self.feed(n, new_id, 0, P.FEED_SET_ID)

    def feeder_calibrate(self, n):
        """Feeder calibration (mode 2, the config page's "Calibration" button)."""
        self.feed(n, 0, 0, P.FEED_CALIBRATE)

    def feeder_status(self, n):
        """(done, failed, remaining tape mm) of feeder n (reg 0x14+n)."""
        return P.d_feed_status(self.read(P.r_feed_status(n)))

    def wait_feed(self, n, timeout=3.0, retries=6):
        """Wait for feeder n; on 'failed' resend b0 = 100, b1 = remaining, up to `retries`
        times (CFunNodeFeed::waitFeedDone). Returns True when done."""
        state = {"retries": retries}

        def done():
            ok, failed, left = self.feeder_status(n)
            if ok:
                return True
            if failed:
                if state["retries"] <= 0:
                    raise MachineError(f"feeder {n}: feed failed, {left} left")
                state["retries"] -= 1
                self.feed(n, 100, left, 0)
            return False
        self._wait(f"feeder {n}", timeout * (retries + 1), done)
        return True

    def peel(self, n, strength, length):
        """Peeler n (1..19 right, 20..48 left): strength 10..100, length 4..64."""
        reg, data = P.p_peel(n, strength, length)
        self.write(reg, data)

    def peeler_ready(self, n):
        """Peeler of feeder n ready (reg 11 right / 13 left, b0 bit0).
        UNVERIFIED: NeoDen4.exe has this read but never calls it."""
        reg = P.R_PEEL_R_STATUS if n <= 19 else P.R_PEEL_L_STATUS
        return bool(self.read(reg)[0] & 1)

    def vibration(self, a, b):
        """Vibration table command (reg 16, CFunNodeFeedS::command).
        UNVERIFIED: NeoDen4.exe never calls it; the vibration feeder is vibration_feeder()."""
        self.write(P.R_VIB, P.p_vib(a, b))

    vib_table = vibration

    def vibration_feeder(self, on, strength=0, freq=0):
        """Vibration feeder (reg 6 b5 = strength nVibCurrent 0..50 when on, 0 = off;
        b6 = frequency nVibFreq 100..250; CFunNodeMainBoard::vibrationFeeder)."""
        with self.lock:
            self.cfg[5] = strength & 0xFF if on else 0
            self.cfg[6] = freq & 0xFF
            self.write(P.R_CONFIG, self.cfg)

    # ---- start-up (MainWindow::initHardware) --------------------------------------------

    def home_all(self, run_speed=80, timeout=60.0):
        """CMachine::homeAll: blow pulse, nozzles up and at 0 deg, move to (0, 400) at speed 30. (The
        callers first send held parts to the waste box: drop_held.)"""
        for n in range(1, 5):
            self.blow(n)
        time.sleep(0.1)
        for n in range(1, 5):
            self.valve(n, P.VALVE_OFF)
        for n in range(1, 5):
            self.rotate(n, 0.0, SPEED_HOME, wait=False)
        self.raise_all()
        self.set_xy_speed(30, 6)
        self.set_rail_speed(100)
        self.move_xy(0.0, P.Y_MAX_MM, wait=True, timeout=timeout)
        self.set_xy_speed(run_speed, 8)

    def startup(self, run_speed=80, timeout=60.0, rehome=None):
        """Start-up after opening the port: always a full XY home, then homeAll (blow pulse,
        nozzles up and at 0 deg, park at (0, 400)), XY speed nRunSpeed and rail speed 200.
        Returns the XY state seen first.

        NeoDen4.exe skips the home when the controller says it is still referenced; here the
        home always runs and cannot be switched off (owner's decision): the stored position
        is never trusted after a program start. `rehome` is accepted and ignored (old callers)."""
        st = self.xy_state()
        self.home_xy(timeout)
        self.home_all(run_speed, timeout)
        self.set_xy_speed(run_speed, FORCE_DEFAULT)
        self.set_rail_speed(200)
        return st
