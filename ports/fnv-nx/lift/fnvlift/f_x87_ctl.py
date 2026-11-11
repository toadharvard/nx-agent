"""x87, part 2: the stack-depth pre-pass, the per-site stack sync of the static path, and the control/status instructions.

Two stack models (R8). STATIC: the pre-pass proves a depth 0..8 at every instruction (entry 0; a call changes it by the callee's net return depth from
/home/user/l3facts/fpmodes/depth.tsv, tools/fpmodes.py: columns addr, status, max, min, ret=, conf=, ind=; override with FNVNX_FPDEPTH); each stack slot is a
C local s<j> (j = 0 is the bottom of the function's own stack, so st(i) at depth d is s<d-1-i>; GCC keeps them in registers); c->fx[] / c->ftop are touched
only at calls, tail jumps, returns and non-local exits (store the live slots) and after a call or at a non-local re-entry (reload). ft0 is the physical
register that the function's bottom slot sits above: slot j lives in c->fx[(ft0-1-j)&7], TOP = (ft0-depth)&7. DYNAMIC: everything goes through c->fx / c->ftop
(fnvl_x87.h dyn_*), with tags (k==0 is empty) and the stack fault. A function is dynamic when the proof fails: a join conflict, a depth outside 0..8, a callee
with a conflict or without a usable row (no file, or an address outside the image), an indirect call (result class unknown), or an instruction the static path
does not model (ffree, fincstp, fdecstp: X87Dyn from the handlers)."""
import os
from capstone import x86 as X
from .core import X87Dyn, Unsupported

TAB = None
DEFAULT = '/home/user/l3facts/fpmodes/depth.tsv'


def table():
    """addr -> (status, net return depth), False when the file is missing"""
    global TAB
    if TAB is None:
        TAB = {}
        try:
            for l in open(os.environ.get('FNVNX_FPDEPTH', DEFAULT)):
                f = l.rstrip('\n').split('\t')
                kv = dict(x.split('=') for x in f[2:] if '=' in x)
                TAB[int(f[0], 16)] = (f[1], int(kv['ret']))
        except (OSError, ValueError, KeyError):
            TAB = False
    return TAB


def callee_ret(img, t):
    """net x87 depth change of a direct call target, None = unknown. Every entry that uses x87 or calls anything has a row; no row = a leaf without x87 = 0."""
    T = table()
    if T is False:
        return None
    r = T.get(t)
    if r is None:
        return 0 if img.off(t) is not None else None
    return r[1] if r[0] in ('static', 'inferred') else None


def effect(b0, mr):
    """-> (net depth change, resets the stack). Forms the lifter does not model change nothing here; their handler raises Unsupported."""
    g = (mr >> 3) & 7
    if mr < 0xc0:
        if b0 in (0xd8, 0xdc, 0xda, 0xde):
            return (-1 if g == 3 else 0), False
        t = {0xd9: {0: 1, 3: -1}, 0xdb: {0: 1, 1: -1, 3: -1, 5: 1, 7: -1}, 0xdd: {0: 1, 1: -1, 3: -1}, 0xdf: {0: 1, 1: -1, 3: -1, 4: 1, 5: 1, 6: -1, 7: -1}}.get(b0, {})
        return t.get(g, 0), False
    if b0 == 0xd8:
        return (-1 if g == 3 else 0), False
    if b0 == 0xd9:                                                    # fld sti, fld1..fldz push; fptan fsincos fxtract push; fpatan fyl2x fyl2xp1 pop
        return (1 if mr < 0xc8 or 0xe8 <= mr <= 0xee else -1 if 0xd8 <= mr <= 0xdf else {0xf1: -1, 0xf2: 1, 0xf3: -1, 0xf4: 1, 0xf9: -1, 0xfb: 1}.get(mr, 0)), False
    if b0 == 0xda:
        return (-2 if mr == 0xe9 else 0), False
    if b0 == 0xdb:
        return 0, mr == 0xe3
    if b0 == 0xdd:
        return (-1 if 0xd8 <= mr <= 0xdf or mr >= 0xe8 else 0), False
    if b0 == 0xde:
        return (-2 if mr == 0xd9 else -1), False
    if b0 == 0xdf:
        return (-1 if mr >= 0xe8 or mr < 0xc8 else 0), False
    return 0, False


def succs(cx, a, ins, m):
    ops, nxt, I = ins.operands, a + ins.size, cx.insns
    if m in ('ret', 'retf', 'int3', 'hlt', 'ud2'):
        return []
    if m == 'jmp':
        if ops[0].type == X.X86_OP_IMM:
            return [t for t in [ops[0].imm & 0xffffffff] if t in I]
        t = cx.tables.get(a)
        return [x for x in (t if t is not None else cx.taken) if x in I]
    if m.startswith('j') or m in ('loop', 'loope', 'loopne'):
        return [t for t in [ops[0].imm & 0xffffffff] if t in I] + [nxt]
    return [nxt]


def prepass(cx, img, force, entry):
    """sets cx.xm ('s' static / 'd' dynamic / None: no x87), cx.xd (depth before each instruction), cx.xa (after), cx.xmax, cx.xe (entry depth)"""
    cx.xm, cx.xd, cx.xa, cx.xmax, cx.xe, cx.xwhy = None, {}, {}, 0, entry or 0, ''
    if not any(0xd8 <= i.opcode[0] <= 0xdf for i in cx.insns.values()):
        return
    cx.kinds.add('x87')
    cx.xm = 'd'
    if force == 'dyn':
        cx.kinds.add('x87d')
        return
    ok = analyze(cx, img)
    cx.xm = 's' if ok else 'd'
    cx.kinds.add('x87s' if ok else 'x87d')
    cx.kinds.add('x87why:' + cx.xwhy) if cx.xwhy else None
    if not ok:
        cx.xd, cx.xa = {}, {}


def no(cx, why):
    cx.xwhy = why
    return False


def analyze(cx, img):
    I, D, A = cx.insns, cx.xd, cx.xa
    todo = [(cx.start, cx.xe)]
    while True:
        while todo:
            a, d = todo.pop()
            if a in D:
                if D[a] != d:
                    return no(cx, 'join conflict')
                continue
            D[a] = d
            ins = I[a]
            m = ins.mnemonic.split()[-1]
            b0 = ins.opcode[0]
            e, reset = effect(b0, ins.modrm) if 0xd8 <= b0 <= 0xdf else (0, False)
            if m == 'call':
                o = ins.operands[0]
                if o.type == X.X86_OP_IMM:
                    e = callee_ret(img, o.imm & 0xffffffff)
                elif o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and not o.mem.segment:
                    e = 0                                        # import: Win32 / CRT calls return nothing on the x87 stack
                else:
                    return no(cx, 'indirect call')               # result class unknown
                if e is None:
                    return no(cx, 'callee without a usable depth row')
            nd = 0 if reset else d + e
            if nd < 0 or nd > 8 or d > 8:
                return no(cx, 'depth out of range')
            A[a] = nd
            cx.xmax = max(cx.xmax, d, nd)
            todo.extend((s, nd) for s in succs(cx, a, ins, m) if s in I)
        rest = [a for a in sorted(I) if a not in D]               # labels only reached from outside (funclet entries, address-taken)
        if not rest:
            return True
        todo.append((rest[0], 0))


# ---- the static path: store / reload of the live slots ----
def store(cx, d):
    return ''.join('c->fx[(ft0-%d)&7]=s%d;' % (j + 1, j) for j in range(d)) + ''.join('c->fx[(ft0-%d)&7].k=0;' % (j + 1) for j in range(d, cx.xmax)) + 'c->ftop=(ft0-%d)&7;' % d


def load(d):
    return 'ft0=(c->ftop+%d)&7;' % d + ''.join('s%d=c->fx[(ft0-%d)&7];' % (j, j + 1) for j in range(d))


def decl(cx):
    if cx.xm != 's':
        return ''
    return ' fv %s; u32 ft0; %s' % (','.join('s%d' % j for j in range(max(cx.xmax, 1))), load(cx.xe))


def case_pre(cx, x):
    return load(cx.xd.get(x, 0)) if cx.xm == 's' else ''


def fix_lines(cx, a, n0):
    """static path: the lines emitted for the instruction at `a` carry SYNC_OUT / SYNC_IN / goto nonlocal; add the x87 stack traffic there"""
    d, da, L = cx.xd.get(a, 0), cx.xa.get(a, 0), cx.L
    for i in range(n0, len(L)):
        t = L[i]
        if 'SYNC_OUT;' in t:
            t = t.replace('SYNC_OUT;', store(cx, da if 'fnvl_raise(2,' in t else d) + 'SYNC_OUT;')
        if 'SYNC_IN;' in t and 'after_tail' not in t:
            t = t.replace('SYNC_IN;', 'SYNC_IN;' + load(da))
        elif 'goto nonlocal;' in t and 'SYNC_IN;' not in t:
            t = t.replace('goto nonlocal;', store(cx, d) + 'goto nonlocal;')
        L[i] = t


def env(cx, ins, b0, g):
    """fldenv (D9 /4), fnstenv (D9 /6), frstor (DD /4), fnsave (DD /6). The image is the protected-mode layout of SDM Vol 1 8.1.10 figures 8-9 (32-bit operand size: m28 / m108)
    and 8-11 (66 prefix: m14 / m94), selected by the operand-size prefix; the whole x87 state lives in c->fx / c->ftop, so a function with one of these is lifted with the
    dynamic stack (X87Dyn: the static path keeps the stack in C locals and cannot hand the physical registers and tags to an image).

    LOADS (fldenv, frstor): FCW through FLDCW (the one control-word hook: host rounding mode synced), FSW -> TOP, C0 C2 C3 (the lifter's locals), the exception bits IE..PE, SF, C1 -> c->fex
    (the host sticky flags are cleared: c->fex plus the host flags is what FSWV reads); FTW: tag 11 = empty register, any other tag = non-empty (the tag of a non-empty register is
    recomputed from its contents by every store, SDM 8.1.7, so 00/01/10 carry no state); frstor also loads ST(0..7) as 80-bit images into M80 carriers; FIP and FDP are loaded into
    c->fip / c->fdp.
    STORES (fnstenv, fnsave): FCW, FSW (FSWV: TOP, C0..C3, C1, sticky bits), FTW from the register contents, FIP, FCS | FOP << 16, FDP, FDS from the last-instruction state (c->fip, c->fdp:
    rt/fnvl_x87.h, SDM 8.1.8, assumptions A-FDP A-FDS A-FOP A-RES there); fnsave then writes ST(0..7) and performs FNINIT; fnstenv then masks all exceptions (FCW |= 3F via FLDCW).
    Assumptions: A-EMPTY a register that the image marks non-empty but whose carrier is empty holds the real indefinite (the value the tree gives every read of an empty register:
    dyn_rd; the carrier of an empty register keeps no value; fnsave writes it for an empty register too); A-ES the FSW bits ES (7) and B (15) are not kept, no x87 exception is ever pending
    in the model (R8: all masked, FSWV never reports them)."""
    if cx.xm == 's':
        raise X87Dyn()
    d32 = 0 if ins.prefix[2] == 0x66 else 1
    a = cx.ea(ins, cx.ops[0])
    if g == 6:
        cx.em(('FNSTENV(%s,%d,FSWV(c->ftop));' if b0 == 0xd9 else 'FNSAVE(%s,%d,FSWV(c->ftop));') % (a, d32))
    else:
        cx.em(('FLDENV(%s,%d);' if b0 == 0xd9 else 'FRSTOR(%s,%d);') % (a, d32))


# ---- control and status instructions (b0 d8..df); returns True when handled ----
def ctl(cx, ins, b0, mr, g):
    em = cx.em
    top = '((ft0-%d)&7)' % cx.xdp if cx.xm == 's' else 'c->ftop'
    if mr < 0xc0:
        if b0 == 0xd9 and g == 5:
            em('FLDCW(M16(%s));' % cx.ea(ins, cx.ops[0]))
        elif b0 == 0xd9 and g == 7:
            em('M16(%s)=c->fcw;' % cx.ea(ins, cx.ops[0]))
        elif b0 == 0xdd and g == 7:
            em('M16(%s)=FSWV(%s);' % (cx.ea(ins, cx.ops[0]), top))
        elif b0 in (0xd9, 0xdd) and g in (4, 6):
            env(cx, ins, b0, g)
        else:
            return False
        return True
    if b0 == 0xdf and mr == 0xe0:
        em('eax=(eax&0xffff0000u)|FSWV(%s);' % top)
    elif b0 == 0xdb and mr == 0xe2:
        em('FNCLEX();')
    elif b0 == 0xdb and mr == 0xe3:
        em('fnvl_x87_init(); c0=c2=c3=0;' + ('ft0=0;' if cx.xm == 's' else ''))
    elif b0 == 0xd9 and mr == 0xd0:
        pass                                                     # fnop
    elif b0 == 0xdd and g == 0 or b0 == 0xdf and g == 0 or b0 == 0xd9 and mr in (0xf6, 0xf7):
        if cx.xm == 's':
            raise X87Dyn()
        i = mr & 7
        if mr == 0xf6:
            em('c->ftop=(c->ftop-1)&7;')                         # fdecstp
        elif mr == 0xf7:
            em('c->ftop=(c->ftop+1)&7;')                         # fincstp
        else:
            em('c->fx[(c->ftop+%d)&7].k=0;' % i + (' c->ftop=(c->ftop+1)&7;' if b0 == 0xdf else ''))     # ffree / ffreep
    else:
        return False
    return True
