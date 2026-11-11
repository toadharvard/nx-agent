/* Thread entry and the no-body routine of the packaged runtime (tools/liftall.py). C++, compiled with the lifted-code flags (guest registers fixed).
     u32 fnvnx_lift_enter(u32 exe_eip, u32 esp, u32 teb)
   Runs the guest function at image address exe_eip on a fresh thread state: struct cpu in this frame, x19..x28 saved and restored, guest registers zero except
   esp, fs base = teb, fcw 0x027f, mxcsr 0x1f80, DF 0. M32(esp) is the sentinel return address. The entry is called through the dispatcher; the loop below is the
   non-local-transfer loop of every lifted function: while the address the last `ret` popped (c->ra) is not the sentinel, serve it as an entry. Returns eax. */
#include "fnvl.h"
extern "C" {
void fnvl_raise_ud(u32 addr);
u32 fnvl_thread_main(u32 eip, u32 sp, u32 teb) __attribute__((noinline));
void fnvl_nobody(void);
}
static_assert(__builtin_offsetof(struct cpu, ra) == 320, "rt.c FNVL_PHF_REL stores the missed address at c->ra (offset 320)");

extern "C" u32 fnvl_thread_main(u32 eip, u32 sp, u32 teb)
{
    struct cpu cpu;
    u32 *z = (u32 *)&cpu;
    for (u32 i = 0; i < sizeof cpu / 4; i++) z[i] = 0;
    c = &cpu; eax = ecx = edx = ebx = ebp = esi = edi = 0; esp = sp;
    cpu.fs = teb; cpu.fcw = 0x027f; cpu.mxcsr = 0x1f80;
    u32 sent = M32(sp);
    cpu.ra = eip;
    do { u32 t = c->ra; REGS(); fnvl_dispatch(t)(); REGS(); } while (c->ra != sent);
    return eax;
}

__asm__(".text\n.globl fnvnx_lift_enter\n.type fnvnx_lift_enter,%function\nfnvnx_lift_enter:\n"
        " stp x29,x30,[sp,#-112]!\n mov x29,sp\n stp x19,x20,[sp,#16]\n stp x21,x22,[sp,#32]\n stp x23,x24,[sp,#48]\n stp x25,x26,[sp,#64]\n stp x27,x28,[sp,#80]\n"
        " bl fnvl_thread_main\n"
        " ldp x19,x20,[sp,#16]\n ldp x21,x22,[sp,#32]\n ldp x23,x24,[sp,#48]\n ldp x25,x26,[sp,#64]\n ldp x27,x28,[sp,#80]\n ldp x29,x30,[sp],#112\n ret\n"
        ".size fnvnx_lift_enter,.-fnvnx_lift_enter\n");

/* The one routine every address without a body resolves to (README: the table is total; this measuring build reports such addresses, it does not hide them). */
extern "C" void fnvl_nobody(void) { REGS(); fnvl_raise_ud(c->ra); REGS(); }
