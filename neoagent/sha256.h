#ifndef SHA256_H
#define SHA256_H
#include <stddef.h>

typedef struct { unsigned int h[8]; unsigned char buf[64]; unsigned int n; unsigned long long len; } sha256_t;
typedef struct { sha256_t in; unsigned char ipad[64], opad[64]; } hmac_t;

void sha256_init(sha256_t *s);
void sha256_update(sha256_t *s, const void *data, size_t len);
void sha256_final(sha256_t *s, unsigned char out[32]);
void sha256(const void *data, size_t len, unsigned char out[32]);
void hmac_sha256_init(hmac_t *m, const void *key, size_t klen);
void hmac_sha256_update(hmac_t *m, const void *data, size_t len);
void hmac_sha256_final(hmac_t *m, unsigned char out[32]);
void hex32(const unsigned char d[32], char out[65]);

#endif
