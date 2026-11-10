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
        self.selfcalls = {}
        self.calls, self.tails = set(), set()    # direct call targets / jumps to entries outside the function (meta, closure of the bench)
        self.nonlocal_ = False                   # a `goto nonlocal` / `goto after_tail` was emitted: the function needs the loop
        self.imports = False

    def em(self, t):
        self.L.append(t)

    def lab(self, a):
        return 'L_%08x' % a

    def ea(self, ins, op):
        m = op.mem
        parts = []
        if m.segment:
            if m.segment == X.X86_REG_FS:
                parts.append('fsb')
                self.usesfs = True
            elif m.segment not in (X.X86_REG_DS, X.X86_REG_SS, X.X86_REG_ES, X.X86_REG_CS):
                raise Unsupported('segment')
        if m.base:
            parts.append(reg(ins.reg_name(m.base))[0])
        if m.index:
            parts.append('%s*%du' % (reg(ins.reg_name(m.index))[0], m.scale))
        if m.disp or not parts:
            parts.append('0x%xu' % (m.disp & 0xffffffff))
        return '(u32)(' + '+'.join(parts) + ')'

    def rd(self, ins, op, size=None):
        s = (size or op.size) * 8
        if op.type == X.X86_OP_IMM:
            return '0x%xu' % (op.imm & ((1 << s) - 1))
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            return b if bits == 32 else ('((%s>>%d)&%s)' % (b, sh, MASK[bits]))
        return 'M%d(%s)' % (s, self.ea(ins, op))

    def wr(self, ins, op, v):
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            if bits == 32:
                return '%s=(%s);' % (b, v)
            mk = (1 << bits) - 1
            return '%s=(%s&~0x%xu)|(((u32)(%s)&0x%xu)<<%d);' % (b, b, mk << sh, v, mk, sh)
        return 'M%d(%s)=(%s);' % (op.size * 8, self.ea(ins, op), v)

    def zsp(self, s):
        return 'zf=(r&%s)==0; sf=(r>>%d)&1; pf=PAR(r);' % (MASK[s], s - 1)

    def xr(self, ins, op):
        n = ins.reg_name(op.reg)
        if not n.startswith('xmm'):
            raise Unsupported('register ' + n)
        return 'x' + n[3:]

    def fmem(self, ins, op):
        return ('MF32' if op.size == 4 else 'MF64') + '(' + self.ea(ins, op) + ')'


def explore(img, lo, hi, code, start=None, extra=()):
    """Recursive descent over [lo, hi) from `start` (default lo) and the `extra` labels: -> (insns by address, jump tables by jmp address,
    address-taken labels)."""
    insns = {}
    work = [lo if start is None else start] + [x for x in extra if lo <= x < hi]
    tables = {}
    i0 = bisect.bisect_left(img.taken, lo)
    i1 = bisect.bisect_left(img.taken, hi)
    taken = sorted(set(img.taken[i0:i1]))

    def decode(a):
        g = md.disasm(code[a - lo:a - lo + 16], a, 1)
        for ins in g:
            return ins
        raise Unsupported('undecodable bytes')

    while work:
        a = work.pop()
        while lo <= a < hi and a not in insns:
            ins = decode(a)
            insns[a] = ins
            m = ins.mnemonic
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
            if m in ('ret', 'retf', 'int3', 'hlt', 'ud2'):
                break
            if m.startswith('j') or m in ('loop', 'loope', 'loopne'):
                work.append(ops[0].imm & 0xffffffff)
            a = nxt
    return insns, tables, taken


def dispatch(cx, ins):
    h = OPS.get(cx.m)
    if h is not None and h(cx, ins) is not SKIP:
        return
    for pred, fn in FALLBACKS:
        if pred(cx, ins):
            fn(cx, ins)
            return
    raise Unsupported('insn ' + (cx.pfx + ' ' if cx.pfx else '') + cx.m)


def lift(img, addr, size, popn=None, start=None, owns=(), strict=True):
    """-> (C text, meta). Raises Unsupported(reason).
    [addr, addr+size) is the code range of the function (for a funclet: of its parent); `start` (default addr) the entry the body is the descent
    from, named L_<start>; `owns` the other universe entries with this range as parent: they are explored and are labels of the nonlocal loop;
    strict=False (instest only) turns falling off the decoded code into the stub event fnvl_raise(2, at) instead of a lift-time error."""
    lo, hi = addr, addr + size
    start = addr if start is None else start
    code = img.bytes(addr, size)
    owns = sorted(x for x in owns if lo <= x < hi)
    insns, tables, taken = explore(img, lo, hi, code, start, owns if start == addr else ())
    order = sorted(insns)
    cx = Ctx(addr, size, insns, tables, taken)
    em = cx.em
    kinds = cx.kinds
    for idx, a in enumerate(order):
        ins = insns[a]
        m = ins.mnemonic
        ops = ins.operands
        nxt = a + ins.size
        em('%s: ;' % cx.lab(a))
        pfx = ''
        if m.startswith('rep ') or m.startswith('repe ') or m.startswith('repne '):
            pfx, m = m.split(' ', 1)
        if m.startswith('lock '):
            m = m.split(' ', 1)[1]
            kinds.add('lock')
        cx.a, cx.nxt, cx.m, cx.pfx, cx.ops = a, nxt, m, pfx, ops
        cx.s = ops[0].size * 8 if ops else 32
        cx.term = False
        cx.b0 = ins.opcode[0]
        cx.modrm = ins.modrm
        dispatch(cx, ins)
        if not cx.term and (idx + 1 == len(order) or order[idx + 1] != nxt):
            if nxt in insns:
                em('goto %s;' % cx.lab(nxt))
            elif not strict:
                em('SYNC_OUT; fnvl_raise(2,0x%xu); return;' % nxt)
            else:
                raise Unsupported('falls into undecoded bytes at %x' % nxt)
    if start != order[0]:
        cx.L.insert(0, 'goto %s;' % cx.lab(start))
    if cx.nonlocal_:
        labs = sorted(set(x for x in cx.callrets + owns + list(tables_all(tables)) + taken if x in insns))
        cases = ' '.join('case 0x%xu: goto %s;' % (x, cx.lab(x)) for x in labs)
        em('after_tail: ; if(esp>E){ SYNC_OUT; return; }')
        em('nonlocal: ; for(;;){ u32 t=c->ra; if(esp>E){ SYNC_OUT; return; } switch(t){ %s default: break; } SYNC_OUT; REGS(); fnvl_dispatch(t)(); REGS(); SYNC_IN; }' % cases)
    body = '\n'.join(cx.L)
    fl = ('SYNC_F_IN;' if 'x87' in kinds else '') + ('SYNC_X_IN;' if 'sse' in kinds else '')
    body = body.replace('SYNC_OUT;', 'SYNC_OUT;' + ('SYNC_F_OUT;' if 'x87' in kinds else '') + ('SYNC_X_OUT;' if 'sse' in kinds else ''))
    body = body.replace('SYNC_IN;', 'SYNC_IN;' + fl)
    decl = ''.join('extern "C" void L_%08x(void);\n' % t for t in sorted(cx.calls | cx.tails))
    txt = '%sextern "C" void L_%08x(void)\n{ DECL; SYNC_IN;%s%s u32 E=esp; TRACE(0x%xu);\n%s\n}\n' % (
        decl, start, fl, ' u32 fsb=c->fs;' if cx.usesfs else '', start, body)
    if cx.usesfs:
        kinds.add('fs')
    meta = dict(addr=start, size=size, selfcalls=cx.selfcalls, callrets=cx.callrets, kinds=sorted(kinds), n=len(order), tables=len(tables),
                calls=sorted(cx.calls), tails=sorted(cx.tails))
    return txt, meta


def tables_all(tables):
    for v in tables.values():
        yield from v
