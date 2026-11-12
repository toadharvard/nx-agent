"""Decode, recursive descent, jump tables, emit loop, the per-function context and the handler registry.

Dispatch order per instruction (same as the PoC): mnemonic handler (@op); a handler may return SKIP to fall
through; then the fallbacks in registration order (@fallback(pred): x87 by opcode byte, SSE by xmm operand);
then Unsupported('insn ...')."""
import bisect
import capstone
from capstone import x86 as X

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True


class Unsupported(Exception):
    pass


class X87Dyn(Exception):
    """raised by an x87 handler of the static-depth path that cannot be modelled statically: the function is lifted again with the dynamic stack"""


R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']
SUB = {}
for i, r in enumerate(R32):
    SUB[r] = (r, 0, 32)
    SUB[r[1:]] = (r, 0, 16)
for r in 'abcd':
    SUB[r + 'l'] = ('e' + r + 'x', 0, 8)
    SUB[r + 'h'] = ('e' + r + 'x', 8, 8)
for r in ('si', 'di', 'bp', 'sp'):
    SUB[r + 'l'] = ('e' + r, 0, 8)
MASK = {8: '0xffu', 16: '0xffffu', 32: '0xffffffffu'}
CC = {'o': 'of', 'no': '!of', 'b': 'cf', 'ae': '!cf', 'e': 'zf', 'ne': '!zf', 'be': '(cf|zf)', 'a': '!(cf|zf)',
      's': 'sf', 'ns': '!sf', 'p': 'pf', 'np': '!pf', 'l': '(sf!=of)', 'ge': '(sf==of)', 'le': '(zf||sf!=of)',
      'g': '(!zf&&sf==of)'}

SKIP = object()      # a handler returns this when its extra condition fails: dispatch continues
OPS = {}             # mnemonic -> handler(cx, ins)
FALLBACKS = []       # (pred(cx, ins), handler(cx, ins)), tried in order when no mnemonic handler took the insn


def op(*names):
    def deco(fn):
        for n in names:
            OPS[n] = fn
        return fn
    return deco


def fallback(pred):
    def deco(fn):
        FALLBACKS.append((pred, fn))
        return fn
    return deco


def reg(name):
    if name not in SUB:
        raise Unsupported('register ' + name)
    return SUB[name]


class Ctx:
    """What the PoC closures carried: output list, per-function state and operand helpers.
    Per-instruction fields (set by the emit loop): a, nxt, m, pfx, ops, s, b0, modrm, term."""

    def __init__(self, addr, size, insns, tables, taken):
        self.addr, self.size = addr, size
        self.lo, self.hi = addr, addr + size
        self.insns, self.tables, self.taken = insns, tables, taken
        self.L = []
        self.callrets = []
        self.usesfs = False
        self.kinds = set()
        self.macc = 'M'       # memory accessor prefix of rd/wr: M, or LK inside a locked instruction (lift2)
        self.selfcalls = {}
        self.noentry = set()                     # direct targets (calls, jumps, fall-throughs) routed to the dispatcher because they are not entries (meta; check (c): all outside the executable sections)
        self.calls, self.tails = set(), set()    # direct call targets / jumps to entries outside the function (meta, closure of the bench)
        self.nonlocal_ = False                   # a `goto nonlocal` / `goto after_tail` was emitted: the function needs the loop
        self.imports = False
        self.raises = set()                      # addresses of instructions that can raise: ContinueExecution resumes there, so they are labels of the loop

    def em(self, t):
        self.L.append(t)

    def lab(self, a):
        return 'L_%08x' % a

    def ea(self, ins, op):
        """effective address of a memory operand. 0x67 (16-bit addressing, SDM Vol 1 3.7.5): base + index + disp are summed modulo 2^16 and zero-extended; the segment base is added after.
        Segment overrides: FS = c->fs (fnvl_seg_set keeps it right); GS = FNVL_GS_BASE() by the CURRENT c->sel[GS] (the null / code-selector #GP is the guard lift2 emits before the
        instruction, gs_guard); ES/CS/SS/DS (and the defaults) are flat while the selector in that register is a valid flat one (open class: DS/ES/SS/FS reloaded, README of the lift lead)."""
        m = op.mem
        seg = None
        if m.segment:
            if m.segment == X.X86_REG_FS:
                seg = 'fsb'
                self.usesfs = True
            elif m.segment == X.X86_REG_GS:
                seg = 'FNVL_GS_BASE()'
            elif m.segment not in (X.X86_REG_DS, X.X86_REG_SS, X.X86_REG_ES, X.X86_REG_CS):
                raise Unsupported('segment')
        parts = []
        if m.base:
            parts.append(reg(ins.reg_name(m.base))[0])
        if m.index:
            parts.append('%s*%du' % (reg(ins.reg_name(m.index))[0], m.scale))
        if m.disp or not (parts or seg):
            parts.append('0x%xu' % (m.disp & 0xffffffff))
        off = '+'.join(parts)
        if parts and ins.prefix[3] == 0x67:
            off = '((%s)&0xffffu)' % off
        return '(u32)(' + '+'.join(([seg] if seg else []) + ([off] if off else [])) + ')'

    def rd(self, ins, op, size=None):
        s = (size or op.size) * 8
        if op.type == X.X86_OP_IMM:
            return '0x%xu' % (op.imm & ((1 << s) - 1))
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            return b if bits == 32 else ('((%s>>%d)&%s)' % (b, sh, MASK[bits]))
        return '%s%d(%s)' % (self.macc, s, self.ea(ins, op))

    def wr(self, ins, op, v):
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            if bits == 32:
                return '%s=(%s);' % (b, v)
            mk = (1 << bits) - 1
            return '%s=(%s&~0x%xu)|(((u32)(%s)&0x%xu)<<%d);' % (b, b, mk << sh, v, mk, sh)
        return '%s%d(%s)=(%s);' % (self.macc, op.size * 8, self.ea(ins, op), v)

    def zsp(self, s):
        return 'zf=(r&%s)==0; sf=(r>>%d)&1; pf=PAR(r);' % (MASK[s], s - 1)

    def xr(self, ins, op):
        n = ins.reg_name(op.reg)
        if not n.startswith('xmm'):
            raise Unsupported('register ' + n)
        return 'x' + n[3:]

    def fmem(self, ins, op):
        return ('MF32' if op.size == 4 else 'MF64') + '(' + self.ea(ins, op) + ')'


def explore(img, lo, hi, code, start=None, extra=(), fall_hi=None):
    """Recursive descent over [lo, hi) from `start` (default lo) and the `extra` labels: -> (insns by address, jump tables by jmp address,
    address-taken labels, addresses of undecodable bytes a path reaches). Falling through (not jumping) past hi decodes on up to fall_hi: only
    true undecodable bytes end such a path. `code` covers [lo, fall_hi)."""
    fall_hi = hi if fall_hi is None else fall_hi
    insns, bad = {}, set()
    work = [lo if start is None else start] + [x for x in extra if lo <= x < hi]
    tables = {}
    i0 = bisect.bisect_left(img.taken, lo)
    i1 = bisect.bisect_left(img.taken, hi)
    taken = sorted(set(img.taken[i0:i1]))

    def decode(a):
        g = md.disasm(code[a - lo:a - lo + 16], a, 1)
        for ins in g:
            return ins
        from . import f_sys
        return f_sys.nop_gap(code[a - lo:a - lo + 16])      # reserved-NOP encodings capstone refuses (every CPU executes them as NOP)

    while work:
        a = work.pop()
        fell = False
        while (lo <= a < hi or (fell and a < fall_hi)) and a not in insns and a not in bad:
            ins = decode(a)
            if ins is None:
                bad.add(a)
                break
            insns[a] = ins
            m = ins.mnemonic.split(' ')[-1]      # the instruction under its prefix words (`repne jne`, `bnd jmp`, `lock ...`): control flow is decided by the instruction, as the emitter does
            nxt = a + ins.size
            ops = ins.operands
            if m == 'jmp':
                if ops[0].type == X.X86_OP_IMM:
                    work.append(ops[0].imm & 0xffffffff)
                elif ops[0].type == X.X86_OP_MEM and ops[0].mem.scale == 4 and ops[0].mem.index and (a + ins.size - 4) in img.rset:
                    t = ops[0].mem.disp & 0xffffffff
                    ent = []
                    while t in img.rset and lo <= img.u32(t) < hi:
                        ent.append(img.u32(t))
                        t += 4
                        if lo <= t < hi and t in insns:
                            break
                    tables[a] = ent
                    work.extend(ent)
                else:
                    work.extend(taken)
                break
            if m in ('ret', 'retf', 'hlt', 'ud2') or (m == 'int3' and nxt >= hi):     # an int3 inside the extent can be resumed after (#BP, ContinueExecution)
                break
            if m.startswith('j') or m in ('loop', 'loope', 'loopne'):
                work.append(ops[0].imm & 0xffffffff)
            a = nxt
            fell = True
    return insns, tables, taken, bad


EMIT = ''      # 'dsl': integer instructions are emitted from the proven DSL forms (f_dsl.py) before the hand handlers; '' = the hand handlers only. Set by the caller with set_emit (an argument of the generator, no environment)


def set_emit(mode):
    global EMIT
    if mode not in ('', 'dsl'):
        raise ValueError('emit mode %r (want "dsl" or "")' % (mode,))
    EMIT = mode


JTAB = None      # facts of tools/jtab.py: {site: (lo, hi, targets)} from the `jtab_tables` records of ehtables.json; set by set_jtab()
JTAB_RED = None  # sites tools/jtab.py could not prove an extent for: {site: why}


def set_jtab(facts):
    """facts: the parsed ehtables.json (a dict) of the module being lifted, or None. The only source of jump-table extents: this module never analyses a table itself."""
    global JTAB, JTAB_RED
    if facts is None:
        JTAB = JTAB_RED = None
        return
    JTAB = {int(r['site'], 16): (r['lo'], r['hi'], tuple(int(w, 16) for w in r['targets'])) for r in facts['jtab_tables']}
    JTAB_RED = {int(r['site'], 16): r['why'] for r in facts.get('jtab_red', ())}


def tab_tails(img, insns, lo, hi):
    """Code pointers a `jmp [reloc disp + idx*4]` can hand to the dispatcher (rule R3): the proved slot values of the site's table (the `jtab_tables` record of
    its address, extent proved by tools/jtab.py), outside [lo, hi). A site with no record (no proved extent) is Unsupported."""
    out = set()
    for a, ins in insns.items():
        if ins.mnemonic.split(' ')[-1] != 'jmp' or not ins.operands or ins.operands[0].type != X.X86_OP_MEM:
            continue
        m = ins.operands[0].mem
        if m.scale == 4 and m.index and (a + ins.size - 4) in img.rset:
            if JTAB is None:
                raise Unsupported('jump table at %x: no jtab facts loaded' % a)
            if a not in JTAB:
                raise Unsupported('jump table at %x: no proved extent (%s)' % (a, (JTAB_RED or {}).get(a, 'no jtab record')))
            out.update(v for v in JTAB[a][2] if not lo <= v < hi)
    return sorted(out)


def dispatch(cx, ins):
    from . import f_cpufeat, f_sys
    if f_cpufeat.missing(ins):         # CPUID-UD rule (f_cpufeat.py): a form the model's CPUID denies is #UD before any handler of any family runs
        f_sys.ud_site(cx)
        return
    if EMIT == 'dsl':
        from . import f_dsl
        if f_dsl.emit_form(cx, ins) is not SKIP:
            return
    h = OPS.get(cx.m)
    if h is not None and h(cx, ins) is not SKIP:
        return
    for pred, fn in FALLBACKS:
        if pred(cx, ins):
            fn(cx, ins)
            return
    raise Unsupported('insn ' + (cx.pfx + ' ' if cx.pfx else '') + cx.m)


def lift(img, addr, size, popn=None, start=None, owns=None, strict=True, x87=None):
    """-> (C text, meta). Raises Unsupported(reason).
    [addr, addr+size) is the code range of the function (for a funclet: of its parent); `start` (default addr) the entry the body is the descent
    from, named L_<start>; `owns` the other universe entries with this range as parent: they are explored and are labels of the nonlocal loop;
    strict=False (instest only) turns falling off the decoded code into the stub event fnvl_raise(2, at) instead of a lift-time error.
    x87 (instest only): None = the pre-pass decides (f_x87_ctl.py), 'dyn' = dynamic x87 stack, an int = static stack with that entry depth."""
    from . import universe
    if start is None and owns is None:
        ua = universe.entry_args(addr)
        if ua is not None and ua['start'] != ua['addr']:        # an inner entry: its own function from its label inside the parent's range
            addr, size, start = ua['addr'], ua['size'], ua['start']
    start = addr if start is None else start
    lo, hi = addr, addr + size
    if owns is None:
        owns = universe.load()[1].get(addr, ())
    owns = sorted(x for x in owns if lo <= x < hi)
    return lift1(img, lo, hi, start, owns, strict, True, x87)


def lift1(img, lo, hi, start, owns, strict, explore_owns, x87=None):
    try:
        return lift2(img, lo, hi, start, owns, strict, explore_owns, x87)
    except X87Dyn:                                  # an x87 form the static stack cannot model: again with the dynamic stack
        return lift2(img, lo, hi, start, owns, strict, explore_owns, 'dyn')


NO_ACCESS = ('lea', 'nop', 'prefetch', 'prefetchnta', 'prefetcht0', 'prefetcht1', 'prefetcht2', 'prefetchw')      # a memory operand that is only an address / a hint: no segment check


def gs_guard(cx, ins):
    """the #GP of a `gs:` access (SDM Vol 3 5.3 / 3.4.2): one guard at the start of the instruction, whichever handler uses the operand (ea adds the base). With GS holding the null selector any
    access is #GP(0); a write through a code segment (GS loaded with CS: readable code, not writable) is #GP(0) too. #GP(0) is STATUS_ACCESS_VIOLATION {0, 0xFFFFFFFF} (f_sys.gp_cond)."""
    from . import f_sys
    if ins.mnemonic.split(' ')[-1] in NO_ACCESS:
        return
    if any(o.type == X.X86_OP_MEM and o.mem.segment == X.X86_REG_CS and o.access & capstone.CS_AC_WRITE for o in ins.operands):
        # CS-store: CS is a readable, executable, not writable code segment, so a store through it is #GP(0) (SDM Vol 3 5.3.1 "Limit and Type Checking": writing to a code segment); a load through CS is legal.
        # TRANSITIONAL: "is a store" is taken from capstone's operand access here (hand path); on the DSL path it comes from the certified Form (an explicit AV fault that L3b's builder emits for a store with effective segment CS) once
        # item 23 moves the shape there, and this check then goes away.
        # Windows reports a ring-3 #GP with error code 0 as STATUS_ACCESS_VIOLATION {0, 0xFFFFFFFF} at the instruction: Wine 9.0 dlls/ntdll/unix/signal_i386.c, segv_handler, `case TRAP_x86_PROTFLT:` (lines 1925-1937 at tag
        # wine-9.0: err == 0 and not is_privileged_instr -> rec.ExceptionCode = EXCEPTION_ACCESS_VIOLATION, NumberParameters = 2, ExceptionInformation[0] = 0, [1] = 0xffffffff); the same row as f_sys.gp_cond. Nothing was modified yet.
        cx.em(f_sys.gp_cond(cx, '1'))
        return
    g = [o for o in ins.operands if o.type == X.X86_OP_MEM and o.mem.segment == X.X86_REG_GS]
    if not g:
        return
    cond = 'SELK(c->sel[SEG_GS])==0'
    if any(o.access & capstone.CS_AC_WRITE for o in g):
        cond += '||SELK(c->sel[SEG_GS])==SELK(SEL_CS)'
    cx.em(f_sys.gp_cond(cx, cond))


def lift2(img, lo, hi, start, owns, strict, explore_owns, x87):
    from . import f_x87_ctl as xc, f_sys
    addr, size = lo, hi - lo
    fall_hi = hi          # a body is exactly its range: falling past it is a transfer to the next address (f_flow.jump_out), never a copy of the code that follows
    code = img.bytes(addr, hi - addr + 16)      # 16 more bytes: the last instruction may straddle hi
    insns, tables, taken, bad = explore(img, lo, hi, code, start, owns if start == addr and explore_owns else (), fall_hi)
    if start not in insns and start not in bad:     # an entry with an empty range (the universe found no decodable byte at it): its body is the #UD at its address
        if next(iter(md.disasm(img.bytes(start, 16), start, 1)), None) is not None:
            raise Unsupported('entry %x decodes but lies outside its range' % start)
        bad = set(bad) | {start}
    order = sorted(insns)
    def head4(a):
        """the first four bytes at `a` (what KiTrap06Handler scans for F0): from `code`, beyond it from the image (none when the image has no byte there)"""
        t = code[a - lo:a - lo + 4]
        if len(t) < 4:
            try:
                t = img.bytes(a, 4)
            except (TypeError, ValueError, IndexError):
                pass
        return t
    cx = Ctx(addr, size, insns, tables, taken)
    cx.start = start
    xc.prepass(cx, img, 'dyn' if x87 == 'dyn' else None, x87 if isinstance(x87, int) else 0)     # x87 stack-depth pre-pass: cx.xm 's' static / 'd' dynamic / None
    em = cx.em
    kinds = cx.kinds
    from . import universe
    uent = getattr(img, 'entries', None)          # a synthetic image of the unit tests names its entries itself
    uent = universe.load()[0] if uent is None else uent
    cx.uent = uent
    for idx, a in enumerate(order):
        ins = insns[a]
        m = ins.mnemonic
        ops = ins.operands
        nxt = a + ins.size
        em('%s: ;' % cx.lab(a))
        if a == start or a in uent:
            em('TRACE(0x%xu);' % a)        # every pass over an entry address is a trace event (bench), wherever the function was entered
        pfx = ''
        if m.startswith('rep ') or m.startswith('repe ') or m.startswith('repne '):
            pfx, m = m.split(' ', 1)
        bad_lock = False
        locked = False
        w = m.split(' ')
        if 'lock' in w[:-1] and all(x + ' ' in f_sys.STRIP for x in w[:-1]):      # LOCK among the leading prefix words (capstone: `lock add`, `xacquire lock add`): SDM Vol 2 "LOCK"
            m = w[-1]
            bad_lock = not f_sys.lock_ok(ins, m)
            if not bad_lock:
                kinds.add('lock')
                locked = True
        cx.a, cx.nxt, cx.m, cx.pfx, cx.ops = a, nxt, m, pfx, ops
        cx.s = ops[0].size * 8 if ops else 32
        cx.term = False
        cx.xdp = cx.xd.get(a, 0)
        n0 = len(cx.L)
        cx.b0 = ins.opcode[0]
        cx.modrm = ins.modrm
        if bad_lock:
            f_sys.ud_site(cx)                  # LOCK on an instruction that cannot take it (or with a register destination): #UD
        elif f_sys.locked(ins, m, locked):
            # R9 atomic rule (rt/fnvl.h LK_*): the body runs on a private copy of the memory operand, the commit is a compare-and-swap, a lost race restores the registers and flags and runs it again
            gs_guard(cx, ins)
            cx.macc = 'LK'
            em('{ LK_BEGIN(%d); lk_%x: ;' % (f_sys.lock_size(ins), a))
            dispatch(cx, ins)
            cx.macc = 'M'
            em('if(!lk_commit(lk_)){ LK_RESTORE; goto lk_%x; } }' % a)
        else:
            gs_guard(cx, ins)
            dispatch(cx, ins)
        if any('FNVL_RAISE(FNVL_UD,' in t for t in cx.L[n0:]) and 0xf0 in head4(a):      # KiTrap06Handler: a #UD with an F0 byte among the first four bytes at Eip is STATUS_INVALID_LOCK_SEQUENCE
            cx.L[n0:] = [t.replace('FNVL_RAISE(FNVL_UD,', 'FNVL_RAISE(FNVL_LOCK,') for t in cx.L[n0:]]
        if not cx.term and (idx + 1 == len(order) or order[idx + 1] != nxt):
            if nxt in insns or nxt in bad:
                em('goto %s;' % cx.lab(nxt))
            elif not strict:
                em('SYNC_OUT; FNVL_RAISE(2,0x%xu); return;' % nxt)
            else:       # the successor is neither decoded here nor undecodable: control passes to it exactly like `jmp nxt` (one rule, f_flow.jump_out)
                from . import f_flow
                f_flow.jump_out(cx, nxt)
        if cx.xm == 's':
            xc.fix_lines(cx, a, n0)
    for a in sorted(bad):      # bytes that do not decode: the CPU raises #UD there
        cx.nonlocal_ = True
        em('%s: ; SYNC_OUT; REGS(); FNVL_RAISE(%s,0x%xu); REGS(); SYNC_IN; goto nonlocal;' % (cx.lab(a), 'FNVL_LOCK' if 0xf0 in head4(a) else 'FNVL_UD', a))
    if not order or start != order[0]:       # also an entry whose first byte does not decode: its body is the #UD at that address
        cx.L.insert(0, 'goto %s;' % cx.lab(start))
    if cx.nonlocal_:
        labs = sorted(set(x for x in cx.callrets + owns + list(tables_all(tables)) + taken + sorted(cx.raises | bad) if x in insns or x in bad))
        cases = ' '.join('case 0x%xu: %sgoto %s;' % (x, xc.case_pre(cx, x), cx.lab(x)) for x in labs)
        em('after_tail: ; if(esp>E){ SYNC_OUT; return; }')
        em('nonlocal: ; for(;;){ u32 t=c->ra; if(esp>E){ SYNC_OUT; return; } switch(t){ %s default: break; } SYNC_OUT; REGS(); fnvl_dispatch(t)(); REGS(); SYNC_IN; }' % cases)
    body = '\n'.join(cx.L)
    # R4 raise-site flush rule (rt/fnvl.h RAISE_OUT / RAISE_IN): every explicit call of the OS dispatcher flushes the EFLAGS and x87 condition-bit locals into struct cpu before it and reloads them after
    fl = 'SYNC_X_IN;' if 'sse' in kinds else ''
    body = body.replace('SYNC_OUT;', 'SYNC_OUT;' + ('SYNC_X_OUT;' if 'sse' in kinds else ''))
    body = body.replace('SYNC_IN;', 'SYNC_IN;' + fl)
    decl = ''.join('extern "C" void L_%08x(void);\n' % t for t in sorted(cx.calls | cx.tails))
    txt = '%sextern "C" void L_%08x(void)\n{ DECL; SYNC_IN;%s%s%s u32 E=esp; %s\n%s\n}\n' % (
        decl, start, xc.decl(cx), fl, ' u32 fsb=c->fs;' if cx.usesfs else '', '', body)
    if cx.usesfs:
        kinds.add('fs')
    meta = dict(addr=start, size=size, selfcalls=cx.selfcalls, callrets=cx.callrets, kinds=sorted(kinds), n=len(order), tables=len(tables),
                calls=sorted(cx.calls), tails=sorted(cx.tails), noentry=sorted(cx.noentry), tabtails=tab_tails(img, insns, lo, hi))
    return txt, meta


def tables_all(tables):
    for v in tables.values():
        yield from v
