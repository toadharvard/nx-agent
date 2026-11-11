/* What the OS does on a hardware exception in a Win32 process, on the guest state (R4). C++ (it includes fnvl.h: guest registers w19..w26,
   c in x27); compile with the lifted-code flags, -x c++, WITHOUT -DFNVL_TRACE:  aarch64-linux-gnu-g++ -x c++ <FLAGS> -c seh.c
   Entry points:
     fnvl_raise(code, at)            hardware fault of the instruction at guest address `at` (code: FNVL_DE0/DEOVF/BP/UD) -- called by lifted code
     fnvl_raise_av(access, addr, at) access violation (access 0 read, 1 write, 8 execute); the host fault handler calls it with the guest eip
     fnvl_os_RtlUnwind, fnvl_os_RaiseException   guest entries of the two kernel32 services (stdcall: arguments on the guest stack, `ret 16`)
   Contract after return: ContinueExecution -> guest registers loaded from the CONTEXT and c->ra = Context.Eip; a handler that left by a
   non-local transfer (the exe's __except_handler3/4 jump to their except block) -> registers and c->ra exactly as the handler left them. The
   lifted raise site then runs the non-local loop, which serves Eip when it is a label of the faulting function (the faulting instruction, an
   except/catch continuation) or an entry; it cannot serve any other address (documented limit). Unhandled: fnvl_os_unhandled(record, context)
   (rt/unhandled.c; the benches supply their own stop hook): the process ends, or execution continues from the CONTEXT. Not modelled: EFLAGS (lifted flags are locals: Context.EFlags = 0x202), FP/SSE restore
   from the CONTEXT, nested/collided unwind dispositions (reported as STATUS_INVALID_DISPOSITION to the unhandled hook). */
#include "fnvl.h"
extern "C" {
u32 fnvl_os_unhandled(u32 record, u32 context);
void fnvl_raise(u32 code, u32 at);
void fnvl_raise_ud(u32 at);
void fnvl_raise_av(u32 access, u32 addr, u32 at);
void fnvl_os_RtlUnwind(void);
void fnvl_os_RaiseException(void);
}
enum { CTX_SIZE = 716, REC_SIZE = 80, RET_SENT = 0xDEADBEE0u, EXC_UNWINDING = 2, EXC_EXIT_UNWIND = 4, EXC_NONCONT = 1 };
enum { C_FLAGS = 0, C_FLOAT = 28, C_GS = 140, C_FS = 144, C_ES = 148, C_DS = 152, C_EDI = 156, C_ESI = 160, C_EBX = 164, C_EDX = 168, C_ECX = 172, C_EAX = 176,
       C_EBP = 180, C_EIP = 184, C_CS = 188, C_EFL = 192, C_ESP = 196, C_SS = 200 };

static void zero(u32 p, u32 n) { for (u32 i = 0; i < n; i += 4) M32(p + i) = 0; }

static void f80(u32 p, double d) {          /* double -> 80-bit extended at guest address p */
    u64 b = *(u64*)&d; u32 sg = (u32)(b >> 63), e = (u32)(b >> 52) & 0x7ff; u64 f = b & ((1ull << 52) - 1), m; u32 ex;
    if (e == 0x7ff) { m = (1ull << 63) | (f << 11); ex = 0x7fff; }
    else if (e) { m = (1ull << 63) | (f << 11); ex = e - 1023 + 16383; }
    else if (!f) { m = 0; ex = 0; }
    else { int n = 64 - __builtin_clzll(f); m = f << (64 - n); ex = 16383 + n - 1 - 1074; }
    M64(p) = m; M16(p + 8) = (u16)(ex | sg << 15);
}

/* CONTEXT of the guest state now (esp/eip given) */
static u32 make_context(u32 top, u32 sp, u32 eip) {      /* placed below `top`; Context.Esp = sp */
    u32 x = top - CTX_SIZE; zero(x, CTX_SIZE);
    M32(x + C_FLAGS) = 0x1000F;
    M32(x + C_FLOAT) = c->fcw; M32(x + C_FLOAT + 4) = ((c->ftop & 7) << 11) | c->fc;
    u32 tag = 0; for (int i = 0; i < 8; i++) tag |= ((c->ftag >> i & 1) ? 0u : 3u) << 2 * i;
    M32(x + C_FLOAT + 8) = tag;
    for (int i = 0; i < 8; i++) f80(x + C_FLOAT + 28 + 10 * i, c->st[(c->ftop + i) & 7]);
    M32(x + C_FS) = 0x3B; M32(x + C_ES) = 0x23; M32(x + C_DS) = 0x23; M32(x + C_CS) = 0x1B; M32(x + C_SS) = 0x23;
    M32(x + C_EDI) = edi; M32(x + C_ESI) = esi; M32(x + C_EBX) = ebx; M32(x + C_EDX) = edx; M32(x + C_ECX) = ecx; M32(x + C_EAX) = eax; M32(x + C_EBP) = ebp;
    M32(x + C_EIP) = eip; M32(x + C_EFL) = 0x202; M32(x + C_ESP) = sp;
    return x;
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
        if (r == 0) {          /* ExceptionContinueExecution: load the guest registers back and continue at Context.Eip */
            eax = M32(ctx + C_EAX); ecx = M32(ctx + C_ECX); edx = M32(ctx + C_EDX); ebx = M32(ctx + C_EBX); ebp = M32(ctx + C_EBP);
            esi = M32(ctx + C_ESI); edi = M32(ctx + C_EDI); esp = M32(ctx + C_ESP); c->ra = M32(ctx + C_EIP);
            return 1;
        }
        if (r != 1) { M32(rec) = 0xC0000026u; return 2; }       /* nested / collided / invalid disposition */
        prev = frame; frame = M32(frame);
    }
    return 2;
}

static void raise_rec(u32 code, u32 flags, u32 addr, u32 np, const u32 *info, u32 eip, u32 sp) {
    u32 rec = sp - REC_SIZE; zero(rec, REC_SIZE);
    M32(rec) = code; M32(rec + 4) = flags; M32(rec + 12) = addr; M32(rec + 16) = np;
    for (u32 i = 0; i < np; i++) M32(rec + 20 + 4 * i) = info[i];
    u32 ctx = make_context(rec, sp, eip); esp = ctx;            /* record and context live on the guest stack below the faulting esp */
    u32 saved_esp = sp;
    int d = dispatch(rec, ctx);
    if (d == 2) {              /* no handler took it: the OS's last step (rt/unhandled.c). It returns only for EXCEPTION_CONTINUE_EXECUTION */
        if (!fnvl_os_unhandled(rec, ctx)) return;
        eax = M32(ctx + C_EAX); ecx = M32(ctx + C_ECX); edx = M32(ctx + C_EDX); ebx = M32(ctx + C_EBX); ebp = M32(ctx + C_EBP);
        esi = M32(ctx + C_ESI); edi = M32(ctx + C_EDI); esp = M32(ctx + C_ESP); c->ra = M32(ctx + C_EIP);
    }
    (void)saved_esp;
}

extern "C" void fnvl_raise(u32 code, u32 at) {
    u32 sp = esp, eip = at, info[2] = { 0, 0 };
    u32 nt = code == FNVL_DE0 ? 0xC0000094u : code == FNVL_DEOVF ? 0xC0000095u : code == FNVL_BP ? 0x80000003u : 0xC000001Du;
    if (code == FNVL_BP) eip = at + 1;                      /* Windows: ExceptionAddress is the int3, Context.Eip the byte after it */
    raise_rec(nt, 0, at, code == FNVL_BP ? 1 : 0, info, eip, sp);
}

/* a transfer to an address without a body (the dispatcher's miss, rt/rt.c): #UD at that address */
extern "C" void fnvl_raise_ud(u32 at) { fnvl_raise(FNVL_UD, at); }

extern "C" void fnvl_raise_av(u32 access, u32 addr, u32 at) {
    u32 info[2] = { access, addr };
    raise_rec(0xC0000005u, 0, at, 2, info, at, esp);
}

/* guest entry, stdcall: RaiseException(code, flags, nargs, args) */
extern "C" void fnvl_os_RaiseException(void) {
    u32 ra = M32(esp), code = M32(esp + 4), fl = M32(esp + 8), n = M32(esp + 12), args = M32(esp + 16), sp = esp + 20, info[15];
    if (n > 15) n = 15;
    for (u32 i = 0; i < n; i++) info[i] = M32(args + 4 * i);
    esp = sp;                                              /* the caller's state after the call returns */
    raise_rec(code, fl, ra, n, info, ra, sp);
    c->ra = c->ra;                                         /* ContinueExecution: c->ra = Context.Eip (the return address), registers loaded */
}

/* guest entry, stdcall: RtlUnwind(TargetFrame, TargetIp, ExceptionRecord, ReturnValue) */
extern "C" void fnvl_os_RtlUnwind(void) {
    u32 ra = M32(esp), target = M32(esp + 4), rec = M32(esp + 12), rv = M32(esp + 16), sp0 = esp, sp = esp + 20;
    if (!rec) { rec = sp0 - REC_SIZE; zero(rec, REC_SIZE); M32(rec) = 0xC0000027u; M32(rec + 12) = ra; sp0 = rec; }
    M32(rec + 4) |= EXC_UNWINDING | (target ? 0 : EXC_EXIT_UNWIND);
    u32 save_eax = eax; eax = rv; u32 ctx = make_context(sp0, sp, ra); eax = save_eax; esp = ctx;
    u32 fs0 = c->fs, frame = M32(fs0);
    while (frame != 0xffffffffu && frame != target) {
        u32 disp = esp - 16, r = 1; zero(disp, 16); M32(disp) = frame; esp = disp;
        if (!call_handler(M32(frame + 4), rec, frame, ctx, disp, &r)) return;      /* the handler left non-locally */
        esp = disp + 16;
        M32(fs0) = M32(frame); frame = M32(frame);                                   /* unlink */
    }
    esp = sp; c->ra = ra;                                                            /* return to the caller (ret 16) */
}
