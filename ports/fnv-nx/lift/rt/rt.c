/* Runtime of lifted code: the dispatcher `image address or import key -> guest entry`.
   Compiled WITHOUT -ffixed-x19..x27 on purpose: it is ordinary AAPCS code, and lifted code must find the guest registers
   (w19..w26) and the cpu pointer (x27) unchanged after a call into it. It includes no lifted header for the same reason.
   Build (ILP32): aarch64-linux-gnu-gcc -mabi=ilp32 -O2 -fPIC -ffreestanding -fno-stack-protector -fno-asynchronous-unwind-tables -c rt.c

   The table (tools/phf.py table_asm; tools/liftgen.py --finish and the benches write it) is position independent: slots {key, offset of the entry from
   fnvl_disp_table} (an empty slot: a key that does not hash to it, offset 0), the displacement table fnvl_g and the hash parameters fnvl_phf_params
   {K1, K2, NBITS, TBITS}: bucket = (a*K1)>>(32-NBITS); slot = ((a ^ g[bucket]) * K2) >> (32-TBITS). The table holds every entry of the universe, so a
   relocated pointer always hits. Any other address resolves to fnvl_nobody (rt/enter.cc), which raises #UD at that address through the SEH runtime; the
   address travels in c->ra (the callers of the dispatcher hold the thread's struct cpu in x27; c->ra is at offset 248, checked by rt/enter.cc). */
#include "fnvl_abi.h"
typedef unsigned int u32;
typedef unsigned short u16;
extern const u32 fnvl_disp_table[];
extern const u16 fnvl_g[];
extern const u32 fnvl_phf_params[4];
extern void fnvl_nobody(void);
static const u32 *fnvl_slot(u32 a)
{
    const u32 *q = fnvl_phf_params;
    u32 i = ((a ^ fnvl_g[(a * q[0]) >> (32 - q[2])]) * q[1]) >> (32 - q[3]);
    const u32 *s = fnvl_disp_table + 2 * i;
    return s[0] == a && s[1] ? s : 0;
}
/* Is `a` a key of the table (an entry of the lifted universe, an import key, a runtime key)? Pure: touches no guest state, so ordinary AAPCS code
   (rt/dispatch.c, the dispatch of native indirect calls) may ask it. */
int fnvl_has_entry(u32 a) { return fnvl_slot(a) != 0; }
void (*fnvl_dispatch(u32 a))(void)
{
    const u32 *s = fnvl_slot(a);
    if (s) return (void (*)(void))((const char *)fnvl_disp_table + (int)s[1]);
    u32 *cp; __asm__("mov %w0, w27" : "=r"(cp)); cp[FNVL_CPU_RA_OFF / 4] = a;     /* c->ra; the offset is checked against struct cpu by rt/fnvl.h */
    return fnvl_nobody;
}
