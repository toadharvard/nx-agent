/* Trap stubs for code pointers in the image whose original target has no native implementation: each stub knows
 * the original address it stands for and reports it when called (docs/LINK.md asks for exactly this). */
#include "internal.h"
#include <stdio.h>
#include <stdlib.h>

static uint32_t origs[FNVNX_TRAP_SLOTS];
static uint32_t used;

static void default_report(uint32_t orig) {
    fprintf(stderr, "fnvnx: call of original function 0x%08x, which has no native implementation\n", (unsigned)orig);
    abort();
}
void (*fnvnx_trap_report)(uint32_t orig) = default_report;

static void *trap_call(unsigned k) {
    fnvnx_trap_report(origs[k]);
    return NULL;
}

/* 4^6 stubs; the arguments of the original call are ignored */
#define TS(a, b, c, d, e, g) \
    static void *ts_##a##b##c##d##e##g(void) { return trap_call(a * 1024 + b * 256 + c * 64 + d * 16 + e * 4 + g); }
#define TS4(a, b, c, d, e) TS(a, b, c, d, e, 0) TS(a, b, c, d, e, 1) TS(a, b, c, d, e, 2) TS(a, b, c, d, e, 3)
#define TS16(a, b, c, d) TS4(a, b, c, d, 0) TS4(a, b, c, d, 1) TS4(a, b, c, d, 2) TS4(a, b, c, d, 3)
#define TS64(a, b, c) TS16(a, b, c, 0) TS16(a, b, c, 1) TS16(a, b, c, 2) TS16(a, b, c, 3)
#define TS256(a, b) TS64(a, b, 0) TS64(a, b, 1) TS64(a, b, 2) TS64(a, b, 3)
#define TS1024(a) TS256(a, 0) TS256(a, 1) TS256(a, 2) TS256(a, 3)
TS1024(0) TS1024(1) TS1024(2) TS1024(3)

#define TT(a, b, c, d, e, g) ts_##a##b##c##d##e##g,
#define TT4(a, b, c, d, e) TT(a, b, c, d, e, 0) TT(a, b, c, d, e, 1) TT(a, b, c, d, e, 2) TT(a, b, c, d, e, 3)
#define TT16(a, b, c, d) TT4(a, b, c, d, 0) TT4(a, b, c, d, 1) TT4(a, b, c, d, 2) TT4(a, b, c, d, 3)
#define TT64(a, b, c) TT16(a, b, c, 0) TT16(a, b, c, 1) TT16(a, b, c, 2) TT16(a, b, c, 3)
#define TT256(a, b) TT64(a, b, 0) TT64(a, b, 1) TT64(a, b, 2) TT64(a, b, 3)
#define TT1024(a) TT256(a, 0) TT256(a, 1) TT256(a, 2) TT256(a, 3)
static void *(*const stubs[FNVNX_TRAP_SLOTS])(void) = {TT1024(0) TT1024(1) TT1024(2) TT1024(3)};

fnvnx_trap_fn_t fnvnx_trap_fn(uint32_t orig) {
    uint32_t k;
    for (k = 0; k < used; k++)
        if (origs[k] == orig) return stubs[k];
    if (used == FNVNX_TRAP_SLOTS) return NULL;
    origs[used] = orig;
    return stubs[used++];
}

uint32_t fnvnx_trap_value(void *ctx, uint32_t orig) {
    uintptr_t a = (uintptr_t)fnvnx_trap_fn(orig);
    (void)ctx;
    return a <= 0xffffffffu ? (uint32_t)a : 0;   /* a 64-bit host cannot store it in a 32-bit image word */
}
