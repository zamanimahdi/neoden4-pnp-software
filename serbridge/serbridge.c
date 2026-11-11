/*
 * serbridge - NeoDen4 mainboard serial bridge for the machine's own Windows XP PC.
 *
 * Opens the mainboard's serial port (COM2, 115200 8N1, DTR/RTS on, like
 * UsartInit.exe and NeoDen4.exe) and serves register transactions over TCP,
 * so the control program can run on any PC. The transactions are done here,
 * next to the board, byte by byte, exactly like NeoDen4.exe (CFunNode::readReg /
 * writeReg); see ../docs/SERBRIDGE.md and the author's protocol notes.
 *
 * Plain C, Win32 + Winsock only; 32-bit, runs on XP.
 *
 * usage: serbridge [--port 5602] [--bind 0.0.0.0] [--com COM2] [--baud 115200]
 *                  [--trace] [--serial-tcp host:port]
 *   --serial-tcp  use a TCP byte stream instead of the COM port (tests, simulator)
 */
#include <winsock2.h>
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VERSION        "1.0"
#define DEFAULT_PORT   5602
#define ANSWER_MS      500              /* rs232_readSyn timeout used by NeoDen4.exe */
#define MIN_POLLS      49               /* NeoDen4.exe polls the status up to 49 times */
#define DEFAULT_POLL_MS 300             /* ... we also keep polling at least this long */

/* request / reply (little-endian), see ../docs/SERBRIDGE.md */
enum { CMD_INFO = 1, CMD_READ = 2, CMD_WRITE = 3, CMD_STATUS = 4, CMD_RAW = 5, CMD_DRAIN = 6, CMD_BYE = 7 };
#pragma pack(push, 1)
typedef struct { unsigned char cmd, addr; unsigned short flags; unsigned char data[8]; unsigned int arg; } req_t;
typedef struct { int status; unsigned int len; } rep_t;
#pragma pack(pop)

/* result codes (shared with neomach/link.py) */
enum { R_OK = 1, E_UNKNOWN = -1, E_NOT_OPEN = -2, E_WRITE = -3, E_TIMEOUT = -4, E_CHECK = -5,
       E_NO_UPDATE = -6, E_NOT_WRITTEN = -7, E_DATA_SHORT = -8, E_DATA_CRC = -9 };

enum { T_STATUS = 0, T_CLEAR = 1, T_READ = 2, T_WRITE = 3 };
#define F_UPDATED 0x10
#define F_WRITTEN 0x40
#define LONG_ADDR 0x3F

static struct {
    char com[32];
    unsigned int baud;
    int trace;
    char stcp_host[64];                 /* --serial-tcp */
    int stcp_port;
    HANDLE h;                           /* COM port */
    SOCKET s;                           /* or TCP byte stream */
    DWORD last_timeout;
    unsigned long stray;
} g;

static void log_msg(const char *fmt, ...)
{
    va_list ap; SYSTEMTIME t;
    GetLocalTime(&t);
    printf("%02d:%02d:%02d.%03d ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    printf("\n"); fflush(stdout);
}

/* ---- CRC-16/XMODEM (poly 0x1021, init 0), as in NeoDen4.exe ---- */
static unsigned short crc16(const unsigned char *p, int n)
{
    unsigned short c = 0;
    int i;
    while (n--) {
        c ^= (unsigned short)(*p++ << 8);
        for (i = 0; i < 8; i++)
            c = (unsigned short)((c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1);
    }
    return c;
}

/* ---- serial line: COM port or TCP byte stream ---- */
static int ser_is_open(void)
{
    return g.h != INVALID_HANDLE_VALUE || g.s != INVALID_SOCKET;
}

static void ser_close(void)
{
    if (g.h != INVALID_HANDLE_VALUE) { CloseHandle(g.h); g.h = INVALID_HANDLE_VALUE; }
    if (g.s != INVALID_SOCKET) { closesocket(g.s); g.s = INVALID_SOCKET; }
}

static int ser_open(void)
{
    if (ser_is_open())
        return 1;
    if (g.stcp_port) {
        struct sockaddr_in a; struct hostent *he; int one = 1;
        memset(&a, 0, sizeof a);
        a.sin_family = AF_INET;
        a.sin_port = htons((unsigned short)g.stcp_port);
        a.sin_addr.s_addr = inet_addr(g.stcp_host);
        if (a.sin_addr.s_addr == INADDR_NONE) {
            if (!(he = gethostbyname(g.stcp_host))) { log_msg("serial-tcp: unknown host %s", g.stcp_host); return 0; }
            memcpy(&a.sin_addr, he->h_addr, 4);
        }
        g.s = socket(AF_INET, SOCK_STREAM, 0);
        if (g.s == INVALID_SOCKET || connect(g.s, (struct sockaddr *)&a, sizeof a) != 0) {
            log_msg("serial-tcp: cannot connect to %s:%d", g.stcp_host, g.stcp_port);
            ser_close();
            return 0;
        }
        setsockopt(g.s, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof one);
        log_msg("serial line = tcp %s:%d", g.stcp_host, g.stcp_port);
        return 1;
    } else {
        char path[48]; DCB dcb; COMMTIMEOUTS to;
        snprintf(path, sizeof path, "\\\\.\\%s", g.com);
        g.h = CreateFileA(path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (g.h == INVALID_HANDLE_VALUE) {
            log_msg("cannot open %s (error %lu) - is NeoDen4.exe still running?", g.com, GetLastError());
            return 0;
        }
        SetupComm(g.h, 512, 512);
        memset(&dcb, 0, sizeof dcb);
        dcb.DCBlength = sizeof dcb;
        GetCommState(g.h, &dcb);
        dcb.BaudRate = g.baud;
        dcb.ByteSize = 8;
        dcb.Parity = NOPARITY;
        dcb.StopBits = ONESTOPBIT;
        dcb.fBinary = TRUE;
        dcb.fParity = FALSE;
        dcb.fOutxCtsFlow = FALSE;
        dcb.fOutxDsrFlow = FALSE;
        dcb.fDsrSensitivity = FALSE;
        dcb.fOutX = FALSE;
        dcb.fInX = FALSE;
        dcb.fNull = FALSE;
        dcb.fAbortOnError = FALSE;
        dcb.fDtrControl = DTR_CONTROL_ENABLE;
        dcb.fRtsControl = RTS_CONTROL_ENABLE;
        if (!SetCommState(g.h, &dcb)) {
            log_msg("cannot configure %s (error %lu)", g.com, GetLastError());
            ser_close();
            return 0;
        }
        memset(&to, 0, sizeof to);
        to.ReadIntervalTimeout = MAXDWORD;      /* reads return at once until set_read_timeout */
        to.WriteTotalTimeoutMultiplier = 10;
        to.WriteTotalTimeoutConstant = 1000;
        SetCommTimeouts(g.h, &to);
        g.last_timeout = 0;
        EscapeCommFunction(g.h, SETDTR);
        EscapeCommFunction(g.h, SETRTS);
        PurgeComm(g.h, PURGE_TXCLEAR | PURGE_RXCLEAR | PURGE_TXABORT | PURGE_RXABORT);
        log_msg("serial line = %s %u 8N1", g.com, g.baud);
        return 1;
    }
}

static int ser_write(const unsigned char *p, int n)
{
    if (g.s != INVALID_SOCKET)
        return send(g.s, (const char *)p, n, 0) == n;
    {
        DWORD w = 0;
        return WriteFile(g.h, p, (DWORD)n, &w, NULL) && (int)w == n;
    }
}

/* Read up to n bytes, waiting at most timeout_ms in total (0 = only what is waiting). */
static int ser_read(unsigned char *p, int n, DWORD timeout_ms)
{
    int got = 0;
    DWORD t0 = GetTickCount();
    if (g.s != INVALID_SOCKET) {
        while (got < n) {
            fd_set fs; struct timeval tv; DWORD el = GetTickCount() - t0; int r;
            DWORD left = el >= timeout_ms ? 0 : timeout_ms - el;
            FD_ZERO(&fs); FD_SET(g.s, &fs);
            tv.tv_sec = left / 1000; tv.tv_usec = (left % 1000) * 1000;
            if (select(0, &fs, NULL, NULL, &tv) <= 0)
                break;
            r = recv(g.s, (char *)p + got, n - got, 0);
            if (r <= 0) { ser_close(); break; }
            got += r;
        }
        return got;
    }
    if (g.last_timeout != timeout_ms) {
        COMMTIMEOUTS to;
        memset(&to, 0, sizeof to);
        if (timeout_ms == 0)
            to.ReadIntervalTimeout = MAXDWORD;
        else
            to.ReadTotalTimeoutConstant = timeout_ms;
        to.WriteTotalTimeoutMultiplier = 10;
        to.WriteTotalTimeoutConstant = 1000;
        SetCommTimeouts(g.h, &to);
        g.last_timeout = timeout_ms;
    }
    while (got < n) {
        DWORD r = 0, el = GetTickCount() - t0;
        if (!ReadFile(g.h, p + got, (DWORD)(n - got), &r, NULL)) { ser_close(); break; }
        got += (int)r;
        if (timeout_ms == 0 || r == 0 || el >= timeout_ms)
            break;
    }
    return got;
}

/* ---- transactions, as in NeoDen4.exe ---- */
static int answer_ok(unsigned char sent, unsigned char ans, int prefix)
{
    unsigned char b[2];
    b[0] = sent; b[1] = (unsigned char)(ans & 0xF0);
    return (crc16(b, prefix ? 1 : 2) & 0xF) == (ans & 0xF);
}

static int xchg(unsigned char b, int prefix, unsigned char *flags)
{
    unsigned char a;
    if (!ser_write(&b, 1)) return E_WRITE;
    if (ser_read(&a, 1, ANSWER_MS) != 1) return E_TIMEOUT;
    if (!answer_ok(b, a, prefix)) return E_CHECK;
    if (flags) *flags = (unsigned char)(a & 0xF0);
    return R_OK;
}

static int cmd(int type, int addr, unsigned char *flags)
{
    int r;
    if (addr < LONG_ADDR)
        return xchg((unsigned char)(type << 6 | addr), 0, flags);
    /* NeoDen4.exe sends the plain 0x3F prefix for "clear" too (CIoSerial::clearLong) */
    r = xchg((unsigned char)((type == T_CLEAR ? 0 : type) << 6 | LONG_ADDR), 1, NULL);
    if (r != R_OK) return r;
    return xchg((unsigned char)addr, 0, flags);
}

static void drain(void)
{
    unsigned char junk[64];
    int n = ser_read(junk, sizeof junk, 0);
    if (n > 0) {
        g.stray += (unsigned long)n;
        if (g.trace) log_msg("  %d stray bytes", n);
    }
}

static int poll_flag(int addr, unsigned char flag, DWORD poll_ms)
{
    DWORD t0 = GetTickCount();
    int n = 0, r;
    unsigned char f;
    while (n < MIN_POLLS || GetTickCount() - t0 < poll_ms) {
        if ((r = cmd(T_STATUS, addr, &f)) != R_OK) return r;
        if (f & flag) return R_OK;
        n++;
    }
    return flag == F_UPDATED ? E_NO_UPDATE : E_NOT_WRITTEN;
}

static int read_reg(int addr, unsigned char out[8], DWORD poll_ms)
{
    unsigned char frame[9];
    int r;
    drain();
    if ((r = cmd(T_CLEAR, addr, NULL)) != R_OK) return r;
    if ((r = poll_flag(addr, F_UPDATED, poll_ms)) != R_OK) return r;
    if ((r = cmd(T_READ, addr, NULL)) != R_OK) return r;
    if (ser_read(frame, 9, ANSWER_MS) != 9) return E_DATA_SHORT;
    if ((crc16(frame, 8) & 0xFF) != frame[8]) return E_DATA_CRC;
    memcpy(out, frame, 8);
    return R_OK;
}

static int write_reg(int addr, const unsigned char data[8], DWORD poll_ms)
{
    unsigned char frame[9];
    int r;
    memcpy(frame, data, 8);
    frame[8] = (unsigned char)(crc16(data, 8) & 0xFF);
    drain();
    if ((r = cmd(T_CLEAR, addr, NULL)) != R_OK) return r;
    if ((r = cmd(T_WRITE, addr, NULL)) != R_OK) return r;
    if (!ser_write(frame, 9)) return E_WRITE;
    return poll_flag(addr, F_WRITTEN, poll_ms);
}

/* ---- TCP server ---- */
static int send_all(SOCKET c, const void *p, int n)
{
    const char *q = (const char *)p;
    while (n > 0) {
        int r = send(c, q, n, 0);
        if (r <= 0) return 0;
        q += r; n -= r;
    }
    return 1;
}

static int recv_all(SOCKET c, void *p, int n)
{
    char *q = (char *)p;
    while (n > 0) {
        int r = recv(c, q, n, 0);
        if (r <= 0) return 0;
        q += r; n -= r;
    }
    return 1;
}

static int reply(SOCKET c, int status, const void *data, unsigned int len)
{
    rep_t h;
    h.status = status; h.len = len;
    return send_all(c, &h, sizeof h) && (len == 0 || send_all(c, data, (int)len));
}

static void serve(SOCKET c)
{
    req_t q;
    unsigned char buf[256];
    char info[160];
    int r, n;
    for (;;) {
        DWORD poll_ms;
        if (!recv_all(c, &q, sizeof q)) return;
        poll_ms = q.arg ? q.arg : DEFAULT_POLL_MS;
        if (q.cmd == CMD_BYE) { reply(c, R_OK, NULL, 0); return; }
        if (q.cmd == CMD_INFO) {
            n = snprintf(info, sizeof info, "serbridge %s line=%s baud=%u open=%d stray=%lu",
                         VERSION, g.stcp_port ? "tcp" : g.com, g.baud, ser_open(), g.stray);
            if (!reply(c, R_OK, info, (unsigned int)n)) return;
            continue;
        }
        if (!ser_open()) { if (!reply(c, E_NOT_OPEN, NULL, 0)) return; continue; }
        switch (q.cmd) {
        case CMD_READ:
            r = read_reg(q.addr, buf, poll_ms);
            if (g.trace) log_msg("read  0x%02x -> %d", q.addr, r);
            if (!reply(c, r, buf, r == R_OK ? 8 : 0)) return;
            break;
        case CMD_WRITE:
            r = write_reg(q.addr, q.data, poll_ms);
            if (g.trace) log_msg("write 0x%02x %02x %02x %02x %02x %02x %02x %02x %02x -> %d", q.addr,
                                 q.data[0], q.data[1], q.data[2], q.data[3], q.data[4], q.data[5],
                                 q.data[6], q.data[7], r);
            if (!reply(c, r, NULL, 0)) return;
            break;
        case CMD_STATUS:
            drain();
            r = cmd(T_STATUS, q.addr, buf);
            if (!reply(c, r, buf, r == R_OK ? 1 : 0)) return;
            break;
        case CMD_RAW:                   /* debug: send flags bytes of data, read up to arg bytes */
            n = q.flags > 8 ? 8 : q.flags;
            if (n && !ser_write(q.data, n)) { if (!reply(c, E_WRITE, NULL, 0)) return; break; }
            n = ser_read(buf, q.arg > sizeof buf ? (int)sizeof buf : (int)q.arg, q.arg ? ANSWER_MS : 0);
            if (!reply(c, R_OK, buf, (unsigned int)(n < 0 ? 0 : n))) return;
            break;
        case CMD_DRAIN:
            n = ser_read(buf, sizeof buf, 0);
            if (!reply(c, R_OK, buf, (unsigned int)(n < 0 ? 0 : n))) return;
            break;
        default:
            if (!reply(c, E_UNKNOWN, NULL, 0)) return;
        }
    }
}

static void usage(void)
{
    printf("serbridge %s - NeoDen4 mainboard serial bridge\n"
           "usage: serbridge [--port 5602] [--bind 0.0.0.0] [--com COM2] [--baud 115200]\n"
           "                 [--trace] [--serial-tcp host:port]\n", VERSION);
}

int main(int argc, char **argv)
{
    WSADATA wsa;
    SOCKET srv;
    struct sockaddr_in a;
    int i, port = DEFAULT_PORT, one = 1;
    const char *bind_addr = "0.0.0.0";

    strcpy(g.com, "COM2");
    g.baud = 115200;
    g.h = INVALID_HANDLE_VALUE;
    g.s = INVALID_SOCKET;
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bind") && i + 1 < argc) bind_addr = argv[++i];
        else if (!strcmp(argv[i], "--com") && i + 1 < argc) snprintf(g.com, sizeof g.com, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--baud") && i + 1 < argc) g.baud = (unsigned int)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--trace")) g.trace = 1;
        else if (!strcmp(argv[i], "--serial-tcp") && i + 1 < argc) {
            char *c;
            snprintf(g.stcp_host, sizeof g.stcp_host, "%s", argv[++i]);
            if (!(c = strchr(g.stcp_host, ':'))) { usage(); return 2; }
            *c = 0;
            g.stcp_port = atoi(c + 1);
        } else { usage(); return 2; }
    }
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { printf("WSAStartup failed\n"); return 1; }
    log_msg("serbridge %s", VERSION);
    ser_open();                         /* open now so a busy port shows at once; retried per request */

    srv = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = inet_addr(bind_addr);
    if (bind(srv, (struct sockaddr *)&a, sizeof a) != 0 || listen(srv, 1) != 0) {
        log_msg("cannot listen on %s:%d", bind_addr, port);
        return 1;
    }
    log_msg("listening on %s:%d", bind_addr, port);
    for (;;) {
        struct sockaddr_in ca; int cl = sizeof ca;
        SOCKET c = accept(srv, (struct sockaddr *)&ca, &cl);
        if (c == INVALID_SOCKET) continue;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof one);
        log_msg("client %s connected", inet_ntoa(ca.sin_addr));
        serve(c);
        closesocket(c);
        log_msg("client disconnected");
    }
}
