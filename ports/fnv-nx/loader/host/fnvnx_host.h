/* Host (Linux/macOS, 64-bit) backend of the loader's OS hooks. A 64-bit host process cannot own the addresses
 * 0x00400000..0x02100000 reliably, so the whole mapping window is reserved PROT_NONE somewhere and the image is
 * mapped into it with bias = reservation - window_lo. On the Switch the same hooks map at the original addresses
 * with bias 0 (docs/DATAIMAGE.md "Horizon backend"). */
#ifndef FNVNX_HOST_H
#define FNVNX_HOST_H

#include "fnvnx_loader.h"
#include <stdio.h>

typedef struct fnvnx_host {
    FILE *f;
    uint32_t file_size;
    uint8_t *res;
    size_t res_size;
    uint32_t window_lo, window_hi;
    uintptr_t bias;
    struct { uintptr_t lo, hi; unsigned prot; } maps[FNVNX_MAX_SECTIONS + 2];
    unsigned nmaps, protect_calls;
    int verbose;
} fnvnx_host;

/* Opens the file and reserves [window_lo, window_hi); fills *os. */
int fnvnx_host_open(fnvnx_host *h, const char *path, uint32_t window_lo, uint32_t window_hi, fnvnx_os *os);
void fnvnx_host_close(fnvnx_host *h);

#endif
