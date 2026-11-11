/*
 * camserver - NeoDen4 camera server for the machine's own Windows XP PC.
 *
 * Loads the machine's camera DLL (old camera: imgdll.dll + CGUSB2.dll,
 * new camera: NeodenCamera.dll) exactly like NeoDen4.exe does, and serves
 * the two cameras over TCP so the vision / UI program can run on any PC.
 *
 * Plain C, Win32 + Winsock 1.1/2 only; 32-bit (the DLLs are x86).
 * See ../docs/CAMSERVER.md for the protocol.
 *
 * usage: camserver [--port 5600] [--bind 0.0.0.0] [--dir <dll folder>] [--old | --new]
 */
#include <winsock2.h>
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VERSION        "1.0"
#define DEFAULT_PORT   5600
#define SENSOR_W       1280             /* both cameras: 1280 x 1024 sensor */
#define SENSOR_H       1024
#define MAX_PIXELS     (SENSOR_W * SENSOR_H)

/* request / reply (little-endian) */
enum {
    CMD_INFO = 1, CMD_EXPOSURE = 2, CMD_GAIN = 3, CMD_WINDOW = 4,
    CMD_LED = 5, CMD_GRAB = 6, CMD_RESET = 7, CMD_BYE = 8
};
#pragma pack(push, 1)
typedef struct { unsigned char cmd, cam; unsigned short flags; int a, b, c; } req_t;
typedef struct { int status; unsigned int len; } rep_t;
#pragma pack(pop)

/* logical cameras: 0 = up-looking fixed camera (CImgBoard), 1 = head camera (CImgHead);
 * cam | 0x80 = raw DLL camera id */
#define CAM_UP   0
#define CAM_HEAD 1

/* ---- old camera: imgdll.dll (CGImagetech CGU2-130M via CGUSB2.dll) ---- */
typedef int (__cdecl *old_init_t)(void);
typedef int (__cdecl *old_set_t)(int cam, int mode, int exposure, int gain);
typedef int (__cdecl *old_setsize_t)(int cam, int w, int h);
typedef int (__cdecl *old_capture_t)(int cam, void *bgr);          /* w*h*3 bytes, 1 s timeout */

/* ---- new camera: NeodenCamera.dll (Cypress FX3) ---- */
typedef int  (__cdecl *new_init_t)(void);
typedef int  (__cdecl *new_id_u16_t)(int id, unsigned short v);
typedef int  (__cdecl *new_id_t)(int id);
typedef int  (__cdecl *new_id_u16x2_t)(int id, unsigned short a, unsigned short b);
typedef char (__cdecl *new_readasy_t)(int id, void *buf, unsigned int len, DWORD timeout_ms, char trigger);

static struct {
    int backend;                        /* 0 none, 1 old, 2 new */
    int ncams;
    HMODULE dll;
    old_init_t old_init; old_set_t old_set; old_setsize_t old_setsize; old_capture_t old_capture;
    new_init_t new_init; new_id_u16_t new_exp, new_gain, new_led; new_id_t new_reset;
    new_id_u16x2_t new_lt, new_wh; new_readasy_t new_readasy;
    struct { int exp, gain, w, h; } cam[2];   /* per logical camera */
} g;

static unsigned char *frame_buf;        /* old: BGR, new: gray */

static void log_msg(const char *fmt, ...)
{
    va_list ap; SYSTEMTIME t;
    GetLocalTime(&t);
    printf("%02d:%02d:%02d.%03d ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
    printf("\n"); fflush(stdout);
}

static void *need(const char *name)
{
    void *p = (void *)GetProcAddress(g.dll, name);
    if (!p) log_msg("missing export %s", name);
    return p;
}

static int load_old(const char *dir)
{
    char path[MAX_PATH];
    _snprintf(path, sizeof path, "%s\\imgdll.dll", dir);
    if (!(g.dll = LoadLibraryA(path))) return 0;
    g.old_init = (old_init_t)need("img_init");
    g.old_set = (old_set_t)need("img_set");
    g.old_setsize = (old_setsize_t)need("img_setSize");
    g.old_capture = (old_capture_t)need("img_capture");
    if (!g.old_init || !g.old_set || !g.old_setsize || !g.old_capture || (g.ncams = g.old_init()) <= 0) {
        FreeLibrary(g.dll); g.dll = NULL; return 0;
    }
    g.backend = 1;
    return 1;
}

static int load_new(const char *dir)
{
    char path[MAX_PATH];
    _snprintf(path, sizeof path, "%s\\NeodenCamera.dll", dir);
    if (!(g.dll = LoadLibraryA(path))) return 0;
    g.new_init = (new_init_t)need("img_init");
    g.new_exp = (new_id_u16_t)need("img_set_exp");
    g.new_gain = (new_id_u16_t)need("img_set_gain");
    g.new_led = (new_id_u16_t)need("img_led");
    g.new_reset = (new_id_t)need("img_reset");
    g.new_lt = (new_id_u16x2_t)need("img_set_lt");
    g.new_wh = (new_id_u16x2_t)need("img_set_wh");
    g.new_readasy = (new_readasy_t)need("img_readAsy");
    if (!g.new_init || !g.new_exp || !g.new_gain || !g.new_led || !g.new_reset || !g.new_lt
        || !g.new_wh || !g.new_readasy || (g.ncams = g.new_init()) <= 0) {
        FreeLibrary(g.dll); g.dll = NULL; return 0;
    }
    g.backend = 2;
    return 1;
}

/* DLL camera id of a logical camera (old: up 0 / head 1, new: up 5 / head 1, like NeoDen4.exe) */
static int dll_id(int cam)
{
    if (cam & 0x80) return cam & 0x7f;
    if (g.backend == 1) return cam == CAM_UP ? 0 : 1;
    return cam == CAM_UP ? 5 : 1;
}

/* settings slot of a camera: 0 = up camera, 1 = head (or any other raw id) */
static int slot(int cam) { return dll_id(cam) == dll_id(CAM_UP) ? 0 : 1; }

static int old_apply(int cam)
{
    /* img_set(cam, mode, exposure, gain): mode = vendor request 0xB0 value; NeoDen4 uses 1 for the
     * up camera and 2 for the head camera */
    int s = slot(cam), id = dll_id(cam);
    return g.old_set(id, id == 0 ? 1 : 2, g.cam[s].exp, g.cam[s].gain);
}

static int do_exposure(int cam, int v)
{
    if (v < 1) v = 1;
    g.cam[slot(cam)].exp = v;
    if (g.backend == 1) return old_apply(cam);
    return g.new_exp(dll_id(cam), (unsigned short)v);
}

static int do_gain(int cam, int v)
{
    g.cam[slot(cam)].gain = v;
    if (g.backend == 1) return old_apply(cam);
    return g.new_gain(dll_id(cam), (unsigned short)v);
}

static int do_window(int cam, int w, int h)
{
    int s = slot(cam), r;
    if (w < 16 || h < 16 || w > SENSOR_W || h > SENSOR_H) return 0;
    if (g.backend == 1) r = g.old_setsize(dll_id(cam), w, h);
    else {                                          /* centred, like CameraNew::setWindow */
        r = g.new_lt(dll_id(cam), (unsigned short)((SENSOR_W - w) / 2), (unsigned short)((SENSOR_H - h) / 2));
        Sleep(50);
        if (r) r = g.new_wh(dll_id(cam), (unsigned short)w, (unsigned short)h);
        Sleep(50);
    }
    if (r) { g.cam[s].w = w; g.cam[s].h = h; }
    return r;
}

/* grab one frame as 8-bit gray, as delivered (no rotation); returns bytes or 0 */
static unsigned int do_grab(int cam, int timeout_ms)
{
    int s = slot(cam), w = g.cam[s].w, h = g.cam[s].h, i;
    if (timeout_ms <= 0) timeout_ms = 1000;
    if (g.backend == 1) {
        if (!g.old_capture(dll_id(cam), frame_buf)) return 0;
        for (i = 0; i < w * h; i++) frame_buf[i] = frame_buf[3 * i];    /* gray repeated in B,G,R */
    } else {
        if (!g.new_readasy(dll_id(cam), frame_buf, (unsigned int)(w * h), (DWORD)timeout_ms, 1)) return 0;
    }
    return (unsigned int)(w * h);
}

static int send_all(SOCKET s, const void *p, int n)
{
    const char *c = (const char *)p;
    while (n > 0) { int k = send(s, c, n, 0); if (k <= 0) return 0; c += k; n -= k; }
    return 1;
}

static int recv_all(SOCKET s, void *p, int n)
{
    char *c = (char *)p;
    while (n > 0) { int k = recv(s, c, n, 0); if (k <= 0) return 0; c += k; n -= k; }
    return 1;
}

static int reply(SOCKET s, int status, const void *data, unsigned int len)
{
    rep_t r; r.status = status; r.len = len;
    return send_all(s, &r, sizeof r) && (!len || send_all(s, data, (int)len));
}

static void serve(SOCKET c)
{
    req_t q;
    while (recv_all(c, &q, sizeof q)) {
        int st = 0;
        switch (q.cmd) {
        case CMD_INFO: {
            char t[256];
            int n = _snprintf(t, sizeof t, "camserver %s backend=%s cams=%d up=%dx%d head=%dx%d",
                              VERSION, g.backend == 1 ? "old" : "new", g.ncams,
                              g.cam[0].w, g.cam[0].h, g.cam[1].w, g.cam[1].h);
            if (!reply(c, 1, t, (unsigned int)n)) return;
            continue;
        }
        case CMD_EXPOSURE: st = do_exposure(q.cam, q.a); break;
        case CMD_GAIN:     st = do_gain(q.cam, q.a); break;
        case CMD_WINDOW:   st = do_window(q.cam, q.a, q.b); break;
        case CMD_LED:      st = g.backend == 2 ? g.new_led(dll_id(q.cam), (unsigned short)q.a) : 0; break;
        case CMD_RESET:    st = g.backend == 2 ? g.new_reset(dll_id(q.cam)) : 1; break;
        case CMD_GRAB: {
            int s = slot(q.cam);
            unsigned int n = do_grab(q.cam, q.a);
            unsigned short wh[2];
            if (!n) { log_msg("grab cam %d failed", q.cam); if (!reply(c, 0, NULL, 0)) return; continue; }
            wh[0] = (unsigned short)g.cam[s].w; wh[1] = (unsigned short)g.cam[s].h;
            { rep_t r; r.status = 1; r.len = n + 4;
              if (!send_all(c, &r, sizeof r) || !send_all(c, wh, 4) || !send_all(c, frame_buf, (int)n)) return; }
            continue;
        }
        case CMD_BYE: reply(c, 1, NULL, 0); return;
        default: st = -1; break;
        }
        if (st <= 0) log_msg("cmd %d cam %d (%d,%d) -> %d", q.cmd, q.cam, q.a, q.b, st);
        if (!reply(c, st > 0 ? 1 : st, NULL, 0)) return;
    }
}

int main(int argc, char **argv)
{
    WSADATA wsa; SOCKET ls; struct sockaddr_in a;
    int port = DEFAULT_PORT, i, want = 0, one = 1;
    const char *bind_addr = "0.0.0.0";
    char dir[MAX_PATH];

    GetModuleFileNameA(NULL, dir, sizeof dir);
    if (strrchr(dir, '\\')) *strrchr(dir, '\\') = 0;
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bind") && i + 1 < argc) bind_addr = argv[++i];
        else if (!strcmp(argv[i], "--dir") && i + 1 < argc) { strncpy(dir, argv[++i], sizeof dir - 1); dir[sizeof dir - 1] = 0; }
        else if (!strcmp(argv[i], "--old")) want = 1;
        else if (!strcmp(argv[i], "--new")) want = 2;
        else { printf("usage: camserver [--port 5600] [--bind 0.0.0.0] [--dir <dll folder>] [--old | --new]\n"); return 2; }
    }
    SetDllDirectoryA(dir);                  /* imgdll.dll loads CGUSB2.dll from here (XP SP1+) */

    /* NeoDen4.exe tries the old camera first, then the new one */
    if (!((want != 2 && load_old(dir)) || (want != 1 && load_new(dir)))) {
        log_msg("no camera: neither imgdll.dll nor NeodenCamera.dll in %s found a camera", dir);
        return 1;
    }
    log_msg("camserver %s: %s camera DLL, %d camera(s)", VERSION, g.backend == 1 ? "old (imgdll)" : "new (NeodenCamera)", g.ncams);

    frame_buf = (unsigned char *)malloc(MAX_PIXELS * 3);
    if (!frame_buf) return 1;
    /* start like NeoDen4.exe: 1024 x 1024 windows; head exposure 25 gain 8; up camera exposure
     * 150 (= 50 * fBrightness 3.0); the new up camera keeps the DLL's default gain (NeoDen4 never
     * sets it), the old DLL takes exposure and gain together, so the old one gets 8 */
    g.cam[CAM_UP].exp = 150; g.cam[CAM_UP].gain = 8;
    g.cam[CAM_HEAD].exp = 25; g.cam[CAM_HEAD].gain = 8;
    for (i = 0; i < 2; i++) do_window(i, 1024, 1024);
    do_exposure(CAM_UP, 150);
    do_exposure(CAM_HEAD, 25);
    do_gain(CAM_HEAD, 8);

    if (WSAStartup(MAKEWORD(2, 0), &wsa)) { log_msg("WSAStartup failed"); return 1; }
    ls = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)port); a.sin_addr.s_addr = inet_addr(bind_addr);
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 1)) { log_msg("cannot listen on %s:%d", bind_addr, port); return 1; }
    log_msg("listening on %s:%d", bind_addr, port);

    for (;;) {                              /* one client at a time */
        struct sockaddr_in peer; int pl = sizeof peer;
        SOCKET c = accept(ls, (struct sockaddr *)&peer, &pl);
        if (c == INVALID_SOCKET) continue;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof one);
        log_msg("client %s connected", inet_ntoa(peer.sin_addr));
        serve(c);
        closesocket(c);
        log_msg("client disconnected");
    }
}
