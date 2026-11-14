/* fnvnx_dispatch: the runtime of DISPATCH1 (toolchain/dispatch/fnvnx_dispatch.cc is the contract; this file is its one implementation).
   Every indirect call of native code whose target is not a named function calls `fnvnx_dispatch(fp, &site, desc)` on a miss of its site cache:

     fp    an exe code address (code pointers in the image are exe addresses and stay so)
     site  the call site's own 64-bit cache word, low half = fp, high half = the entry
     desc  the call's shape, `conv:ret:arg,arg,..` (the plugin's string literal)

   RULE, in this order, a function of (fp, desc) and of the three read-only tables below:
     1. a native root defines the body of exe address fp: the entry is that body. The table is fnvnx_native_disp_table (tools/dispgen.py --prefix
        fnvnx_native over contract/nativerows.py's rows: the symbols bound to exe addresses by the plugins' facts and the body selector's NATIVE
        verdicts; one source for it, the same bind and bodysel facts as everything else).
     2. else fp is a key of the lifted dispatcher (rt/rt.c: an entry of the lifted universe, or an import key): the entry is the BRIDGE of desc, the
        function tools/bridgegen.py generated for that descriptor (rt/bridges). A bridge has the call site's AAPCS signature, marshals its arguments
        into the x86 frame, ecx and edx by the descriptor's convention, runs the guest function at fnvnx_call_fp (the word the call sequence stored
        just before the call) with fnvnx_guest_invoke_regs, and returns eax / edx:eax / ST0 by the descriptor's return class.
        The bridge is found by exact lookup: FNV-1a of desc through the minimal perfect hash of the descriptors of every admitted object. Every
        descriptor that can reach this function is a string the plugin wrote into some admitted object's section .fnvnx.disp, and bridgegen builds
        a bridge for each of them and fails the build for any it cannot (descriptors of an unknown form, shapes the x86 convention does not
        define). So the lookup is total over what can reach it, and there is no "not found" branch to take.
     3. otherwise the address is no entry: the CPU would raise #UD at fp. The guest runtime does exactly that for an address that is not a key of
        its table (fnvl_nobody, rt/enter.cc): fnvnx_guest_invoke(fp) dispatches it, the SEH runtime builds the exception and runs the handlers.
        A handler that continues execution resumes at fp, the faulting address, so the exception is raised again (a #UD at fp is not skipped).
        This function does not return.
   The site word is written with one 64-bit store, so a reader (the hit path: one 64-bit load) sees the key of one target with the entry of that
   target. The tables are read-only. fnvnx_call_fp is thread-local: each host thread has its own (defined by the platform). */
#include "fnvl_host.h"          /* fnvnx_call_fp: one definition, the platform's (platform/win32/thread.cpp); one declaration, this header */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned long long u64;
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
void fnvnx_guest_invoke(u32 addr, u32 ecx, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out);
int fnvl_has_entry(u32 a);

extern const u32 fnvnx_native_disp_table[];
extern const u16 fnvnx_native_g[];
extern const u32 fnvnx_native_phf_params[4];

struct fnvnx_bridge_row { u32 key; const char *desc; void (*fn)(void); };
extern const struct fnvnx_bridge_row fnvnx_bridge_table[];
extern const u16 fnvnx_bridge_g[];
extern const u32 fnvnx_bridge_phf_params[4];

static u32 desc_key(const char *s)       /* FNV-1a, 32 bit: tools/bridgegen.py computes the same function */
{
    u32 h = 2166136261u;
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}

void *fnvnx_dispatch(u32 fp, u64 *site, const char *desc)
{
    void *e;
    const u32 *q = fnvnx_native_phf_params;
    const u32 *s = fnvnx_native_disp_table + 2 * (((fp ^ fnvnx_native_g[(fp * q[0]) >> (32 - q[2])]) * q[1]) >> (32 - q[3]));
    if (s[0] == fp && s[1]) {
        e = (void *)((const char *)fnvnx_native_disp_table + (int)s[1]);
    } else if (fnvl_has_entry(fp)) {
        u32 k = desc_key(desc);
        q = fnvnx_bridge_phf_params;
        e = (void *)fnvnx_bridge_table[((k ^ fnvnx_bridge_g[(k * q[0]) >> (32 - q[2])]) * q[1]) >> (32 - q[3])].fn;
    } else {
        struct fnvnx_guest_ret out;
        for (;;) fnvnx_guest_invoke(fp, 0, 0, 0, 0, &out);
    }
    __atomic_store_n(site, ((u64)(u32)e << 32) | fp, __ATOMIC_RELAXED);
    return e;
}
