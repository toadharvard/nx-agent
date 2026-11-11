/* Thread entry, native -> guest calls and the no-body routine of the packaged runtime (tools/liftall.py). C++, compiled with the lifted-code flags
   (guest registers fixed in w19..w26, c in x27). The exported functions are assembly wrappers that save and restore x19..x28, so ordinary AAPCS code
   (loader, platform) calls them like any C function:

     u32  fnvnx_lift_enter(u32 exe_eip, u32 esp, u32 teb)
          Runs the guest function at image address exe_eip on a fresh thread state (struct cpu in this frame): guest registers zero except esp, fs base =
          teb, fcw 0x027f, mxcsr 0x1f80, DF 0. esp must point at a free dword: the runtime stores the return sentinel FNVL_SENT there (arguments of the
          function, if any, are above it). Returns eax when the function returns to the sentinel.
     void fnvnx_lift_attach(void *cpu_mem, u32 esp, u32 teb)        cpu_mem: fnvnx_lift_cpu_bytes bytes, 16-aligned, alive until the detach
     void fnvnx_lift_detach(void)
          A host thread that does not start in guest code but calls back into it (fnvnx_guest_invoke) attaches a guest thread state first.
     void fnvnx_guest_invoke(u32 addr, u32 ecx, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out)
          Native code calls the guest function at `addr` on the current thread's guest state: pushes args[nargs-1] .. args[0] and the sentinel below
          the guest esp of the import call in progress (or of the attach), sets ecx (the `this` of a thiscall; any value otherwise), runs the function
          and returns eax, edx and, for kind 2, the popped ST0. It works for stdcall and cdecl alike: the activation is over when a `ret` pops the
          sentinel with esp above the pushed frame, and the guest esp is then reset to its value before the call.
          kind: 0 integer / void / pointer result, 1 64-bit integer (edx:eax), 2 float or double in ST0.
     void fnvnx_guest_current(u32 *esp, u32 *teb)
          The guest esp and TEB of the calling host thread's guest state (inside an import call: the esp of that call).
   Host hooks the runtime needs (provided by the loader / platform):
     struct cpu **fnvl_host_tls_slot(void)      a pointer-sized slot private to the calling host thread, zero in a new thread
     unsigned long long fnvl_host_ticks_ns(void)  (cpumodel.c)

   The loop in each of them is the non-local-transfer loop of every lifted function (README): while the address the last `ret` popped (c->ra) is not
   the sentinel of this activation, serve it as an entry through the dispatcher. */
#include "fnvl.h"
#include "imp.h"
struct fnvnx_guest_ret { u32 eax, edx; double st0; };
extern "C" {
void fnvl_raise_ud(u32 addr);
struct cpu **fnvl_host_tls_slot(void);
u32 fnvl_thread_main(u32 eip, u32 sp, u32 teb) __attribute__((noinline));
void fnvl_attach_main(void *mem, u32 sp, u32 teb) __attribute__((noinline));
void fnvl_detach_main(void) __attribute__((noinline));
void fnvl_current_main(u32 *sp, u32 *teb) __attribute__((noinline));
void fnvl_invoke_main(u32 addr, u32 ecxv, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out) __attribute__((noinline));
void fnvl_nobody(void);
extern const u32 fnvnx_lift_cpu_bytes;
}
static_assert(__builtin_offsetof(struct cpu, ra) == 248, "rt.c FNVL_PHF_REL stores the missed address at c->ra (offset 248)");
const u32 fnvnx_lift_cpu_bytes = sizeof(struct cpu);

static void cpu_reset(struct cpu *p, u32 sp, u32 teb)
{
    u32 *z = (u32 *)p;
    for (u32 i = 0; i < sizeof *p / 4; i++) z[i] = 0;
    p->r[4] = sp; fnvl_seg_init(p, teb); p->fcw = 0x027f; p->mxcsr = 0x1f80;      /* segment state: process constants, fs base = teb (rt/fnvl.h) */
}
static inline void fp_set(u32 fpcr, u32 fpsr) { u64 a = fpcr, b = fpsr; __asm__ volatile("msr fpcr, %0\n msr fpsr, %1" :: "r"(a), "r"(b)); }
static inline void fp_park(void)
{
    u64 a, b;
    __asm__ volatile("mrs %0, fpcr\n mrs %1, fpsr" : "=r"(a), "=r"(b));
    c->hfpcr = (u32)a; c->hfpsr = (u32)b;
    a = 0; __asm__ volatile("msr fpcr, %0" :: "r"(a));
}

extern "C" u32 fnvl_thread_main(u32 eip, u32 sp, u32 teb)
{
    struct cpu cpu;
    cpu_reset(&cpu, sp, teb);
    c = &cpu; *fnvl_host_tls_slot() = c;
    eax = ecx = edx = ebx = ebp = esi = edi = 0; esp = sp;
    fp_set(0, 0);
    M32(sp) = FNVL_SENT;
    cpu.ra = eip;
    do { u32 t = c->ra; REGS(); fnvl_dispatch(t)(); REGS(); } while (!(c->ra == FNVL_SENT && esp > sp));
    u32 r = eax;
    fp_set(0, 0);
    *fnvl_host_tls_slot() = 0;
    return r;
}

extern "C" void fnvl_attach_main(void *mem, u32 sp, u32 teb)
{
    struct cpu *p = (struct cpu *)mem;
    cpu_reset(p, sp, teb);
    *fnvl_host_tls_slot() = p;
}

extern "C" void fnvl_detach_main(void) { *fnvl_host_tls_slot() = 0; }

/* esp and TEB of the guest thread this host thread carries: during an import call, the esp of the call (it points at the return address) */
extern "C" void fnvl_current_main(u32 *sp, u32 *teb) { struct cpu *p = *fnvl_host_tls_slot(); *sp = p->r[4]; *teb = p->teb; }

extern "C" void fnvl_invoke_main(u32 addr, u32 ecxv, u32 nargs, const u32 *args, u32 kind, struct fnvnx_guest_ret *out)
{
    c = *fnvl_host_tls_slot();
    u32 base = c->r[4];
    eax = c->r[0]; ecx = ecxv; edx = c->r[2]; ebx = c->r[3]; esp = base; ebp = c->r[5]; esi = c->r[6]; edi = c->r[7];
    fp_set(c->hfpcr, c->hfpsr);
    for (u32 i = nargs; i--; ) { esp -= 4; M32(esp) = args[i]; }
    esp -= 4; M32(esp) = FNVL_SENT;
    u32 sp0 = esp;
    c->ra = addr;
    for (;;) {
        u32 t = c->ra; REGS(); fnvl_dispatch(t)(); REGS();
        if (esp > sp0) {
            if (c->ra == FNVL_SENT) break;
            if (c->nf) __builtin_longjmp((void **)c->nf->jb, 1);       /* left to a frame older than this callback: imp.h */
        }
    }
    out->eax = eax; out->edx = edx; out->st0 = 0;
    if (kind == 2) { out->st0 = fv_d(dyn_rd(0)); dyn_pop(); }
    c->r[4] = base;
    fp_park();
}

#define WRAP(name, body) \
    __asm__(".text\n.globl " #name "\n.type " #name ",%function\n" #name ":\n" \
            " stp x29,x30,[sp,#-112]!\n mov x29,sp\n stp x19,x20,[sp,#16]\n stp x21,x22,[sp,#32]\n stp x23,x24,[sp,#48]\n stp x25,x26,[sp,#64]\n stp x27,x28,[sp,#80]\n" \
            " bl " #body "\n" \
            " ldp x19,x20,[sp,#16]\n ldp x21,x22,[sp,#32]\n ldp x23,x24,[sp,#48]\n ldp x25,x26,[sp,#64]\n ldp x27,x28,[sp,#80]\n ldp x29,x30,[sp],#112\n ret\n" \
            ".size " #name ",.-" #name "\n");
WRAP(fnvnx_lift_enter, fnvl_thread_main)
WRAP(fnvnx_lift_attach, fnvl_attach_main)
WRAP(fnvnx_lift_detach, fnvl_detach_main)
WRAP(fnvnx_guest_invoke, fnvl_invoke_main)
WRAP(fnvnx_guest_current, fnvl_current_main)

/* The routine an address that is not a key of the dispatcher table resolves to (rt/rt.c): #UD at that address, through the SEH runtime (rt/seh.cc).
   The table holds every entry of the universe (tools/liftall.py --finish fails otherwise), so no relocated code pointer and no direct target gets here. */
extern "C" void fnvl_nobody(void) { REGS(); fnvl_raise_ud(c->ra); REGS(); }
