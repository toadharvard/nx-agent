"""System and control instructions of the integer family (W14): cpuid/rdtsc (the one reference CPU model, rt/cpumodel.c), pushad/popad, pushfd/popfd,
software interrupts, into/bound, the privileged instructions, loop/loope/loopne, `repz ret`.

Faults are never trap paths: they go through fnvl_raise(code, at) / fnvl_raise_av (rt/seh.c) exactly like #DE and #UD (f_flow.raise_site)."""
from capstone import x86 as X
from .core import op, Unsupported
from . import f_flow


def site(cx, call, term=True, traps=False):
    """emit a hardware-exception site: `call` is the C statement raising it; ContinueExecution resumes at Context.Eip, so the instruction (retry) and, for a
    trap, the next instruction are labels of the non-local loop"""
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    cx.raises.add(cx.nxt)
    cx.em('SYNC_OUT; REGS(); %s REGS(); SYNC_IN; goto nonlocal;' % call)
    cx.term = term


def raise_code(cx, code, at=None):
    site(cx, 'fnvl_raise(%s,0x%xu);' % (code, cx.a if at is None else at))


# int imm8 in a ring-3 Win32 process. Vector -> (what the process observes, confidence).
#   3     breakpoint: IDT[3] has DPL 3 (the debugger interface). STATUS_BREAKPOINT 0x80000003; a trap, KiTrap03 reports ExceptionAddress = EIP-1: the byte of the
#         imm8 (cd 03 is two bytes: at = a+1), Context.Eip = the next instruction. High.
#   4     `int 4` = into without the OF test: IDT[4] DPL 3, KiTrap04 -> STATUS_INTEGER_OVERFLOW 0xC0000095, a trap (as FNVL_INTO). Medium.
#   5     IDT[5] (bound) DPL 3 on NT -> STATUS_ARRAY_BOUNDS_EXCEEDED 0xC000008C. Low (not sure of the gate DPL of vector 5).
#   0x2c  KiRaiseAssertion, DPL 3 (DbgRaiseAssertionFailure): STATUS_ASSERTION_FAILURE 0xC0000420, Context.Eip rewound onto the int. Medium (Vista+ kernels).
#   0x2d  KiDebugService, DPL 3 (DbgPrint/DbgBreakPoint services): a breakpoint, STATUS_BREAKPOINT, like vector 3. Low on the Eip details (the real kernel also
#         skips the int3 byte that follows the int 2d idiom and passes eax/ecx/edx as ExceptionInformation; neither is modelled).
#   0x2e  the legacy system-service gate is DPL 3 on real NT, but a lifted process has no kernel behind it: it is the default row (AV). Low.
#   other every other vector: the gate has DPL 0, so `int n` from ring 3 is #GP -> STATUS_ACCESS_VIOLATION, ExceptionInformation = {0, 0xFFFFFFFF}. This is also
#         vector 0x29 (fast-fail does not exist on the reference OS version) and `int 6` (the only one in FalloutNV.exe). Medium (the rule is the task's).
INT_VECTORS = {3: ('FNVL_BP', 1), 4: ('FNVL_INTO', 1), 5: ('FNVL_BOUNDS', 0), 0x2c: ('FNVL_ASSERT', 0), 0x2d: ('FNVL_BP', 1)}


@op('int')
def h_int(cx, ins):
    v = cx.ops[0].imm & 0xff
    row = INT_VECTORS.get(v)
    if row is None:
        site(cx, 'fnvl_raise_av(0,0xffffffffu,0x%xu);' % cx.a)
    else:
        raise_code(cx, row[0], cx.a + row[1])


@op('into')
def h_into(cx, ins):
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    cx.raises.add(cx.nxt)
    cx.em('if(of){ SYNC_OUT; REGS(); fnvl_raise(FNVL_INTO,0x%xu); REGS(); SYNC_IN; goto nonlocal; }' % cx.a)


@op('bound')
def h_bound(cx, ins):
    ops = cx.ops
    s = cx.s
    cx.nonlocal_ = True
    cx.raises.add(cx.a)
    ea = cx.ea(ins, ops[1])
    cx.em('{ u32 ad=%s; s32 i=(s32)(s%d)%s; if(i<(s32)(s%d)M%d(ad)||i>(s32)(s%d)M%d(ad+%d)){ SYNC_OUT; REGS(); fnvl_raise(FNVL_BOUNDS,0x%xu); REGS(); SYNC_IN; goto nonlocal; } }' % (
        ea, s, cx.rd(ins, ops[0]), s, s, s, s, s // 8, cx.a))


# privileged in ring 3 (IOPL 0): STATUS_PRIVILEGED_INSTRUCTION. Windows delivers it for #GP of these opcodes.
PRIV = ('cli', 'sti', 'hlt', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd', 'rdmsr', 'wrmsr', 'invd', 'wbinvd', 'clts', 'lgdt', 'lidt', 'lldt', 'ltr',
        'invlpg', 'rdpmc', 'sysexit')


@op(*PRIV)
def h_priv(cx, ins):
    raise_code(cx, 'FNVL_PRIV')


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


@op('loop', 'loope', 'loopne')
def h_loop(cx, ins):
    c = {'loop': '', 'loope': '&&zf', 'loopne': '&&!zf'}[cx.m]
    cx.em('ecx--;')
    cond_jump(cx, 'ecx!=0' + c, cx.ops[0].imm & 0xffffffff)


@op('repz ret', 'repnz ret', 'repe ret', 'repne ret')
def h_repz_ret(cx, ins):
    f_flow.h_ret(cx, ins)
