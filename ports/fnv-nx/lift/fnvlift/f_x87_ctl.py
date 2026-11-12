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
from . import sites

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
    cx.mmx_ok = set()
    fown_prepass(cx)
    has_x87 = any(0xd8 <= i.opcode[0] <= 0xdf for i in cx.insns.values())
    if any(is_mmx(i) for i in cx.insns.values()):
        mmx_prepass(cx)
        if has_x87:
            force = 'dyn'            # MMX aliases the physical x87 registers: the static stack keeps them in C locals, so a function with both uses the dynamic stack
    if not has_x87:
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


def is_mmx(ins):
    """an MMX instruction: EMMS or any instruction with an mm register operand (SDM Vol 1 9.2: the instructions that use or set the MMX state)"""
    return ins.mnemonic.split()[-1] == 'emms' or any(o.type == X.X86_OP_REG and ins.reg_name(o.reg).startswith('mm') for o in ins.operands)


def mmx_prepass(cx):
    """cx.mmx_ok = the addresses where the x87 register file is provably CANONICAL for MMX (every carrier an M80 image, TOP = 0, all tags valid: rt/fnvl_x87.h MMX_ENTER) on EVERY path.
    Forward must-dataflow, optimistic start, AND at joins. A non-EMMS MMX instruction establishes the state; EMMS (tags empty), any x87 instruction (changes carriers, TOP, tags), any call
    (the callee may run x87 code) and any entry from outside the straight flow (function start, address-taken labels, jump-table targets, other universe entries, instructions after a call)
    leave or start it unknown. Proof: the state is established only by MMX_ENTER, and every instruction that can change the registers or enters from outside kills it (A-RESUME in rt/fnvl_x87.h
    covers an exception handler that resumes inside the region)."""
    I = cx.insns
    from . import universe
    uent = universe.load()[0]
    roots = {cx.start} | {t for ts in cx.tables.values() for t in ts} | set(cx.taken) | {a for a in I if a in uent}
    preds = {a: [] for a in I}
    for a in I:
        ins = I[a]; m = ins.mnemonic.split()[-1]
        for t in succs(cx, a, ins, m):
            if t in preds:
                preds[t].append(a)
    kills = lambda ins, m: m == 'emms' or 0xd8 <= ins.opcode[0] <= 0xdf or m == 'call'
    after = {a: True for a in I}
    before = {a: a not in roots for a in I}
    changed = True
    while changed:
        changed = False
        for a in sorted(I):
            ins = I[a]; m = ins.mnemonic.split()[-1]
            b = a not in roots and all(after[p] for p in preds[a]) and bool(preds[a])
            r = False if kills(ins, m) else True if is_mmx(ins) else b
            if b != before[a] or r != after[a]:
                before[a], after[a] = b, r
                changed = True
    cx.mmx_ok = {a for a in I if before[a]}      # an instruction after a call: its predecessor's `after` is False (a call kills), so the AND is False


FLAGLESS_MEM = {(0xd9, 0), (0xdd, 0), (0xdb, 5), (0xdb, 0), (0xdf, 0), (0xdf, 4), (0xdb, 7), (0xdd, 2), (0xdd, 3)}      # exact moves: loads, m80 / m64-of-double stores, fbld


def x87_flaggy(ins):
    """an x87 instruction whose lifted body can raise a host FPSR flag (rt/fnvl_x87.h: FOWN_X87 first): everything except the control instructions (they read/replace the sets themselves)
    and the exact data movers (flagless: no host floating-point operation in their body)"""
    b0 = ins.opcode[0]
    if not 0xd8 <= b0 <= 0xdf:
        return False
    mr = ins.modrm; g = (mr >> 3) & 7
    if mr < 0xc0:
        return not (b0 == 0xd9 and g in (4, 5, 6, 7) or b0 == 0xdd and g in (4, 6, 7) or (b0, g) in FLAGLESS_MEM)
    if b0 == 0xd9:
        return not (mr < 0xe0 or mr in (0xe0, 0xe1, 0xe5, 0xf6, 0xf7) or 0xe8 <= mr <= 0xee)       # fld sti, fxch, fnop, fstpnce, fchs, fabs, fxam, constants, fdecstp, fincstp
    if b0 in (0xda, 0xdb):
        return not (mr < 0xe0 and g < 4 or b0 == 0xdb and mr in (0xe2, 0xe3))             # fcmovcc, fnclex, fninit
    if b0 == 0xdd:
        return not (mr < 0xc8 or 0xd0 <= mr <= 0xdf)                                       # ffree, fst / fstp st(i)
    if b0 == 0xdf:
        return 0xe8 <= mr <= 0xf7                                                           # fucomip / fcomip (ffreep and fnstsw ax move no flag)
    return True


SSE_FLAG_PREFIX = ('add', 'sub', 'mul', 'div', 'sqrt', 'min', 'max', 'cmp', 'comis', 'ucomis', 'cvt', 'rcp', 'rsqrt')


def sse_flaggy(ins):
    """an SSE floating-point instruction (xmm / mm operand, arithmetic, compare, convert, reciprocal): integer SIMD (`p...`), moves, logic, shuffles raise no flag"""
    m = ins.mnemonic.split()[-1]
    return m.startswith(SSE_FLAG_PREFIX) and any(o.type == X.X86_OP_REG and ins.reg_name(o.reg)[:2] in ('xm', 'mm') for o in ins.operands)


def fown_prepass(cx):
    """cx.fown_in = {address: 1 | 2} where the FPSR owner (rt/fnvl_x87.h) is provably that family on EVERY path to the instruction; elsewhere the lifter emits the dynamic check.
    Forward must-dataflow, optimistic start (TOP), meet = equal-or-unknown. An x87 / SSE flag-raising instruction sets its family; a call (the callee may change the owner), an entry from
    outside the straight flow (function start, address-taken labels, jump-table targets, other universe entries) and an instruction no predecessor reaches give unknown."""
    I = cx.insns
    cx.fown_in = {}
    fam = {a: (1 if x87_flaggy(i) else 2 if sse_flaggy(i) else 0) for a, i in I.items()}
    if not any(fam.values()):
        return
    from . import universe
    uent = universe.load()[0]
    roots = {cx.start} | {t for ts in cx.tables.values() for t in ts} | set(cx.taken) | {a for a in I if a in uent}
    preds = {a: [] for a in I}
    for a in I:
        ins = I[a]; m = ins.mnemonic.split()[-1]
        for t in succs(cx, a, ins, m):
            if t in preds:
                preds[t].append(a)
    TOP = -1
    before = {a: TOP for a in I}
    after = {a: TOP for a in I}
    changed = True
    while changed:
        changed = False
        for a in sorted(I):
            ins = I[a]; m = ins.mnemonic.split()[-1]
            if a in roots or not preds[a]:
                b = 0
            else:
                vs = {after[p] for p in preds[a]} - {TOP}
                b = TOP if not vs else vs.pop() if len(vs) == 1 else 0
            r = 0 if m == 'call' else fam[a] if fam[a] else b
            if b != before[a] or r != after[a]:
                before[a], after[a] = b, r
                changed = True
    cx.fown_in = {a: b for a, b in before.items() if b in (1, 2)}


def fown_emit(cx, f):
    """emit the owner check of family f (1 x87, 2 SSE) before the current instruction unless fown_prepass proved it"""
    if cx.fown_in.get(cx.a) != f:
        cx.em('FOWN_X87();' if f == 1 else 'FOWN_SSE();')


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
            t = t.replace('SYNC_OUT;', store(cx, da if 'FNVL_RAISE(2,' in t else d) + 'SYNC_OUT;')
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
    dyn_rd; an empty register keeps the 80-bit image it last held, fv_free, which an MMX read sees, but every x87 read of it gives the indefinite; fnsave writes the indefinite for an empty register too); A-ES the FSW bits ES (7) and B (15) are not kept, no x87 exception is ever pending
    in the model (invariant I-FPMASK, fnvlift/fpfacts.py: all exceptions masked, FSWV never reports them)."""
    if cx.xm == 's':
        raise X87Dyn()
    d32 = 0 if ins.prefix[2] == 0x66 else 1
    a = cx.ea(ins, cx.ops[0])
    if g == 6:
        cx.em(('FNSTENV(%s,%d,FSWV(c->ftop),0x%xu);' if b0 == 0xd9 else 'FNSAVE(%s,%d,FSWV(c->ftop),0x%xu);') % (a, d32, cx.a))
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
        elif sites.fsw_form(b0, mr) == 'mem' and g == 7:       # FSTSW m16 (the site predicate is fnvlift/sites.py, shared with the `sites` facts)
            em('FSW_ST16(%s,FSWV(%s),0x%xu);' % (cx.ea(ins, cx.ops[0]), top, cx.a))
        elif b0 in (0xd9, 0xdd) and g in (4, 6):            # FLDENV FRSTOR FNSTENV FNSAVE (g 6: sites.fsw_form 'mem')
            env(cx, ins, b0, g)
        else:
            return False
        return True
    if sites.fsw_form(b0, mr) == 'ax':
        em('FSW_AX(FSWV(%s),0x%xu);' % (top, cx.a))
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
            em('fv_free(&c->fx[(c->ftop+%d)&7]);' % i + (' c->ftop=(c->ftop+1)&7;' if b0 == 0xdf else ''))     # ffree / ffreep
    else:
        return False
    return True
