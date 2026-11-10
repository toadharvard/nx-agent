/* Runtime of lifted code: the dispatcher and the native -> lifted entry bridge.
   Compiled WITHOUT -ffixed-x19..x27 on purpose: it is ordinary AAPCS code, and lifted code must find the guest registers
   (w19..w26) and the cpu pointer (x27) unchanged after a call into it. It includes no lifted header for the same reason.
   Build (ILP32): aarch64-linux-gnu-gcc -mabi=ilp32 -O2 -fPIC -ffreestanding -fno-stack-protector -fno-asynchronous-unwind-tables -c rt.c
   The entry table (fnvl_tab, fnvl_ntab, and for FNVL_PHF the hash parameters) is generated: bench/chain.py writes it. */
typedef unsigned int u32;
typedef unsigned short u16;
struct fnvl_ent { u32 a; void (*f)(void); };
extern const struct fnvl_ent fnvl_tab[];
extern const u32 fnvl_ntab;

/* Bench hooks. The test harness (Unicorn) stops or records at these addresses; the real runtime replaces them (SEH dispatch, trace off). */
__attribute__((weak, noinline)) void fnvl_raise_ud(u32 addr) { __asm__ volatile("" :: "r"(addr) : "memory"); }
__attribute__((weak, noinline)) void fnvl_raise(u32 code, u32 at) { __asm__ volatile("" :: "r"(code), "r"(at) : "memory"); }
__attribute__((weak, noinline)) void fnvl_trace(u32 entry, u32 esp) { __asm__ volatile("" :: "r"(entry), "r"(esp) : "memory"); }
static void fnvl_miss(void) {}

#ifdef FNVL_PHF
/* Generated perfect hash (bench/phf.py): bucket = (a*K1)>>(32-NBITS); idx = ((a ^ g[bucket]) * K2) >> (32-TBITS); the slot holds the key, so a
   pointer that is not an entry is caught by the compare (cannot happen for a relocated pointer: the table is total by construction). */
extern const u16 fnvl_g[];
void (*fnvl_dispatch(u32 a))(void)
{
    u32 i = ((a ^ fnvl_g[(a * FNVL_K1) >> (32 - FNVL_NBITS)]) * FNVL_K2) >> (32 - FNVL_TBITS);
    if (fnvl_tab[i].a == a) return fnvl_tab[i].f;
    fnvl_raise_ud(a);
    return fnvl_miss;
}
#else
/* Reference implementation: binary search over the table sorted by address. */
void (*fnvl_dispatch(u32 a))(void)
{
    u32 lo = 0, hi = fnvl_ntab;
    while (lo < hi) {
        u32 m = (lo + hi) >> 1, k = fnvl_tab[m].a;
        if (k == a) return fnvl_tab[m].f;
        if (k < a) lo = m + 1; else hi = m;
    }
    fnvl_raise_ud(a);
    return fnvl_miss;
}
#endif

/* void fnvl_enter(struct cpu *c, void (*fn)(void)): native code calls a lifted entry. Saves x19..x28 (AAPCS callee-saved), loads the guest
   registers from c->r[0..7] into w19..w26, c into x27, calls fn, stores the guest registers back to c->r and restores. */
__asm__(".text\n.globl fnvl_enter\n.type fnvl_enter,%function\nfnvl_enter:\n"
        " stp x29,x30,[sp,#-112]!\n mov x29,sp\n stp x19,x20,[sp,#16]\n stp x21,x22,[sp,#32]\n stp x23,x24,[sp,#48]\n stp x25,x26,[sp,#64]\n"
        " stp x27,x28,[sp,#80]\n"
        " mov w27,w0\n mov w9,w1\n ldp w19,w20,[x27]\n ldp w21,w22,[x27,#8]\n ldp w23,w24,[x27,#16]\n ldp w25,w26,[x27,#24]\n"
        " blr x9\n"
        " stp w19,w20,[x27]\n stp w21,w22,[x27,#8]\n stp w23,w24,[x27,#16]\n stp w25,w26,[x27,#24]\n"
        " ldp x19,x20,[sp,#16]\n ldp x21,x22,[sp,#32]\n ldp x23,x24,[sp,#48]\n ldp x25,x26,[sp,#64]\n ldp x27,x28,[sp,#80]\n ldp x29,x30,[sp],#112\n ret\n"
        ".size fnvl_enter,.-fnvl_enter\n");
