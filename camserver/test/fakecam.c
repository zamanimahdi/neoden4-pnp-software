/* Fake camera DLLs for testing camserver without the machine.
 * Build twice: -DNEW -> NeodenCamera.dll (img_init, img_set_exp, ..., img_readAsy)
 *              -DOLD -> imgdll.dll       (img_init, img_set, img_setSize, img_capture)
 * Frames: gradient + a bright disc whose brightness follows the exposure; every call is
 * logged to fakecam.log so the test can check what camserver sent. */
#include <windows.h>
#include <stdio.h>

static FILE *lg;
static int expo[8] = {25, 25, 25, 25, 25, 25, 25, 25}, ww[8], hh[8];

static void L(const char *fmt, int a, int b, int c, int d)
{
    if (!lg) lg = fopen("fakecam.log", "a");
    if (lg) { fprintf(lg, fmt, a, b, c, d); fputc('\n', lg); fflush(lg); }
}

static void draw(int id, unsigned char *p, int w, int h, int stride)
{
    int x, y, cx = w / 2 + 37, cy = h / 2 - 21;
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++) {
            int v = (x + y) / 16 + id * 10;
            if ((x - cx) * (x - cx) + (y - cy) * (y - cy) < 40 * 40) v = expo[id & 7] * 6;
            if (v > 255) v = 255;
            p[(y * w + x) * stride] = (unsigned char)v;
            if (stride == 3) { p[(y * w + x) * 3 + 1] = (unsigned char)v; p[(y * w + x) * 3 + 2] = (unsigned char)v; }
        }
}

#ifdef NEW
__declspec(dllexport) int __cdecl img_init(void) { int i; for (i = 0; i < 8; i++) { ww[i] = 1280; hh[i] = 1024; } L("init", 0, 0, 0, 0); return 2; }
__declspec(dllexport) int __cdecl img_set_exp(int id, unsigned short v) { expo[id & 7] = v; L("exp %d %d", id, v, 0, 0); return 1; }
__declspec(dllexport) int __cdecl img_set_gain(int id, unsigned short v) { L("gain %d %d", id, v, 0, 0); return 1; }
__declspec(dllexport) int __cdecl img_led(int id, unsigned short v) { L("led %d %d", id, v, 0, 0); return 1; }
__declspec(dllexport) int __cdecl img_reset(int id) { L("reset %d", id, 0, 0, 0); return 1; }
__declspec(dllexport) int __cdecl img_capture(int id) { L("capture %d", id, 0, 0, 0); return 1; }
__declspec(dllexport) int __cdecl img_set_lt(int id, unsigned short x, unsigned short y) { L("lt %d %d %d", id, x, y, 0); return 1; }
__declspec(dllexport) int __cdecl img_set_wh(int id, unsigned short w, unsigned short h) { ww[id & 7] = w; hh[id & 7] = h; L("wh %d %d %d", id, w, h, 0); return 1; }
__declspec(dllexport) int __cdecl img_read(int id, void *b, unsigned int n, int t) { (void)b; L("read %d %d %d", id, (int)n, t, 0); return 0; }
__declspec(dllexport) char __cdecl img_readAsy(int id, void *b, unsigned int n, DWORD t, char trig)
{
    L("readAsy %d %d %d %d", id, (int)n, (int)t, trig);
    if (n != (unsigned int)(ww[id & 7] * hh[id & 7])) return 0;
    draw(id, (unsigned char *)b, ww[id & 7], hh[id & 7], 1);
    return 1;
}
#endif

#ifdef OLD
__declspec(dllexport) int __cdecl img_init(void) { ww[0] = ww[1] = 1024; hh[0] = hh[1] = 1024; L("init", 0, 0, 0, 0); return 2; }
__declspec(dllexport) int __cdecl img_set(int cam, int mode, int e, int g) { expo[cam & 7] = e; L("set %d %d %d %d", cam, mode, e, g); return 1; }
__declspec(dllexport) int __cdecl img_setRoi(int cam, int a, int b, int c) { L("setRoi %d %d %d %d", cam, a, b, c); return 1; }
__declspec(dllexport) int __cdecl img_setSize(int cam, int w, int h) { ww[cam & 7] = w; hh[cam & 7] = h; L("setSize %d %d %d", cam, w, h, 0); return 1; }
__declspec(dllexport) int __cdecl img_capture(int cam, void *bgr) { L("capture %d", cam, 0, 0, 0); draw(cam, (unsigned char *)bgr, ww[cam & 7], hh[cam & 7], 3); return 1; }
#endif
