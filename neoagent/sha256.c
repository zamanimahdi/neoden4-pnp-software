/* SHA-256 and HMAC-SHA256 (FIPS 180-4, RFC 2104). Plain C, no dependencies. */
#include <string.h>
#include "sha256.h"

static const unsigned int K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void block(sha256_t *s, const unsigned char *p)
{
    unsigned int w[64], a, b, c, d, e, f, g, h, t1, t2;
    int i;
    for (i = 0; i < 16; i++) w[i] = (unsigned int)p[4 * i] << 24 | (unsigned int)p[4 * i + 1] << 16 | (unsigned int)p[4 * i + 2] << 8 | p[4 * i + 3];
    for (; i < 64; i++) {
        unsigned int s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        unsigned int s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
    for (i = 0; i < 64; i++) {
        t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

void sha256_init(sha256_t *s)
{
    static const unsigned int H0[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 };
    memcpy(s->h, H0, sizeof H0);
    s->len = 0; s->n = 0;
}

void sha256_update(sha256_t *s, const void *data, size_t len)
{
    const unsigned char *p = (const unsigned char *)data;
    s->len += len;
    while (len) {
        size_t k = 64 - s->n;
        if (k > len) k = len;
        memcpy(s->buf + s->n, p, k);
        s->n += (unsigned int)k; p += k; len -= k;
        if (s->n == 64) { block(s, s->buf); s->n = 0; }
    }
}

void sha256_final(sha256_t *s, unsigned char out[32])
{
    unsigned long long bits = s->len * 8;
    unsigned char pad = 0x80, z = 0, lb[8];
    int i;
    sha256_update(s, &pad, 1);
    while (s->n != 56) sha256_update(s, &z, 1);
    for (i = 0; i < 8; i++) lb[i] = (unsigned char)(bits >> (56 - 8 * i));
    sha256_update(s, lb, 8);
    for (i = 0; i < 8; i++) { out[4 * i] = (unsigned char)(s->h[i] >> 24); out[4 * i + 1] = (unsigned char)(s->h[i] >> 16); out[4 * i + 2] = (unsigned char)(s->h[i] >> 8); out[4 * i + 3] = (unsigned char)s->h[i]; }
}

void sha256(const void *data, size_t len, unsigned char out[32])
{
    sha256_t s;
    sha256_init(&s); sha256_update(&s, data, len); sha256_final(&s, out);
}

void hmac_sha256_init(hmac_t *m, const void *key, size_t klen)
{
    unsigned char k[64], kk[32];
    int i;
    memset(k, 0, 64);
    if (klen > 64) { sha256(key, klen, kk); memcpy(k, kk, 32); } else memcpy(k, key, klen);
    for (i = 0; i < 64; i++) { m->ipad[i] = k[i] ^ 0x36; m->opad[i] = k[i] ^ 0x5c; }
    sha256_init(&m->in); sha256_update(&m->in, m->ipad, 64);
}

void hmac_sha256_update(hmac_t *m, const void *data, size_t len) { sha256_update(&m->in, data, len); }

void hmac_sha256_final(hmac_t *m, unsigned char out[32])
{
    unsigned char ih[32];
    sha256_t o;
    sha256_final(&m->in, ih);
    sha256_init(&o); sha256_update(&o, m->opad, 64); sha256_update(&o, ih, 32); sha256_final(&o, out);
}

void hex32(const unsigned char d[32], char out[65])
{
    static const char hx[] = "0123456789abcdef";
    int i;
    for (i = 0; i < 32; i++) { out[2 * i] = hx[d[i] >> 4]; out[2 * i + 1] = hx[d[i] & 15]; }
    out[64] = 0;
}
