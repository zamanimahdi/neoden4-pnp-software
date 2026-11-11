"""Command line for bring-up on the machine, one step at a time.

    python -m neomach --url tcp://192.168.1.20:5602 status
    python -m neomach --url tcp://... home
    python -m neomach --url tcp://... move 100 80
    python -m neomach --url tcp://... light on        (up camera light)
    python -m neomach --url tcp://... read 5          (raw register, hex out)

Commands: info, status, head, startup [SPEED], home, correct, move X Y, rel DX DY,
rotate N DEG, z N MM (0..13), up, valve N -1|0|1, suck N on|off, blow N on|off, vacuum N,
led 0|1|2|3 (off/inner/outer/both), light on|off, beep on|off,
feed N STRENGTH [LENGTH [MODE]], fstatus N, peel N STRENGTH LENGTH, read ADDR, write ADDR HEX16.
Addresses accept decimal or 0x.. hex. Use --url sim:// to try without the machine.
"""
import argparse
import sys

from . import proto as P
from .board import Mainboard


def num(s):
    return int(s, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m neomach", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="tcp://127.0.0.1:5602")
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    mb = Mainboard.connect(a.url)
    c, x = a.cmd, a.args
    onoff = lambda s: s.lower() in ("1", "on", "true", "yes")       # noqa: E731
    try:
        if c == "info":
            print(mb.info())
        elif c == "status":
            s = mb.status()
            print(f"xy_state {s['xy_state']} ({s['xy_state_name']})  rail_done {s['rail_done']}  "
                  f"start {s['input0']}  rail_idle {s['input1']}  raw {s['raw'].hex(' ')}")
        elif c == "head":
            idle, vac = mb.head_status()
            for n in range(4):
                print(f"nozzle {n + 1}: {'idle' if idle[n] else 'busy'}  vacuum {vac[n]}")
        elif c == "startup":
            st = mb.startup(int(x[0]) if x else 80)
            print("startup done (XY state before:", st, ")")
        elif c == "suck":
            mb.suck(num(x[0]), onoff(x[1]))
        elif c == "blow":
            mb.blow(num(x[0]), onoff(x[1]))
        elif c == "home":
            mb.home_xy()
            print("homed, position", mb.pos)
        elif c == "correct":
            mb.xy_correct()
        elif c == "move":
            mb.move_xy(float(x[0]), float(x[1]))
        elif c == "rel":
            print("note: the position is only known after 'home' in this same process")
            mb.move_rel(float(x[0]), float(x[1]))
        elif c == "rotate":
            mb.rotate(num(x[0]), float(x[1]))
        elif c == "z":
            mb.set_z(num(x[0]), float(x[1]))
        elif c == "up":
            mb.raise_all()
        elif c == "valve":
            mb.valve(num(x[0]), num(x[1]))
        elif c == "vacuum":
            print(mb.vacuum(num(x[0])))
        elif c == "led":
            mb.head_led(num(x[0]))
        elif c == "light":
            mb.up_light(onoff(x[0]))
        elif c == "beep":
            mb.beep(onoff(x[0]))
        elif c == "feed":
            n = num(x[0])
            mb.feed(n, num(x[1]), num(x[2]) if len(x) > 2 else 0, num(x[3]) if len(x) > 3 else 0)
            print("done" if mb.wait_feed(n) else "not done")
        elif c == "fstatus":
            print("done %s  failed %s  remaining %d" % mb.feeder_status(num(x[0])))
        elif c == "peel":
            mb.peel(num(x[0]), num(x[1]), num(x[2]))
        elif c == "read":
            addr = num(x[0])
            print(f"{P.reg_name(addr)}: {mb.read(addr).hex(' ')}")
        elif c == "write":
            mb.write(num(x[0]), bytes.fromhex(x[1]))
        else:
            ap.error(f"unknown command {c}")
    finally:
        mb.close()


if __name__ == "__main__":
    sys.exit(main())
