# serbridge + neomach: machine control

`serbridge.exe` runs on the machine's own Windows XP PC, opens the mainboard's
serial port (COM2) and serves register transactions over TCP. `neomach` is the
Python side: protocol, links and a high-level `Mainboard` API. Protocol source:
the author's protocol notes (from NeoDen4.exe V4.1.3).

```
NeoDen4 PC (Windows XP)                          any PC (Python 3)
  COM2 ── mainboard                                neomach.Mainboard
  serbridge.exe  ── TCP 5602 ─────────────────►    tcp://<pc>:5602
                                                   (or serial://COM2 directly, or sim://)
```

## Install on the machine PC

1. Close NeoDen4.exe: only one program can open COM2.
2. Copy `serbridge\serbridge.exe` anywhere (it needs no DLLs) and run it:
   ```
   serbridge.exe --trace
   ```
   It prints `serial line = COM2 115200 8N1` and `listening on 0.0.0.0:5602`.
   Options: `--port`, `--bind`, `--com COM2`, `--baud 115200`, `--trace` (log every transaction).
3. Allow TCP 5602 in the XP firewall if a firewall is on.

For a supervisor (e.g. neoagent): no config file, no working directory, logs to stdout.
Exit codes: 2 bad arguments, 1 cannot listen. If COM2 cannot be opened it keeps running and
retries on every request (`INFO` shows `open=0/1`). Terminating it is safe (no state; the OS
releases COM2). One TCP client at a time; start it with `--bind 127.0.0.1` behind an agent.

Build: `sh serbridge/build.sh` (MSYS2 `mingw-w64-i686-gcc`). The exe is 32-bit, static, and
imports only functions that exist in the machine's own XP SP2 `kernel32`, `ws2_32` and
`msvcrt` (checked against the DLLs from the disk image).

## First bring-up on the machine (one step at a time)

From the other PC (`python -m neomach --url tcp://<machine PC>:5602 <command>`):

| step | command | what it proves |
|---|---|---|
| 1 | `info` | bridge reachable, `open=1` = COM2 opened |
| 2 | `status` | read path works; after power-up expect `xy_state 1 (not homed)` |
| 3 | `head` | head status: four nozzles idle, vacuum about 0 |
| 4 | `light on` / `light off` | write path works (up-camera light) |
| 5 | `led 100` / `led 0` | head LED (value scale not known yet; try a few) |
| 6 | clear the work area, hand on the emergency stop: `home` | XY homing; ends at X 0, Y 400.05 mm |
| 7 | `move 100 100`, then small moves | XY motion and step scale (0.009525 mm/step) |
| 8 | `rotate 1 90`, `z 1 2`, `up` | nozzle rotation and depth (start with small Z!) |

`move`, `rel` and `home` must run in the same process to know the position: the board does
not report XY, so the program keeps the last target, exactly like NeoDen4.exe. In your own
program keep one `Mainboard` object for the whole session.

NeoDen4.exe also sets, at start-up: `set_xy_speed(<speed>, 9)` (probably `nRunSpeed=80`
from Config.ini) and `set_rail_speed(200)`.

## Python API (`neomach.Mainboard`)

```python
from neomach import Mainboard, MachineError, LinkError, NotHomed, StepLoss, SafetyRefused, Cancelled
mb = Mainboard.connect("tcp://192.168.1.20:5602")   # "serial://COM2", "sim://"

# XY
mb.home_xy(); mb.xy_correct()
mb.move_xy(120.0, 80.0)                  # wait=True, timeout=30
mb.move_xy(120.0, 80.0, slow=True)       # moveToSlow: (x-1, y-1), last mm at speed 10, speed restored
mb.move_xy(120.0, 80.0, speed=30)        # speed for this move only
mb.move_rel(1, 0); mb.position()         # last target (the board never reports XY)
mb.set_xy_speed(80, 9)                   # alias xy_speed

# start-up: ALWAYS a full XY home (NeoDen4.exe skips it when the controller is still
# referenced; here it cannot be switched off), then homeAll, XY speed nRunSpeed, rail 200
mb.startup(run_speed=80)                 # mb.home_all() = blow pulse, Z up, 0 deg, back to (0, 400)

# homing waits for the real end (the status may still show the old state right after the
# pulse) and a home that ends "not homed" raises NotHomed at once instead of a timeout.
# Step loss during or before a move: XY correction + target re-sent, up to
# mb.step_loss_retries (3, like NeoDen4.exe) per move, then StepLoss. Every one is kept in
# mb.step_losses and reported to mb.on_step_loss(state, target, retry) - show it to the user:
# repeated step losses mean a crash or a mechanical problem.

# head
mb.rotate(1, 90); mb.set_z(1, 5.2); mb.raise_all(); mb.wait_idle(1)    # set_z alias depth
#   Z 0 = up .. 12 mm manual (13 = camera-correction stamp); speeds: 50 slider / manual turn, 100 buttons, 150 homing
mb.suck(1); mb.blow(1); mb.suck(1, False)    # = valve(1, -1 / +1 / 0)
mb.vacuum(1); mb.part_present(1, threshold=-40)
mb.head_status()                         # (idle[4], vacuum[4])

# lights, outputs, inputs
mb.up_light(True)                        # up camera light, alias output
mb.head_led(1)                           # head camera lamp: 0 off, 1 inner ring, 2 outer, 3 both
mb.beep(False); mb.board_in_ready(True)
mb.inputs()                              # (start button pressed, rail idle)
mb.take_start_key()                      # True once per start-button press

# rail (speed 20..200 = 2 x percent; + = loading direction)
mb.rail_speed(200); mb.rail_move(25.4); mb.rail_done(); mb.rail_stop()

# feeders (1..50), peelers (1..19 right, 20..48 left), vibration
mb.feed(12, strength=60, length=4); mb.wait_feed(12)   # retries a failed feed like NeoDen4.exe
mb.feeder_status(12)                     # (done, failed, remaining tape mm)
mb.feeder_set_id(12, 21); mb.feeder_calibrate(12)      # feeder modes 1 and 2
mb.peel(5, strength=80, length=12)
mb.vibration_feeder(True, strength=18, freq=100)       # reg 6 b5 / b6 (nVibCurrent / nVibFreq)
mb.peeler_ready(5); mb.vibration(a, b)   # UNVERIFIED: regs 11/13/16 are never used by NeoDen4.exe

# status, raw registers
mb.status()                              # xy_state (+ xy_state_name), rail_done, input0, input1
mb.read(addr); mb.write(addr, data8)

# long waits: progress and cancel (e.g. from a UI thread)
mb.progress = lambda what, elapsed_s, timeout_s: ...
mb.cancel()                              # the running wait raises Cancelled
```

Errors: `LinkError` (`.code` as in the table below: no answer, check, CRC, missing flag) for the
link; `MachineError` and its subclasses for the machine: `NotHomed`, `StepLoss` (state 3/4),
`SafetyRefused` (e.g. lowering while XY is busy), `MachineTimeout`, `Cancelled`.

Safety rules (from NeoDen4.exe): every XY or rail move raises all nozzles first; a nozzle is
not lowered below 1 mm while XY is busy; XY targets are range-checked (0..416.24 x 0..400.05 mm);
XY must be homed; step loss is reported and `xy_correct()` clears it.

XY states (reg 5 b0): 0 idle, 1 not homed, 2 busy, 3 step loss X, 4 step loss Y, 5 step loss X and Y.

Manual-page values in NeoDen4.exe (from `SETTINGS_MANUAL_SPEC.md`): Nozzle Down = Z 12 mm at
speed 100 (back to 0 on release); Turn left / right = +180 / −180 deg at speed 50; rail
Forward / Backward = `rail_move(±10000, wait=False)` then `rail_stop()` on release; keyboard
jog step 0.1 mm, Ctrl = one motor step (0.009525 mm), Shift = 1 mm.

### Beeper

`mb.beep(on)` switches the buzzer (reg 7 byte 5 bit 0). `mb.beep_pulse(count, on_s, off_s)`
beeps a pattern on a timer thread (does not hold the caller up; `wait=True` blocks).
`mb.sound = False` mutes every `beep_pulse` (master switch); `beep()` stays usable for a
manual buzzer button. NeoDen4.exe beeps at job end (runJob), when a board leaves
(unloadPcb) and when a fiducial needs the user (locateMarks); in the new software each of
these is a job-runner setting (on / off).

## Vacuum: pick detection without a fixed threshold (`neomach.vacuum`)

NeoDen4.exe checks a pick with a fixed threshold (job column, default -40): the average of
10 pressure reads must be below it. `Mainboard.pick()` instead:

1. switches suction on with the nozzle still up and measures this nozzle's **open-air level
   and noise** (`open_ms`, 80 ms);
2. lowers the nozzle: touching the part seals the tip and the pressure **falls**; the edge is
   found relative to the open level (at least `min_drop`, and `noise_k` x noise, held for
   `hold` samples). The **sealed level** comes from the settled samples after the edge;
3. waits the pick delay, lifts, and decides with an **integral**: the time-weighted seal
   fraction S = mean of (open − p) / (open − sealed) over the last `integrate_ms`
   (1 = fully sealed, 0 = open air). Held if S ≥ `held_fraction` (0.5): the automatic
   threshold is the midpoint between this pick's open and sealed levels.

Errors (`PickError`, with `.result` = `PickResult`: levels, threshold, integrals, edge time and
all samples for a plot):

| reason | when |
|---|---|
| no pressure drop | the nozzle never sealed: missed part / empty pocket |
| lost on the way up | S < 0.5 after lifting: the pressure went back toward open air |
| weak seal | drop < `weak_fraction` (60 %) of this nozzle's usual drop: tilted / leaking part, wrong nozzle |
| ... nozzle or filter clogged? | the open-air level is already far toward the sealed level |

```python
from neomach import VacuumLearner, VacuumParams, PickError
lr = VacuumLearner.load("vacuum.json")          # or VacuumLearner(VacuumParams(...))
try:
    r = mb.pick(nozzle, 12 - pick_height, speed=..., delay_ms=pick_delay, learner=lr)
    mb.watch_held(nozzle, r, 100)                # leak integral while the part waits / travels
except PickError as e:
    print(e, e.result.open_level, e.result.sealed_level, e.result.seal_up)
lr.save("vacuum.json")
lr.threshold(nozzle)                             # automatic threshold for later checks
```

`VacuumLearner` keeps per-nozzle open / sealed levels (EMA of good picks), the typical drop
(for "weak seal"), the clog check, and an automatic threshold. `VacuumWatch` integrates
max(0, p − sealed) / drop over time while a held part travels; past `leak_limit_s` (30 ms of
open-air equivalent) it reports the part as lost or slipping. All numbers are in
`VacuumParams` and are guesses until measured on the machine.

Difference from NeoDen4.exe: suction goes on **before** contact (NeoDen4.exe switches it on
at the bottom). That is what gives a fresh open-air baseline on every pick.

## TCP protocol (port 5602)

Little-endian, one request at a time, one client at a time.

Request, 16 bytes: `u8 cmd, u8 addr, u16 flags, u8 data[8], u32 arg`.
Reply: `i32 status, u32 len`, then `len` bytes.

| cmd | name | in | reply data |
|---|---|---|---|
| 1 | INFO | — | ASCII `serbridge 1.0 line=COM2 baud=115200 open=1 stray=0` |
| 2 | READ | addr, arg = poll ms (0 → 300) | 8 data bytes |
| 3 | WRITE | addr, data, arg = poll ms | — |
| 4 | STATUS | addr | 1 byte: answer flags (0x10 updated, 0x40 written) |
| 5 | RAW | data = bytes to send, flags = how many (≤ 8), arg = bytes to read back | what came back (500 ms) |
| 6 | DRAIN | — | bytes waiting on the line |
| 7 | BYE | — | — (connection closes) |

READ and WRITE are the same sequences as NeoDen4.exe's `CFunNode::readReg` / `writeReg`:
clear → (poll until 0x10) → read 8+1 bytes, or clear → write 8+1 bytes → poll until 0x40.
Polling runs at least 49 times (NeoDen4.exe's count) and at least `arg` ms.

Status codes (also `LinkError.code` in Python):

| code | meaning |
|---|---|
| 1 | ok |
| -1 | unknown command |
| -2 | serial port not open (NeoDen4.exe still running? wrong `--com`?) |
| -3 | serial write failed |
| -4 | no answer from the board within 500 ms |
| -5 | answer check nibble wrong |
| -6 | update flag never set (read) |
| -7 | written flag never set (write) |
| -8 | data frame incomplete |
| -9 | data frame CRC wrong |

## Simulator and tests

`neomach/sim.py` implements the byte protocol and a small machine model (homing, XY
motion, nozzles, valves and vacuum, lights, rail, feeders with failures, peelers).

```
python -m neomach.sim --port 5700                                   # byte stream over TCP
serbridge.exe --serial-tcp 127.0.0.1:5700                           # bridge against it
python -m neomach --url sim:// status                               # in-process
python tests/test_neomach.py                                        # all of the below
```

The test runs one machine scenario over `sim://`, over pyserial (`socket://`) and through
the real `serbridge.exe` (`--serial-tcp`), plus protocol and error checks.

## Not verified on the machine yet

- the whole link: answers, polling counts and timings are from the code, not from a capture
- head lamp value 3 (both rings), valve / rotate / depth channel 0 (= all?), speed byte units
- regs 11, 13 (peeler ready) and 16 (vibration table): never used by NeoDen4.exe
- whether the board needs a pause between polls (NeoDen4.exe polls back to back)
- the long-address "clear" (sent as the same bytes as a status poll, like NeoDen4.exe)
