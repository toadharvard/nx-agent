/* inflate.h - zlib (RFC 1950) / DEFLATE (RFC 1951) decompressor of the Win32 layer (used by the PNG icon decoder). */
#ifndef FNVNX_INFLATE_H
#define FNVNX_INFLATE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Decompresses the whole zlib stream src[0..n). Returns 0 and a malloc'ed buffer in *out (length *out_n; free with free()),
 * else a FNVNX_INF_* error with *out = NULL. The stream must end exactly at its Adler-32 (no trailing bytes). */
enum { FNVNX_INF_OK = 0, FNVNX_INF_HEADER = 1, FNVNX_INF_TRUNCATED = 2, FNVNX_INF_BAD_CODE = 3, FNVNX_INF_DISTANCE = 4,
       FNVNX_INF_ADLER = 5, FNVNX_INF_TOO_BIG = 6, FNVNX_INF_TRAILING = 7 };
int fnvnx_inflate_zlib(const unsigned char *src, size_t n, unsigned char **out, size_t *out_n);   /* output <= 256 MiB */
int fnvnx_inflate_zlib_max(const unsigned char *src, size_t n, unsigned char **out, size_t *out_n, size_t max_out);
#ifdef __cplusplus
}
#endif
#endif
