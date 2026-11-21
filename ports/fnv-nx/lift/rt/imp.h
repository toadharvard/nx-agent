/* Import entries and native <-> guest transitions (included after fnvl.h by the generated imp.cpp and by enter.cc; lifted-code flags).

   An import entry I_<key> is the guest entry of one row of the bridge table (tools/impgen.py): arguments from the guest stack by the row's
   prototype, the native call under the AAPCS, the result into eax / edx:eax / ST0, `ret N` applied, c->ra = the popped return address.

   While native code runs:
   - c->r[0..7] hold the guest registers of the import call (esp = the address of the return address). A callback into guest code
     (fnvnx_guest_invoke, enter.cc) starts from them and pushes below that esp.
   - the host FPCR is the default (round to nearest, no flush-to-zero); the guest's FPCR and FPSR are parked in c->hfpcr / c->hfpsr and come back on
     return, so native arithmetic neither sees the guest rounding mode nor leaves bits in the guest's sticky flags. A callback runs with the parked
     values and parks what it leaves (an x86 callback's control-word change survives its return).
   - the entry's fnvl_nframe is the top of c->nf. When guest code running in a callback leaves by a non-local transfer to a frame older than the
     callback (RtlUnwind to an outer handler, longjmp), the callback loop cannot return through the native frames: it jumps to the frame's
     __builtin_setjmp point with the guest registers live, the entry returns to its lifted caller with c->ra pending, and that caller's non-local
     loop goes on (README, control-transfer rule, case 1). The native frames in between are discarded without running native cleanup. */
struct fnvl_nframe { struct fnvl_nframe *prev; u64 jb[5]; };     /* __builtin_setjmp stores 64-bit words (fp, resume pc, sp) whatever the pointer size */
#define FNVL_SENT 0x000ffff0u      /* return address of a guest activation started by the runtime; below every key and every image */
/* A transfer from lifted code into a native function: the caller's guest registers go to c->r[] (esp included) so that a bridge called from the native body
   (fnvnx_guest_invoke_regs) finds them, and the frame is linked for non-local exits. FP is NOT touched: guest -> native-body is guest -> guest (rule R-FP, G1).
   The ONE definition of the save; fnvl_native_in (an import entry) is this plus the park. */
static inline __attribute__((always_inline)) void fnvl_native_enter(struct fnvl_nframe *nf)
{
    c->r[0] = eax; c->r[1] = ecx; c->r[2] = edx; c->r[3] = ebx; c->r[4] = esp; c->r[5] = ebp; c->r[6] = esi; c->r[7] = edi;
    nf->prev = c->nf; c->nf = nf;
}
static inline __attribute__((always_inline)) void fnvl_native_in(struct fnvl_nframe *nf)
{
    u64 a, b;
    fnvl_native_enter(nf);
    __asm__ volatile("mrs %0, fpcr\n mrs %1, fpsr" : "=r"(a), "=r"(b));
    c->hfpcr = (u32)a; c->hfpsr = (u32)b;
    if (a) { a = 0; __asm__ volatile("msr fpcr, %0" :: "r"(a)); }
}
static inline __attribute__((always_inline)) void fnvl_native_leave(struct fnvl_nframe *nf) { c->nf = nf->prev; }
static inline __attribute__((always_inline)) void fnvl_native_out(struct fnvl_nframe *nf)
{
    u64 a = c->hfpcr, b = c->hfpsr;
    __asm__ volatile("msr fpsr, %0" :: "r"(b));
    if (a) __asm__ volatile("msr fpcr, %0" :: "r"(a));
    fnvl_native_leave(nf);
}
/* x87 result of a native function: pushed as a binary64 carrier (the dynamic stack image every lifted call site syncs with) */
static inline void fnvl_ret_st0(double d) { dyn_push(fv_f64(d)); }

/* The two halves of a raise from native code under an import (rt/unhandled.cc, rt/enter.cc). Native code running under an import entry has c->nf set (fnvl_native_in links the frame
   before the native call; every native entry from guest code is fnvl_native_in or fnvl_native_enter), so a non-local exit always has the setjmp point of that entry.
   fnvl_native_guest: the guest state of the import call back in the registers (c->r[], FPCR/FPSR from the park) -> the esp of the call (it points at the return address).
   fnvl_native_exit: leave the native frames by the non-local exit to the entry's __builtin_setjmp point; the entry returns to its lifted caller with c->ra pending. Does not return. */
extern "C" u32 fnvl_native_guest(void);
extern "C" __attribute__((noreturn)) void fnvl_native_exit(void);
/* The one non-local transfer of an entry body (a guest-state body whose bridge row is bind entry): control goes to guest address `eip` with the guest stack at `esp` and the registers as the body
   left them, and the bridge does not run its return code (it would pop the entry's own return address). Sets ra and esp, then leaves through fnvl_native_exit to the bridge's frame; the
   bridge returns to its lifted caller with ra pending (README control-transfer rule). Does not return. Defined in rt/enter.cc, the only entry-side TU that may write ra (rt/fnvl.h). */
extern "C" __attribute__((noreturn)) void fnvl_native_transfer(u32 eip, u32 esp);
/* The raise protocol's resume address (rt/seh.cc ContinueExecution, the DBGSRV service): a hardware raise is called by lifted code directly, with no entry frame, and returns with ra = the
   resume address; the lifted caller then goes on from ra (README, control-transfer rule). This sets ra and nothing else. Defined in rt/enter.cc beside fnvl_native_transfer. */
extern "C" void fnvl_raise_resume(u32 eip);
