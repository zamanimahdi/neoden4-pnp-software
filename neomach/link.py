"""Links to the NeoDen4 mainboard: register transactions over three transports.

    open_link("tcp://192.168.1.20:5602")         serbridge.exe on the machine PC (network)
    open_link("serial://COM2")                   the board's serial port on this PC (pyserial);
    open_link("serial:///dev/ttyS1")             ... on the machine's own Linux PC (COM2 = ttyS1)
    open_link("serial://socket://127.0.0.1:5700")  a simulator serving the byte stream over TCP
    open_link("sim://")                          in-process simulator (neomach.sim)

Every link offers status(addr), read_reg(addr) and write_reg(addr, data8), with
the same sequences NeoDen4.exe uses (CFunNode::readReg / writeReg):

    read : clear (0x40|a) -> poll status until flag 0x10 -> read (0x80|a) + 9 bytes
    write: clear (0x40|a) -> write (0xC0|a) + 9 bytes -> poll status until flag 0x40
"""
import socket
import struct
import time

from . import proto as P

# result codes (shared with serbridge.c)
OK = 1
E_UNKNOWN, E_NOT_OPEN, E_WRITE, E_TIMEOUT, E_CHECK, E_NO_UPDATE, E_NOT_WRITTEN, E_DATA_SHORT, E_DATA_CRC = \
    -1, -2, -3, -4, -5, -6, -7, -8, -9
ERRORS = {
    E_UNKNOWN: "unknown command", E_NOT_OPEN: "serial port not open", E_WRITE: "serial write failed",
    E_TIMEOUT: "no answer from the board (500 ms)", E_CHECK: "answer check failed",
    E_NO_UPDATE: "update flag (0x10) never set", E_NOT_WRITTEN: "written flag (0x40) never set",
    E_DATA_SHORT: "data frame incomplete", E_DATA_CRC: "data frame CRC wrong",
}

ANSWER_MS = 500         # rs232_readSyn timeout used by NeoDen4.exe
MIN_POLLS = 49          # NeoDen4.exe polls the status up to 49 times
POLL_MS = 300           # ... we also keep polling for at least this long


class LinkError(Exception):
    def __init__(self, code, addr=None, what=""):
        self.code, self.addr = code, addr
        where = f" ({P.reg_name(addr)})" if addr is not None else ""
        super().__init__(f"{what}{ERRORS.get(code, f'error {code}')}{where}")


# ---- byte transport + transaction engine (direct serial / simulator) -----------

class SerialLink:
    """Transactions done here, byte by byte, over a port with write(b) / read(n, timeout_s)."""

    def __init__(self, port, poll_ms=POLL_MS, log=None):
        self.port, self.poll_ms, self.log = port, poll_ms, log
        self.stray = 0

    def close(self):
        self.port.close()

    def info(self):
        return f"serial {self.port.name}"

    def _xchg(self, b, prefix=False):
        try:
            self.port.write(bytes([b]))
        except Exception:
            raise LinkError(E_WRITE)
        r = self.port.read(1, ANSWER_MS / 1000)
        if not r:
            raise LinkError(E_TIMEOUT)
        if not P.answer_ok(b, r[0], prefix):
            raise LinkError(E_CHECK)
        return r[0] & 0xF0

    def _cmd(self, typ, addr):
        if addr < P.LONG:
            return self._xchg(typ << 6 | addr)
        # NeoDen4.exe sends the plain 0x3F prefix for "clear" too (CIoSerial::clearLong)
        self._xchg((0 if typ == P.T_CLEAR else typ) << 6 | P.LONG, prefix=True)
        return self._xchg(addr)

    def _drain(self):
        junk = self.port.read(64, 0)
        if junk:
            self.stray += len(junk)
            if self.log:
                self.log(f"stray bytes: {junk.hex(' ')}")

    def _poll(self, addr, flag):
        t_end, n = time.monotonic() + self.poll_ms / 1000, 0
        while n < MIN_POLLS or time.monotonic() < t_end:
            if self._cmd(P.T_STATUS, addr) & flag:
                return True
            n += 1
        return False

    def status(self, addr):
        self._drain()
        try:
            return self._cmd(P.T_STATUS, addr)
        except LinkError as e:
            raise LinkError(e.code, addr)

    def read_reg(self, addr):
        self._drain()
        try:
            self._cmd(P.T_CLEAR, addr)
            if not self._poll(addr, P.F_UPDATED):
                raise LinkError(E_NO_UPDATE)
            self._cmd(P.T_READ, addr)
            frame = self.port.read(9, ANSWER_MS / 1000)
            if len(frame) < 9:
                raise LinkError(E_DATA_SHORT)
            if not P.data_ok(frame):
                raise LinkError(E_DATA_CRC)
            return bytes(frame[:8])
        except LinkError as e:
            raise LinkError(e.code, addr, "read: ")

    def write_reg(self, addr, data8):
        frame = P.data_frame(data8)
        self._drain()
        try:
            self._cmd(P.T_CLEAR, addr)
            self._cmd(P.T_WRITE, addr)
            try:
                self.port.write(frame)
            except Exception:
                raise LinkError(E_WRITE)
            if not self._poll(addr, P.F_WRITTEN):
                raise LinkError(E_NOT_WRITTEN)
        except LinkError as e:
            raise LinkError(e.code, addr, "write: ")


class PySerialPort:
    """pyserial port: a serial device (COM2 on Windows, /dev/ttyS1 on the machine's Linux PC),
    or any pyserial URL such as socket://host:port."""

    def __init__(self, url, baud=115200):
        import serial
        self.name = url
        self.s = serial.serial_for_url(url, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                       timeout=ANSWER_MS / 1000, write_timeout=1)
        self._timeout = ANSWER_MS / 1000
        try:
            self.s.dtr = True           # UsartInit.exe raises DTR and RTS on COM2
            self.s.rts = True
        except Exception:
            pass
        try:                            # Linux: answer each byte at once (fewer ms per poll)
            self.s.set_low_latency_mode(True)
        except Exception:
            pass

    def write(self, b):
        self.s.write(b)

    def read(self, n, timeout):
        if timeout == 0:
            k = self.s.in_waiting
            return self.s.read(min(n, k)) if k else b""
        if timeout != self._timeout:    # setting the timeout reconfigures the port (tcsetattr)
            self.s.timeout = self._timeout = timeout
        return self.s.read(n)

    def close(self):
        self.s.close()


# ---- serbridge.exe over TCP ----------------------------------------------------

BRIDGE_PORT = 5602
B_INFO, B_READ, B_WRITE, B_STATUS, B_RAW, B_DRAIN, B_BYE = 1, 2, 3, 4, 5, 6, 7
REQ = struct.Struct("<BBH8sI")      # cmd, addr, flags, data[8], arg  (16 bytes)
REP = struct.Struct("<iI")          # status, length; then `length` bytes


class BridgeLink:
    """Transactions done by serbridge.exe next to the board; one request per call."""

    def __init__(self, host, port=BRIDGE_PORT, poll_ms=POLL_MS, timeout=5.0):
        self.addr, self.poll_ms = (host, port), poll_ms
        self.sock = socket.create_connection(self.addr, timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        try:
            self._req(B_BYE)
        except Exception:
            pass
        self.sock.close()

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c:
                raise ConnectionError("serbridge closed the connection")
            b += c
        return b

    def _req(self, cmd, addr=0, data=b"", flags=0, arg=0):
        self.sock.sendall(REQ.pack(cmd, addr, flags, bytes(data).ljust(8, b"\0"), arg))
        status, n = REP.unpack(self._recv(REP.size))
        return status, self._recv(n) if n else b""

    def _check(self, status, addr, what):
        if status != OK:
            raise LinkError(status, addr, what)

    def info(self):
        st, d = self._req(B_INFO)
        return d.decode("ascii", "replace")

    def status(self, addr):
        st, d = self._req(B_STATUS, addr, arg=self.poll_ms)
        self._check(st, addr, "")
        return d[0]

    def read_reg(self, addr):
        st, d = self._req(B_READ, addr, arg=self.poll_ms)
        self._check(st, addr, "read: ")
        return d

    def write_reg(self, addr, data8):
        st, _ = self._req(B_WRITE, addr, bytes(data8), arg=self.poll_ms)
        self._check(st, addr, "write: ")

    def raw(self, data, nread):
        """Debug: send up to 8 raw bytes, read back up to `nread` bytes (500 ms)."""
        st, d = self._req(B_RAW, 0, bytes(data), flags=len(data), arg=nread)
        return d


def open_link(url, **kw):
    if url.startswith("tcp://"):
        host, _, port = url[6:].partition(":")
        return BridgeLink(host, int(port or BRIDGE_PORT), **kw)
    if url.startswith("serial://"):
        return SerialLink(PySerialPort(url[9:]), **kw)
    if url.startswith("sim://"):
        from .sim import MainboardSim, SimPort
        return SerialLink(SimPort(MainboardSim()), **kw)
    raise ValueError(f"unknown link url {url!r} (tcp://host:5602, serial://COM2, sim://)")
