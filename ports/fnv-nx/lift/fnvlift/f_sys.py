"""System and control instructions of the integer family (W14, W15): cpuid/rdtsc (the one reference CPU model, rt/cpumodel.c), pushad/popad, pushfd/popfd,
software interrupts, into/bound/icebp, the privileged instructions, segment registers and far transfers, the descriptor-table readers, loop/jcxz, `repz ret`.

Faults are never trap paths: they go through fnvl_raise(code, at) / fnvl_raise_av (rt/seh.cc) exactly like #DE and #UD (f_flow.raise_site).

THE TOTALITY RULE (W15). The lifter executes the instruction set of the REFERENCE CPU MODEL: a 32-bit Windows user-mode process (ring 3, CPL 3, IOPL 0, CR4.TSD = 0,
flat segments), on the CPU that rt/cpumodel.c's cpuid describes (FPU VME DE PSE TSC MSR PAE MCE CX8 APIC SEP MTRR PGE MCA CMOV PAT PSE36 CLFSH MMX FXSR SSE SSE2, NX; leaf 1 ECX = 0,
no leaf 7, no 0x80000001 ECX). Every instruction capstone decodes in 32-bit mode falls in exactly one class, and the class decides the body (proof: the classes are
exclusive by their guards and exhaustive because the last one, UD, is the complement of everything before it):
  1. implemented with its SDM semantics (f_int, f_string, f_flow, f_x87*, f_sse, f_simd_int and the handlers below);
  2. CPL-0 instruction or I/O instruction at IOPL 0 (PRIV_MN, and MOV to/from CRn/DRn): #GP(0); the Windows status comes from Windows' own opcode table (trap0d_privileged);
  3. instruction that loads a segment register, a far transfer, or the descriptor-table instructions: exact on the model GDT of rt/fnvl.h (fnvl_gdt_hi / fnvl_seg_ok), #GP(selector)
     -> STATUS_ACCESS_VIOLATION otherwise;
  4. reserved-NOP / hint space (0F 0D, 0F 18..0F 1F /r not claimed above: bnd*, cldemote, endbr*, rdssp, nop Ev): SDM Vol 2 A.4, Table A-6 "reserved-NOP": no architectural effect;
  5. prefix-only variants whose prefix the model CPU ignores (F2 BND on branches, 3E NOTRACK, F2/F3 XACQUIRE/XRELEASE with LOCK): the instruction without the prefix;
  6. UD: everything else the decoder knows. The model advertises none of the extensions that introduced it (VEX/EVEX/XOP encodings, the 0F 38 and 0F 3A maps, SSE3/SSE4a,
     MOVBE, POPCNT, RDRAND, XSAVE/OSXSAVE/XGETBV, BMI/ADX/..., VMX/SMX/SVM/SGX, CET, RTM/HLE, MPX, PadLock, SYSCALL/SYSRET/SWAPGS which Intel defines only in 64-bit mode): SDM Vol 2
     "Exceptions" of each instruction lists #UD for CPUID.<feature> = 0.
Legal-on-the-model instructions that are NOT implemented anywhere are named in LEGAL_UNIMPLEMENTED and stay Unsupported (never silently #UD)."""
import capstone
from capstone import x86 as X
from .core import op, fallback, Unsupported, SKIP
from . import core, f_flow


def site(cx, call, term=True, traps=False):
    """emit a hardware-exception site: `call` is the C statement raising it; ContinueExecution resumes at Context.Eip, so the instruction (retry) and, for a
    trap, the next instruction are labels of the non-local loop"""
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    cx.raises.add(cx.nxt)
    cx.em('SYNC_OUT; REGS(); %s REGS(); SYNC_IN; goto nonlocal;' % call)
    cx.term = term


def raise_code(cx, code, at=None):
    site(cx, 'FNVL_RAISE(%s,0x%xu);' % (code, cx.a if at is None else at))


# int imm8 in a ring-3 Win32 process. Vector -> (what the process observes, confidence).
#   3     breakpoint: IDT[3] has DPL 3 (the debugger interface). STATUS_BREAKPOINT 0x80000003; a trap, KiTrap03 reports ExceptionAddress = EIP-1: the byte of the
#         imm8 (cd 03 is two bytes: at = a+1), Context.Eip = the next instruction. High.
#   4     `int 4` = into without the OF test: IDT[4] DPL 3, KiTrap04 -> STATUS_INTEGER_OVERFLOW 0xC0000095, a trap (as FNVL_INTO). Medium.
#   5     IDT[5] (bound) DPL 3 on NT -> STATUS_ARRAY_BOUNDS_EXCEEDED 0xC000008C. Low (not sure of the gate DPL of vector 5).
#   0x2c  KiRaiseAssertion, DPL 3 (DbgRaiseAssertionFailure): STATUS_ASSERTION_FAILURE 0xC0000420, Context.Eip rewound onto the int. Medium (Vista+ kernels).
#   0x3 / 0x2d  Wine 9.0 is the declared implementation (dlls/ntdll/unix/signal_i386.c): int3 = trap_handler TRAP_x86_BPTFLT + setup_raise_exception; int 2d = handle_interrupt case 0x2d.
#         The row passes the address of the instruction itself (at = a); rt/seh.cc fnvl_raise holds the rules (FNVL_BP, FNVL_DBGSRV) with the citations.
#   0x2e  the legacy system-service gate is DPL 3 on real NT, but a lifted process has no kernel behind it: it is the default row (AV). Low.
#   other every other vector: the gate has DPL 0, so `int n` from ring 3 is #GP -> STATUS_ACCESS_VIOLATION, ExceptionInformation = {0, 0xFFFFFFFF}. This is also
#         vector 0x29 (fast-fail does not exist on the reference OS version) and `int 6` (the only one in FalloutNV.exe). Medium (the rule is the task's).
INT_VECTORS = {3: ('FNVL_BP', 0), 4: ('FNVL_INTO', 1), 5: ('FNVL_BOUNDS', 0), 0x2c: ('FNVL_ASSERT', 0), 0x2d: ('FNVL_DBGSRV', 0)}


@op('int')
def h_int(cx, ins):
    v = cx.ops[0].imm & 0xff
    row = INT_VECTORS.get(v)
    if row is None:
        site(cx, 'FNVL_RAISE_AV(0,0xffffffffu,0x%xu);' % cx.a)
    else:
        raise_code(cx, row[0], cx.a + row[1])


@op('into')
def h_into(cx, ins):
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    cx.raises.add(cx.nxt)
    cx.em('if(of){ SYNC_OUT; REGS(); FNVL_RAISE(FNVL_INTO,0x%xu); REGS(); SYNC_IN; goto nonlocal; }' % cx.a)


@op('bound')
def h_bound(cx, ins):
    ops = cx.ops
    s = cx.s
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    ea = cx.ea(ins, ops[1])
    cx.em('{ u32 ad=%s; s32 i=(s32)(s%d)%s; if(i<(s32)(s%d)M%d(ad)||i>(s32)(s%d)M%d(ad+%d)){ SYNC_OUT; REGS(); FNVL_RAISE(FNVL_BOUNDS,0x%xu); REGS(); SYNC_IN; goto nonlocal; } }' % (
        ea, s, cx.rd(ins, ops[0]), s, s, s, s, s // 8, cx.a))


# ---- class 2: CPL-0 instructions and I/O at IOPL 0 -------------------------------------------------------------------------------------------------------------------
# The CPU raises #GP(0) (SDM Vol 2, each instruction's Exceptions section: "#GP(0) if the current privilege level is not 0"; IN/OUT/INS/OUTS/CLI/STI: "if CPL > IOPL" and, for the I/O
# instructions, the TSS I/O bitmap that a Win32 process has all-ones; RDPMC: CR4.PCE = 0). SYSEXIT at CPL 3 is #GP(0) too (SDM: SYSEXIT).
# What the process sees is Windows' decision, not the CPU's: KiTrap0DHandler (ReactOS ntoskrnl/ke/i386/traphdlr.c, a transcription of the NT kernel, the "readable reference"):
#   * the #GP error code is 0 (instruction-raised, not a selector): the bytes at EIP are scanned past the prefix bytes KiTrapPrefixTable (F2 F3 67 F0 66 2E 3E 26 64 65 36);
#   * first opcode byte F4 (HLT), or 0F xx with xx in {08 invd, 09 wbinvd, 35 sysexit, 21/23 mov dr, 06 clts, 20/22 mov cr, 30 wrmsr, 33 rdpmc}, or 0F 00 with ModRM&38 == 10 (LLDT)
#     or ModRM == 18 (LTR), or 0F 01 with ModRM&38 == 10 (LGDT) or ModRM == 18 (LIDT) or ModRM == 30 (LMSW)  -> STATUS_PRIVILEGED_INSTRUCTION 0xC0000096, no parameters;
#   * else, because CPL 3 > IOPL 0: first byte FA (CLI), FB (STI), or one of KiTrapIoTable (E4 E5 E6 E7 EC ED EE EF 6C 6D 6E 6F)  -> STATUS_PRIVILEGED_INSTRUCTION;
#   * everything else (RDMSR 0F 32 and INVLPG 0F 01 /7 are NOT in the table, nor are the unlisted ModRM values above) -> STATUS_ACCESS_VIOLATION, ExceptionInformation {0, 0xFFFFFFFF}.
#   The exact-byte rows (LTR, LIDT, LMSW) are as in ReactOS: they look like masks that lost their `&0x38`, but no other source of the kernel's table is available, so the transcription
#   is followed to the byte. Medium confidence for those three rows (a register-form or [reg+..] LIDT/LTR/LMSW is AV by this table), high for the rest.
# A #GP with a nonzero error code (selector faults, `int n` through a DPL-0 gate) never reaches the table: always STATUS_ACCESS_VIOLATION {0, 0xFFFFFFFF} (gp_cond / gp_av).
TRAP0D_PREFIX = (0xf2, 0xf3, 0x67, 0xf0, 0x66, 0x2e, 0x3e, 0x26, 0x64, 0x65, 0x36)
TRAP0D_IO = (0xe4, 0xe5, 0xe6, 0xe7, 0xec, 0xed, 0xee, 0xef, 0x6c, 0x6d, 0x6e, 0x6f)
TRAP0D_0F = (0x08, 0x09, 0x35, 0x21, 0x23, 0x06, 0x20, 0x22, 0x30, 0x33)


def trap0d_privileged(b):
    """KiTrap0DHandler's privileged-opcode decision over the instruction bytes b (prefixes included): True -> STATUS_PRIVILEGED_INSTRUCTION, False -> STATUS_ACCESS_VIOLATION"""
    b = bytes(b) + b'\0' * 4
    i = 0
    while b[i] in TRAP0D_PREFIX:
        i += 1
    o = b[i]
    if o == 0xf4:
        return True
    if o == 0x0f:
        o1, m = b[i + 1], b[i + 2]
        return (o1 in TRAP0D_0F or (o1 == 0x00 and ((m & 0x38) == 0x10 or m == 0x18)) or (o1 == 0x01 and ((m & 0x38) == 0x10 or m == 0x18 or m == 0x30)))
    return o in (0xfa, 0xfb) or o in TRAP0D_IO


def gp_av(cx):
    """an unconditional ring-3 #GP that Windows reports as an access violation {0, 0xFFFFFFFF} (a fault: Eip = the instruction)"""
    site(cx, 'FNVL_RAISE_AV(0,0xffffffffu,0x%xu);' % cx.a)


def gp_cond(cx, cond):
    """C `if` statement raising that #GP when `cond` holds (selector and far-transfer checks); nothing was modified yet when it fires"""
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    return 'if(%s){ SYNC_OUT; REGS(); FNVL_RAISE_AV(0,0xffffffffu,0x%xu); REGS(); SYNC_IN; goto nonlocal; }' % (cond, cx.a)


def gp0(cx, ins):
    """a CPL-0-only instruction at CPL 3: #GP(0), reported as Windows reports it"""
    if trap0d_privileged(ins.bytes):
        raise_code(cx, 'FNVL_PRIV')
    else:
        gp_av(cx)


PRIV_MN = ('cli', 'sti', 'hlt', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd', 'rdmsr', 'wrmsr', 'invd', 'wbinvd', 'wbnoinvd', 'clts', 'lgdt', 'lidt', 'lldt', 'ltr',
           'lmsw', 'invlpg', 'rdpmc', 'sysexit')
# WBNOINVD (F3 0F 09): the model has no WBNOINVD, a CPU without it ignores the F3 prefix and executes WBINVD (SDM Vol 2, WBNOINVD) -> the same 0F 09 row.


@op(*PRIV_MN)
def h_priv(cx, ins):
    gp0(cx, ins)


def ud_site(cx):
    raise_code(cx, 'FNVL_UD')


@op('cpuid')
def h_cpuid(cx, ins):
    cx.em('{ u32 o[4]; fnvl_cpuid(eax,ecx,o); eax=o[0]; ebx=o[1]; ecx=o[2]; edx=o[3]; }')


@op('rdtsc')
def h_rdtsc(cx, ins):
    cx.em('{ u64 t=fnvl_rdtsc(); eax=(u32)t; edx=(u32)(t>>32); }')


@op('pushal', 'pushaw')
def h_pusha(cx, ins):
    if cx.m == 'pushal':
        cx.em('{ u32 t=esp; esp-=32; M32(esp+28)=eax; M32(esp+24)=ecx; M32(esp+20)=edx; M32(esp+16)=ebx; M32(esp+12)=t; M32(esp+8)=ebp; M32(esp+4)=esi; M32(esp)=edi; }')
    else:
        cx.em('{ u32 t=esp; esp-=16; M16(esp+14)=eax; M16(esp+12)=ecx; M16(esp+10)=edx; M16(esp+8)=ebx; M16(esp+6)=t; M16(esp+4)=ebp; M16(esp+2)=esi; M16(esp)=edi; }')


@op('popal', 'popaw')
def h_popa(cx, ins):
    if cx.m == 'popal':
        cx.em('{ u32 t=esp; edi=M32(t); esi=M32(t+4); ebp=M32(t+8); ebx=M32(t+16); edx=M32(t+20); ecx=M32(t+24); eax=M32(t+28); esp=t+32; }')
    else:
        cx.em('{ u32 t=esp; edi=(edi&0xffff0000u)|M16(t); esi=(esi&0xffff0000u)|M16(t+2); ebp=(ebp&0xffff0000u)|M16(t+4); ebx=(ebx&0xffff0000u)|M16(t+8); '
              'edx=(edx&0xffff0000u)|M16(t+10); ecx=(ecx&0xffff0000u)|M16(t+12); eax=(eax&0xffff0000u)|M16(t+14); esp=t+16; }')


@op('pushfd')
def h_pushfd(cx, ins):
    cx.em('{ u32 v=EFL_PUSH(); esp-=4; M32(esp)=v; }')


@op('popfd')
def h_popfd(cx, ins):
    cx.em('{ u32 v=M32(esp); esp+=4; EFL_POP(v); }')


@op('pushf')
def h_pushf(cx, ins):
    cx.em('{ u32 v=EFL_PUSH()&0xffffu; esp-=2; M16(esp)=(u16)v; }')


@op('popf')       # 16-bit: only the low word is loaded: AC and ID (bits 18, 21) are untouched
def h_popf(cx, ins):
    cx.em('{ u32 v=M16(esp); esp+=2; u32 k=c->eflx; EFL_POP(v); c->eflx=k; }')


def cond_jump(cx, cond, t):
    if cx.lo <= t < cx.hi:
        cx.em('if(%s) goto %s;' % (cond, cx.lab(t)))
    else:
        f_flow.jump_out(cx, t, cond)


def osz16(ins):
    return ins.prefix[2] == 0x66


def asz16(ins):
    return ins.prefix[3] == 0x67


@op('loop', 'loope', 'loopne')
def h_loop(cx, ins):
    c = {'loop': '', 'loope': '&&zf', 'loopne': '&&!zf'}[cx.m]
    if asz16(ins):      # the 0x67 prefix makes the counter CX (SDM LOOP: "the count register is CX/ECX by address-size attribute")
        cx.em('ecx=(ecx&0xffff0000u)|((ecx-1)&0xffffu);')
        cond_jump(cx, '(ecx&0xffffu)!=0' + c, cx.ops[0].imm & 0xffffffff)
        return
    cx.em('ecx--;')
    cond_jump(cx, 'ecx!=0' + c, cx.ops[0].imm & 0xffffffff)


@op('jcxz')         # 67 E3: jump if CX == 0 (jecxz is the unprefixed form, in f_flow)
def h_jcxz(cx, ins):
    cond_jump(cx, '(ecx&0xffffu)==0', cx.ops[0].imm & 0xffffffff)


@op('repz ret', 'repnz ret', 'repe ret', 'repne ret')
def h_repz_ret(cx, ins):
    f_flow.h_ret(cx, ins)


# ---- class 3: segment registers, far transfers, descriptor-table instructions --------------------------------------------------------------------------------------
# Model (rt/fnvl.h): CS, DS=ES=SS, FS (base = TEB) of the process; fnvl_gdt_hi / fnvl_seg_ok decide every selector. State needed to be exact (the lead owns struct cpu):
# the six selector values c->sel[6] and the TEB base c->teb (fnvl_seg_set / fnvl_seg_get of rt/fnvl.h); reads of a segment register use fnvl_seg_get (f_int.SEG_STATE = True).
SEGIDX = {'es': 0, 'cs': 1, 'ss': 2, 'ds': 3, 'fs': 4, 'gs': 5}


def seg_load(cx, sg):
    """C text loading the segment register `sg` from the selector in the C variable `s` (a u32 < 65536): SDM Vol 2 MOV/POP Sreg "#GP(selector) if the selector is invalid for the
    register" is gp_cond over fnvl_seg_ok (nothing changed yet); then the register takes the value (fnvl_seg_set). FS also re-bases the cached `fsb`."""
    i = SEGIDX[sg]
    t = '%s fnvl_seg_set(c,%d,s);' % (gp_cond(cx, '!fnvl_seg_ok(%d,s)' % i), i)
    if sg == 'fs':
        cx.usesfs = True
        t += ' fsb=c->fs;'
    return t


def mov_to_sreg(cx, ins, sg):
    """MOV Sreg, r/m16 (8E /r). MOV CS is #UD (SDM: "If attempting to load the CS register, #UD")."""
    if sg == 'cs':
        ud_site(cx)
        return
    cx.em('{ u32 s=(%s)&0xffffu; %s }' % (cx.rd(ins, cx.ops[1], 2), seg_load(cx, sg)))


def pop_sreg(cx, ins, sg):
    """POP Sreg: the word at the stack top (a dword with 32-bit operand size) -> the register, then ESP += 2 or 4. Nothing is modified when the load faults."""
    w = 2 if osz16(ins) else 4
    cx.em('{ u32 s=M%d(esp)&0xffffu; %s esp+=%du; }' % (8 * w, seg_load(cx, sg), w))


@op('les', 'lds', 'lss', 'lfs', 'lgs')
def h_lxs(cx, ins):
    """LES/LDS/LSS/LFS/LGS r, m16:16|32: offset, then selector; the segment register is loaded (and may fault) before the general register is written (SDM pseudo-code)"""
    sg = {'les': 'es', 'lds': 'ds', 'lss': 'ss', 'lfs': 'fs', 'lgs': 'gs'}[cx.m]
    w = cx.ops[0].size
    cx.em('{ u32 ad=%s; u32 o=M%d(ad); u32 s=M16(ad+%du); %s %s }' % (cx.ea(ins, cx.ops[1]), 8 * w, w, seg_load(cx, sg), cx.wr(ins, cx.ops[0], 'o')))


def far_target(cx, sel):
    """a far JMP/CALL through a code-segment selector: the only code descriptor of the model GDT is CS (flat, DPL 3, non-conforming); the transfer requires RPL <= CPL (always true at
    CPL 3) and DPL == CPL, so it is valid exactly for the CS index (SDM Vol 3 5.8.1 / 5.8.1.2: JMP/CALL to a non-conforming code segment); anything else (a data selector, the null selector,
    a call-gate / TSS selector, kernel segments) is #GP(selector). CS keeps the model value SEL_CS (the new CS RPL is CPL)."""
    return gp_cond(cx, 'SELK(%s)!=SELK(SEL_CS)' % sel)


def h_ljmp(cx, ins):
    """JMP ptr16:16/32 (EA): both operands are immediates in capstone; far_target decides the selector, the offset is the new EIP (base 0)"""
    cx.kinds.add('farjmp')
    sel, off = cx.ops[0].imm & 0xffff, cx.ops[1].imm & (0xffff if osz16(ins) else 0xffffffff)
    cx.em('{ u32 s=0x%xu; %s c->ra=0x%xu; goto nonlocal; }' % (sel, far_target(cx, 's'), off))
    cx.term = True


def far_call(cx, ins, sel, off):
    """CALL far: pushes the return CS (zero-extended to the operand size) then the return EIP, transfers like a far jump; the callee's RETF pops them (h_retf)"""
    w = 2 if osz16(ins) else 4
    cx.kinds.add('farcall')
    cx.callrets.append(cx.nxt)
    cx.nonlocal_ = True
    cx.em('{ u32 s=%s, t=%s; %s esp-=%du; M%d(esp)=SEL_CS; esp-=%du; M%d(esp)=0x%xu; SYNC_OUT; REGS(); fnvl_dispatch(t)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (
        sel, off, far_target(cx, 's'), w, 8 * w, w, 8 * w, cx.nxt, cx.nxt))


@op('lcall')
def h_lcall(cx, ins):
    o = cx.ops
    if o[0].type == X.X86_OP_MEM:       # 66 FF /3 (capstone's name for the 16-bit operand-size form): m16:16
        far_mem(cx, ins, o[0], True)
        return
    far_call(cx, ins, '0x%xu' % (o[0].imm & 0xffff), '0x%xu' % (o[1].imm & (0xffff if osz16(ins) else 0xffffffff)))


def far_mem(cx, ins, mop, call):
    """FF /3 (call) and FF /5 (jmp) with a memory operand: m16:32, or m16:16 with the 66 prefix; the offset comes first, the selector after it"""
    w = 2 if osz16(ins) else 4
    ad = cx.ea(ins, mop)
    if call:
        far_call_mem(cx, ins, ad, w)
        return
    cx.kinds.add('farjmp')
    cx.em('{ u32 ad=%s, t=M%d(ad), s=M16(ad+%du); %s c->ra=t; goto nonlocal; }' % (ad, 8 * w, w, far_target(cx, 's')))
    cx.nonlocal_ = True
    cx.term = True


def far_call_mem(cx, ins, ad, w):
    cx.kinds.add('farcall')
    cx.callrets.append(cx.nxt)
    cx.nonlocal_ = True
    cx.em('{ u32 ad=%s, t=M%d(ad), s=M16(ad+%du); %s esp-=%du; M%d(esp)=SEL_CS; esp-=%du; M%d(esp)=0x%xu; SYNC_OUT; REGS(); fnvl_dispatch(t)(); REGS(); SYNC_IN; if(c->ra!=0x%xu) goto nonlocal; }' % (
        ad, 8 * w, w, far_target(cx, 's'), w, 8 * w, w, 8 * w, cx.nxt, cx.nxt))


def is_far_ff(ins):
    """FF /3 and FF /5 are the far CALL / JMP through memory. capstone names them `call`/`jmp` (32-bit operand size) or `lcall`/`ljmp` (66 prefix) inconsistently, so the
    encoding decides: opcode FF, ModRM.reg 3 or 5 (SDM Vol 2 CALL/JMP: FF /3 CALL m16:32, FF /5 JMP m16:32)."""
    return ins.opcode[0] == 0xff and ins.modrm is not None and (ins.modrm >> 3) & 7 in (3, 5) and ins.operands and ins.operands[0].type == X.X86_OP_MEM


_near = {n: core.OPS[n] for n in ('call', 'jmp')}


@op('call')
def h_call_or_far(cx, ins):
    if is_far_ff(ins) and (ins.modrm >> 3) & 7 == 3:
        far_mem(cx, ins, ins.operands[0], True)
    else:
        _near['call'](cx, ins)


@op('jmp')
def h_jmp_or_far(cx, ins):
    if is_far_ff(ins) and (ins.modrm >> 3) & 7 == 5:
        far_mem(cx, ins, ins.operands[0], False)
    else:
        _near['jmp'](cx, ins)


@op('ljmp')
def h_ljmp_any(cx, ins):
    if cx.ops[0].type == X.X86_OP_MEM:
        far_mem(cx, ins, cx.ops[0], False)
    else:
        h_ljmp(cx, ins)


@op('retf')
def h_retf(cx, ins):
    """RETF [imm16]: pops EIP then CS (and imm16 bytes). Same-level return only (an outer level would need RPL > CPL = 3): valid exactly when the popped CS is SEL_CS (index of CS, RPL 3);
    RPL < CPL, another index, the null selector are #GP(selector) (SDM Vol 2 RET, protected-mode far return: "#GP(selector) if the return code segment selector RPL is less than CPL / null / not a code segment")."""
    n = cx.ops[0].imm & 0xffff if cx.ops else 0
    if n:
        cx.kinds.add('retn')
    cx.kinds.add('farret')
    w = 2 if osz16(ins) else 4
    cx.em('{ u32 ra=M%d(esp), s=M%d(esp+%du)&0xffffu; %s esp+=%du; c->ra=ra; SYNC_OUT; return; }' % (8 * w, 8 * w, w, gp_cond(cx, 's!=SEL_CS'), 2 * w + n))
    cx.term = True


@op('iretd', 'iret')
def h_iret(cx, ins):
    """IRET/IRETD at CPL 3 returning to CPL 3 (SDM Vol 3 6.12.1 "Return from interrupt... without privilege change"): pops EIP, CS, EFLAGS; CS checked like RETF. EFLAGS loads like POPF at CPL 3
    (EFL_POP: the arithmetic flags and DF, AC/ID; IOPL, IF, TF, NT, RF, VM, VIF, VIP are not changed by a CPL 3 IRET or not modelled: header comment of EFL_POP). A 16-bit IRET pops IP, CS, FLAGS
    (word): EIP = IP, the high EFLAGS bits stay."""
    w = 2 if cx.m == 'iret' else 4
    cx.kinds.add('farret')
    t = 'EFL_POP(fl);' if w == 4 else '{ u32 k=c->eflx; EFL_POP(fl); c->eflx=k; }'
    cx.em('{ u32 ra=M%d(esp), s=M%d(esp+%du)&0xffffu, fl=M%d(esp+%du); %s esp+=%du; %s c->ra=ra; SYNC_OUT; return; }' % (
        8 * w, 8 * w, w, 8 * w, 2 * w, gp_cond(cx, 's!=SEL_CS'), 3 * w, t))
    cx.term = True


@op('int1')
def h_int1(cx, ins):
    """INT1 / ICEBP (F1): #DB as a trap (SDM Vol 2 INT1: "not subject to IDT-gate DPL checks"). KiTrap01: STATUS_SINGLE_STEP, ExceptionAddress = Context.Eip =
    the NEXT instruction. (`int 1`, CD 01, is the vector-1 gate with DPL 0: #GP -> access violation, INT_VECTORS default.)"""
    raise_code(cx, 'FNVL_STEP', cx.nxt)


def store16(cx, ins, o, val):
    """store a 16-bit value (selector / machine status word) to r/m16, or to a 32-bit register zero-extended (SDM SLDT/STR/SMSW: 'the high-order 16 bits are zero-extended' for a 32-bit register
    destination; memory always gets 16 bits)"""
    if o.type == X.X86_OP_MEM:
        return 'M16(%s)=(%s);' % (cx.ea(ins, o), val)
    return cx.wr(ins, o, '(u32)(%s)' % val)


@op('sldt')
def h_sldt(cx, ins):
    cx.em(store16(cx, ins, cx.ops[0], 'FNVL_LDTR_SEL'))


@op('str')
def h_str(cx, ins):
    cx.em(store16(cx, ins, cx.ops[0], 'FNVL_TR_SEL'))


@op('smsw')
def h_smsw(cx, ins):
    o = cx.ops[0]
    cx.em(store16(cx, ins, o, 'FNVL_CR0') if o.type == X.X86_OP_MEM or o.size == 2 else cx.wr(ins, o, 'FNVL_CR0'))     # 32-bit register: CR0[31:0]


@op('sgdt', 'sidt')
def h_sgdt(cx, ins):
    """SGDT/SIDT m: 16-bit limit at +0, 32-bit base at +2 (SDM: the full base is stored in 32-bit mode, also with the 66 prefix); the values are the model's FNVL_*TR_*"""
    k = 'GDTR' if cx.m == 'sgdt' else 'IDTR'
    cx.em('{ u32 ad=%s; M16(ad)=FNVL_%s_LIMIT; M32(ad+2u)=FNVL_%s_BASE; }' % (cx.ea(ins, cx.ops[0]), k, k))


@op('arpl')
def h_arpl(cx, ins):
    """ARPL r/m16, r16 (SDM Vol 2 ARPL; protected mode, legal at CPL 3): if RPL(dest) < RPL(src): ZF = 1 and dest.RPL = src.RPL, else ZF = 0"""
    o = cx.ops
    cx.em('{ u32 d=%s, s=%s; if((d&3u)<(s&3u)){ zf=1; %s } else zf=0; }' % (cx.rd(ins, o[0]), cx.rd(ins, o[1]), cx.wr(ins, o[0], '(d&~3u)|(s&3u)')))


@op('lar', 'lsl')
def h_lar(cx, ins):
    """LAR/LSL r, r/m16 (SDM Vol 2): the selector must name a descriptor accessible at CPL 3 (here: one of the model GDT, DPL 3; TI = 1, null, kernel/TSS indices: ZF = 0, destination
    unchanged). LAR loads descriptor-high & 0x00FFFF00 (16-bit destination: its low word), LSL the scaled limit. Only ZF is written."""
    o = cx.ops
    v = '(hi&0x00ffff00u)' if cx.m == 'lar' else 'fnvl_gdt_limit(hi)'
    cx.em('{ u32 hi=fnvl_gdt_hi(%s&0xffffu); if(hi){ zf=1; %s } else zf=0; }' % (cx.rd(ins, o[1], 2), cx.wr(ins, o[0], v)))


@op('verr', 'verw')
def h_verr(cx, ins):
    """VERR/VERW r/m16: ZF = 1 when the segment is readable (VERR) / writable (VERW) at CPL 3 with RPL(selector) irrelevant (DPL 3): every model descriptor is readable; VERW also needs a
    data segment (type bit 3 clear) with the write bit (bit 1): CS is not writable."""
    sel = '%s&0xffffu' % cx.rd(ins, cx.ops[0], 2)
    cx.em('{ u32 hi=fnvl_gdt_hi(%s); zf=%s; }' % (sel, 'hi!=0' if cx.m == 'verr' else '(hi!=0&&!(hi&0x800u)&&(hi&0x200u)!=0)'))


# ---- class 4/5: hints, prefix-only variants -----------------------------------------------------------------------------------------------------------------------
@op('pause')
def h_pause(cx, ins):
    """PAUSE (F3 90): no architectural effect (SDM Vol 2 PAUSE: a spin-loop hint, a NOP on processors without it); the host hint keeps spin waits polite"""
    cx.em('__asm__ volatile("yield");')


@op('mfence', 'lfence', 'sfence')
def h_fence(cx, ins):
    """MFENCE/LFENCE/SFENCE (SSE2/SSE: in the model): ordering only. One full inner-shareable barrier is at least as strong as each (class rule for threads: the guest's x86-TSO is
    implemented by the memory model owner, a fence must never be weaker than the x86 one)."""
    cx.em('__sync_synchronize();')


@op('clflush')
def h_clflush(cx, ins):
    """CLFLUSH m8 (CLFSH = 1): no architectural effect on data; it faults like a load of the address (SDM: #PF / #GP as a read), so the address is read once"""
    cx.em('{ volatile u8 t_=M8(%s); (void)t_; }' % cx.ea(ins, cx.ops[0]))


@op('tzcnt')
def h_tzcnt(cx, ins):
    """F3 0F BC: TZCNT needs BMI1 (absent in the model); a CPU without it ignores the F3 prefix and executes BSF (SDM Vol 2 TZCNT: "on processors that do not support TZCNT the instruction
    is executed as BSF")."""
    cx.m = 'bsf'
    core.OPS['bsf'](cx, ins)


@op('lzcnt')
def h_lzcnt(cx, ins):
    """F3 0F BD: LZCNT needs ABM/LZCNT (CPUID.80000001H:ECX.LZCNT, absent): executed as BSR (SDM Vol 2 LZCNT)."""
    cx.m = 'bsr'
    core.OPS['bsr'](cx, ins)


STRIP = ('bnd ', 'notrack ', 'xacquire ', 'xrelease ', 'lock ')


@fallback(lambda cx, ins: cx.m.startswith(STRIP))
def h_prefix_only(cx, ins):
    """BND (F2 on call/jmp/ret/jcc: MPX), NOTRACK (3E on an indirect call/jmp: CET), XACQUIRE/XRELEASE (F2/F3 on a LOCKed or XCHG instruction: HLE) are encoded as REPNE/REP/DS-override
    prefixes that a CPU without the extension ignores (SDM Vol 1 17.3 / Vol 2 "Instruction prefixes": BND ... ignored; Vol 1 16.2: HLE prefixes are ignored; Vol 1 CET: NOTRACK is DS
    segment override); the model has none, so the body is the instruction without the prefix word."""
    m = cx.m
    while m.startswith(STRIP):
        m = m.split(' ', 1)[1]
    cx.m = m
    core.dispatch(cx, ins)


# ---- class 6: UD ------------------------------------------------------------------------------------------------------------------------------------------------------
LEGAL_UNIMPLEMENTED = {
    'fxsave': 'FXSR is in the model; the 512-byte image is x87 + SSE state (x87 owner)',
    'fxrstor': 'FXSR is in the model; the 512-byte image is x87 + SSE state (x87 owner)',
    'sysenter': 'SEP is in the model: the fast system-call gate; a lifted process has no kernel behind it (lead decision, like int 0x2e)',
}
SSE3_SSE4A = ('addsubpd', 'addsubps', 'haddpd', 'haddps', 'hsubpd', 'hsubps', 'movddup', 'movshdup', 'movsldup', 'monitor', 'mwait', 'extrq', 'insertq', 'movntss', 'movntsd')    # fisttp (SSE3, x87 encoding) is the x87 owner's


def encoding(ins):
    """-> (kind, bytes after the legacy prefixes). kind: 'vex' (C4/C5, ModRM.mod == 3 in 32-bit mode), 'evex' (62 with mod == 3), 'xop' (8F with map >= 8), 'map38', 'map3a', 'map0f' (0F 0F: 3DNow!), '0f', '1'"""
    b = bytes(ins.bytes) + b'\0\0\0'
    i = 0
    while b[i] in TRAP0D_PREFIX:
        i += 1
    o = b[i]
    if o in (0xc4, 0xc5, 0x62) and (b[i + 1] & 0xc0) == 0xc0:
        return ('evex' if o == 0x62 else 'vex'), b[i:]
    if o == 0x8f and (b[i + 1] & 0x1f) >= 8:
        return 'xop', b[i:]
    if o == 0x0f:
        return ('map38' if b[i + 1] == 0x38 else 'map3a' if b[i + 1] == 0x3a else 'map0f' if b[i + 1] == 0x0f else '0f'), b[i:]
    return '1', b[i:]


def _is_simd(cx, ins):
    from . import f_sse
    return cx.ops and any(f_sse.isx(ins, o) or f_sse.ismm(ins, o) for o in cx.ops)


@fallback(lambda cx, ins: encoding(ins)[0] in ('vex', 'evex', 'xop', 'map38', 'map3a', 'map0f') or cx.m in SSE3_SSE4A)
def h_absent_ext(cx, ins):
    """UD by absent extension: VEX/EVEX/XOP encodings (AVX, AVX2, FMA, BMI, AVX-512, XOP: CPUID.1:ECX.AVX = 0 etc.), the whole of the 0F 0F map (3DNow!, AMD only: the reference CPU of rt/cpumodel.c is Intel and cpuid reports no 3DNow), the whole of the 0F 38 / 0F 3A opcode maps (SSSE3, SSE4.1/4.2, AES, PCLMUL, SHA,
    GFNI, MOVBE, CRC32, ADX, INVPCID, CET, MOVDIR: no instruction of those maps belongs to the SSE2-era ISA of the model), SSE3 and SSE4a (CPUID.1:ECX.SSE3 = 0; f_sse's lddqu is an OPS handler and wins)."""
    ud_site(cx)


def _default_pred(cx, ins):
    return not (0xd8 <= cx.b0 <= 0xdf) and not _is_simd(cx, ins)


@fallback(_default_pred)
def h_default(cx, ins):
    """the complement (class 6 above): reserved-NOP space executes as NOP, the explicitly legal-but-unimplemented raise Unsupported, everything else is #UD"""
    if cx.m in LEGAL_UNIMPLEMENTED:
        raise Unsupported('insn %s: %s' % (cx.m, LEGAL_UNIMPLEMENTED[cx.m]))
    kind, b = encoding(ins)
    if kind == '0f' and (b[1] == 0x0d or 0x18 <= b[1] <= 0x1f):
        return      # SDM Vol 2 Table A-6 / A.4: reserved-NOP, no architectural effect (a memory operand is not accessed)
    ud_site(cx)


# ---- decoder completeness for reserved-NOP space ---------------------------------------------------------------------------------------------------------------
class NopIns:
    """a decoded-by-rule `nop`: what core.explore needs of an instruction object (capstone's decoder refuses some reserved-NOP ModRM values that every CPU executes as NOP)"""
    mnemonic = 'nop'
    operands = ()
    op_str = ''

    def __init__(self, raw, pfx, opc, modrm):
        self.bytes, self.size = bytes(raw), len(raw)
        self.opcode = [0x0f, opc, 0, 0]
        self.modrm = modrm
        self.prefix = [0, 0, 0x66 if 0x66 in pfx else 0, 0x67 if 0x67 in pfx else 0]

    def reg_name(self, r):
        return 'r%d' % r


def nop_gap(b):
    """reserved-NOP instruction `[prefixes] 0F 0D|18..1F /r` that capstone does not decode, as a NopIns, else None (SDM Vol 2, Table A-6 "reserved-NOP": 0F 19..1F take a ModRM and
    execute as NOP Ev for every ModRM value, 0F 0D and 0F 18 for every memory ModRM value; LOCK makes them #UD, so a LOCK prefix is not claimed). Length = prefixes + 0F + opcode + ModRM + SIB + displacement by the addressing form."""
    b = bytes(b)
    i = 0
    while i < len(b) and b[i] in TRAP0D_PREFIX:
        i += 1
    if i + 2 >= len(b) or b[i] != 0x0f or not (b[i + 1] == 0x0d or 0x18 <= b[i + 1] <= 0x1f) or 0xf0 in b[:i]:
        return None
    pfx, m = b[:i], b[i + 2]
    n = i + 3
    mod, rm = m >> 6, m & 7
    if mod == 3 and b[i + 1] in (0x0d, 0x18):      # PREFETCH groups take a memory operand only (the register forms are #UD: QEMU agrees)
        return None
    if 0x67 in pfx:     # 16-bit addressing: no SIB; disp16 for mod 0 rm 6 and mod 2
        n += 2 if (mod == 0 and rm == 6) or mod == 2 else 1 if mod == 1 else 0
    elif mod != 3:
        if rm == 4:
            n += 1
        n += 1 if mod == 1 else 4 if mod == 2 or (mod == 0 and (rm == 5 or (rm == 4 and (b[i + 3] & 7) == 5))) else 0
    return NopIns(b[:n], pfx, b[i + 1], m)


# ---- LOCK validity (for core.lift2, which strips the `lock ` word) ---------------------------------------------------------------------------------------------------
LOCKABLE = ('add', 'adc', 'and', 'btc', 'btr', 'bts', 'cmpxchg', 'cmpxchg8b', 'dec', 'inc', 'neg', 'not', 'or', 'sbb', 'sub', 'xor', 'xadd', 'xchg')


def lock_ok(ins, m):
    """SDM Vol 2 "LOCK": the prefix is valid only on the instructions of LOCKABLE whose destination operand is memory (XCHG: either operand; it locks implicitly); any other LOCK is #UD. m is the
    mnemonic without the `lock ` word. A #UD Windows reports as STATUS_INVALID_LOCK_SEQUENCE 0xC000001E when F0 is among the first 4 bytes at Eip (KiTrap06Handler), else STATUS_ILLEGAL_INSTRUCTION."""
    if m not in LOCKABLE:
        return False
    if m == 'xchg':
        return any(o.type == X.X86_OP_MEM for o in ins.operands)
    return bool(ins.operands) and ins.operands[0].type == X.X86_OP_MEM


def locked(ins, m, prefixed):
    """The instructions that are one atomic read-modify-write of their memory operand: a valid LOCK prefix (lock_ok), and XCHG with a memory operand, which locks without the prefix (SDM Vol 2 "XCHG")."""
    return prefixed or (m == 'xchg' and any(o.type == X.X86_OP_MEM for o in ins.operands))


def lock_size(ins):
    """Bits of the memory operand of a locked instruction (every instruction of LOCKABLE has exactly one)."""
    return [o.size for o in ins.operands if o.type == X.X86_OP_MEM][0] * 8
