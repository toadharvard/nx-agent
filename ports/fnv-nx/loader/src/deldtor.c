/* Adapters that let image code pointers to MSVC deleting destructors (S2 address map kind G) call the native
 * Itanium destructors with the MSVC contract: void *??_G(void *this, unsigned flags), bit 0 = delete, bit 1 =
 * vector delete. S2 maps ??_G to D0 (destroy + delete); without bit 0 we must destroy only, i.e. call D1, which S2's
 * table fnvnx_addrmap_deldtors gives per original address. Without a table (host tests) D1 is searched in the
 * object's vtable as the slot right before D0, where GCC places it (not for abstract classes: both slots are 0). */
#include "internal.h"
#include <stdio.h>
#include <stdlib.h>

typedef void (*dtor_fn)(void *self);

static struct { dtor_fn d0, d1; uint32_t orig; } targets[FNVNX_DELDTOR_SLOTS];
static uint32_t used;
static uintptr_t text_lo, text_hi;

/* S2's table, when its addrmap.c is linked (weak: the loader links without it too) */
#if defined(__GNUC__)
extern const fnvnx_s2_deldtor fnvnx_addrmap_deldtors[] __attribute__((weak));
extern const unsigned fnvnx_addrmap_deldtor_count __attribute__((weak));
#endif
static const fnvnx_s2_deldtor *table;
static uint32_t ntable;
static int table_set;

void fnvnx_deldtor_set_table(const fnvnx_s2_deldtor *t, uint32_t n) {
    table = t;
    ntable = t ? n : 0;
    table_set = t != NULL;
}

static const fnvnx_s2_deldtor *table_find(uint32_t orig) {
    uint32_t lo = 0, hi;
    if (!table_set) {
#if defined(__GNUC__)
        if (&fnvnx_addrmap_deldtor_count && fnvnx_addrmap_deldtors) {
            table = fnvnx_addrmap_deldtors;
            ntable = fnvnx_addrmap_deldtor_count;
        }
#endif
        table_set = 1;
    }
    hi = table ? ntable : 0;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (table[mid].orig < orig) lo = mid + 1;
        else hi = mid;
    }
    return lo < ntable && table && table[lo].orig == orig ? &table[lo] : NULL;
}

void fnvnx_deldtor_set_text(const void *lo, const void *hi) {
    text_lo = (uintptr_t)lo;
    text_hi = (uintptr_t)hi;
}

static void default_trap(uint32_t orig, void *self, unsigned flags) {
    fprintf(stderr, "fnvnx: unsupported call of deleting destructor 0x%08x (this=%p flags=%u)\n", (unsigned)orig, self,
            flags);
    abort();
}
void (*fnvnx_deldtor_trap)(uint32_t orig, void *self, unsigned flags) = default_trap;

static void *dd_call(unsigned k, void *self, unsigned flags) {
    dtor_fn d0 = targets[k].d0;
    if (!(flags & 2u)) {
        if (flags & 1u) {
            d0(self);
            return self;
        } else if (targets[k].d1) {
            targets[k].d1(self);
            return self;
        } else if (!table) {
            void **vptr = *(void ***)self;
            unsigned i;
            /* D0 sits in the primary vtable, D1 right before it. The vtable ends at the first word that is not a
             * native code address (the next vtable's offset-to-top or type_info pointer); without a text range
             * the search is bounded only by the slot count. */
            for (i = 1; i < 256; i++) {
                uintptr_t v = (uintptr_t)vptr[i];
                if (text_hi && (v < text_lo || v >= text_hi)) break;
                if (v == (uintptr_t)d0) {
                    ((dtor_fn)(uintptr_t)vptr[i - 1])(self);
                    return self;
                }
            }
        }
    }
    fnvnx_deldtor_trap(targets[k].orig, self, flags);
    return self;
}

/* FNVNX_DELDTOR_SLOTS = 4^5 stubs, each passing its own index */
#define DD(a, b, c, d, e) \
    static void *dd_##a##b##c##d##e(void *s, unsigned f) { return dd_call(a * 256 + b * 64 + c * 16 + d * 4 + e, s, f); }
#define DD4(a, b, c, d) DD(a, b, c, d, 0) DD(a, b, c, d, 1) DD(a, b, c, d, 2) DD(a, b, c, d, 3)
#define DD16(a, b, c) DD4(a, b, c, 0) DD4(a, b, c, 1) DD4(a, b, c, 2) DD4(a, b, c, 3)
#define DD64(a, b) DD16(a, b, 0) DD16(a, b, 1) DD16(a, b, 2) DD16(a, b, 3)
#define DD256(a) DD64(a, 0) DD64(a, 1) DD64(a, 2) DD64(a, 3)
DD256(0) DD256(1) DD256(2) DD256(3)

#define TE(a, b, c, d, e) dd_##a##b##c##d##e,
#define TE4(a, b, c, d) TE(a, b, c, d, 0) TE(a, b, c, d, 1) TE(a, b, c, d, 2) TE(a, b, c, d, 3)
#define TE16(a, b, c) TE4(a, b, c, 0) TE4(a, b, c, 1) TE4(a, b, c, 2) TE4(a, b, c, 3)
#define TE64(a, b) TE16(a, b, 0) TE16(a, b, 1) TE16(a, b, 2) TE16(a, b, 3)
#define TE256(a) TE64(a, 0) TE64(a, 1) TE64(a, 2) TE64(a, 3)
static const fnvnx_deldtor_fn_t stubs[FNVNX_DELDTOR_SLOTS] = {TE256(0) TE256(1) TE256(2) TE256(3)};

fnvnx_deldtor_fn_t fnvnx_deldtor_fn(void (*d0)(void *self), uint32_t orig) {
    uint32_t k;
    const fnvnx_s2_deldtor *t;
    if (!d0) return NULL;
    for (k = 0; k < used; k++)
        if (targets[k].d0 == d0) return stubs[k];
    if (used == FNVNX_DELDTOR_SLOTS) return NULL;
    t = table_find(orig);
    targets[used].d0 = d0;
    targets[used].d1 = t && (uintptr_t)t->d0 == (uintptr_t)d0 ? (dtor_fn)(uintptr_t)t->d1 : NULL;
    targets[used].orig = orig;
    return stubs[used++];
}

uint32_t fnvnx_deldtor_adapter(void *ctx, uint32_t orig, uint32_t native_d0) {
    fnvnx_deldtor_fn_t fn;
    uintptr_t a;
    (void)ctx;
    fn = fnvnx_deldtor_fn((dtor_fn)(uintptr_t)native_d0, orig);
    a = (uintptr_t)fn;
    return a <= 0xffffffffu ? (uint32_t)a : 0;   /* a 64-bit host cannot store it in a 32-bit image word */
}
