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
#define FNVNX_RAWVT_DYNK(vp, k) (k)
#define FNVNX_RAWVT_DEL0(o, flag) ((void *(__thiscall *)(void *, unsigned))(*(void ***)(o))[0])((void *)(o), (unsigned)(flag))
#define FNVNX_RAWVT_FIELD(vt, f) ((vt)->f)
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
/* slot 0 never shifts: an exe call of slot 0 is ??_G(flags); natively slot 0 is the complete destructor D1 (= flag 0)
 * and slot 1 the deleting D0, so the unshifted call is right for flag 0 and leaks for flag 1 - never frees wrongly */
#if defined(__aarch64__)
/* the bounds by PC-relative address (adrp/add), not through GOT words: a vptr is stored by its constructor the same
 * way (adrp/add, the run-time address), while a GOT word or any other stored address keeps the link-time value when
 * the image runs at another address than it was linked for (S2: link 7f0f8526, the emulator's load offset; the
 * SetParent "Worl" stop). An undefined weak symbol's address equals that of __fnvnx_m1_none, which is never defined.
 * The {start, end} words of __fnvnx_m1k_ranges are stored addresses: they get the load delta, the PC-relative
 * address of the table minus a stored word that holds it. */
extern char __fnvnx_m1_none[] __attribute__((weak, visibility("hidden")));
#define FNVNX_PCREL(sym) __extension__({ unsigned long long a_; \
    __asm__(".weak " #sym "\n\t.hidden " #sym "\n\tadrp %0, " #sym "\n\tadd %0, %0, :lo12:" #sym : "=r"(a_)); \
    (unsigned long)a_; })
static const struct fnvnx_m1k_range *const fnvnx_m1k_ranges_word __attribute__((used)) = __fnvnx_m1k_ranges;
static inline unsigned fnvnx_rawvt_m1(const void *vp, unsigned k)
{
    unsigned long v = (unsigned long)vp, s, e;
    if (k == 0) return 0;
    s = FNVNX_PCREL(__fnvnx_m1_start); e = FNVNX_PCREL(__fnvnx_m1_end);
    if (v - s < e - s) return 1;
    s = FNVNX_PCREL(__fnvnx_m1k4_start); e = FNVNX_PCREL(__fnvnx_m1k4_end);
    if (v - s < e - s) return k > 4;
    if (FNVNX_PCREL(__fnvnx_m1k_count) != FNVNX_PCREL(__fnvnx_m1_none)) {
        const struct fnvnx_m1k_range *r = (const struct fnvnx_m1k_range *)FNVNX_PCREL(__fnvnx_m1k_ranges);
        unsigned long i, n = *(const volatile unsigned long *)FNVNX_PCREL(__fnvnx_m1k_count);
        unsigned long d = (unsigned long)r - (unsigned long)*(const struct fnvnx_m1k_range *const volatile *)&fnvnx_m1k_ranges_word;
        for (i = 0; i < n; i++)
            if (v - (r[i].start + d) < r[i].end - r[i].start)
                return k > r[i].k;
    }
    return 0;
}
#else
static inline unsigned fnvnx_rawvt_m1(const void *vp, unsigned k)
{
    if (k == 0) return 0;
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
#endif
#define FNVNX_RAWVT_DYN(vp, k) (vp)[(k) + fnvnx_rawvt_m1((const void *)(vp), (unsigned)(k))]
/* a slot index in pointer arithmetic on the vptr, `*(*(long**)p + 7)`: k + 0/1 */
#define FNVNX_RAWVT_DYNK(vp, k) ((k) + fnvnx_rawvt_m1((const void *)(vp), (unsigned)(k)))
/* a call of the exe's slot 0, the deleting destructor ??_G(flags): natively a minus-one table has D1 (destroy) at 0
 * and D0 (destroy and free) at 1, so flag bit 1 goes to D0; any other table keeps the exe's call */
static inline void *fnvnx_rawvt_del0(void *o, unsigned flag)
{
    void **vp = *(void ***)o;
    if ((flag & 1) && fnvnx_rawvt_m1((const void *)vp, 1)) {
        ((void (*)(void *))vp[1])(o);
        return o;
    }
    return ((void *(*)(void *, unsigned))vp[0])(o, flag);
}
#define FNVNX_RAWVT_DEL0(o, flag) fnvnx_rawvt_del0((void *)(o), (unsigned)(flag))
/* the byte-offset spelling `(char*)*(void**)x + off` (off = 4 * slot): off + 4 when the table is minus-one */
#define FNVNX_RAWVT_DYNOFF(vp, off) ((off) + 4 * fnvnx_rawvt_m1((const void *)(vp), (unsigned)(off) / 4))
/* a field f of a struct that models the vftable, `obj->vt->f(obj, ...)`: the slot of f's offset, with the m1 test
 * (vt must have no side effects; it is read more than once) */
#define FNVNX_RAWVT_FIELD(vt, f) \
    ((__typeof__((vt)->f))((void *const *)(vt))[FNVNX_RAWVT_DYNK((vt), __builtin_offsetof(__typeof__(*(vt)), f) / sizeof(void *))])
#endif
#endif
