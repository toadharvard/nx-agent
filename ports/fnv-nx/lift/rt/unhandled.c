/* The last step of exception dispatch when no frame of fs:[0] took the exception (rt/seh.c): what RtlUserThreadStart's own handler does in
   Windows 7. C++, lifted-code flags.
     r = UnhandledExceptionFilter(&EXCEPTION_POINTERS{record, context})      kernel32, the platform's body: it calls the filter the program set with
                                                                              SetUnhandledExceptionFilter (guest code, through fnvnx_guest_invoke)
     r == EXCEPTION_CONTINUE_EXECUTION (-1): return; seh.c loads the registers from the CONTEXT and continues at Context.Eip
     otherwise: the process terminates with the exception code as its exit code (TerminateProcess(GetCurrentProcess(), code)); no return. */
#include "fnvl.h"
#include "imp.h"
extern "C" {
u32 UnhandledExceptionFilter(u32 pointers);
u32 TerminateProcess(u32 process, u32 code);
u32 GetCurrentProcess(void);
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
    if (r != 0xffffffffu) for (;;) TerminateProcess(GetCurrentProcess(), code);
    fnvl_native_out(&nf); REGS();
    esp = ptrs + 8;
    return 1;
}
