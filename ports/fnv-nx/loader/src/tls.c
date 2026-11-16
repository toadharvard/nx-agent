/* Per-thread TLS block (rule S5), see include/fnvnx_tls.h. */
#include "fnvnx_tls.h"
#include "internal.h"

int fnvnx_tls_layout_read(const fnvnx_image *img, fnvnx_tls_layout *out) {
    const fnvnx_pe *pe;
    const uint8_t *t;
    uint32_t start, end, idx, cb, zf, chars, n, shift = 0, size;
    if (!img || !out || !img->pe) return FNVNX_E_ARG;
    memset(out, 0, sizeof *out);
    pe = img->pe;
    if (pe->ndirs <= FNVNX_DIR_TLS || pe->dir[FNVNX_DIR_TLS].size == 0) return FNVNX_OK;
    if (pe->dir[FNVNX_DIR_TLS].size < 24) return FNVNX_E_FORMAT;
    t = (const uint8_t *)fnvnx_image_ptr(img, img->load_base + pe->dir[FNVNX_DIR_TLS].rva, 24);
    if (!t) return FNVNX_E_RANGE;
    start = fnvnx_rd32(t + 0);
    end = fnvnx_rd32(t + 4);
    idx = fnvnx_rd32(t + 8);
    cb = fnvnx_rd32(t + 12);
    zf = fnvnx_rd32(t + 16);
    chars = fnvnx_rd32(t + 20);
    if (end < start) return FNVNX_E_FORMAT;
    size = end - start;
    if (zf > 0xffffffffu - size) return FNVNX_E_FORMAT;
    /* the fields are relocated words: current VAs once the image is relocated, preferred-base VAs before */
    shift = img->relocated ? 0 : img->delta;
    start += shift;
    idx += shift;
    if (cb) cb += shift;
    if (!fnvnx_image_ptr(img, idx, 4)) return FNVNX_E_RANGE;
    if (size && !fnvnx_image_ptr(img, start, size)) return FNVNX_E_RANGE;
    n = (chars >> 20) & 0xfu;
    out->present = 1;
    out->template_va = start;
    out->template_size = size;
    out->zero_fill = zf;
    out->block_size = size + zf;
    out->align = n ? (uint32_t)1 << (n - 1) : 1u;
    out->index_va = idx;
    out->callbacks_va = cb;
    return FNVNX_OK;
}

int fnvnx_tls_block_init(const fnvnx_image *img, const fnvnx_tls_layout *l, void *block) {
    const void *src;
    if (!img || !l || !block || !l->present) return FNVNX_E_ARG;
    if (l->block_size < l->template_size || l->block_size - l->template_size != l->zero_fill) return FNVNX_E_FORMAT;
    if (l->template_size) {
        src = fnvnx_image_ptr(img, l->template_va, l->template_size);
        if (!src) return FNVNX_E_RANGE;
        memcpy(block, src, l->template_size);
    }
    memset((uint8_t *)block + l->template_size, 0, l->zero_fill);
    return FNVNX_OK;
}
