/* Stores the prelinked native entries into the code-address words of the data image (see fnvnx_prelink.h). */
#include "fnvnx_prelink.h"
#include "fnvnx_loader.h"

static uint32_t rd32(const uint8_t *b) {
    return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}

static void wr32(uint8_t *b, uint32_t v) {
    b[0] = (uint8_t)v;
    b[1] = (uint8_t)(v >> 8);
    b[2] = (uint8_t)(v >> 16);
    b[3] = (uint8_t)(v >> 24);
}

int fnvnx_prelink_apply(const struct fnvnx_ptr_rec *tab, uint32_t text_lo, uint32_t text_hi,
                        fnvnx_va_fn at, void *ctx, uint32_t *applied, uint32_t *bad_index) {
    uint32_t i = 0;
    uint64_t prev_end = 0; /* 64-bit: va + 4 cannot wrap */
    int err = 0;

    for (; tab[i].image_va != 0 || tab[i].native != 0; i++) {
        uint32_t va = tab[i].image_va, nat = tab[i].native, cur;
        uint8_t *w;

        /* strictly increasing and non-overlapping; prev_end is 0 before the first record */
        if (va < prev_end) { err = FNVNX_PRELINK_ORDER; break; }
        w = at(ctx, va, 4);
        if (w == NULL) { err = FNVNX_PRELINK_UNMAPPED; break; }
        cur = rd32(w);
        if (cur < text_lo || cur >= text_hi) { err = FNVNX_PRELINK_NOT_CODE; break; }
        if (nat == 0 || (nat >= text_lo && nat < text_hi)) { err = FNVNX_PRELINK_BAD_NATIVE; break; }
        wr32(w, nat);
        prev_end = (uint64_t)va + 4;
    }
    if (err != 0) {
        if (bad_index != NULL) *bad_index = i;
        return err;
    }
    if (applied != NULL) *applied = i;
    return 0;
}

int fnvnx_image_identity(const uint8_t *file, size_t size, const uint8_t want[32]) {
    uint8_t got[32];
    uint8_t diff = 0;
    unsigned k;

    fnvnx_sha256(file, size, got);
    for (k = 0; k < 32; k++) diff = (uint8_t)(diff | (got[k] ^ want[k]));
    return diff == 0 ? 0 : FNVNX_PRELINK_IDENTITY;
}
