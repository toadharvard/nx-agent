/* SHA-256 (FIPS 180-4). The one content-key hash of fnv-nx (accepted assumption 2). Portable C99, no libc beyond memcpy/memset. */
#ifndef FNVNX_SHA256_H
#define FNVNX_SHA256_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void fnvnx_sha256(const void *data, size_t size, uint8_t out[32]);

#ifdef __cplusplus
}
#endif

#endif
