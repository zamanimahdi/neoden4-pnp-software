/* neoagent: camera proxy to camserver on localhost (protocol: ../docs/CAMSERVER.md).
 * Frames go to the browser as 8-bit gray BMP (every browser shows BMP; no codec needed on XP).
 * Frames are turned like NeoDen4.exe does: up camera transpose + flip both, head flip both. */
#include "neoagent.h"

#pragma pack(push, 1)
typedef struct { unsigned char cmd, cam; unsigned short flags; int a, b, c; } creq_t;
typedef struct { int status; unsigned int len; } crep_t;
#pragma pack(pop)

static SOCKET cs = INVALID_SOCKET;
static CRITICAL_SECTION cam_lock;
static LONG cam_lock_init;

static void lock_cam(void)
{
    if (InterlockedCompareExchange(&cam_lock_init, 1, 0) == 0) InitializeCriticalSection(&cam_lock), cam_lock_init = 2;
    while (cam_lock_init != 2) Sleep(1);
    EnterCriticalSection(&cam_lock);
}

static int io(void *p, int n, int send_it)
{
    char *c = (char *)p;
    while (n > 0) {
        int k = send_it ? send(cs, c, n, 0) : recv(cs, c, n, 0);
        if (k <= 0) return 0;
        c += k; n -= k;
    }
    return 1;
}

static int cam_connect(char *err, int errn)
{
    struct sockaddr_in a;
    int to = 8000;
    if (cs != INVALID_SOCKET) return 1;
    cs = socket(AF_INET, SOCK_STREAM, 0);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons((unsigned short)A.cam_port); a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(cs, (struct sockaddr *)&a, sizeof a)) {
        closesocket(cs); cs = INVALID_SOCKET;
        _snprintf(err, errn, "camserver not reachable on port %d", A.cam_port);
        return 0;
    }
    setsockopt(cs, SOL_SOCKET, SO_RCVTIMEO, (const char *)&to, sizeof to);
    return 1;
}

/* one request; *data (malloc) gets the reply payload */
static int call(int cmd, int cam, int a, int b, char **data, unsigned int *len, char *err, int errn)
{
    creq_t q;
    crep_t r;
    int attempt;
    for (attempt = 0; attempt < 2; attempt++) {
        if (!cam_connect(err, errn)) return 0;
        memset(&q, 0, sizeof q);
        q.cmd = (unsigned char)cmd; q.cam = (unsigned char)cam; q.a = a; q.b = b;
        if (io(&q, sizeof q, 1) && io(&r, sizeof r, 0)) {
            char *buf = NULL;
            if (r.len) {
                buf = (char *)malloc(r.len);
                if (!buf || !io(buf, (int)r.len, 0)) { free(buf); goto broken; }
            }
            if (data) *data = buf; else free(buf);
            if (len) *len = r.len;
            if (r.status != 1) _snprintf(err, errn, "camserver: command %d failed (status %d)", cmd, r.status);
            return r.status == 1;
        }
broken:
        closesocket(cs); cs = INVALID_SOCKET;               /* reconnect once */
    }
    _snprintf(err, errn, "camserver connection lost");
    return 0;
}

int cam_info(char *out, int max)
{
    char *d = NULL, err[200];
    unsigned int n = 0;
    int ok;
    lock_cam();
    ok = call(1, 0, 0, 0, &d, &n, err, sizeof err);
    LeaveCriticalSection(&cam_lock);
    if (!ok) { json_str(out, max, err); return 0; }
    if ((int)n >= max - 3) n = max - 3;
    memcpy(out + 1, d, n); out[0] = '"'; out[n + 1] = '"'; out[n + 2] = 0;
    free(d);
    return 1;
}

int cam_set(int cam, int exposure, int gain, int w, int h, int led, char *err, int errn)
{
    int ok = 1;
    lock_cam();
    if (w > 0 && h > 0) ok = ok && call(4, cam, w, h, NULL, NULL, err, errn);
    if (exposure > 0) ok = ok && call(2, cam, exposure, 0, NULL, NULL, err, errn);
    if (gain >= 0) ok = ok && call(3, cam, gain, 0, NULL, NULL, err, errn);
    if (led >= 0) ok = ok && call(5, cam, led, 0, NULL, NULL, err, errn);
    LeaveCriticalSection(&cam_lock);
    return ok;
}

int cam_grab_bmp(int cam, int scale, char **bmp, long *len, char *err, int errn)
{
    char *d = NULL;
    unsigned int n = 0;
    int w, h, ow, oh, stride, x, y, i;
    unsigned char *src, *px;
    long size;
    if (scale < 1) scale = 1;
    if (scale > 8) scale = 8;
    lock_cam();
    if (!call(6, cam, 1000, 0, &d, &n, err, errn)) { LeaveCriticalSection(&cam_lock); return 0; }
    LeaveCriticalSection(&cam_lock);
    w = ((unsigned char)d[0]) | ((unsigned char)d[1] << 8);
    h = ((unsigned char)d[2]) | ((unsigned char)d[3] << 8);
    if (n < (unsigned int)(4 + w * h)) { free(d); _snprintf(err, errn, "short frame"); return 0; }
    src = (unsigned char *)d + 4;
    /* orientation like NeoDen4: up (0): out[r][c] = in[N-1-c][N-1-r] (square), head: 180 deg */
    if ((cam & 0x7f) == 0 && w == h) { ow = w; oh = h; }
    else { ow = w; oh = h; }
    ow /= scale; oh /= scale;
    stride = (ow + 3) & ~3;
    size = 54 + 1024 + (long)stride * oh;
    *bmp = (char *)malloc(size);
    if (!*bmp) { free(d); return 0; }
    memset(*bmp, 0, 54 + 1024);
    {   /* BITMAPFILEHEADER + BITMAPINFOHEADER, 8 bit, gray palette, bottom-up rows */
        unsigned char *b = (unsigned char *)*bmp;
        b[0] = 'B'; b[1] = 'M';
        *(unsigned int *)(b + 2) = (unsigned int)size;
        *(unsigned int *)(b + 10) = 54 + 1024;
        *(unsigned int *)(b + 14) = 40;
        *(int *)(b + 18) = ow; *(int *)(b + 22) = oh;
        *(unsigned short *)(b + 26) = 1; *(unsigned short *)(b + 28) = 8;
        *(unsigned int *)(b + 34) = (unsigned int)(stride * oh);
        *(unsigned int *)(b + 46) = 256;
        for (i = 0; i < 256; i++) { b[54 + 4 * i] = b[55 + 4 * i] = b[56 + 4 * i] = (unsigned char)i; }
    }
    px = (unsigned char *)*bmp + 54 + 1024;
    for (y = 0; y < oh; y++) {
        unsigned char *row = px + (long)(oh - 1 - y) * stride;      /* bottom-up */
        for (x = 0; x < ow; x++) {
            int sx = x * scale, sy = y * scale, ix, iy;
            if ((cam & 0x7f) == 0 && w == h) { ix = w - 1 - sy; iy = h - 1 - sx; }   /* transpose + flip */
            else { ix = w - 1 - sx; iy = h - 1 - sy; }                             /* flip both */
            row[x] = src[(long)iy * w + ix];
        }
    }
    free(d);
    *len = size;
    return 1;
}
