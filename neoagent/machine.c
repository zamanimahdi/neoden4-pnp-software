/* neoagent: machine control for the web UI, through serbridge on localhost (docs/SERBRIDGE.md).
 *
 * Mirrors neomach/board.py (same payloads, same safety rules, from NeoDen4.exe):
 *   - every XY or rail move raises all nozzles first;
 *   - a nozzle is not lowered below 1 mm while XY is not idle;
 *   - XY must be homed; step loss is reported; XY targets are range-checked;
 *   - registers 6 (config) and 7 (switch) are kept as shadow copies and written whole;
 *   - the board does not report XY: the last target is kept here, shared by all clients.
 * Only one client controls the machine at a time: POST /api/mc/lock?client=<id> (renew every
 * few seconds; it expires after 15 s); every command that moves or switches needs it. */
#include <math.h>
#include "neoagent.h"

#define XY_STEP 0.009525
#define RAIL_STEP 0.0127
#define X_OFF 43700
#define X_MAX_STEPS 43700
#define Y_MAX_STEPS 42000
#define Z_SAFE 1.0
#define SPEED_DEF 0x96
#define LOCK_MS 15000

#pragma pack(push, 1)
typedef struct { unsigned char cmd, addr; unsigned short flags; unsigned char data[8]; unsigned int arg; } sbreq_t;
typedef struct { int status; unsigned int len; } sbrep_t;
#pragma pack(pop)

static SOCKET sb = INVALID_SOCKET;
static CRITICAL_SECTION mlock;
static LONG minit;
static unsigned char cfg[8], sw[8];
static int homed;
static double pos_x, pos_y, z[5], ang[5];
static char owner[64];
static DWORD owner_until;
static volatile LONG cancel_flag;

static void ensure_init(void)
{
    if (InterlockedCompareExchange(&minit, 1, 0) == 0) {
        InitializeCriticalSection(&mlock);
        cfg[0] = 80; cfg[1] = 9;              /* like NeoDen4.exe (SETTINGS_MANUAL_SPEC 1.7): never send XY speed 0 */
        minit = 2;
    }
    while (minit != 2) Sleep(1);
}

static int sbio(void *p, int n, int s)
{
    char *c = (char *)p;
    while (n > 0) { int k = s ? send(sb, c, n, 0) : recv(sb, c, n, 0); if (k <= 0) return 0; c += k; n -= k; }
    return 1;
}

/* one serbridge transaction; returns status (1 ok, <0 error), data out (max 64 bytes) */
static int sbcall(int cmd, int addr, const unsigned char *data8, unsigned int arg, unsigned char *out, int *outn, char *err, int errn)
{
    sbreq_t q;
    sbrep_t r;
    int attempt;
    for (attempt = 0; attempt < 2; attempt++) {
        if (sb == INVALID_SOCKET) {
            struct sockaddr_in a;
            int to = 10000;
            sb = socket(AF_INET, SOCK_STREAM, 0);
            memset(&a, 0, sizeof a);
            a.sin_family = AF_INET; a.sin_port = htons((unsigned short)A.sb_port); a.sin_addr.s_addr = inet_addr("127.0.0.1");
            if (connect(sb, (struct sockaddr *)&a, sizeof a)) {
                closesocket(sb); sb = INVALID_SOCKET;
                _snprintf(err, errn, "serbridge not reachable on port %d", A.sb_port);
                return -100;
            }
            setsockopt(sb, SOL_SOCKET, SO_RCVTIMEO, (const char *)&to, sizeof to);
        }
        memset(&q, 0, sizeof q);
        q.cmd = (unsigned char)cmd; q.addr = (unsigned char)addr; q.arg = arg;
        if (data8) memcpy(q.data, data8, 8);
        if (sbio(&q, sizeof q, 1) && sbio(&r, sizeof r, 0)) {
            unsigned char buf[256];
            if (r.len > sizeof buf || (r.len && !sbio(buf, (int)r.len, 0))) goto broken;
            if (out) { memcpy(out, buf, r.len); if (outn) *outn = (int)r.len; }
            if (r.status != 1) {
                static const char *msg[] = { "", "", "serial port not open (NeoDen4.exe running?)", "serial write failed",
                                             "no answer from the board", "answer check wrong", "update flag never set",
                                             "written flag never set", "data frame incomplete", "data frame CRC wrong" };
                _snprintf(err, errn, "reg %d: %s (%d)", addr, (r.status <= -2 && r.status >= -9) ? msg[-r.status] : "error", r.status);
            }
            return r.status;
        }
broken:
        closesocket(sb); sb = INVALID_SOCKET;
    }
    _snprintf(err, errn, "serbridge connection lost");
    return -101;
}

static int rd(int addr, unsigned char d[8], char *err, int errn)
{
    int n = 0;
    unsigned char buf[64];
    if (sbcall(2, addr, NULL, 0, buf, &n, err, errn) != 1) return 0;
    if (n < 8) { _snprintf(err, errn, "short read"); return 0; }
    memcpy(d, buf, 8);
    return 1;
}

static int wr(int addr, const unsigned char d[8], char *err, int errn) { return sbcall(3, addr, d, 0, NULL, NULL, err, errn) == 1; }

static void put16(unsigned char *p, int v) { p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8); }
static void put32(unsigned char *p, int v) { put16(p, v); put16(p + 2, v >> 16); }
static int iround(double v) { return (int)(v < 0 ? -floor(-v + 0.5) : floor(v + 0.5)); }

static const char *xy_name(int s)
{
    switch (s) { case 0: return "idle"; case 1: return "not homed"; case 2: return "busy";
                 case 3: return "step loss X"; case 4: return "step loss Y"; case 5: return "step loss XY";
                 default: return "unknown"; }
}

static int xy_state(char *err, int errn)
{
    unsigned char d[8];
    return rd(5, d, err, errn) ? d[0] : -1;
}

/* poll until done(); 10 ms period; cancel flag; returns 1 ok, 0 error (err filled) */
typedef int (*done_fn)(void *ctx, char *err, int errn);

static int wait_for(const char *what, int timeout_ms, done_fn done, void *ctx, char *err, int errn)
{
    DWORD t0 = GetTickCount();
    for (;;) {
        int r = done(ctx, err, errn);
        if (r < 0) return 0;
        if (r) return 1;
        if (InterlockedExchange(&cancel_flag, 0)) { _snprintf(err, errn, "%s: cancelled", what); return 0; }
        if ((int)(GetTickCount() - t0) > timeout_ms) { _snprintf(err, errn, "%s: not finished after %d s", what, timeout_ms / 1000); return 0; }
        LeaveCriticalSection(&mlock);           /* let status reads and STOP through while waiting */
        Sleep(10);
        EnterCriticalSection(&mlock);
    }
}

static int done_xy_idle(void *ctx, char *err, int errn)
{
    int s = xy_state(err, errn);
    (void)ctx;
    if (s < 0) return -1;
    if (s == 1) { _snprintf(err, errn, "XY is not homed: home first"); return -1; }
    if (s >= 3 && s <= 5) { _snprintf(err, errn, "XY %s: run XY correction, then move again", xy_name(s)); return -1; }
    return s == 0;
}

static int done_xy_idle_any(void *ctx, char *err, int errn) { int s = xy_state(err, errn); (void)ctx; return s < 0 ? -1 : s == 0; }

static int done_nozzle(void *ctx, char *err, int errn)
{
    unsigned char d[8];
    int n = (int)(INT_PTR)ctx;
    if (!rd(0, d, err, errn)) return -1;
    return n == 0 ? (d[4] & 15) == 15 : (d[4] >> (n - 1)) & 1;
}

static int done_rail(void *ctx, char *err, int errn)
{
    unsigned char d[8];
    (void)ctx;
    if (!rd(5, d, err, errn)) return -1;
    return (d[1] >> 1) & 1;
}

static int depth(int n, double mm, int speed, int wait, char *err, int errn)
{
    unsigned char d[8] = { 0 };
    put16(d, iround(mm * 1000)); d[2] = (unsigned char)speed; d[3] = (unsigned char)n;
    if (!wr(2, d, err, errn)) return 0;
    z[n] = mm;
    return !wait || wait_for("nozzle", 5000, done_nozzle, (void *)(INT_PTR)n, err, errn);
}

static int raise_all(char *err, int errn)
{
    int n;
    for (n = 1; n <= 4; n++) if (!depth(n, 0.0, SPEED_DEF, 1, err, errn)) return 0;
    return 1;
}

static int nozzles_up(void) { int n; for (n = 1; n <= 4; n++) if (z[n] > Z_SAFE) return 0; return 1; }

static int write_cfg(char *err, int errn) { return wr(6, cfg, err, errn); }

static int sw_pulse(int byte, int bit, char *err, int errn)
{
    int ok;
    sw[byte] |= (unsigned char)bit;
    ok = wr(7, sw, err, errn);
    sw[byte] &= (unsigned char)~bit;
    return ok;
}

static int sw_bit(int byte, int bit, int on, char *err, int errn)
{
    if (on) sw[byte] |= (unsigned char)bit; else sw[byte] &= (unsigned char)~bit;
    return wr(7, sw, err, errn);
}

static int xy_target(double x, double y, int *sx, int *sy, char *err, int errn)
{
    *sx = iround(x / XY_STEP); *sy = iround(y / XY_STEP);
    if (*sx < 0 || *sx > X_MAX_STEPS || *sy < 0 || *sy > Y_MAX_STEPS) {
        _snprintf(err, errn, "XY (%.3f, %.3f) mm outside 0..%.2f x 0..%.2f", x, y, X_MAX_STEPS * XY_STEP, Y_MAX_STEPS * XY_STEP);
        return 0;
    }
    return 1;
}

static int move1(double x, double y, int wait, char *err, int errn)
{
    unsigned char d[8];
    int sx, sy, s;
    if (!xy_target(x, y, &sx, &sy, err, errn)) return 0;
    s = xy_state(err, errn);
    if (s < 0) return 0;
    if (s == 1) { _snprintf(err, errn, "XY is not homed: home first"); return 0; }
    if (s >= 3 && s <= 5) { _snprintf(err, errn, "XY %s: run XY correction first", xy_name(s)); return 0; }
    if (!nozzles_up() && !raise_all(err, errn)) return 0;
    put32(d, sx - X_OFF); put32(d + 4, sy);
    if (!wr(8, d, err, errn)) return 0;
    pos_x = x; pos_y = y;
    return !wait || wait_for("XY move", 30000, done_xy_idle, NULL, err, errn);
}

static int move_xy(double x, double y, int slow, int speed, char *err, int errn)
{
    int old = cfg[0], ok = 1, sx, sy;
    if (!xy_target(x, y, &sx, &sy, err, errn)) return 0;
    if (speed > 0) { cfg[0] = (unsigned char)speed; cfg[1] = cfg[1] ? cfg[1] : 9; if (!write_cfg(err, errn)) return 0; }
    if (slow) {
        ok = move1(x - 1 < 0 ? 0 : x - 1, y - 1 < 0 ? 0 : y - 1, 1, err, errn);
        if (ok) { cfg[0] = 10; ok = write_cfg(err, errn); }
    }
    if (ok) ok = move1(x, y, 0, err, errn);
    if (cfg[0] != old) { char e2[200]; cfg[0] = (unsigned char)old; write_cfg(e2, sizeof e2); }
    return ok && wait_for("XY move", 30000, done_xy_idle, NULL, err, errn);
}

/* ---------------------------------------------------------------- HTTP glue */
static int locked_by(const char *client)
{
    if (!owner[0] || (int)(GetTickCount() - owner_until) > 0) return 0;
    return client && !strcmp(owner, client);
}

int machine_lock(const char *client, int release, char *out, int max)
{
    ensure_init();
    EnterCriticalSection(&mlock);
    if (release) { if (locked_by(client)) owner[0] = 0; }
    else if (!owner[0] || (int)(GetTickCount() - owner_until) > 0 || !strcmp(owner, client)) {
        if (strcmp(owner, client)) log_msg("machine control taken by %s", client);
        strncpy(owner, client, sizeof owner - 1);
        owner_until = GetTickCount() + LOCK_MS;
    }
    _snprintf(out, max, "{\"ok\":%s,\"owner\":", locked_by(client) || release ? "true" : "false");
    json_str(out + strlen(out), max - (int)strlen(out), (owner[0] && (int)(GetTickCount() - owner_until) <= 0) ? owner : "");
    strcat(out, "}");
    LeaveCriticalSection(&mlock);
    return 1;
}

void machine_cancel(void) { InterlockedExchange(&cancel_flag, 1); }

/* status JSON (no lock needed) */
int machine_status(char *out, int max)
{
    unsigned char s5[8], s0[8];
    char err[200] = "", info[200] = "";
    int ok5, ok0, n = 0, i;
    ensure_init();
    EnterCriticalSection(&mlock);
    {   unsigned char buf[200]; int bn = 0;
        if (sbcall(1, 0, NULL, 0, buf, &bn, err, sizeof err) == 1) { buf[bn < 199 ? bn : 199] = 0; strcpy(info, (char *)buf); } }
    ok5 = rd(5, s5, err, sizeof err);
    ok0 = ok5 && rd(0, s0, err, sizeof err);
    n = _snprintf(out, max, "{\"ok\":%s,\"bridge\":", ok5 && ok0 ? "true" : "false");
    n += json_str(out + n, max - n, info);
    n += _snprintf(out + n, max - n, ",\"error\":");
    n += json_str(out + n, max - n, ok5 && ok0 ? "" : err);
    if (ok5) {
        if (s5[0] == 1) homed = 0;
        n += _snprintf(out + n, max - n, ",\"xy_state\":%d,\"xy_state_name\":\"%s\",\"rail_done\":%s,\"input0\":%s,\"input1\":%s",
                       s5[0], xy_name(s5[0]), (s5[1] >> 1) & 1 ? "true" : "false", s5[3] & 1 ? "true" : "false", (s5[3] >> 1) & 1 ? "false" : "true");
    }
    if (ok0) {
        n += _snprintf(out + n, max - n, ",\"idle\":[");
        for (i = 0; i < 4; i++) n += _snprintf(out + n, max - n, "%s%s", i ? "," : "", (s0[4] >> i) & 1 ? "true" : "false");
        n += _snprintf(out + n, max - n, "],\"vacuum\":[%d,%d,%d,%d]", (signed char)s0[0], (signed char)s0[1], (signed char)s0[2], (signed char)s0[3]);
    }
    if (homed) n += _snprintf(out + n, max - n, ",\"pos\":[%.3f,%.3f]", pos_x, pos_y);
    else n += _snprintf(out + n, max - n, ",\"pos\":null");
    n += _snprintf(out + n, max - n, ",\"z\":[%.3f,%.3f,%.3f,%.3f],\"angle\":[%.2f,%.2f,%.2f,%.2f]",
                   z[1], z[2], z[3], z[4], ang[1], ang[2], ang[3], ang[4]);
    n += _snprintf(out + n, max - n, ",\"light\":%s,\"beep\":%s,\"board_ready\":%s,\"xy_speed\":%d,\"rail_speed\":%d,\"owner\":",
                   sw[4] & 1 ? "true" : "false", sw[5] & 1 ? "true" : "false", sw[5] & 2 ? "true" : "false", cfg[0], cfg[3]);
    n += json_str(out + n, max - n, (owner[0] && (int)(GetTickCount() - owner_until) <= 0) ? owner : "");
    n += _snprintf(out + n, max - n, "}");
    LeaveCriticalSection(&mlock);
    return n;
}

static double qd(const char *q, const char *k, double def, int *have)
{
    char v[64];
    if (!query_get(q, k, v, sizeof v) || !v[0]) { if (have) *have = 0; return def; }
    if (have) *have = 1;
    return atof(v);
}

/* one machine command; returns 1 ok / 0 error (err filled). extra JSON goes to out. */
int machine_cmd(const char *cmd, const char *q, const char *client, char *out, int max, char *err, int errn)
{
    int ok = 0, have, n = (int)qd(q, "n", 0, NULL);
    ensure_init();
    out[0] = 0;
    EnterCriticalSection(&mlock);
    if (!locked_by(client)) {
        _snprintf(err, errn, "take control first (another client%s%s has it, or the lock expired)", owner[0] ? ": " : "", owner);
        LeaveCriticalSection(&mlock);
        return 0;
    }
    owner_until = GetTickCount() + LOCK_MS;
    cancel_flag = 0;

    if (!strcmp(cmd, "home")) {
        ok = raise_all(err, errn) && sw_pulse(0, 1, err, errn) && wait_for("XY homing", 60000, done_xy_idle_any, NULL, err, errn);
        if (ok) { homed = 1; pos_x = 0; pos_y = Y_MAX_STEPS * XY_STEP; }
    } else if (!strcmp(cmd, "correct")) {
        ok = raise_all(err, errn) && sw_pulse(0, 2, err, errn) && wait_for("XY correction", 30000, done_xy_idle_any, NULL, err, errn);
    } else if (!strcmp(cmd, "move") || !strcmp(cmd, "rel")) {
        double x = qd(q, "x", 0, NULL), y = qd(q, "y", 0, NULL);
        if (!homed) _snprintf(err, errn, "position unknown: home first");
        else {
            if (!strcmp(cmd, "rel")) { x += pos_x; y += pos_y; }
            ok = move_xy(x, y, (int)qd(q, "slow", 0, NULL), (int)qd(q, "speed", 0, NULL), err, errn);
        }
    } else if (!strcmp(cmd, "z")) {
        double mm = qd(q, "mm", 0, NULL);
        if (n < 1 || n > 4 || mm < 0 || mm > 13) _snprintf(err, errn, "nozzle 1..4, Z 0..12 mm (13 for the stamp)");
        else {
            int s = mm > Z_SAFE ? xy_state(err, errn) : 0;
            if (s < 0) ;
            else if (s != 0) _snprintf(err, errn, "nozzle %d not lowered: XY is %s", n, xy_name(s));
            else ok = depth(n, mm, (int)qd(q, "speed", SPEED_DEF, NULL), (int)qd(q, "wait", 1, NULL), err, errn);
        }
    } else if (!strcmp(cmd, "rotate")) {
        unsigned char d[8] = { 0 };
        double deg = qd(q, "deg", 0, NULL);
        if (n < 1 || n > 4) _snprintf(err, errn, "nozzle 1..4");
        else {
            put16(d, -iround(deg * 10)); d[2] = (unsigned char)(int)qd(q, "speed", SPEED_DEF, NULL); d[3] = (unsigned char)n;
            ok = wr(1, d, err, errn) && (!(int)qd(q, "wait", 1, NULL) || wait_for("nozzle", 5000, done_nozzle, (void *)(INT_PTR)n, err, errn));
            if (ok) ang[n] = deg;
        }
    } else if (!strcmp(cmd, "raise")) {
        ok = raise_all(err, errn);
    } else if (!strcmp(cmd, "valve")) {
        unsigned char d[8] = { 0 };
        d[0] = (unsigned char)(int)qd(q, "state", 0, NULL); d[1] = (unsigned char)(int)qd(q, "ch", 0, NULL);
        ok = wr(3, d, err, errn);
    } else if (!strcmp(cmd, "led")) {
        unsigned char d[8] = { 0 };
        d[0] = (unsigned char)(int)qd(q, "v", 0, NULL);
        ok = wr(4, d, err, errn);
    } else if (!strcmp(cmd, "light")) ok = sw_bit(4, 1, (int)qd(q, "on", 0, NULL), err, errn);
    else if (!strcmp(cmd, "beep")) ok = sw_bit(5, 1, (int)qd(q, "on", 0, NULL), err, errn);
    else if (!strcmp(cmd, "board_ready")) ok = sw_bit(5, 2, (int)qd(q, "on", 0, NULL), err, errn);
    else if (!strcmp(cmd, "xy_speed")) {
        cfg[0] = (unsigned char)(int)qd(q, "v", 80, NULL); cfg[1] = (unsigned char)(int)qd(q, "force", 9, NULL);
        ok = write_cfg(err, errn);
    } else if (!strcmp(cmd, "rail_speed")) { cfg[3] = (unsigned char)(int)qd(q, "v", 200, NULL); ok = write_cfg(err, errn); }
    else if (!strcmp(cmd, "rail_move")) {
        unsigned char d[8] = { 0 };
        put32(d + 4, iround(qd(q, "mm", 0, NULL) / RAIL_STEP));
        ok = (nozzles_up() || raise_all(err, errn)) && wr(9, d, err, errn)
             && (!(int)qd(q, "wait", 1, NULL) || wait_for("rail move", 30000, done_rail, NULL, err, errn));
    } else if (!strcmp(cmd, "rail_stop")) ok = sw_pulse(1, 2, err, errn);
    else if (!strcmp(cmd, "feed")) {
        unsigned char d[8] = { 0 };
        if (n < 1 || n > 50) _snprintf(err, errn, "feeder 1..50");
        else { d[0] = (unsigned char)(int)qd(q, "p0", 0, NULL); d[1] = (unsigned char)(int)qd(q, "p1", 0, NULL); d[7] = (unsigned char)(int)qd(q, "mode", 0, NULL);
               ok = wr(0x46 + n, d, err, errn); }
    } else if (!strcmp(cmd, "feeder")) {
        unsigned char d[8];
        if (n < 1 || n > 50) _snprintf(err, errn, "feeder 1..50");
        else if ((ok = rd(0x14 + n, d, err, errn)))
            _snprintf(out, max, "\"done\":%s,\"failed\":%s,\"remaining\":%d", (d[0] >> 5) & 1 ? "true" : "false", (d[0] >> 6) & 1 ? "true" : "false", d[0] & 31);
    } else if (!strcmp(cmd, "peel")) {
        unsigned char d[8] = { 0 };
        int a = (int)qd(q, "a", 0, NULL), b = (int)qd(q, "b", 0, NULL);
        if (n >= 1 && n <= 19) { d[0] = (unsigned char)n; d[1] = (unsigned char)b; d[2] = (unsigned char)a; ok = wr(12, d, err, errn); }
        else if (n >= 20 && n <= 48) { d[0] = (unsigned char)(n - 0x13); d[1] = (unsigned char)b; d[2] = (unsigned char)a; ok = wr(14, d, err, errn); }
        else _snprintf(err, errn, "peel feeder 1..48");
    } else if (!strcmp(cmd, "vib")) {
        unsigned char d[8] = { 0 };
        d[0] = (unsigned char)(int)qd(q, "a", 0, NULL); d[1] = (unsigned char)(int)qd(q, "b", 0, NULL);
        ok = wr(16, d, err, errn);
    } else if (!strcmp(cmd, "vib_feeder")) {
        int on = (int)qd(q, "on", 0, NULL);
        cfg[5] = on ? (unsigned char)(int)qd(q, "v", 0, NULL) : 0; cfg[6] = (unsigned char)(int)qd(q, "p", 0, NULL);
        ok = write_cfg(err, errn);
    } else if (!strcmp(cmd, "reg_read")) {
        unsigned char d[8];
        int addr = (int)qd(q, "addr", -1, &have);
        if (!have || addr < 0 || addr > 0x78) _snprintf(err, errn, "addr 0..0x78");
        else if ((ok = rd(addr, d, err, errn)))
            _snprintf(out, max, "\"data\":\"%02x%02x%02x%02x%02x%02x%02x%02x\"", d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
    } else if (!strcmp(cmd, "reg_write")) {
        unsigned char d[8] = { 0 };
        char hx[32];
        int addr = (int)qd(q, "addr", -1, &have), i;
        query_get(q, "data", hx, sizeof hx);
        if (!have || addr < 0 || addr > 0x78 || strlen(hx) != 16) _snprintf(err, errn, "addr 0..0x78, data = 16 hex digits");
        else {
            for (i = 0; i < 8; i++) { char t[3] = { hx[2 * i], hx[2 * i + 1], 0 }; d[i] = (unsigned char)strtol(t, NULL, 16); }
            if (addr == 6) memcpy(cfg, d, 8);
            if (addr == 7) memcpy(sw, d, 8);
            ok = wr(addr, d, err, errn);
        }
    } else _snprintf(err, errn, "unknown machine command %s", cmd);
    if (!ok && strcmp(cmd, "feeder") && strcmp(cmd, "reg_read")) log_msg("machine %s: %s", cmd, err);
    LeaveCriticalSection(&mlock);
    return ok;
}
