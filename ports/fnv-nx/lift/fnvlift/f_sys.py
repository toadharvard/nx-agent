"""System and control instructions of the integer family (W14, W15): cpuid/rdtsc (the one reference CPU model, rt/cpumodel.c), pushad/popad, pushfd/popfd,
software interrupts, into/bound/icebp, the privileged instructions, segment registers and far transfers, the descriptor-table readers, loop/jcxz, `repz ret`.

Faults are never trap paths: they go through fnvl_raise(code, at) / fnvl_raise_av (rt/seh.cc) exactly like #DE and #UD (f_flow.raise_site).

THE TOTALITY RULE (W15). The lifter executes the instruction set of the REFERENCE CPU MODEL: a 32-bit Windows user-mode process (ring 3, CPL 3, IOPL 0, CR4.TSD = 0,
flat segments), on the CPU that rt/cpumodel.c's cpuid describes (FPU VME DE PSE TSC MSR PAE MCE CX8 APIC SEP MTRR PGE MCA CMOV PAT PSE36 CLFSH MMX FXSR SSE SSE2 HTT, NX; leaf 1 ECX = 0,
no leaf 7, no 0x80000001 ECX). Every instruction capstone decodes in 32-bit mode falls in exactly one class, and the class decides the body (proof: the classes are
exclusive by their guards and exhaustive because the last one, UD, is the complement of everything before it):
  1. implemented with its SDM semantics (f_int, f_flow, f_x87*, f_sse, f_simd_int and the handlers below);
  2. CPL-0 instruction or I/O instruction at IOPL 0 (proof/privset.py, and MOV to/from CRn/DRn): #GP(0); the status is Wine 9.0's opcode table (wine_is_privileged);
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


# int imm8 in a ring-3 Win32 process: the gates are the record's (platform/declared/x86sys.json int_ring3.vectors, via fnvlift/x86sys.py); the row each gate raises and its address are the
# int_vector / int_at columns of fnvlift/faults.json (Wine 9.0 signal_i386.c, cited per row).  A vector the record does not list is UNDECIDED (int_ring3.other_vectors): the instruction is Unsupported.






# ---- class 2: CPL-0 instructions and I/O at IOPL 0 -------------------------------------------------------------------------------------------------------------------
# The CPU raises #GP(0) (SDM Vol 2, each instruction's Exceptions section: "#GP(0) if the current privilege level is not 0"; IN/OUT/INS/OUTS/CLI/STI: "if CPL > IOPL" and, for the I/O
# instructions, the TSS I/O bitmap that a Win32 process has all-ones; RDPMC: CR4.PCE = 0). SYSEXIT at CPL 3 is #GP(0) too (SDM: SYSEXIT).
# What the process sees: Wine 9.0 dlls/ntdll/unix/signal_i386.c segv_handler + is_privileged_instr (the declared implementation; documentation is silent, platform/declared/x86sys.json precedence):
#   * a #GP with error code 0 (instruction-raised) is scanned past the legacy prefix bytes (2E 36 3E 26 64 65 66 67 F0 F2 F3);
#   * first opcode byte F4 (HLT), FA (CLI), FB (STI), 6C-6F (INS/OUTS), E4-E7 and EC-EF (IN/OUT), or 0F 20-23 (MOV CRn/DRn)  -> EXCEPTION_PRIV_INSTRUCTION (STATUS_PRIVILEGED_INSTRUCTION), no parameters;
#   * every other #GP(0) (RDMSR, WRMSR, LGDT, LIDT, LLDT, LTR, LMSW, INVLPG, INVD, WBINVD, CLTS, RDPMC, SYSEXIT ...) -> EXCEPTION_ACCESS_VIOLATION, ExceptionInformation {0, 0xFFFFFFFF}.
# A #GP with a nonzero error code (selector faults, `int n` through a DPL-0 gate) never reaches that table: always STATUS_ACCESS_VIOLATION {0, 0xFFFFFFFF} (gp_cond / gp_av).
WINE_PREFIX = (0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3)
WINE_PRIV_OPC = (0xf4, 0xfa, 0xfb, 0x6c, 0x6d, 0x6e, 0x6f, 0xe4, 0xe5, 0xe6, 0xe7, 0xec, 0xed, 0xee, 0xef)
WINE_PRIV_0F = (0x20, 0x21, 0x22, 0x23)


def wine_is_privileged(b):
    """Wine 9.0 is_privileged_instr over the instruction bytes b (prefixes included): True -> EXCEPTION_PRIV_INSTRUCTION, False -> EXCEPTION_ACCESS_VIOLATION {0, 0xFFFFFFFF}"""
    b = bytes(b) + b'\0' * 2
    i = 0
    while b[i] in WINE_PREFIX:
        i += 1
    o = b[i]
    return o in WINE_PRIV_OPC or (o == 0x0f and b[i + 1] in WINE_PRIV_0F)


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
    if wine_is_privileged(ins.bytes):
        raise_code(cx, 'FNVL_PRIV')
    else:
        gp_av(cx)


# The privileged set is the SDM's (proof/privset.py, rows of proof/sdmpriv.py); its Forms are proof/forms_sys.privileged and reach the lifter through core.set_emit (a privileged mnemonic has no hand handler).


def ud_site(cx):
    raise_code(cx, 'FNVL_UD')


















def cond_jump(cx, cond, t):
    if cx.lo <= t < cx.hi:
        cx.em('if(%s) goto %s;' % (cond, cx.lab(t)))
    else:
        f_flow.jump_out(cx, t, cond)


def osz16(ins):
    return ins.prefix[2] == 0x66


def asz16(ins):
    return ins.prefix[3] == 0x67






@op('repz ret', 'repnz ret', 'repe ret', 'repne ret')
def h_repz_ret(cx, ins):
    f_flow.h_ret(cx, ins)


# ---- class 3: segment registers, far transfers, descriptor-table instructions --------------------------------------------------------------------------------------
# Model: the selectors of platform/declared/x86sys.json (generated rt/gen/fnvl_sys.h: fnvl_seg_ok decides every selector). State needed to be exact (the lead owns struct cpu):
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




def store16(cx, ins, o, val):
    """store a 16-bit value (selector / machine status word) to r/m16, or to a 32-bit register zero-extended (SDM SLDT/STR/SMSW: 'the high-order 16 bits are zero-extended' for a 32-bit register
    destination; memory always gets 16 bits)"""
    if o.type == X.X86_OP_MEM:
        return 'M16(%s)=(%s);' % (cx.ea(ins, o), val)
    return cx.wr(ins, o, '(u32)(%s)' % val)






# LAR / LSL / SLDT / SMSW / SGDT / SIDT have no hand handler: they need descriptor, LDTR, CR0 or descriptor-table values that platform/declared/x86sys.json leaves undecided (or does not state);
# the instruction is Unsupported until the record states them.
# VERR / VERW need the descriptor high words, which platform/declared/x86sys.json does not state: Unsupported until it does.


# ---- class 4/5: hints, prefix-only variants -----------------------------------------------------------------------------------------------------------------------


@op('mfence', 'lfence', 'sfence')
def h_fence(cx, ins):
    """MFENCE/LFENCE/SFENCE (SSE2/SSE: in the model): ordering only. One full inner-shareable barrier is at least as strong as each (class rule for threads: the guest's x86-TSO is
    implemented by the memory model owner, a fence must never be weaker than the x86 one)."""
    cx.em('__sync_synchronize();')


@op('clflush')
def h_clflush(cx, ins):
    """CLFLUSH m8 (CLFSH = 1): no architectural effect on data; it faults like a load of the address (SDM: #PF / #GP as a read), so the address is read once"""
    cx.em('{ volatile u8 t_=M8(%s); (void)t_; }' % cx.ea(ins, cx.ops[0]))


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
    while b[i] in WINE_PREFIX:
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
    while i < len(b) and b[i] in WINE_PREFIX:
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
