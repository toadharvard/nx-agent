#ifndef FNVNX_TLS_H
#define FNVNX_TLS_H
/* Per-thread TLS block of the PE contract (loader rule S5): thread-local storage exactly as the PE TLS directory
 * (IMAGE_TLS_DIRECTORY32) describes it, for every thread.  Each thread's block is block_size bytes: the template
 * bytes copied from the image, then zero_fill zero bytes.  Addresses are current VAs of the mapped image. */
#include "fnvnx_loader.h"
#include <stdint.h>

typedef struct fnvnx_tls_layout {
    int      present;        /* 0: the image has no TLS directory; every other field is 0 */
    uint32_t template_va;    /* StartAddressOfRawData (current VA) */
    uint32_t template_size;  /* EndAddressOfRawData - StartAddressOfRawData */
    uint32_t zero_fill;      /* SizeOfZeroFill */
    uint32_t block_size;     /* template_size + zero_fill */
    uint32_t align;          /* from Characteristics bits 20..23 (IMAGE_SCN_ALIGN_*): 1 << (n-1); n == 0 gives 1 */
    uint32_t index_va;       /* AddressOfIndex */
    uint32_t callbacks_va;   /* AddressOfCallBacks, 0 if none */
} fnvnx_tls_layout;

/* Read and validate the TLS directory. FNVNX_OK with present = 0 when there is no directory. Errors:
 * FNVNX_E_ARG (NULL argument), FNVNX_E_FORMAT (directory size 1..23, End < Start, template_size + zero_fill
 * overflows 32 bits), FNVNX_E_RANGE (directory, template or index word not wholly inside one mapped section).
 * A directory holding preferred-base VAs in an image at another base (not yet relocated) is handled by shifting:
 * every VA gets + img->delta until img->relocated is set. */
int fnvnx_tls_layout_read(const fnvnx_image *img, fnvnx_tls_layout *out);

/* Initialise one thread's block (block_size bytes at `block`): the template bytes from the image, then zero_fill
 * zero bytes. Nothing beyond block_size is written. FNVNX_E_ARG (NULL, or l->present == 0), FNVNX_E_FORMAT
 * (block_size != template_size + zero_fill), FNVNX_E_RANGE (template not in the image). */
int fnvnx_tls_block_init(const fnvnx_image *img, const fnvnx_tls_layout *l, void *block);

#endif
