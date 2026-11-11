"""NeoDen4 mainboard serial protocol: framing, checks and register payloads.

From NeoDen4.exe V4.1.3 (CIoSerial / CFunNode*, see the author's protocol notes).
COM2, 115200 8N1. The PC sends one command byte at a time and the board answers
each with one byte; data phases are 8 bytes plus one check byte.

    command byte   type << 6 | addr        (addr 0..0x3E)
                   type << 6 | 0x3F, addr  (addr >= 0x3F, two bytes, each answered)
    answer byte    flags (high nibble) | crc16([sent, flags]) & 0xF
                   (for the 0x3F prefix byte: crc16([prefix]) & 0xF, flags ignored)
    data           8 bytes + crc16(data) & 0xFF
"""
import struct

T_STATUS, T_CLEAR, T_READ, T_WRITE = 0, 1, 2, 3     # command bits 7..6
F_UPDATED = 0x10        # status flag: fresh data ready to read
F_WRITTEN = 0x40        # status flag: the last write was taken
LONG = 0x3F             # addresses >= 0x3F use a prefix byte

# register addresses (CFunNode::regName labels 0..16, then the feeders)
R_HEAD_STATUS, R_HEAD_ROTATE, R_HEAD_DEPTH, R_HEAD_VALVE, R_HEAD_LED = 0, 1, 2, 3, 4
R_STATUS, R_CONFIG, R_SWITCH, R_XY, R_RAIL, R_TRAY = 5, 6, 7, 8, 9, 10
R_PEEL_R_STATUS, R_PEEL_R, R_PEEL_L_STATUS, R_PEEL_L = 11, 12, 13, 14
R_VIB_STATUS, R_VIB = 15, 16
FEEDERS = 50


def r_feed_status(n):
    return 0x14 + n                 # feeder 1..50 -> 0x15..0x46


def r_feed_cmd(n):
    return 0x46 + n                 # feeder 1..50 -> 0x47..0x78


REG_NAMES = {
    0: "head status", 1: "head rotation", 2: "head depth", 3: "head valve", 4: "head LED",
    5: "controller status", 6: "controller config", 7: "controller switch", 8: "XY target",
    9: "rail move", 10: "tray target", 11: "right peeler status", 12: "right peeler peel",
    13: "left peeler status", 14: "left peeler peel", 15: "vibration status", 16: "vibration command",
}


def reg_name(addr):
    if addr in REG_NAMES:
        return REG_NAMES[addr]
    if 0x15 <= addr <= 0x46:
        return f"feeder {addr - 0x14} status"
    if 0x47 <= addr <= 0x78:
        return f"feeder {addr - 0x46} command"
    return f"reg 0x{addr:02x}"


# ---- checks -----------------------------------------------------------------

def crc16(data, crc=0):
    """CRC-16/XMODEM: poly 0x1021, init 0, MSB first (tables at 0x004c3380 / 0x004c3580)."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cmd_bytes(typ, addr):
    """Bytes the PC sends to address one register (one or two)."""
    return [typ << 6 | addr] if addr < LONG else [typ << 6 | LONG, addr]


def answer_for(sent, flags=0, prefix=False):
    """The answer byte the board sends back for `sent`."""
    if prefix:
        return (flags & 0xF0) | (crc16([sent]) & 0xF)
    return (flags & 0xF0) | (crc16([sent, flags & 0xF0]) & 0xF)


def answer_ok(sent, answer, prefix=False):
    buf = [sent] if prefix else [sent, answer & 0xF0]
    return (crc16(buf) & 0xF) == (answer & 0xF)


def data_frame(data8):
    data8 = bytes(data8)
    if len(data8) != 8:
        raise ValueError("data must be 8 bytes")
    return data8 + bytes([crc16(data8) & 0xFF])


def data_ok(frame9):
    return len(frame9) == 9 and (crc16(frame9[:8]) & 0xFF) == frame9[8]


# ---- units --------------------------------------------------------------------

XY_STEP_MM = 0.009525           # 0x004c30c8
RAIL_STEP_MM = 0.0127           # 0x004c30d8
X_OFFSET = 0xAAB4               # register X = steps - 43700
X_MAX_STEPS = 0xAAB4            # 43700 steps = 416.24 mm
Y_MAX_STEPS = 0xA410            # 42000 steps = 400.05 mm
X_MAX_MM = X_MAX_STEPS * XY_STEP_MM
Y_MAX_MM = Y_MAX_STEPS * XY_STEP_MM


def mm_to_steps(mm, step):
    return int(round(mm / step))


def xy_steps(x_mm, y_mm):
    """mm -> (x_steps, y_steps); ValueError outside the travel (CFunNodeMainBoard::moveXY)."""
    sx, sy = mm_to_steps(x_mm, XY_STEP_MM), mm_to_steps(y_mm, XY_STEP_MM)
    if not (0 <= sx <= X_MAX_STEPS and 0 <= sy <= Y_MAX_STEPS):
        raise ValueError(f"XY ({x_mm:.3f}, {y_mm:.3f}) mm outside 0..{X_MAX_MM:.2f} x 0..{Y_MAX_MM:.2f}")
    return sx, sy


# ---- payloads (8 bytes; layouts from the CFunNode* classes) --------------------

def p_rotate(nozzle, angle_deg, speed):                 # reg 1, CFunNodeHead::rotate
    return struct.pack("<hBB4x", -int(round(angle_deg * 10)), speed & 0xFF, nozzle)


def p_depth(nozzle, z_mm, speed):                        # reg 2, CFunNodeHead::depthSet
    return struct.pack("<hBB4x", int(round(z_mm * 1000)), speed & 0xFF, nozzle)


def p_valve(channel, state):                             # reg 3, CFunNodeHead::setValve
    return struct.pack("<BB6x", state & 0xFF, channel & 0xFF)


LED_OFF, LED_INNER, LED_OUTER, LED_BOTH = 0, 1, 2, 3     # head camera lamp (reg 4 b0)
VALVE_VACUUM, VALVE_OFF, VALVE_BLOW = -1, 0, 1           # reg 3 b0 (CFunNodeHead::setValve)
Z_MAX_MM = 13.0                                          # reg 2: 0 = up; 12 mm is the manual scale,
                                                         # 13 mm the camera-correction "Trail" stamp
FEED_RUN, FEED_SET_ID, FEED_CALIBRATE = 0, 1, 2          # feeder command b7


def p_led(value):                                        # reg 4, CFunNodeHead::setLed (b0 = lamp 0..3)
    return struct.pack("<B7x", value & 0xFF)


def p_xy(x_mm, y_mm):                                    # reg 8, CFunNodeMainBoard::moveXY
    sx, sy = xy_steps(x_mm, y_mm)
    return struct.pack("<ii", sx - X_OFFSET, sy)


def p_rail(mm):                                          # reg 9, CFunNodeMainBoard::railMove
    return struct.pack("<4xi", mm_to_steps(mm, RAIL_STEP_MM))


def p_peel(peeler, strength, length):
    """(reg, payload) for a peel: b0 peeler, b1 length, b2 strength (CFunNodePeel::peel);
    right peeler reg 12 for 1..19, left reg 14 for 20..48 (sent as id - 19)."""
    if 1 <= peeler <= 19:
        return R_PEEL_R, struct.pack("<BBB5x", peeler, length & 0xFF, strength & 0xFF)
    if 20 <= peeler <= 48:
        return R_PEEL_L, struct.pack("<BBB5x", peeler - 0x13, length & 0xFF, strength & 0xFF)
    raise ValueError("peeler must be 1..48")


def p_feed(b0, b1=0, mode=FEED_RUN):
    """reg 0x46+n (CFunNodeFeed): mode 0 feed (b0 strength 10..100, b1 length mm 2..32),
    mode 1 change ID (b0 new ID), mode 2 calibrate."""
    return struct.pack("<BB5xB", b0 & 0xFF, b1 & 0xFF, mode & 0xFF)


def p_vib(a, b):                                         # reg 16, CFunNodeFeedS::command
    return struct.pack("<BB6x", a & 0xFF, b & 0xFF)


# ---- decoders -----------------------------------------------------------------

def d_head_status(d):
    """reg 0 -> (idle bits nozzle 1..4 as a list, vacuum values 1..4 as signed ints)."""
    vac = list(struct.unpack("<4b", d[:4]))
    return [bool(d[4] >> i & 1) for i in range(4)], vac


def d_status(d):
    """reg 5 -> dict (CFunNodeMainBoard::readStatus and its callers)."""
    return {
        "xy_state": d[0],                   # 0 idle, 1 not homed, 2 busy, 3 X / 4 Y / 5 XY step loss
        "rail_done": bool(d[1] >> 1 & 1),
        "input0": bool(d[3] & 1),           # start button (1 = pressed)
        "input1": not bool(d[3] >> 1 & 1),  # rail / board-out sensor, inverted: True = rail idle
        "flag3": bool(d[3] >> 3 & 1),
        "raw": bytes(d),
    }


def d_feed_status(d):
    """reg 0x14+n -> (done, failed, remaining) (CFunNodeFeed::getStatus)."""
    return bool(d[0] >> 5 & 1), bool(d[0] >> 6 & 1), d[0] & 0x1F
