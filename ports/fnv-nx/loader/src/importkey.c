/* Lookup over the generated import table (include/fnvnx_importkey.h). Freestanding-safe, ILP32-safe. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "fnvnx_importkey.h"

#define DLL_MAX 64

/* Windows module-name rule: ASCII lowercase, ".dll" appended when the name has no '.'. 0 when it does not fit. */
static int norm_dll(const char *in, char *out) {
    size_t n = 0;
    int dot = 0;
    for (; in[n] != '\0'; n++) {
        char c = in[n];
        if (n + 5 > DLL_MAX) return 0;
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        if (c == '.') dot = 1;
        out[n] = c;
    }
    if (!dot) {
        memcpy(out + n, ".dll", 4);
        n += 4;
    }
    out[n] = '\0';
    return 1;
}

uint32_t fnvnx_import_key(const char *dll, const char *name, uint32_t ordinal) {
    char d[DLL_MAX + 1];
    size_t lo = 0, hi = fnvnx_import_row_count;
    if (dll == NULL || !norm_dll(dll, d)) return 0;
    if (name != NULL) {
        while (lo < hi) {
            size_t mid = lo + (hi - lo) / 2;
            const fnvnx_import_row *r = &fnvnx_import_rows[mid];
            int c = strcmp(d, r->dll);
            if (c == 0) c = strcmp(name, r->name);
            if (c == 0) return (uint32_t)r->value;
            if (c < 0) hi = mid; else lo = mid + 1;
        }
        return 0;
    }
    if (ordinal == FNVNX_BY_NAME) return 0;
    for (; lo < hi; lo++) { /* one (dll, ordinal) per row (checked by the generator) */
        const fnvnx_import_row *r = &fnvnx_import_rows[lo];
        if (r->ordinal == ordinal && strcmp(d, r->dll) == 0) return (uint32_t)r->value;
    }
    return 0;
}

const fnvnx_import_row *fnvnx_import_by_key(uint32_t key) {
    uint32_t off;
    if (key < FNVNX_IMPORT_BASE) return NULL;
    off = key - FNVNX_IMPORT_BASE;
    if (off % FNVNX_IMPORT_STEP != 0 || off / FNVNX_IMPORT_STEP >= fnvnx_import_key_count) return NULL;
    return &fnvnx_import_rows[fnvnx_import_by_key_idx[off / FNVNX_IMPORT_STEP]];
}
