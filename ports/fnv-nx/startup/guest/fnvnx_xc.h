/* fnvnx_xc.h - guest static initialization (stream S10, docs/STARTUP.md).
 *
 * The original executable runs its C++ dynamic initializers from the MSVC CRT table __xc_a..__xc_z
 * (_initterm). Natively:
 *
 *   initializers  one native function per original initializer, keyed by its original address, in a table
 *                 generated at link time (startup/tools/mkxctab.py -> fnvnx_xc_table, sorted by address).
 *                 Mode ADDRESS (no exe): run in original address order, which is the __xc order for the
 *                 exe's own objects (docs/STARTUP.md, "Why address order"). Mode IMAGE (exe mapped by S3):
 *                 run in the order of the image's own __xc table, captured before S3 rewrites code
 *                 pointers; the image only decides the order, the natives always come from fnvnx_xc_table.
 *   Destructors  the destructors the initializers register go through newlib's atexit, the one LIFO list that
 *                 __cxa_atexit also uses.
 *
 * Portable C99 (+ GCC __atomic builtins); compiles for the ILP32 guest and for LP64 host tests.
 */
#ifndef FNVNX_XC_H
#define FNVNX_XC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*fnvnx_pvfv)(void);

/* One original initializer: its address in FalloutNV.exe 1.4.0.525 and the native function. */
struct fnvnx_xc_entry {
    uint32_t orig;
    fnvnx_pvfv fn;
};

/* The generated table (xctab.c from mkxctab.py) defines, sorted by strictly increasing orig:
 *     const struct fnvnx_xc_entry fnvnx_xc_table[];   const uint32_t fnvnx_xc_table_count;
 *     const uint32_t fnvnx_xc_group_lo, fnvnx_xc_group_hi;   (the original initializer group, .text$yc)
 * xc.c references them weakly: a guest linked without xctab.c has an empty table. */

/* Results (negative = nothing was run). */
#define FNVNX_XC_OK           0
#define FNVNX_XC_E_UNSORTED  (-1)   /* table not strictly increasing, or a NULL native */
#define FNVNX_XC_E_NOMEM     (-2)
#define FNVNX_XC_E_NOTFOUND  (-3)   /* locate: no run of initializer pointers */
#define FNVNX_XC_E_ARG       (-4)

/* Run flags. */
#define FNVNX_XC_RUN_LEFTOVERS 0x1u /* IMAGE: afterwards also run (address order) natives the image never named */
#define FNVNX_XC_ALL_ENTRIES   0x2u /* ADDRESS: run every table entry, not only those inside the group */

#define FNVNX_XC_MODE_ADDRESS 1u
#define FNVNX_XC_MODE_IMAGE   2u

#define FNVNX_XC_SAMPLES 16

typedef struct fnvnx_xc_hooks {
    void *ctx;
    /* Optional, around every initializer call; seq counts calls from 0. */
    void (*before)(void *ctx, uint32_t orig, uint32_t seq);
    void (*after)(void *ctx, uint32_t orig, uint32_t seq);
} fnvnx_xc_hooks;

typedef struct fnvnx_xc_report {
    uint32_t mode;              /* FNVNX_XC_MODE_* */
    uint32_t table_entries;     /* natives in the table */
    uint32_t called;            /* initializer calls made */
    uint32_t skipped_outside;   /* ADDRESS: table entries outside [group_lo, group_hi) not run */
    uint32_t image_slots;       /* IMAGE: slots in the captured __xc span */
    uint32_t image_null;        /* IMAGE: NULL slots, skipped as _initterm does */
    uint32_t image_missing;     /* IMAGE: slots naming an address with no native (undecompiled, CRT, libcpmt) */
    uint32_t image_duplicates;  /* IMAGE: slots naming an address already called (called again, as MSVC would) */
    uint32_t not_in_image;      /* IMAGE: group natives the image never named */
    uint32_t leftovers_called;  /* IMAGE + FNVNX_XC_RUN_LEFTOVERS: of those, called afterwards */
    uint32_t inversions;        /* IMAGE: called entries whose address is below the previous one (0: image
                                   order == address order, the claim of docs/STARTUP.md holds for this exe) */
    uint32_t n_missing, n_not_in_image, n_inversions;   /* valid entries in the samples below */
    uint32_t missing_sample[FNVNX_XC_SAMPLES];
    uint32_t not_in_image_sample[FNVNX_XC_SAMPLES];
    uint32_t inversion_sample[FNVNX_XC_SAMPLES];        /* address of the entry that went backwards */
    int32_t bad_index;          /* FNVNX_XC_E_UNSORTED: offending table index */
} fnvnx_xc_report;

/* Checks orig strictly increasing and every fn non-NULL. Returns FNVNX_XC_OK or FNVNX_XC_E_UNSORTED. */
int fnvnx_xc_check_table(const struct fnvnx_xc_entry *t, uint32_t n, int32_t *bad_index);

/* Binary search; NULL if orig has no native. */
const struct fnvnx_xc_entry *fnvnx_xc_find(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t orig);

/* Mode ADDRESS: call the natives with group_lo <= orig < group_hi in address order (all of them with
 * FNVNX_XC_ALL_ENTRIES). The table is checked first; nothing runs if it is malformed. */
int fnvnx_xc_run_address_order(const struct fnvnx_xc_entry *t, uint32_t n, uint32_t group_lo, uint32_t group_hi,
                               uint32_t flags, const fnvnx_xc_hooks *hooks, fnvnx_xc_report *rep);

/* Mode IMAGE: slots = original (pre-fixup) words of the image's __xc span, in table order. NULL slots are
 * skipped; a slot whose address has a native calls it; others are counted as missing. Natives inside the
 * group that no slot names are reported (and run afterwards with FNVNX_XC_RUN_LEFTOVERS). */
int fnvnx_xc_run_image_order(const struct fnvnx_xc_entry *t, uint32_t n, const uint32_t *slots, uint32_t nslots,
                             uint32_t group_lo, uint32_t group_hi, uint32_t flags, const fnvnx_xc_hooks *hooks,
                             fnvnx_xc_report *rep);

/* Finds the image's __xc span in mapped data (e.g. .rdata) holding ORIGINAL values, i.e. before S3's code
 * pointer fixups: mem[i] is the word at va_lo + 4*i. Among the maximal runs of words that are 0 or inside
 * [text_lo, text_hi), takes the one with most words inside [group_lo, group_hi) (only __xc points there),
 * and from its first to last such word extends outwards across non-NULL code pointers (library __xc
 * entries) up to the NULL sentinels __xc_a / __xc_z. Out: [*begin_va, *end_va), slot VAs. Needs at least
 * min_hits group words, else FNVNX_XC_E_NOTFOUND. */
int fnvnx_xc_locate(const uint32_t *mem, uint32_t va_lo, uint32_t nwords, uint32_t text_lo, uint32_t text_hi,
                    uint32_t group_lo, uint32_t group_hi, uint32_t min_hits, uint32_t *begin_va, uint32_t *end_va,
                    uint32_t *hits);

/* The C initializer table __xi_a..__xi_z (_initterm runs it before __xc, MSVC's .CRT$XI*), which the linker places
 * after __xc_z: from xc_end_va (the located __xc span's end), past at most 4 NULL words (__xc_z, __xi_a, padding), the
 * run of code pointers up to the next NULL (__xi_z). [*begin_va, *end_va) may be empty. FNVNX_XC_E_NOTFOUND when
 * xc_end_va is outside mem or no NULL ends the run. */
int fnvnx_xi_locate(const uint32_t *mem, uint32_t va_lo, uint32_t nwords, uint32_t xc_end_va, uint32_t text_lo,
                    uint32_t text_hi, uint32_t *begin_va, uint32_t *end_va);

/* One line per counter and the samples, for the host log. Returns the length written (truncated to cap). */
size_t fnvnx_xc_format_report(const fnvnx_xc_report *rep, char *buf, size_t cap);

/* Startup entry for the guest runtime (S5): runs the generated table. img_slots == NULL -> ADDRESS mode;
 * else IMAGE mode with the captured slots. Group bounds come from the generated table. */
int fnvnx_xc_run_initializers(const uint32_t *img_slots, uint32_t img_nslots, uint32_t flags,
                              const fnvnx_xc_hooks *hooks, fnvnx_xc_report *rep);

#ifdef __cplusplus
}
#endif
#endif
