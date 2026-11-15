/* SHA-256 (FIPS 180-4). The one content-key hash of fnv-nx (accepted assumption ASM-002). Portable C99, no libc beyond memcpy/memset. */
#ifndef FNVNX_SHA256_H
#define FNVNX_SHA256_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FNVNX_SHA256_DIGEST_SIZE 32   /* FIPS 180-4: 8 words of 32 bits */

void fnvnx_sha256(const void *data, size_t size, uint8_t out[FNVNX_SHA256_DIGEST_SIZE]);

#ifdef __cplusplus
}
#endif

#endif
