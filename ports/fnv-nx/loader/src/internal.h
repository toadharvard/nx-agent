#ifndef FNVNX_LOADER_INTERNAL_H
#define FNVNX_LOADER_INTERNAL_H

#include "fnvnx_loader.h"
#include <string.h>

static inline uint16_t fnvnx_rd16(const void *p) {
    const uint8_t *b = (const uint8_t *)p;
    return (uint16_t)(b[0] | (b[1] << 8));
}
static inline uint32_t fnvnx_rd32(const void *p) {
    const uint8_t *b = (const uint8_t *)p;
    return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}
static inline void fnvnx_wr32(void *p, uint32_t v) {
    uint8_t *b = (uint8_t *)p;
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24);
}

/* [a, a+n) inside [lo, hi) without overflow */
static inline int fnvnx_in(uint32_t a, uint32_t n, uint32_t lo, uint32_t hi) {
    return a >= lo && a <= hi && n <= hi - a;
}

/* every hook is required (fnvnx_os) */
static inline int fnvnx_os_complete(const fnvnx_os *os) {
    return os && os->read && os->map_fixed && os->protect && os->alloc && os->free && os->log && os->alloc_va &&
           os->free_va && os->register_image && os->unmap_fixed;
}

void fnvnx_logf(const fnvnx_os *os, const char *fmt, ...);

/* current VA of the NUL-terminated string at va, length into *len (bounded by max), or NULL */
const char *fnvnx_image_str(const fnvnx_image *img, uint32_t va, uint32_t max, uint32_t *len);

#endif
