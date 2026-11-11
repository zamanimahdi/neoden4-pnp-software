"""Tests for neomach (mainboard control) and serbridge.exe, without the machine.

    python tests/test_neomach.py

1. protocol units (CRC, answers, frames, payload layouts against PROTOCOL.md)
2. a machine scenario over three links, all talking to neomach.sim:
     sim://                    in-process
     serial://socket://...     pyserial over the simulator's TCP byte stream
     tcp://... serbridge.exe   the real bridge, its serial side = the simulator (--serial-tcp)
"""
import os
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from neomach import (Mainboard, MachineError, LinkError, NotHomed, StepLoss, SafetyRefused,  # noqa: E402
                     Cancelled, xy_state_name)
from neomach import proto as P  # noqa: E402
from neomach.sim import MainboardSim, serve_tcp  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_protocol():
    assert P.crc16(b"123456789") == 0x31C3                          # XMODEM check value
    # PROTOCOL.md example: head depth, nozzle 1, Z 0, speed 0x96
    assert P.data_frame(P.p_depth(1, 0.0, 0x96)).hex(" ") == "00 00 96 01 00 00 00 00 14"
    assert P.cmd_bytes(P.T_CLEAR, 2) == [0x42] and P.cmd_bytes(P.T_WRITE, 2) == [0xC2]
    assert P.cmd_bytes(P.T_READ, 0x73) == [0xBF, 0x73]              # feeder 45 command, long
    for sent in range(256):
        for flags in (0, 0x10, 0x40, 0x50):
            assert P.answer_ok(sent, P.answer_for(sent, flags))
            assert P.answer_ok(sent, P.answer_for(sent, flags, prefix=True), prefix=True)
    assert not P.answer_ok(0x42, P.answer_for(0x42, 0x10) ^ 1)
    assert P.p_rotate(3, 90, 0x96) == bytes([0x7C, 0xFC, 0x96, 3, 0, 0, 0, 0])     # -900
    assert P.p_xy(0, 0) == (-43700).to_bytes(4, "little", signed=True) + bytes(4)
    assert P.p_xy(P.X_MAX_MM, 0)[:4] == bytes(4)
    for bad in ((-0.1, 0), (0, 400.1), (416.3, 0)):
        try:
            P.p_xy(*bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    # peel: b0 peeler, b1 length, b2 strength
    assert P.p_peel(5, 80, 12) == (P.R_PEEL_R, bytes([5, 12, 80, 0, 0, 0, 0, 0]))
    assert P.p_peel(30, 1, 2) == (P.R_PEEL_L, bytes([11, 2, 1, 0, 0, 0, 0, 0]))
    assert P.p_feed(7, 0, P.FEED_SET_ID) == bytes([7, 0, 0, 0, 0, 0, 0, 1])
    assert P.p_valve(2, P.VALVE_VACUUM) == bytes([0xFF, 2, 0, 0, 0, 0, 0, 0])
    assert P.p_feed(4, 0, 1) == bytes([4, 0, 0, 0, 0, 0, 0, 1])
    assert P.p_rail(12.7) == bytes(4) + (1000).to_bytes(4, "little")
    print("protocol ok")


def scenario(mb, sim, name):
    t0 = time.time()
    try:
        mb.move_xy(10, 10)
        raise AssertionError("moved before homing")
    except NotHomed:
        pass
    mb.set_xy_speed(80)
    mb.set_rail_speed(200)
    mb.home_xy()
    assert sim.xy_steps == [0, P.Y_MAX_STEPS] and mb.pos == (0.0, P.Y_MAX_MM)
    mb.move_xy(120.5, 80.25)
    assert abs(sim.xy_mm()[0] - 120.5) < 0.01 and abs(sim.xy_mm()[1] - 80.25) < 0.01
    mb.move_rel(-20.5, 0.75)
    assert abs(sim.xy_mm()[0] - 100.0) < 0.01 and abs(sim.xy_mm()[1] - 81.0) < 0.01
    # nozzle: rotate, lower, vacuum, part detection
    mb.rotate(2, -45.5)
    mb.set_z(2, 5.25)
    assert sim.angle[2] == -45.5 and sim.z[2] == 5.25
    mb.suck(2)
    time.sleep(0.15)                                        # pressure settles (tau 20 ms)
    assert not mb.part_present(2)
    sim.parts[2] = True
    time.sleep(0.15)
    assert mb.part_present(2)
    # XY move raises the lowered nozzle first
    mb.move_xy(200, 100)
    assert sim.z[2] == 0.0 and mb.nozzles_up()
    # lights / outputs share reg 7: each keeps its bit
    mb.up_light(True)
    mb.beep(True)
    mb.board_ready(True)
    assert sim.up_light and sim.beep and sim.board_ready
    mb.beep(False)
    assert sim.up_light and not sim.beep and sim.board_ready
    # beep pulses: non-blocking by default, silent when mb.sound is False
    n0 = len(sim.writes)
    mb.beep_pulse(count=2, on_s=0.05, off_s=0.05)
    time.sleep(0.3)
    beeps = [d[5] & 1 for a, d in sim.writes[n0:] if a == P.R_SWITCH]
    assert beeps == [1, 0, 1, 0] and not sim.beep, beeps
    mb.sound = False
    n0 = len(sim.writes)
    mb.beep_pulse(count=3, wait=True)
    assert not [a for a, d in sim.writes[n0:] if a == P.R_SWITCH]
    mb.sound = True
    mb.beep(True)                                           # the manual buzzer still works
    mb.beep(False)
    mb.head_led(77)
    assert sim.head_led == 77
    mb.head_led(0)
    assert sim.head_led == 0
    # config bytes shared in reg 6
    mb.vibration_feeder(True, strength=18, freq=100)          # b5 strength, b6 frequency
    assert sim.config[0] == 80 and sim.config[1] == 9 and sim.config[3] == 200 and sim.config[5:7] == b"\x12\x64"
    mb.vibration_feeder(False, freq=100)
    assert sim.config[5:7] == b"\x00\x64"
    # rail
    mb.rail_move(12.7)
    assert sim.rail_steps == 1000
    # feeders: short (12) and long (45) addresses, retries on failure
    mb.feed(12, 4)
    assert mb.wait_feed(12)
    sim.fail_feeds[45] = 2
    mb.feed(45, 2)
    assert mb.wait_feed(45)
    assert [f for f in sim.feeds if f[0] == 45] == [(45, 2, 0, 0), (45, 100, 0, 0), (45, 100, 0, 0)]
    sim.fail_feeds[3] = 99
    mb.feed(3, 4)
    try:
        mb.wait_feed(3, retries=2)
        raise AssertionError("failed feeder reported done")
    except MachineError:
        pass
    # peelers
    mb.peel(5, 3, 7)
    mb.peel(30, 1, 2)
    assert sim.peels[-2:] == [(5, 3, 7), (30, 1, 2)]
    time.sleep(0.05)
    assert mb.peeler_ready(5) and mb.peeler_ready(30)
    mb.vibration(100, 18)
    assert sim.vib[-1] == (100, 18)
    # lowering is refused while XY is busy
    mb.move_xy(10, 10, wait=False)
    try:
        mb.set_z(1, 3.0)
        raise AssertionError("lowered a nozzle while XY was moving")
    except SafetyRefused:
        pass
    mb.wait_xy()
    mb.set_z(1, 3.0)
    mb.raise_all()
    # step loss is reported (automatic correction off), correction clears it
    mb.step_loss_retries = 0
    sim.xy_state = 3
    try:
        mb.move_xy(20, 20)
        raise AssertionError("moved during step loss")
    except StepLoss:
        pass
    mb.xy_correct()
    mb.move_xy(20, 20)
    assert mb.position() == (20, 20) and mb.status()["xy_state_name"] == "idle"
    # slow final approach (moveToSlow): (x-1, y-1) at the normal speed, last mm at speed 10, speed restored
    n0 = len(sim.writes)
    mb.move_xy(150, 90, slow=True)
    w = sim.writes[n0:]
    xy = [struct.unpack("<ii", d) for a, d in w if a == P.R_XY]
    speeds = [d[0] for a, d in w if a == P.R_CONFIG]
    assert [(x + P.X_OFFSET, y) for x, y in xy] == [P.xy_steps(149, 89), P.xy_steps(150, 90)], xy
    assert speeds == [10, 80], speeds
    assert abs(sim.xy_mm()[0] - 150) < 0.01 and sim.config[0] == 80
    # a speed for one move only
    n0 = len(sim.writes)
    mb.move_xy(160, 90, speed=30)
    assert [d[0] for a, d in sim.writes[n0:] if a == P.R_CONFIG] == [30, 80]
    # progress callback and cancel during a long wait
    seen = []
    mb.progress = lambda what, el, to: seen.append(what)
    mb.move_xy(400, 380, wait=False)
    threading.Timer(0.05, mb.cancel).start()
    try:
        mb.wait_xy()
        raise AssertionError("wait not cancelled")
    except Cancelled:
        pass
    assert seen and seen[0] == "XY move"
    mb.progress = None
    mb.wait_xy()
    # state 5 (X and Y step loss) is a step loss too
    sim.xy_state = 5
    try:
        mb.move_xy(30, 30)
        raise AssertionError("moved during XY step loss")
    except StepLoss:
        pass
    mb.xy_correct()
    mb.step_loss_retries = 3
    # Z range is 0..13 mm (13 = the camera-correction stamp), suck / blow
    mb.set_z(1, 13.0)
    mb.raise_all()
    try:
        mb.set_z(1, 13.5)
        raise AssertionError("accepted Z 13.5")
    except ValueError:
        pass
    mb.suck(3)
    assert sim.valve[3] == 0xFF
    mb.blow(3)
    assert sim.valve[3] == 1
    mb.suck(3, False)
    assert sim.valve[3] == 0
    # feeder ID change and calibration (modes 1, 2)
    mb.feeder_set_id(9, 21)
    mb.feeder_calibrate(9)
    assert sim.feeds[-2:] == [(9, 21, 0, 1), (9, 0, 0, 2)]
    # start key latch: one press is reported once
    sim.input0 = False
    mb.status()
    sim.input0 = True
    mb.status()
    sim.input0 = False
    assert mb.take_start_key() and not mb.take_start_key()
    # helpers for the manual page
    mb.board_in_ready(False)
    mb.rail_speed(150)
    assert sim.config[3] == 150 and mb.rail_done()
    mb.wait_idle(0)
    sim.input0 = True
    assert mb.start_pressed()
    sim.input0 = False
    assert xy_state_name(4) == "step loss Y"
    assert sim.bad_frames == 0
    print(f"{name}: scenario ok ({time.time() - t0:.1f} s, {len(sim.writes)} writes)")


def test_homing():
    """Homing waits for the real end, reports a failed home, and step losses are corrected."""
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    mb.home_xy()
    # re-home from idle while the status still shows "idle" for 150 ms after the pulse:
    # must not return before the homing (0.4 s) has really finished
    sim.home_s, sim.switch_lag_s = 0.4, 0.15
    t0 = time.monotonic()
    mb.home_xy()
    dt = time.monotonic() - t0
    assert dt >= 0.4 and sim.xy_state == 0, dt
    # home switch not found: ends in "not homed" -> NotHomed at once, not a 60 s timeout
    sim.home_s, sim.switch_lag_s, sim.home_fails = 0.1, 0.0, True
    t0 = time.monotonic()
    try:
        mb.home_xy()
        raise AssertionError("failed homing not reported")
    except NotHomed as ex:
        assert "failed" in str(ex) and time.monotonic() - t0 < 2, ex
    assert mb.position() is None
    sim.home_fails = False
    mb.home_xy()
    # step loss during a move: corrected and the target re-sent (like NeoDen4.exe)
    seen = []
    mb.on_step_loss = lambda st, target, n: seen.append((st, target, n))
    mb.move_xy(100, 100, wait=False)
    sim.xy_state = 4
    mb.wait_xy()
    assert seen == [(4, (100, 100), 1)] and max(abs(v - 100) for v in sim.xy_mm()) < 0.01, seen
    # step loss already present before a move: corrected first
    sim.xy_state = 3
    mb.move_xy(120, 100)
    assert len(seen) == 2 and seen[1][0] == 3 and abs(sim.xy_mm()[0] - 120) < 0.01
    # it keeps losing steps: after 3 corrections the error is raised
    orig_w, orig_t = sim._write, sim._tick
    lose = []
    def lossy_write(addr, d):
        orig_w(addr, d)
        if addr == P.R_XY:
            lose.append(1)
    def lossy_tick():
        was = sim.xy_state
        now = orig_t()
        if was == 2 and sim.xy_state == 0 and lose:
            lose.clear()
            sim.xy_state = 5
        return now
    sim._write = lossy_write
    sim._tick = lossy_tick
    seen.clear()
    try:
        mb.move_xy(150, 100)
        raise AssertionError("endless step loss not reported")
    except StepLoss:
        pass
    sim._tick, sim._write = orig_t, orig_w
    assert len(seen) == 3 and len(mb.step_losses) == 5, (seen, mb.step_losses)
    sim.xy_state = 0
    mb.close()
    print("homing ok")


def test_startup():
    """Start-up: always an XY home (also when referenced), then homeAll and the speeds."""
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    mb.set_rail_speed(120)                     # reg 6 shadow starts at b0 = 80, b1 = 9 like NeoDen4.exe
    assert sim.config[0] == 80 and sim.config[1] == 9
    assert mb.startup(run_speed=70) == 1       # fresh board: state 1, homes XY
    assert sim.xy_steps == [0, P.Y_MAX_STEPS] and sim.config[0] == 70 and sim.config[1] == 9 and sim.config[3] == 200
    n0 = len(sim.writes)
    assert mb.startup(run_speed=90) == 0       # referenced: still a full home, then homeAll
    w = sim.writes[n0:]
    assert any(a == P.R_SWITCH and d[0] & 1 for a, d in w)
    valves = [(d[1], d[0]) for a, d in w if a == P.R_HEAD_VALVE]
    assert valves[:4] == [(1, 1), (2, 1), (3, 1), (4, 1)] and valves[4:8] == [(1, 0), (2, 0), (3, 0), (4, 0)]
    cfgs = [(d[0], d[1]) for a, d in w if a == P.R_CONFIG]
    assert (30, 6) in cfgs and cfgs[-1][0] == 90 and sim.config[3] == 200
    assert mb.position() == (0.0, P.Y_MAX_MM)
    mb.close()
    print("startup ok")


def test_vacuum():
    """Edge / integral pick detection: good, empty pocket, lost on lift, weak seal, clogged,
    leak watch, automatic threshold, learner save / load."""
    import tempfile
    from neomach.board import PickError
    from neomach.vacuum import VacuumLearner, analyze_pick, seal_fraction
    # pure analysis on a hand-made trace: open -15, seal to -60 at t = 0.1 s, lift, held
    tr = [(i * 0.01, -15, "open") for i in range(8)]
    tr += [(0.08 + i * 0.01, -15 if i < 2 else -60, "down") for i in range(8)]
    tr += [(0.16 + i * 0.01, -60, "up") for i in range(8)]
    r = analyze_pick(tr)
    assert r.held and r.sealed_level == -60 and r.threshold == -37.5 and abs(r.edge_time - 0.02) < 1e-9, r
    assert seal_fraction([(0, -15), (1, -60)], -15, -60) == 0.5
    # through the machine (sim)
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    mb.home_xy()
    lr = VacuumLearner()

    def trial(**setup):
        sim.pick_here[1], sim.drop_on_lift[1] = False, False
        for k, v in setup.items():
            getattr(sim, k)[1] = v
        try:
            return mb.pick(1, 4.0, delay_ms=20, learner=lr), None
        except PickError as e:
            return e.result, str(e)
        finally:
            mb.suck(1, False)

    for _ in range(3):
        r, err = trial(pick_here=True)
        assert r.held and err is None and abs(r.sealed_level + 60) <= 3 and r.seal_up > 0.9, (r.reason, r.sealed_level)
    assert abs(lr.threshold(1) - (-37.5)) <= 3
    r, err = trial()
    assert not r.held and "no pressure drop" in err
    r, err = trial(pick_here=True, drop_on_lift=True)
    assert not r.held and "lost on the way up" in err
    sim.sealed_level[1] = -30
    r, err = trial(pick_here=True)
    sim.sealed_level[1] = -60
    assert not r.held and "weak seal" in err
    sim.open_level[1] = -45
    r, err = trial()
    sim.open_level[1] = -15
    assert not r.held and "clogged" in err
    # leak watch during travel
    sim.pick_here[1] = True
    r = mb.pick(1, 4.0, delay_ms=20, learner=lr)
    w = mb.watch_held(1, r, 50)
    assert w.ok and w.leak < 0.005
    sim.parts[1] = False                                    # the part falls off
    try:
        mb.watch_held(1, r, 200)
        raise AssertionError("lost part not noticed")
    except PickError as e:
        assert "lost or slipping" in str(e)
    mb.suck(1, False)
    # pick without a part available at all must not hang: no edge
    r = mb.pick(2, 4.0, raise_on_fail=False)
    assert not r.held
    mb.suck(2, False)
    # learner persists
    f = os.path.join(tempfile.mkdtemp(), "vac.json")
    lr.save(f)
    lr2 = VacuumLearner.load(f)
    assert lr2.knows(1) and abs(lr2.threshold(1) - lr.threshold(1)) < 1e-9
    mb.close()
    print("vacuum ok")


def with_sim_server():
    sim, ready, stop = MainboardSim(), threading.Event(), threading.Event()
    port = free_port()
    th = threading.Thread(target=serve_tcp, args=(sim, port, "127.0.0.1", ready, stop), daemon=True)
    th.start()
    ready.wait(5)
    return sim, port, stop


def test_inprocess():
    mb = Mainboard.connect("sim://")
    scenario(mb, mb.link.port.sim, "sim://")
    mb.close()


def test_pyserial_socket():
    sim, port, stop = with_sim_server()
    mb = Mainboard.connect(f"serial://socket://127.0.0.1:{port}")
    scenario(mb, sim, "serial://socket")
    mb.close()
    stop.set()


def test_serbridge():
    exe = os.path.join(ROOT, "serbridge", "serbridge.exe")
    if os.name != "nt":
        print("serbridge.exe is Windows-only (not needed on the machine's Linux PC) - skipped")
        return
    if not os.path.exists(exe):
        print("serbridge.exe not built (sh serbridge/build.sh) - skipped")
        return
    sim, sport, stop = with_sim_server()
    bport = free_port()
    p = subprocess.Popen([exe, "--port", str(bport), "--bind", "127.0.0.1",
                          "--serial-tcp", f"127.0.0.1:{sport}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                mb = Mainboard.connect(f"tcp://127.0.0.1:{bport}")
                break
            except OSError:
                time.sleep(0.05)
        info = mb.info()
        assert info.startswith("serbridge 1.0") and "open=1" in info, info
        # raw debug path: a status poll of reg 5 by hand
        a = mb.link.raw(bytes([0x05]), 1)
        assert len(a) == 1 and P.answer_ok(0x05, a[0]), a
        scenario(mb, sim, "serbridge.exe")
        # a silent board comes back from the bridge as LinkError -4
        real = sim.feed
        sim.feed = lambda d: b""
        try:
            mb.status()
            raise AssertionError("no error through the bridge")
        except LinkError as e:
            assert e.code == -4, e
        sim.feed = real
        mb.close()
    finally:
        p.terminate()
        out = p.communicate(timeout=5)[0]
        stop.set()
    assert "listening" in out and "serial line = tcp" in out, out


def test_errors():
    """A board that never answers / answers wrong gives the right error."""
    mb = Mainboard.connect("sim://")
    sim = mb.link.port.sim
    real = sim.feed
    sim.feed = lambda d: b""                                        # silent board
    try:
        mb.status()
        raise AssertionError("no error from a silent board")
    except LinkError as e:
        assert e.code == -4, e
    sim.feed = lambda d: bytes(b ^ 0x0F for b in real(d))           # wrong check nibble
    try:
        mb.status()
        raise AssertionError("bad answer accepted")
    except LinkError as e:
        assert e.code == -5, e
    print("errors ok")


if __name__ == "__main__":
    test_protocol()
    test_errors()
    test_startup()
    test_homing()
    test_vacuum()
    test_inprocess()
    test_pyserial_socket()
    test_serbridge()
    print("all ok")
