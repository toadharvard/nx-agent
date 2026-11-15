/* The last step of exception dispatch when no frame of fs:[0] took the exception (rt/seh.cc): what RtlUserThreadStart's own handler does in
   Windows 7. C++, lifted-code flags.
     r = UnhandledExceptionFilter(&EXCEPTION_POINTERS{record, context})      kernel32, the platform's body: it calls the filter the program set with
                                                                              SetUnhandledExceptionFilter (guest code, through fnvnx_guest_invoke)
     r == EXCEPTION_CONTINUE_EXECUTION (-1): return; seh.cc loads the registers from the CONTEXT and continues at Context.Eip
     otherwise: the process terminates with the exception code as its exit code (ExitProcess(code), the names L5 confirmed); no return. */
#include "fnvl.h"
#include "imp.h"
extern "C" {
u32 UnhandledExceptionFilter(u32 pointers);
void ExitProcess(u32 code);
struct cpu **fnvl_host_tls_slot(void);
void fnvnx_win32_set_raise_backend(void (*)(u32 code, u32 flags, u32 nargs, const u32 *args));
void fnvnx_lift_raise_backend(u32 code, u32 flags, u32 nargs, const u32 *args);
void fnvl_backend_main(u32 code, u32 flags, u32 nargs, const u32 *args) __attribute__((noinline));
void fnvl_runtime_install(void);
int fnvl_raise_rec(u32 code, u32 flags, u32 addr, u32 np, const u32 *info, u32 eip, u32 sp);
u32 fnvl_os_unhandled(u32 record, u32 context);     /* 1: continue from the CONTEXT; 0: the filter left by a non-local transfer */
}
extern "C" u32 fnvl_os_unhandled(u32 record, u32 context)
{
    struct fnvl_nframe nf;
    u32 code = M32(record);
    esp -= 8; M32(esp) = record; M32(esp + 4) = context;          /* EXCEPTION_POINTERS, below the record and the context */
    u32 ptrs = esp;
    fnvl_native_in(&nf);
    if (__builtin_setjmp((void **)nf.jb)) { c->nf = nf.prev; return 0; }      /* the filter left by a non-local transfer: c->ra is pending in the caller's loop */
    u32 r = UnhandledExceptionFilter(ptrs);
    if (r != 0xffffffffu) for (;;) ExitProcess(code);
    fnvl_native_out(&nf); REGS();
    esp = ptrs + 8;
    return 1;
}

/* An exception raised by a native body of the platform (its w32_raise), i.e. inside an import call of this thread: the same dispatch on the guest
   chain, with the guest state of the import call (c->r, rt/imp.h): ExceptionAddress and Context.Eip = the import's return address, Context.Esp =
   the esp of the call. ContinueExecution returns to the native body (changes a handler made to that CONTEXT do not reach native state: ASM-226;
   the CONTEXT of a system DLL frame has no counterpart here). A handler that leaves by a non-local transfer discards the native frames
   (imp.h); on a thread with no import frame the guest code simply goes on from the address it jumped to.
   fnvnx_lift_raise_backend is the AAPCS function the platform is given (fnvl_runtime_install, called from fnvnx_lift_imports_init). */
extern "C" void fnvl_backend_main(u32 code, u32 flags, u32 nargs, const u32 *args) {
    c = *fnvl_host_tls_slot();
    u32 base = c->r[4], info[15];
    eax = c->r[0]; ecx = c->r[1]; edx = c->r[2]; ebx = c->r[3]; esp = base; ebp = c->r[5]; esi = c->r[6]; edi = c->r[7];
    { u64 a = c->hfpcr, b = c->hfpsr; __asm__ volatile("msr fpcr, %0\n msr fpsr, %1" :: "r"(a), "r"(b)); }
    if (nargs > 15) nargs = 15;
    for (u32 i = 0; i < nargs; i++) info[i] = args[i];
    u32 ra = M32(base);
    if (!fnvl_raise_rec(code, flags, ra, nargs, info, ra, base)) {
        if (c->nf) __builtin_longjmp((void **)c->nf->jb, 1);
        for (;;) { u32 t = c->ra; REGS(); fnvl_dispatch(t)(); REGS(); }
    }
    { u64 a, b; __asm__ volatile("mrs %0, fpcr\n mrs %1, fpsr" : "=r"(a), "=r"(b)); c->hfpcr = (u32)a; c->hfpsr = (u32)b; a = 0; __asm__ volatile("msr fpcr, %0" :: "r"(a)); }
    c->r[4] = base;
}
__asm__(".text\n.globl fnvnx_lift_raise_backend\n.type fnvnx_lift_raise_backend,%function\nfnvnx_lift_raise_backend:\n"
        " stp x29,x30,[sp,#-112]!\n mov x29,sp\n stp x19,x20,[sp,#16]\n stp x21,x22,[sp,#32]\n stp x23,x24,[sp,#48]\n stp x25,x26,[sp,#64]\n stp x27,x28,[sp,#80]\n"
        " bl fnvl_backend_main\n"
        " ldp x19,x20,[sp,#16]\n ldp x21,x22,[sp,#32]\n ldp x23,x24,[sp,#48]\n ldp x25,x26,[sp,#64]\n ldp x27,x28,[sp,#80]\n ldp x29,x30,[sp],#112\n ret\n"
        ".size fnvnx_lift_raise_backend,.-fnvnx_lift_raise_backend\n");
extern "C" void fnvl_runtime_install(void) { fnvnx_win32_set_raise_backend(fnvnx_lift_raise_backend); }

