/* What the OS does on a hardware exception in a Win32 process, on the guest state (R4). C++ (it includes fnvl.h: guest registers w19..w26,
   c in x27); compile with the lifted-code flags, -x c++, WITHOUT -DFNVL_TRACE:  aarch64-linux-gnu-g++ -x c++ <FLAGS> -c seh.cc
   Entry points:
     fnvl_raise(code, at)            hardware fault of the instruction at guest address `at` (code: the FNVL_* list of fnvl.h, a total switch) -- called by lifted code
     fnvl_raise_av(access, addr, at) access violation (access 0 read, 1 write, 8 execute); the host fault handler calls it with the guest eip
     fnvl_os_RtlUnwind, fnvl_os_RaiseException   guest entries of the two kernel32 services (stdcall: arguments on the guest stack, `ret 16`)
   Contract after return: ContinueExecution -> the guest state that the CONTEXT's ContextFlags name is loaded from it (ctx_restore: the exact inverse of ctx_capture, field for field) and c->ra = Context.Eip; a handler that left by a
   non-local transfer (the exe's __except_handler3/4 jump to their except block) -> registers and c->ra exactly as the handler left them. The
   lifted raise site then runs the non-local loop, which serves Eip when it is a label of the faulting function (the faulting instruction, an
   except/catch continuation) or an entry; it cannot serve any other address (documented limit). Unhandled: fnvl_os_unhandled(record, context)
   (rt/unhandled.cc; the benches supply their own stop hook): the process ends, or execution continues from the CONTEXT. Not modelled: nested/collided unwind
   dispositions (reported as STATUS_INVALID_DISPOSITION to the unhandled hook).
   THE CONTEXT (Wine 9.0 is the declared implementation): a hardware exception and RaiseException/RtlRaiseException CONTEXT has ContextFlags = CONTEXT_FULL | CONTEXT_DEBUG_REGISTERS | CONTEXT_FLOATING_POINT |
   CONTEXT_EXTENDED_REGISTERS (dlls/ntdll/unix/signal_i386.c save_context: "ContextFlags = CONTEXT_FULL | CONTEXT_DEBUG_REGISTERS", FLOATING_POINT | EXTENDED_REGISTERS when the CPU context has the FXSAVE image, which the
   reference CPU has (FXSR, SSE); software raise: dlls/ntdll/signal_i386.c RtlRaiseException -> raise_exception_full_context: RtlCaptureContext 0x10007, save_fpu, save_fpux, Dr0-7, CONTEXT_DEBUG_REGISTERS). CONTEXT_XSTATE is not set: the
   reference CPU has no AVX (rt/cpumodel.c leaf 1 ECX = 0), as Wine sets it only with CPU_FEATURE_AVX. RtlUnwind's context is RtlCaptureContext's: 0x10007. The model's debug registers Dr0-3, Dr6, Dr7 are c->dr (zero at thread start: the declared
   machine has none set), reported and restored like Wine's x86_thread_data()->dr0..dr7. FloatSave is derived from the FXSAVE image by Wine's own function (dlls/ntdll/unix/thread.c fpux_to_fpu), and a restore without
   CONTEXT_EXTENDED_REGISTERS takes it back through fpu_to_fpux (same file); with CONTEXT_EXTENDED_REGISTERS the image is restored with StatusWord &= ControlWord | 0xff80 (dlls/ntdll/unix/signal_i386.c, the CONTEXT_EXTENDED_REGISTERS arm of the CONTEXT restore: "reset the current interrupt status"). */
#include "fnvl.h"
#include "imp.h"
extern "C" {
u32 fnvl_os_unhandled(u32 record, u32 context);
void fnvl_raise(u32 code, u32 at);
void fnvl_raise_ud(u32 at);
void fnvl_raise_av(u32 access, u32 addr, u32 at);
void fnvl_os_RtlUnwind(void);
void fnvl_os_RaiseException(void);
void fnvl_os_DebugBreak(void);
int fnvl_raise_rec(u32 code, u32 flags, u32 addr, u32 np, const u32 *info, u32 eip, u32 sp, u32 cxf, u32 ef);
}
enum { CTX_SIZE = 716, REC_SIZE = 80, RET_SENT = 0xDEADBEE0u, EXC_UNWINDING = 2, EXC_EXIT_UNWIND = 4, EXC_NONCONT = 1 };
enum { C_FLAGS = 0, C_FLOAT = 28, C_GS = 140, C_FS = 144, C_ES = 148, C_DS = 152, C_EDI = 156, C_ESI = 160, C_EBX = 164, C_EDX = 168, C_ECX = 172, C_EAX = 176,
       C_EBP = 180, C_EIP = 184, C_CS = 188, C_EFL = 192, C_ESP = 196, C_SS = 200 };

static void zero(u32 p, u32 n) { for (u32 i = 0; i < n; i += 4) M32(p + i) = 0; }

/* ---- the CONTEXT: one capture and one restore, both through the same field table (Wine 9.0, see the header) ---- */
enum { CX_I386 = 0x10000, CX_CONTROL = 1, CX_INTEGER = 2, CX_SEGMENTS = 4, CX_FP = 8, CX_DEBUG = 0x10, CX_EXT = 0x20, CX_FULL = CX_CONTROL | CX_INTEGER | CX_SEGMENTS,
       CX_EXCEPTION = CX_I386 | CX_FULL | CX_DEBUG | CX_FP | CX_EXT,      /* 0x1003F: save_context / raise_exception_full_context */
       CX_CAPTURE = CX_I386 | CX_FULL };                                  /* 0x10007: RtlCaptureContext (RtlUnwind) */
/* FXSAVE's MXCSR_MASK of the reference CPU (Intel SDM vol. 1, 11.6.6 "Guidelines for Writing to the MXCSR Register": the mask has every bit set that the CPU lets software set;
   bit 6 DAZ is set when the CPU supports denormals-are-zeros, which every CPU with SSE2 and FXSR of the model does) -- derived from the model's cpuid leaf 1, so a change of the leaf is a build error here. */
static_assert((FNVL_CPUID1_EDX >> 24 & 7u) == 7u, "FXSR (24), SSE (25), SSE2 (26) of cpuid leaf 1 EDX: the model has FXSAVE with MXCSR and DAZ");
#define FNVL_MXCSR_MASK 0xffffu
enum { C_DR = 4, C_EXT = 204, FS_SIZE = 112, XS_SIZE = 512 };           /* Dr0 Dr1 Dr2 Dr3 Dr6 Dr7 at 4; FLOATING_SAVE_AREA 112 bytes at 28; ExtendedRegisters[512] at 204 */
enum { FS_CW = 0, FS_SW = 4, FS_TW = 8, FS_EO = 12, FS_ES = 16, FS_DO = 20, FS_DS = 24, FS_REG = 28, FS_CR0 = 108 };       /* FLOATING_SAVE_AREA */
enum { X_FCW = 0, X_FSW = 2, X_FTW = 4, X_FOP = 6, X_FIP = 8, X_FCS = 12, X_FDP = 16, X_FDS = 20, X_MXCSR = 24, X_MXMASK = 28, X_ST = 32, X_XMM = 160 };   /* XSAVE_FORMAT (FXSAVE image) */
/* EFLAGS of a software raise (RaiseException, DebugBreak, RtlUnwind are API calls, not instructions): bit 1, IF, AC/ID as kept, arithmetic flags 0: the flags are dead across a call (the lifter's
   ABI), so no value is read from the caller; Wine's RtlCaptureContext likewise reports the flags of its own pushfd, not of the caller's instruction */
#define SW_EFL (0x202u | (c->eflx & 0x240000u))
static u32 g16(const u8 *b, u32 o) { return b[o] | b[o + 1] << 8; }
static u32 g32(const u8 *b, u32 o) { return g16(b, o) | g16(b, o + 2) << 16; }
static u64 g64(const u8 *b, u32 o) { return g32(b, o) | (u64)g32(b, o + 4) << 32; }
static void p16(u8 *b, u32 o, u32 v) { b[o] = (u8)v; b[o + 1] = (u8)(v >> 8); }
static void p32(u8 *b, u32 o, u32 v) { p16(b, o, v); p16(b, o + 2, v >> 16); }
static void p64(u8 *b, u32 o, u64 v) { p32(b, o, (u32)v); p32(b, o + 4, (u32)(v >> 32)); }
static void to_guest(u32 dst, const u8 *src, u32 n) { for (u32 i = 0; i < n; i += 4) M32(dst + i) = g32(src, i); }
static void from_guest(u8 *dst, u32 src, u32 n) { for (u32 i = 0; i < n; i += 4) p32(dst, i, M32(src + i)); }

/* the FXSAVE image of the guest state now (struct cpu; the lifted raise site has flushed fx/ftop/fc/x, rt/fnvl.h RAISE_OUT): the registers in ST order as 80-bit images (an empty register keeps its image,
   rt/fnvl_x87.h fv_free), the abridged tag byte (bit = physical register not empty), FOP/FCS/FDS derived as fnstenv does (fx_env_st), MXCSR with the live SSE flags, XMM0-7 (XMM8-15 do not exist in 32-bit mode). */
static void fx_capture(u8 *g) {
    for (u32 i = 0; i < XS_SIZE; i++) g[i] = 0;
    u32 fsw = (c->ftop & 7) << 11 | (c->fc & 0x4500u) | fx_live() | (c->fex & FEX_X87), tag = 0, ip = c->fip, dp = c->fdp;
    for (u32 p = 0; p < 8; p++) if (c->fx[p].k) tag |= 1u << p;
    p16(g, X_FCW, c->fcw); p16(g, X_FSW, fsw); g[X_FTW] = (u8)tag; p16(g, X_FOP, fx_fop()); p32(g, X_FIP, ip); p16(g, X_FCS, ip ? c->sel[SEG_CS] : 0);
    p32(g, X_FDP, dp); p16(g, X_FDS, dp ? c->sel[SEG_DS] : 0); p32(g, X_MXCSR, c->mxcsr | sse_live()); p32(g, X_MXMASK, FNVL_MXCSR_MASK);
    for (u32 i = 0; i < 8; i++) { fnvl_f80 t = fv_x(c->fx[(c->ftop + i) & 7]); p64(g, X_ST + 16 * i, t.m); p16(g, X_ST + 16 * i + 8, t.se); }
    for (u32 i = 0; i < 8; i++) { p64(g, X_XMM + 16 * i, c->x[i].q[0]); p64(g, X_XMM + 16 * i + 8, c->x[i].q[1]); }
}
/* Wine 9.0 dlls/ntdll/unix/thread.c fpux_to_fpu: the 112-byte FLOATING_SAVE_AREA from the FXSAVE image */
static void fpux_to_fpu(u8 *f, const u8 *g) {
    u32 fsw = g16(g, X_FSW), top = (fsw >> 11) & 7, tw = 0xffff0000u;
    for (u32 i = 0; i < FS_SIZE; i++) f[i] = 0;
    p32(f, FS_CW, g16(g, X_FCW)); p32(f, FS_SW, fsw); p32(f, FS_EO, g32(g, X_FIP)); p32(f, FS_ES, g16(g, X_FCS) | g16(g, X_FOP) << 16);
    p32(f, FS_DO, g32(g, X_FDP)); p32(f, FS_DS, g16(g, X_FDS)); p32(f, FS_CR0, fsw | 0xffff0000u);
    for (u32 i = 0; i < 8; i++) {
        u32 tag;
        for (u32 k = 0; k < 10; k++) f[FS_REG + 10 * i + k] = g[X_ST + 16 * i + k];
        if (!((g[X_FTW] >> i) & 1)) tag = 3;
        else { u32 j = (i - top) & 7, ex = g16(g, X_ST + 16 * j + 8) & 0x7fff; u64 m = g64(g, X_ST + 16 * j);
               tag = ex == 0x7fff ? 2 : ex == 0 ? (m ? 2 : 1) : (m >> 63) ? 0 : 2; }
        tw |= tag << (2 * i);
    }
    p32(f, FS_TW, tw);
}
/* Wine 9.0 dlls/ntdll/unix/thread.c fpu_to_fpux: the x87 part of the FXSAVE image from a FLOATING_SAVE_AREA (the SSE part stays) */
static void fpu_to_fpux(u8 *g, const u8 *f) {
    u32 tw = g32(f, FS_TW), tag = 0;
    p16(g, X_FCW, g32(f, FS_CW)); p16(g, X_FSW, g32(f, FS_SW)); p32(g, X_FIP, g32(f, FS_EO)); p16(g, X_FCS, g32(f, FS_ES)); p16(g, X_FOP, g32(f, FS_ES) >> 16);
    p32(g, X_FDP, g32(f, FS_DO)); p16(g, X_FDS, g32(f, FS_DS));
    for (u32 i = 0; i < 8; i++) { if (((tw >> (2 * i)) & 3) != 3) tag |= 1u << i; for (u32 k = 0; k < 10; k++) g[X_ST + 16 * i + k] = f[FS_REG + 10 * i + k]; }
    g[X_FTW] = (u8)tag;
}
/* load the x87 part of the image (always) and the SSE part (sse): the exact inverse of fx_capture. FCW goes through FLDCW (the host rounding mode follows), the sticky flags REPLACE the x87 set like
   FRSTOR (fx_env_ld) and the SSE set like LDMXCSR; FOP, FCS and FDS are derived values (A-FOP, A-FDS of rt/fnvl_x87.h), not stored. */
static void fx_restore(const u8 *g, int sse) {
    u32 fsw = g16(g, X_FSW), top = (fsw >> 11) & 7, tag = g[X_FTW];
    FLDCW(g16(g, X_FCW)); c->ftop = top; c->fex = (c->fex & ~FEX_X87) | (fsw & FEX_X87); if (fown() == 1u) fnvl_fp_x87_clear();
    c->fc = fsw & 0x4500u; c->fip = g32(g, X_FIP); c->fdp = g32(g, X_FDP);
    for (u32 p = 0; p < 8; p++) { u32 i = (p - top) & 7; c->fx[p] = fvmk((tag >> p) & 1 ? FK_M80 : FK_E, g64(g, X_ST + 16 * i), g16(g, X_ST + 16 * i + 8)); }
    if (!sse) return;
    u32 mx = g32(g, X_MXCSR); c->mxcsr = mx; sse_fpcr(mx); if (fown() == 2u) fnvl_fp_x87_clear();
    for (u32 i = 0; i < 8; i++) { c->x[i].q[0] = g64(g, X_XMM + 16 * i); c->x[i].q[1] = g64(g, X_XMM + 16 * i + 8); }
}
/* the CONTEXT of the guest state now: flags = the groups to write; esp/eip/EFlags given */
static u32 ctx_capture(u32 top, u32 flags, u32 sp, u32 eip, u32 ef) {      /* placed below `top`; Context.Esp = sp */
    u32 x = top - CTX_SIZE; zero(x, CTX_SIZE);
    M32(x + C_FLAGS) = flags;
    if (flags & CX_DEBUG) for (u32 i = 0; i < 6; i++) M32(x + C_DR + 4 * i) = c->dr[i];
    if (flags & (CX_FP | CX_EXT)) {
        u8 g[XS_SIZE], f[FS_SIZE]; fx_capture(g); fpux_to_fpu(f, g);
        if (flags & CX_FP) to_guest(x + C_FLOAT, f, FS_SIZE);
        if (flags & CX_EXT) to_guest(x + C_EXT, g, XS_SIZE);
    }
    if (flags & CX_SEGMENTS) { M32(x + C_GS) = fnvl_seg_get(c, SEG_GS); M32(x + C_FS) = fnvl_seg_get(c, SEG_FS); M32(x + C_ES) = fnvl_seg_get(c, SEG_ES); M32(x + C_DS) = fnvl_seg_get(c, SEG_DS); }
    if (flags & CX_INTEGER) { M32(x + C_EDI) = edi; M32(x + C_ESI) = esi; M32(x + C_EBX) = ebx; M32(x + C_EDX) = edx; M32(x + C_ECX) = ecx; M32(x + C_EAX) = eax; }
    if (flags & CX_CONTROL) {
        M32(x + C_EBP) = ebp; M32(x + C_EIP) = eip; M32(x + C_CS) = fnvl_seg_get(c, SEG_CS); M32(x + C_EFL) = ef; M32(x + C_ESP) = sp; M32(x + C_SS) = fnvl_seg_get(c, SEG_SS);
    }
    return x;
}
/* the selectors a restore would load are those the model GDT has (rt/fnvl.h fnvl_seg_ok; CS: the ring-3 code selector); -> 1 all are loadable */
static int ctx_segs_ok(u32 x, u32 fl) {
    if ((fl & CX_SEGMENTS) && !(fnvl_seg_ok(SEG_GS, M32(x + C_GS)) && fnvl_seg_ok(SEG_FS, M32(x + C_FS)) && fnvl_seg_ok(SEG_ES, M32(x + C_ES)) && fnvl_seg_ok(SEG_DS, M32(x + C_DS)))) return 0;
    if (fl & CX_CONTROL) { u32 cs = M32(x + C_CS); if (!(SELK(cs) == SELK(SEL_CS) && (cs & 3) == 3 && fnvl_seg_ok(SEG_SS, M32(x + C_SS)))) return 0; }
    return 1;
}
/* ContinueExecution: load what the CONTEXT's ContextFlags name (the handler may have changed it) into the guest state; c->ra = Context.Eip. A selector that the CPU refuses to load is Wine 9.0's
   case of a bad segment in a restored frame: the CONTEXT is loaded as it is and the fault happens when the segment registers are loaded (dlls/ntdll/unix/signal_i386.c handle_syscall_fault, which reports
   the exception with the frame being restored as its context; segv_handler: STATUS_ACCESS_VIOLATION, ExceptionInformation = {0, 0xffffffff} for a GDT selector). So the whole CONTEXT (selectors included) becomes
   the state first, then the access violation is raised at Context.Eip with the loaded values as ITS context; what that raise does is the state. */
static void ctx_restore(u32 x) {
    u32 fl = M32(x + C_FLAGS) & ~(u32)CX_I386;
    int segs_ok = ctx_segs_ok(x, fl);
    if (fl & CX_DEBUG) for (u32 i = 0; i < 6; i++) c->dr[i] = M32(x + C_DR + 4 * i);
    if (fl & (CX_FP | CX_EXT)) {
        u8 g[XS_SIZE];
        if (fl & CX_EXT) { from_guest(g, x + C_EXT, XS_SIZE); p16(g, X_FSW, g16(g, X_FSW) & (g16(g, X_FCW) | 0xff80u)); }
        else { u8 f[FS_SIZE]; fx_capture(g); from_guest(f, x + C_FLOAT, FS_SIZE); fpu_to_fpux(g, f); }
        fx_restore(g, (fl & CX_EXT) != 0);
    }
    if (fl & CX_SEGMENTS) { fnvl_seg_set(c, SEG_GS, M32(x + C_GS)); fnvl_seg_set(c, SEG_FS, M32(x + C_FS)); fnvl_seg_set(c, SEG_ES, M32(x + C_ES)); fnvl_seg_set(c, SEG_DS, M32(x + C_DS)); }
    if (fl & CX_INTEGER) { edi = M32(x + C_EDI); esi = M32(x + C_ESI); ebx = M32(x + C_EBX); edx = M32(x + C_EDX); ecx = M32(x + C_ECX); eax = M32(x + C_EAX); }
    if (fl & CX_CONTROL) {
        ebp = M32(x + C_EBP); c->ra = M32(x + C_EIP); fnvl_seg_set(c, SEG_CS, M32(x + C_CS)); c->eflags = M32(x + C_EFL); esp = M32(x + C_ESP); fnvl_seg_set(c, SEG_SS, M32(x + C_SS));
    }
    if (!segs_ok) fnvl_raise_av(0, 0xffffffffu, c->ra);
}

/* cdecl guest call of handler h(record, frame, context, dispatcher); -> 1 when it returned (disposition in *r, registers restored), 0 when it
   left by a non-local transfer (state as the handler left it) */
static int call_handler(u32 h, u32 rec, u32 frame, u32 ctx, u32 disp, u32 *r) {
    u32 s[8] = { eax, ecx, edx, ebx, esp, ebp, esi, edi };
    esp -= 16; M32(esp) = rec; M32(esp + 4) = frame; M32(esp + 8) = ctx; M32(esp + 12) = disp;
    esp -= 4; M32(esp) = RET_SENT;
    REGS(); void (*f)(void) = fnvl_dispatch(h); f(); REGS();
    if (c->ra != RET_SENT) return 0;
    *r = eax; eax = s[0]; ecx = s[1]; edx = s[2]; ebx = s[3]; esp = s[4]; ebp = s[5]; esi = s[6]; edi = s[7];
    return 1;
}

/* walk fs:[0]; -> 1 resumed (ContinueExecution), 0 non-local transfer, 2 end of chain / invalid */
static int dispatch(u32 rec, u32 ctx) {
    u32 fs0 = c->fs, frame = M32(fs0), prev = 0;
    while (frame != 0xffffffffu) {
        if ((frame & 3) || frame <= prev) return 2;
        u32 disp = esp - 16, r = 1; zero(disp, 16); M32(disp) = frame; esp = disp;
        if (!call_handler(M32(frame + 4), rec, frame, ctx, disp, &r)) return 0;
        esp = disp + 16;
        if (r == 0) { ctx_restore(ctx); return 1; }      /* ExceptionContinueExecution: the CONTEXT is loaded, continue at Context.Eip */
        if (r != 1) { M32(rec) = 0xC0000026u; return 2; }       /* nested / collided / invalid disposition */
        prev = frame; frame = M32(frame);
    }
    return 2;
}

/* -> 1 execution continues from the CONTEXT (registers loaded, c->ra = Context.Eip), 0 a handler or the filter left by a non-local transfer */
int fnvl_raise_rec(u32 code, u32 flags, u32 addr, u32 np, const u32 *info, u32 eip, u32 sp, u32 cxf, u32 ef) {
    u32 rec = sp - REC_SIZE; zero(rec, REC_SIZE);
    M32(rec) = code; M32(rec + 4) = flags; M32(rec + 12) = addr; M32(rec + 16) = np;
    for (u32 i = 0; i < np; i++) M32(rec + 20 + 4 * i) = info[i];
    u32 ctx = ctx_capture(rec, cxf, sp, eip, ef); esp = ctx;            /* record and context live on the guest stack below the faulting esp */
    int d = dispatch(rec, ctx);
    if (d == 2) {              /* no handler took it: the OS's last step (rt/unhandled.cc). It returns only for EXCEPTION_CONTINUE_EXECUTION */
        if (!fnvl_os_unhandled(rec, ctx)) return 0;
        ctx_restore(ctx);
    }
    return d != 0;
}

/* Hardware exception -> NTSTATUS record, a TOTAL switch over the codes of rt/fnvl.h (the lifter emits no other value; there is no default arm, so a code outside the list is not
   turned into some status). Each row except BP and DBGSRV (Wine 9.0, cited at the cases) is what the Windows 7 kernel's trap handler reports (ReactOS traphdlr.c, a transcription of it):
     DE0 / DEOVF   #DE: STATUS_INTEGER_DIVIDE_BY_ZERO / quotient overflow = STATUS_INTEGER_OVERFLOW; fault, Eip = at, no parameters
     BP            int3: Wine 9.0 (see the case): ExceptionAddress = Context.Eip = at (the int3 byte), 3 parameters {0,0,0}
     DBGSRV        `int 0x2d`: Wine 9.0 handle_interrupt (see the case): eax 1/3/4/5 continue at at + 3, otherwise STATUS_BREAKPOINT, ExceptionAddress = at + 3, Context.Eip = at + 2, parameters {eax, ecx, edx}
     UD            #UD: STATUS_ILLEGAL_INSTRUCTION, fault
     PRIV          #GP(0) of a privileged opcode (KiTrap0D table): STATUS_PRIVILEGED_INSTRUCTION, fault
     BOUNDS        STATUS_ARRAY_BOUNDS_EXCEEDED, fault
     INTO          #OF trap: STATUS_INTEGER_OVERFLOW, ExceptionAddress = at, Context.Eip = at + 1
     ASSERT        `int 0x2c`: STATUS_ASSERTION_FAILURE, ExceptionAddress = Context.Eip = at
     STEP          #DB trap (icebp): STATUS_SINGLE_STEP, at = the address of the NEXT instruction (ExceptionAddress = Context.Eip = at)
     LOCK          #UD with an F0 byte among the first 4 bytes at Eip (KiTrap06Handler): STATUS_INVALID_LOCK_SEQUENCE, fault */
extern "C" void fnvl_raise(u32 code, u32 at) {
    u32 sp = esp, eip = at, addr = at, np = 0, nt = 0, info[3] = { 0, 0, 0 };
    switch (code) {
    case FNVL_DE0:    nt = 0xC0000094u; break;
    case FNVL_DEOVF:  nt = 0xC0000095u; break;
    case FNVL_BP:     nt = 0x80000003u; np = 3; break;          /* Wine 9.0 unix/signal_i386.c trap_handler TRAP_x86_BPTFLT: ExceptionAddress = the int3 byte (Eip - 1), 3 parameters {0,0,0}; setup_raise_exception: Context.Eip-- for EXCEPTION_BREAKPOINT, so Context.Eip = the int3 byte too */
    case FNVL_DBGSRV:                                          /* Wine 9.0 unix/signal_i386.c handle_interrupt case 0x2d */
        if (eax == 1 || eax == 3 || eax == 4 || eax == 5) { c->ra = at + 3; return; }     /* BREAKPOINT_PRINT / LOAD_SYMBOLS / UNLOAD_SYMBOLS / COMMAND_STRING: the service is done, execution continues 3 bytes on, no exception */
        nt = 0x80000003u; addr = at + 3; eip = at + 2; np = 3; info[0] = eax; info[1] = ecx; info[2] = edx;    /* Eip += 3, ExceptionAddress = Eip, then setup_raise_exception's Context.Eip-- */
        break;
    case FNVL_UD:     nt = 0xC000001Du; break;
    case FNVL_PRIV:   nt = 0xC0000096u; break;
    case FNVL_BOUNDS: nt = 0xC000008Cu; break;
    case FNVL_INTO:   nt = 0xC0000095u; eip = at + 1; break;
    case FNVL_ASSERT: nt = 0xC0000420u; break;
    case FNVL_STEP:   nt = 0x80000004u; break;
    case FNVL_LOCK:   nt = 0xC000001Eu; break;
    }
    if (nt == 0) __builtin_unreachable();       /* not reached: every emitted code is a case above */
    fnvl_raise_rec(nt, 0, addr, np, info, eip, sp, CX_EXCEPTION, c->eflags);   /* hardware: EFLAGS as the lifted site flushed it (RAISE_OUT) */
}

/* a transfer to an address without a body (the dispatcher's miss, rt/rt.c): #UD at that address */
extern "C" void fnvl_raise_ud(u32 at) { fnvl_raise(FNVL_UD, at); }

extern "C" void fnvl_raise_av(u32 access, u32 addr, u32 at) {
    u32 info[2] = { access, addr };
    fnvl_raise_rec(0xC0000005u, 0, at, 2, info, at, esp, CX_EXCEPTION, c->eflags);
}

/* guest entry, stdcall: RaiseException(code, flags, nargs, args) */
extern "C" void fnvl_os_RaiseException(void) {
    u32 ra = M32(esp), code = M32(esp + 4), fl = M32(esp + 8), n = M32(esp + 12), args = M32(esp + 16), sp = esp + 20, info[15];
    if (n > 15) n = 15;
    for (u32 i = 0; i < n; i++) info[i] = M32(args + 4 * i);
    esp = sp;                                              /* the caller's state after the call returns */
    fnvl_raise_rec(code, fl, ra, n, info, ra, sp, CX_EXCEPTION, SW_EFL);
    c->ra = c->ra;                                         /* ContinueExecution: c->ra = Context.Eip (the return address), registers loaded */
}

/* guest entry, stdcall, no arguments: kernel32!DebugBreak = EXCEPTION_BREAKPOINT reported at the return address; continuing resumes there */
extern "C" void fnvl_os_DebugBreak(void) {
    u32 ra = M32(esp), sp = esp + 4, info[1] = { 0 };
    esp = sp;
    fnvl_raise_rec(0x80000003u, 0, ra, 1, info, ra, sp, CX_EXCEPTION, SW_EFL);
}

/* guest entry, stdcall: RtlUnwind(TargetFrame, TargetIp, ExceptionRecord, ReturnValue) */
extern "C" void fnvl_os_RtlUnwind(void) {
    u32 ra = M32(esp), target = M32(esp + 4), rec = M32(esp + 12), rv = M32(esp + 16), sp0 = esp, sp = esp + 20;
    if (!rec) { rec = sp0 - REC_SIZE; zero(rec, REC_SIZE); M32(rec) = 0xC0000027u; M32(rec + 12) = ra; sp0 = rec; }
    M32(rec + 4) = M32(rec + 4) | (EXC_UNWINDING | (target ? 0 : EXC_EXIT_UNWIND));
    u32 save_eax = eax; eax = rv; u32 ctx = ctx_capture(sp0, CX_CAPTURE, sp, ra, SW_EFL); eax = save_eax; esp = ctx;
    u32 fs0 = c->fs, frame = M32(fs0);
    while (frame != 0xffffffffu && frame != target) {
        u32 disp = esp - 16, r = 1; zero(disp, 16); M32(disp) = frame; esp = disp;
        if (!call_handler(M32(frame + 4), rec, frame, ctx, disp, &r)) return;      /* the handler left non-locally */
        esp = disp + 16;
        M32(fs0) = M32(frame); frame = M32(frame);                                   /* unlink */
    }
    esp = sp; c->ra = ra;                                                            /* return to the caller (ret 16) */
}
