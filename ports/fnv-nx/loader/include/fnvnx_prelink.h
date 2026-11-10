#ifndef FNVNX_PRELINK_H
#define FNVNX_PRELINK_H
/* Prelinked code pointers of the data image.
 * The link emits fnvnx_image_ptrs[] = { {image_va, native}, ..., {0, 0} }: image_va is the address of a
 * 32-bit little-endian word inside the mapped image (need not be aligned, it comes from the exe's base
 * relocations), native is the guest address of the native entry for the code address that word holds in
 * the original file. fnvnx_prelink_apply stores native into each word. Nothing is scanned. */
#include <stddef.h>
#include <stdint.h>

struct fnvnx_ptr_rec {
    uint32_t image_va;
    uint32_t native;
};

/* Host pointer of `size` mapped bytes at guest VA `va`, or NULL if that range is not mapped. */
typedef uint8_t *(*fnvnx_va_fn)(void *ctx, uint32_t va, uint32_t size);

enum fnvnx_prelink_err {
    FNVNX_PRELINK_OK = 0,
    FNVNX_PRELINK_UNMAPPED = 1,   /* invariant: every record's 4-byte word lies in mapped image memory */
    FNVNX_PRELINK_NOT_CODE = 2,   /* invariant: the word holds a text address (wrong image, or record applied twice) */
    FNVNX_PRELINK_ORDER = 3,      /* invariant: records strictly increasing, words 4 bytes apart or more (no overlap) */
    FNVNX_PRELINK_BAD_NATIVE = 4, /* invariant: native is nonzero and outside the original text range */
    FNVNX_PRELINK_IDENTITY = 5    /* invariant: the exe file is the exact file the tables were built from */
};

/* Applies the table up to the {0,0} terminator, in order. Per record the checks run in this order: ORDER,
 * UNMAPPED, NOT_CODE (word value in [text_lo, text_hi)), BAD_NATIVE. On the first failure the index of the
 * failing record is stored in *bad_index and the code returned; that record is NOT applied, records before
 * it STAY applied (the image is then invalid and must not run). On success returns 0 and stores the record
 * count in *applied. applied and bad_index may be NULL. */
int fnvnx_prelink_apply(const struct fnvnx_ptr_rec *tab, uint32_t text_lo, uint32_t text_hi,
                        fnvnx_va_fn at, void *ctx, uint32_t *applied, uint32_t *bad_index);

/* 0 when SHA-256(file[0..size)) equals want, FNVNX_PRELINK_IDENTITY otherwise. */
int fnvnx_image_identity(const uint8_t *file, size_t size, const uint8_t want[32]);

#endif
