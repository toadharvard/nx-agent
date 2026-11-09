/* fnvnx_rawvt.h - RAWVT1-DYN (S1-rules, orchestrator 2026-11-08): a raw exe slot index on a receiver whose class the
 * prepare rules cannot decide (no classlayout row, not resolved at the exe's call site or by a runtime log) is
 * decided at run time. The link places every native minus-one vtable (GCC's D1/D0 pair at slots 0/1 where MSVC has
 * ??_G at 0: classlayout's minus-one classes, the image vftables' minus-one copies) in section .data.rel.ro.fnvnx_m1
 * and defines __fnvnx_m1_start / __fnvnx_m1_end around it (S2; the TESForm family's K = 4 tables in a second range,
 * __fnvnx_m1k4_*, where only k > 4 shifts). A vptr inside it takes slot k + 1; any other table -
 * COM, an SDK object, an exe table of the image - slot k. The bounds are weak: without them (0 / 0) nothing shifts.
 * Included only by the units RAWVT1-DYN rewrites (never force-included). Under MSVC the index is k. */
#ifndef FNVNX_RAWVT_H
#define FNVNX_RAWVT_H
#if defined(_MSC_VER) && !defined(__clang__) || defined(FNVNX_STATIC_MSVC)
#define FNVNX_RAWVT_DYN(vp, k) (vp)[(k)]
#define FNVNX_RAWVT_DYNOFF(vp, off) (off)
#else
#ifdef __cplusplus
extern "C" {
#endif
extern char __fnvnx_m1_start[] __attribute__((weak, visibility("hidden")));
extern char __fnvnx_m1_end[] __attribute__((weak, visibility("hidden")));
/* the minus-one tables whose pair sits at exe slot 4 (D22-SLOTK: the TESForm family): only slots after 4 shift */
extern char __fnvnx_m1k4_start[] __attribute__((weak, visibility("hidden")));
extern char __fnvnx_m1k4_end[] __attribute__((weak, visibility("hidden")));
/* every other K (1, 2, 3, 6, 9, 16 on a9d9f910): {start, end, k} triples, __fnvnx_m1k_count of them (S2's link) */
struct fnvnx_m1k_range { unsigned long start, end, k; };
extern const struct fnvnx_m1k_range __fnvnx_m1k_ranges[] __attribute__((weak, visibility("hidden")));
extern const unsigned long __fnvnx_m1k_count __attribute__((weak, visibility("hidden")));
#ifdef __cplusplus
}
#endif
static inline unsigned fnvnx_rawvt_m1(const void *vp, unsigned k)
{
    if ((unsigned long)vp - (unsigned long)__fnvnx_m1_start < (unsigned long)__fnvnx_m1_end - (unsigned long)__fnvnx_m1_start)
        return 1;
    if ((unsigned long)vp - (unsigned long)__fnvnx_m1k4_start < (unsigned long)__fnvnx_m1k4_end - (unsigned long)__fnvnx_m1k4_start)
        return k > 4;
    if (&__fnvnx_m1k_count) {
        unsigned long i, n = __fnvnx_m1k_count;
        for (i = 0; i < n; i++)
            if ((unsigned long)vp - __fnvnx_m1k_ranges[i].start < __fnvnx_m1k_ranges[i].end - __fnvnx_m1k_ranges[i].start)
                return k > __fnvnx_m1k_ranges[i].k;
    }
    return 0;
}
/* the indexed expression keeps its type: (vp)[k + 0/1] */
#define FNVNX_RAWVT_DYN(vp, k) (vp)[(k) + fnvnx_rawvt_m1((const void *)(vp), (unsigned)(k))]
/* the byte-offset spelling `(char*)*(void**)x + off` (off = 4 * slot): off + 4 when the table is minus-one */
#define FNVNX_RAWVT_DYNOFF(vp, off) ((off) + 4 * fnvnx_rawvt_m1((const void *)(vp), (unsigned)(off) / 4))
#endif
#endif
