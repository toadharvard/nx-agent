#!/usr/bin/env python3
"""gatecert - the proof half of the gate_reach request kind `gate_cert=<gate>` (L1 CONTRACT).

    A fact `site symbol S, argument k` is certified by the green gate G when every value that can reach argument k of every call of S
    comes from a producer in the universe G declared.  The proof is over the machine code of the linked objects, decoded by the pinned
    binutils objdump (`objdump -dr -t --no-show-raw-insn`; x86-64 with `-M intel`); this module reads only that text.  ONE rule set, two ISAs: a lowerer per
    ISA (a64: ILP32 AArch64, x64: x86-64 SysV) turns each instruction into one op of a small IR, and the rules below run on the IR.

    parse_objdump(text, isa='a64'|'x64') -> Prog        prove(prog, site, k, producers) -> (reasons [(reason, where, detail)], callers [str], values [str])

Rule.  The set CALLERS(F) of a function F is every function holding a call (or tail jump) relocation to F (a jump passes the argument registers as they are).
F's address taken anywhere (any other relocation to F, outside debug/unwind sections) means an indirect caller; that is allowed only when each such
relocation lies inside a producer data symbol (the producer row IS the indirect call path: the table slot of the declared producer); otherwise
`address-taken`.  The value of argument register R of a call site is the set of VALUES found by a backward walk over the control-flow graph of the caller
(every path; a join takes the union; a loop adds nothing new):
  - a register copy: the value of the source register before the instruction;
  - the result of a call (the return register after a call of F): value PROD(F); any other caller-saved register after a call is clobbered (`clobbered`);
  - the address of symbol Y (a64: adrp + add :lo12:; x64: rip-relative lea or a GOT load), or a load from Y at a constant offset;
  - a load from the address of Y + constant offset, or with a register index (`[b, i, lsl #s]`, `[b + i*s]`): the index must be proven in [0, count),
    count = (st_size(Y) - offset) / elt: (a) every path to the load carries an unsigned compare-and-branch of the index (or of a register it was copied,
    extended or masked from) against a constant <= count (a64 cmp + b.lo/b.hs/b.ls/b.hi, x64 cmp + jb/jae/jbe/ja; cbz/tbz are not bounds), a must-analysis over the
    CFG (the weakest path counts); (b) a loop induction variable is the same proof: its entry edge and its back or exit edge carry the bounds, a loop back
    edge adds nothing by itself.  Anything else is `index-unbounded:<function>+<offset>`;
  - THE OUT-PARAMETER SHAPE: a load from a stack slot S is the output of a producer call iff (1) the address of S (any address of the frame at or below S) flows
    only into the declared out argument of a producer call (a copy is followed; any other read of it is an escape: `out-escape`), (2) on every path from entry
    to the load the last access to S is that producer call: no other store to S in between (`out-store`), no path without the call (`out-uninit`), and (3) the
    offset and size lie within the producer's declared out type (`out-type`), `"out": [{"arg": j, "size": n}]` in the producer row.  The frame must have a
    constant stack-pointer offset at every instruction (`sp-unstable`);
  - the value of an argument register at the function entry: PARAM(j), judged by the callers of the function (the call graph, recursively);
  - anything else (constants, stack loads, arithmetic, an unknown call result) has no certificate (`constant`, `stack-load`, `computed`, ...).
A value is accepted iff it is PROD(F) with F a producer of kind function, or an address of / load from Y at a constant offset < size(Y) with Y a
producer of kind data (sizes from the symbol table), or PARAM(j) whose callers are all accepted.  A requested site with no call is `no-site` (a vacuous
certificate is no certificate).  Assumptions: the compiler (OWNER 1) and the pinned objdump decode the objects.
"""
import re

IGNORED_SECTIONS = ('.debug', '.eh_frame', '.note', '.comment', '.gnu', '.ARM', '.stack')
WORD = {'w': 'w', 'x': 'x'}


# ---------------------------------------------------------------------------------------------- ISA records
class ISA:
    def __init__(self, name, args, ret, caller_saved):
        self.name, self.args, self.ret, self.caller_saved = name, list(args), ret, set(caller_saved)


A64 = ISA('a64', range(8), 0, range(19))
X64 = ISA('x64', [7, 6, 2, 1, 8, 9], 0, [0, 1, 2, 6, 7, 8, 9, 10, 11])
ISAS = {'a64': A64, 'x64': X64}


class Insn:
    """One lowered instruction.  op: call tail ret jmp jcc cmp mov movi page addlo addr addimm sp_addr andi load store other.  wr: registers it may write
    ('ALL' = unknown); rd: registers it reads (None = unknown); the other fields depend on op."""
    __slots__ = ('addr', 'mn', 'ops', 'rel', 'op', 'wr', 'rd', 'dst', 'src', 'w', 'imm', 'sym', 'add', 'target', 'cond', 'base', 'idx', 'ext', 'sh', 'off',
                 'size', 'sext', 'pre', 'post', 'dst32')

    def __init__(self, addr, mn, ops):
        self.addr, self.mn, self.ops, self.rel = addr, mn, ops, None
        self.op, self.wr, self.rd = 'other', 'ALL', None
        self.dst = self.src = self.w = self.imm = self.sym = self.add = self.target = self.cond = self.base = self.idx = self.ext = None
        self.sh = self.off = self.size = self.sext = None
        self.pre = self.post = 0
        self.dst32 = False


class Prog:
    def __init__(self, isa='a64'):
        self.isa = ISAS[isa]
        self.funcs = {}       # (object, name) -> {'start': int, 'section': str, 'insns': [Insn]}
        self.syms = {}        # (object, name) -> {'size': int, 'section': str, 'value': int, 'global': bool, 'defined': bool}
        self.datarel = []     # (object, section, offset, type, symbol, addend) relocations of non-code sections
        self.objects = []

    def resolve(self, obj, name):
        """The canonical key of a symbol referenced from `obj`: its definition (a local of obj first, else the one global), else ('', name)."""
        if (obj, name) in self.syms and self.syms[(obj, name)]['defined']:
            return (obj, name)
        glob = sorted(k for k, s in self.syms.items() if k[1] == name and s['defined'] and s['global'])
        return glob[0] if len(glob) == 1 else ('', name)


def split_ops(s):
    out, cur, depth = [], '', 0
    for c in s:
        if c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
        if c == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def num(s):
    return int(s.strip().lstrip('#'), 0)


# ---------------------------------------------------------------------------------------------- a64 lowerer
def a_reg(tok):
    """Register number of a w/x register token, 'zr' for wzr/xzr, 'sp', None otherwise."""
    m = re.fullmatch(r'[wx](\d+)', tok.strip())
    if m:
        return int(m.group(1))
    return {'wzr': 'zr', 'xzr': 'zr', 'sp': 'sp', 'wsp': 'sp'}.get(tok.strip())


A_NOWRITE = {'str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh', 'stlr', 'stlrb', 'stlrh', 'stxr', 'stlxr', 'cmp', 'cmn', 'tst', 'ccmp', 'ccmn',
             'b', 'br', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz', 'nop', 'prfm', 'dmb', 'dsb', 'isb', 'bl', 'blr', 'svc', 'brk'}
A_ATOMIC = ('swp', 'cas', 'ldadd', 'ldclr', 'ldeor', 'ldset', 'ldsmax', 'ldsmin', 'ldumax', 'ldumin')
A_COND = {'lo': 'lo', 'cc': 'lo', 'hs': 'hs', 'cs': 'hs', 'ls': 'ls', 'hi': 'hi'}


def a_regs_in(ops):
    return {a_reg(t) for o in ops for t in re.findall(r'\b[wx]\d+\b|\bw?zr\b|\bsp\b', o) if a_reg(t) not in (None,)}


def lower_a64(i):
    ops, mn = split_ops(i.ops), i.mn
    i.rd = a_regs_in(ops)
    rel = i.rel
    tgt = re.search(r'(?:^|[\s,])([0-9a-f]+)\s+<', i.ops)
    if mn in ('bl', 'blr'):
        i.op, i.wr = 'call', set(range(19))
        i.sym = rel[1] if mn == 'bl' and rel else None
        return
    if mn == 'b' and rel:
        i.op, i.sym, i.wr = 'tail', rel[1], set()
        return
    if mn in ('b', 'br', 'ret'):
        i.op, i.wr = ('jmp' if mn == 'b' else 'ret'), set()
        i.target = int(tgt.group(1), 16) if mn == 'b' and tgt else None
        return
    if mn.startswith('b.') or mn in ('cbz', 'cbnz', 'tbz', 'tbnz'):
        i.op, i.wr = 'jcc', set()
        i.cond = A_COND.get(mn[2:]) if mn.startswith('b.') else 'other'
        i.target = int(tgt.group(1), 16) if tgt else None
        return
    if mn == 'cmp' and len(ops) == 2 and a_reg(ops[0]) is not None and ops[1].startswith('#'):
        i.op, i.wr, i.dst, i.imm, i.w = 'cmp', set(), a_reg(ops[0]), num(ops[1]), ops[0][0]
        return
    if (mn.startswith('st') or mn.startswith(A_ATOMIC)) and mn not in ('str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh') and any(o.startswith('[') for o in ops):
        m = next(o for o in ops if o.startswith('['))
        i.op, i.size, i.idx, i.off = 'store', 64, 'any', 0          # unknown form: it may write anything within 64 bytes of any address near the base
        i.base = a_reg(m.strip('[]!').split(',')[0])
        i.wr = {x for x in map(a_reg, [o for o in ops if not o.startswith('[')]) if isinstance(x, int)}
        i.rd = {x for x in map(a_reg, [o for o in ops if not o.startswith('[')]) if isinstance(x, int)}
        return
    if mn in A_NOWRITE - {'str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh'}:
        i.op, i.wr = 'other', set()
        return
    if mn in ('str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh') and any(o.startswith('[') for o in ops):
        lower_a64_mem(i, ops, store=True)
        return
    d = a_reg(ops[0]) if ops else None
    if mn in ('ldp', 'ldpsw') and len(ops) >= 3:
        lower_a64_mem(i, ops, store=False, pair=True)
        return
    i.wr = {d} if isinstance(d, int) else set()
    i.dst, i.dst32 = d, bool(ops) and ops[0][0] == 'w'
    if mn == 'mov' and len(ops) == 2:
        if ops[1].startswith('#'):
            i.op, i.imm = 'movi', num(ops[1])
        elif a_reg(ops[1]) == 'sp':
            i.op, i.imm = 'sp_addr', 0
        elif isinstance(a_reg(ops[1]), int):
            i.op, i.src, i.w = 'mov', a_reg(ops[1]), ops[1][0]
        return
    if mn == 'movz' and len(ops) == 2 and ops[1].startswith('#'):
        i.op, i.imm = 'movi', num(ops[1])
    elif mn == 'orr' and len(ops) == 3 and a_reg(ops[1]) == 'zr' and isinstance(a_reg(ops[2]), int):
        i.op, i.src, i.w = 'mov', a_reg(ops[2]), ops[2][0]
    elif mn in ('uxtw', 'sxtw') and len(ops) == 2 and isinstance(a_reg(ops[1]), int):
        i.op, i.src, i.w, i.sext = 'mov', a_reg(ops[1]), 'w', mn == 'sxtw'
    elif mn == 'and' and len(ops) == 3 and ops[2].startswith('#'):
        i.op, i.imm = 'andi', num(ops[2])
    elif mn == 'adrp' and rel and 'ADR_PREL_PG_HI21' in rel[0]:
        i.op, i.sym, i.add = 'page', rel[1], rel[2]
    elif mn == 'add' and len(ops) == 3 and rel and 'ADD_ABS_LO12_NC' in rel[0] and isinstance(a_reg(ops[1]), int):
        i.op, i.src, i.sym, i.add = 'addlo', a_reg(ops[1]), rel[1], rel[2]
    elif mn in ('add', 'sub') and len(ops) == 3 and ops[2].startswith('#') and not rel:
        c = num(ops[2]) * (1 if mn == 'add' else -1)
        if a_reg(ops[0]) == 'sp' and a_reg(ops[1]) == 'sp':
            i.op, i.wr, i.post = 'other', {'sp'}, c
        elif a_reg(ops[1]) == 'sp':
            i.op, i.imm = 'sp_addr', c
        elif isinstance(a_reg(ops[1]), int) and mn == 'add':
            i.op, i.src, i.imm = 'addimm', a_reg(ops[1]), c
    elif mn.startswith('ldr') and len(ops) >= 2 and ops[1].startswith('['):
        lower_a64_mem(i, ops, store=False)


def lower_a64_mem(i, ops, store, pair=False):
    """load/store with a `[base...]` operand: base, index/extend, displacement, size, writeback to sp."""
    m = next(o for o in ops if o.startswith('['))
    inner = [t.strip() for t in m.strip('[]!').split(',')]
    base = a_reg(inner[0])
    wb_pre = m.endswith('!')
    post = num(ops[ops.index(m) + 1]) if ops.index(m) + 1 < len(ops) and ops[ops.index(m) + 1].startswith('#') else 0
    sz = {'b': 1, 'h': 2}.get(i.mn[-1], None) if i.mn[:3] in ('ldr', 'str', 'stu') and i.mn[-1] in 'bh' else (8 if ops[0][0] == 'x' else 4 if ops[0][0] == 'w' else 16)
    i.op = 'store' if store else 'load'
    i.base, i.size = base, sz * (2 if pair else 1)
    i.rd = {x for x in map(a_reg, [o for o in ops if not o.startswith('[') and not o.startswith('#')]) if isinstance(x, int)} if store else set()
    if not store and not pair:
        i.dst, i.dst32 = a_reg(ops[0]), ops[0][0] == 'w'
    if len(inner) > 1 and not inner[1].startswith('#') and not (i.rel and 'LO12' in i.rel[0]):
        i.idx = a_reg(inner[1])
        ext = inner[2].split() if len(inner) > 2 else []
        i.ext = ext[0] if ext else None
        i.sh = int(ext[1][1:], 0) if len(ext) == 2 and ext[1].startswith('#') else 0
        i.w = inner[1][0]
        i.off = 0
    else:
        i.off = num(inner[1]) if len(inner) > 1 and inner[1].startswith('#') else 0
    if base == 'sp':
        i.pre = i.off if wb_pre else 0
        if wb_pre:
            i.off = 0
        i.post = post
    regs = set() if store else {x for x in map(a_reg, [o for o in ops if not o.startswith('[') and not o.startswith('#')][:2 if pair else 1]) if isinstance(x, int)}
    if wb_pre or post:
        regs.add(base)
    i.wr = regs


# ---------------------------------------------------------------------------------------------- x64 lowerer (Intel syntax)
X_NAMES = {}
for _n, _r in (('ax', 0), ('cx', 1), ('dx', 2), ('bx', 3), ('sp', 4), ('bp', 5), ('si', 6), ('di', 7)):
    X_NAMES['r' + _n], X_NAMES['e' + _n] = (_r, 'x'), (_r, 'w')
for _k in range(8, 16):
    X_NAMES['r%d' % _k], X_NAMES['r%dd' % _k] = (_k, 'x'), (_k, 'w')
X_COND = {'b': 'lo', 'c': 'lo', 'nae': 'lo', 'ae': 'hs', 'nb': 'hs', 'nc': 'hs', 'be': 'ls', 'na': 'ls', 'a': 'hi', 'nbe': 'hi'}
X_PLAIN = {'mov', 'movzx', 'movsx', 'movsxd', 'lea', 'add', 'sub', 'and', 'or', 'xor', 'shl', 'shr', 'sar', 'inc', 'dec', 'neg', 'not', 'imul', 'cmp', 'test',
           'push', 'pop', 'nop', 'endbr64', 'sal', 'bswap'}
X_PREFIX = ('bnd', 'notrack', 'rep', 'repz', 'repnz', 'lock', 'data16', 'cs', 'ds', 'es', 'ss', 'xacquire', 'xrelease')


def x_reg(tok):
    t = tok.strip()
    if t == 'rsp':
        return ('sp', 'x')
    if t == 'rip':
        return ('rip', 'x')
    return X_NAMES.get(t)


def x_mem(tok):
    """`[BASE+IDX*S+DISP]` (with an optional `DWORD PTR` size prefix) -> dict(size, base, idx, scale, disp) or None."""
    m = re.match(r'^(?:(BYTE|WORD|DWORD|QWORD|XMMWORD|TBYTE|OWORD|YMMWORD) PTR )?(?:[a-z]s:)?\[(.*)\]$', tok.strip())
    if not m:
        return None
    sz = {'BYTE': 1, 'WORD': 2, 'DWORD': 4, 'QWORD': 8, 'XMMWORD': 16, 'OWORD': 16, 'YMMWORD': 32, 'TBYTE': 10}.get(m.group(1))
    base = idx = None
    scale, disp = 1, 0
    for part in re.findall(r'[+-]?[^+-]+', m.group(2)):
        sign = -1 if part.startswith('-') else 1
        p = part.lstrip('+-').strip()
        if '*' in p:
            r, s = p.split('*')
            idx, scale = x_reg(r), int(s, 0)
        elif x_reg(p):
            if base is None:
                base = x_reg(p)
            else:
                idx = x_reg(p)
        else:
            try:
                disp += sign * int(p, 0)
            except ValueError:
                return None
    return dict(size=sz, base=base, idx=idx, scale=scale, disp=disp)


def lower_x64(i):
    mn, text = i.mn, i.ops
    while mn in X_PREFIX and text.split():
        parts = text.split(None, 1)
        mn, text = parts[0], parts[1] if len(parts) > 1 else ''
    i.mn = mn
    ops = split_ops(text)
    rel = i.rel
    tgt = re.match(r'^([0-9a-f]+)\s+<', text)
    if mn == 'call':
        i.op, i.wr, i.sym = 'call', set(X64.caller_saved), (rel[1] if rel else None)
        return
    if mn == 'jmp':
        if rel:
            i.op, i.sym, i.wr = 'tail', rel[1], set()
        elif tgt:
            i.op, i.wr, i.target = 'jmp', set(), int(tgt.group(1), 16)
        else:
            i.op, i.wr = 'ret', set()
        return
    if mn in ('ret', 'hlt', 'ud2'):
        i.op, i.wr = 'ret', set()
        return
    if mn.startswith('j') and len(mn) > 1:
        i.op, i.wr = 'jcc', set()
        i.cond = X_COND.get(mn[1:], 'other')
        i.target = int(tgt.group(1), 16) if tgt else None
        return
    if mn not in X_PLAIN or (mn == 'imul' and len(ops) == 1):
        lower_x_unknown(i, mn, ops)
        return
    mems = [x_mem(o) for o in ops]
    i.rd = {r[0] for o in ops for r in ([x_reg(o)] if x_reg(o) else []) if r}
    for m in mems:
        if m:
            i.rd |= {x[0] for x in (m['base'], m['idx']) if x}
    dst = x_reg(ops[0]) if ops else None
    if mn in ('cmp', 'test'):
        i.wr = set()
        if mn == 'cmp' and len(ops) == 2 and dst and ops[1].lstrip('-').startswith(('0x', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0')) and not x_mem(ops[1]) \
                and isinstance(dst[0], int):
            try:
                i.op, i.dst, i.imm, i.w = 'cmp', dst[0], int(ops[1], 0), dst[1]
            except ValueError:
                i.op = 'other'
        else:
            i.op = 'other'
        return
    if mn == 'push':
        i.op, i.wr, i.post = 'other', {'sp'}, -8
        if mems and mems[0]:
            i.wr = {'sp'}
        return
    if mn == 'pop':
        i.op, i.wr, i.post = 'other', {'sp'} | ({dst[0]} if dst and dst[0] != 'sp' else set()), 8
        return
    if mn in ('nop', 'endbr64'):
        i.op, i.wr = 'other', set()
        return
    if mems and mems[0]:                                 # a memory destination: a store (or a read-modify-write)
        m = mems[0]
        i.op, i.wr, i.base, i.size = 'store', set(), m['base'][0] if m['base'] else None, m['size'] or 8
        i.off, i.idx = m['disp'], (m['idx'][0] if m['idx'] else None)
        i.rd = {r[0] for o in ops[1:] for r in ([x_reg(o)] if x_reg(o) else []) if r and isinstance(r[0], int)}
        return
    if dst is None or not isinstance(dst[0], int) and dst[0] != 'sp':
        return
    if dst[0] == 'sp':
        if mn in ('sub', 'add') and len(ops) == 2 and not x_mem(ops[1]) and not x_reg(ops[1]):
            try:
                i.op, i.wr, i.post = 'other', {'sp'}, num(ops[1]) * (-1 if mn == 'sub' else 1)
            except ValueError:
                pass
        return
    i.wr, i.dst, i.dst32 = {dst[0]}, dst[0], dst[1] == 'w'
    src = ops[1] if len(ops) > 1 else None
    sm = x_mem(src) if src else None
    if mn == 'mov' and src:
        sr = x_reg(src)
        if sr and sr[0] == 'sp':
            i.op, i.imm = 'sp_addr', 0
        elif sr and isinstance(sr[0], int) and sr[1] == dst[1]:
            i.op, i.src, i.w = 'mov', sr[0], sr[1]
        elif re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', src):
            i.op, i.imm = 'movi', int(src, 0)
        elif sm:
            lower_x_load(i, sm, rel)
        return
    if mn == 'lea' and sm:
        if sm['base'] and sm['base'][0] == 'rip' and rel and 'GOT' not in rel[0]:
            i.op, i.sym, i.add = 'addr', rel[1], rel[2] + 4
        elif sm['base'] and sm['base'][0] == 'sp' and not sm['idx']:
            i.op, i.imm = 'sp_addr', sm['disp']
        elif sm['base'] and isinstance(sm['base'][0], int) and not sm['idx'] and not rel:
            i.op, i.src, i.imm = 'addimm', sm['base'][0], sm['disp']
        return
    if mn in ('add', 'sub') and len(ops) == 2 and re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', ops[1]) and x_reg(ops[0]) and x_reg(ops[0])[1] == 'x':
        i.op, i.src, i.imm = 'addimm', dst[0], int(ops[1], 0) * (1 if mn == 'add' else -1)
        return
    if mn == 'and' and len(ops) == 2 and re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', ops[1]):
        i.op, i.imm = 'andi', int(ops[1], 0)
        return
    if mn in ('movsxd',) and src and x_reg(src) and isinstance(x_reg(src)[0], int):
        i.op, i.src, i.w, i.sext = 'mov', x_reg(src)[0], 'w', True
        return
    if mn == 'mov' and sm:
        lower_x_load(i, sm, rel)


X_SIMPLE_DEST = re.compile(r'^(?:[xy]mm\d+|st(?:\(\d\))?|mm\d)$')


def lower_x_unknown(i, mn, ops):
    """An instruction of unknown semantics: it reads every register it names; a first operand in memory is a store of 64 bytes at an unknown index, an
    xmm/x87 destination writes no general register, anything else (a general destination, an implicit operand) writes ALL."""
    i.rd = {r[0] for t in re.findall(r'[a-z][a-z0-9]*', ' '.join(ops)) for r in [x_reg(t)] if r}
    m0 = x_mem(ops[0]) if ops else None
    if m0:
        i.op, i.size, i.idx, i.off, i.base, i.wr = 'store', 64, 'any', 0, (m0['base'][0] if m0['base'] else None), set()
    elif ops and X_SIMPLE_DEST.match(ops[0]) and not mn.startswith(('rep', 'movs', 'stos', 'lods', 'scas', 'cmps')):
        i.wr = set()
    else:
        i.wr, i.rd = 'ALL', None


def lower_x_load(i, m, rel):
    i.op, i.size, i.rd = 'load', m['size'] or 8, set()
    b = m['base']
    if b and b[0] == 'rip' and rel:
        if 'GOTPCREL' in rel[0]:
            i.op, i.sym, i.add = 'addr', rel[1], 0          # the GOT slot holds the address of the symbol
            return
        i.base, i.sym, i.add, i.off = 'rip', rel[1], rel[2] + 4, 0
        return
    i.base = b[0] if b else None
    i.off = m['disp']
    if m['idx']:
        i.idx, i.sh, i.w = m['idx'][0], {1: 0, 2: 1, 4: 2, 8: 3}.get(m['scale'], 0), m['idx'][1]
        i.ext = 'lsl'


# ---------------------------------------------------------------------------------------------- objdump text
def parse_objdump(text, isa='a64'):
    """The Prog of the concatenated `objdump -dr -t --no-show-raw-insn` of objects/archives (one `file format` header per object)."""
    p = Prog(isa)
    lower = lower_a64 if isa == 'a64' else lower_x64
    obj, fn, section, datasec, last = None, None, None, None, None
    pending = []

    def flush():
        for i in pending:
            lower(i)
        pending.clear()
    for ln in text.split('\n') + ['']:
        m = re.match(r'^(.*?):\s+file format', ln)
        if m:
            flush()
            obj = m.group(1)
            p.objects.append(obj)
            fn = section = datasec = last = None
            continue
        if obj is None:
            continue
        m = re.match(r'^Disassembly of section (\S+):', ln)
        if m:
            section, datasec, fn = m.group(1), None, None
            continue
        m = re.match(r'^RELOCATION RECORDS FOR \[(\S+)\]:', ln)
        if m:
            datasec, fn = m.group(1), None
            continue
        m = re.match(r'^([0-9a-f]+) (.{7}) (\S+)\t([0-9a-f]+) (.*)$', ln)
        if m and datasec is None and not re.match(r'^[0-9a-f]+ <', ln):
            name = m.group(5).split()[-1]
            flags, sec = m.group(2), m.group(3)
            p.syms[(obj, name)] = dict(size=int(m.group(4), 16), section=sec, value=int(m.group(1), 16), glob=('g' in flags or 'w' in flags),
                                       defined=sec != '*UND*')
            p.syms[(obj, name)]['global'] = p.syms[(obj, name)].pop('glob')
            continue
        m = re.match(r'^([0-9a-f]+) <(\S+)>:$', ln)
        if m:
            flush()
            fn = (obj, m.group(2))
            p.funcs[fn] = dict(start=int(m.group(1), 16), section=section, insns=[])
            last = None
            continue
        m = re.match(r'^\s+([0-9a-f]+):\s+(R_\w+)\s+(\S+)', ln)
        if m and fn is not None and last is not None:
            sym, add = reloc_target(m.group(3))
            last.rel = (m.group(2), sym, add)
            continue
        m = re.match(r'^([0-9a-f]+) (R_\w+)\s+(\S+)', ln)
        if m and datasec is not None:
            sym, add = reloc_target(m.group(3))
            p.datarel.append((obj, datasec, int(m.group(1), 16), m.group(2), sym, add))
            continue
        m = re.match(r'^\s+([0-9a-f]+):\s+([a-z][\w.]*)\s*(.*)$', ln)
        if m and fn is not None:
            ops = re.split(r'\s+#\s|\s//', m.group(3))[0].strip()
            last = Insn(int(m.group(1), 16), m.group(2), ops)
            p.funcs[fn]['insns'].append(last)
            pending.append(last)
    flush()
    return p


def reloc_target(s):
    m = re.match(r'^(.*?)([+-]0x[0-9a-f]+)?$', s)
    sym = m.group(1)
    add = int(m.group(2).replace('+', ''), 16) if m.group(2) else 0
    return sym, add


# ---------------------------------------------------------------------------------------------- functions: CFG and stack offsets
class Fn:
    """One function: control-flow predecessors/successors of every instruction and the stack-pointer offset before each (relative to the entry)."""

    def __init__(self, key, rec):
        self.key, self.insns = key, rec['insns']
        self.at = {i.addr: n for n, i in enumerate(self.insns)}
        self.preds = [set() for _ in self.insns]
        self.succs = [set() for _ in self.insns]
        for n, i in enumerate(self.insns):
            falls = i.op not in ('jmp', 'ret', 'tail')
            if n + 1 < len(self.insns) and falls:
                self.preds[n + 1].add(n)
                self.succs[n].add(n + 1)
            if i.op in ('jmp', 'jcc') and i.rel is None and i.target in self.at:
                self.preds[self.at[i.target]].add(n)
                self.succs[n].add(self.at[i.target])
        self.delta = self.compute_sp()

    def compute_sp(self):
        """[sp offset before instruction n] or None when two paths disagree or a stack-pointer write is not understood."""
        d = [None] * len(self.insns)
        if not self.insns:
            return d
        d[0] = 0
        work = [0]
        while work:
            n = work.pop()
            i = self.insns[n]
            if (i.wr == 'ALL' and (re.search(r'\b(?:r|e)?sp\b', i.ops) or i.mn in SP_IMPLICIT)) or (i.wr != 'ALL' and 'sp' in i.wr and not (i.pre or i.post)):
                return [None] * len(self.insns)
            out = d[n] + i.pre + i.post
            for s in self.succs[n]:
                if d[s] is None:
                    d[s] = out
                    work.append(s)
                elif d[s] != out:
                    return [None] * len(self.insns)
        return d


SP_IMPLICIT = ('leave', 'enter', 'pushf', 'popf', 'pushfq', 'popfq', 'pusha', 'popa', 'ret')


def reaches_sp(i, r):
    return i.wr == 'ALL' or r in i.wr


# ---------------------------------------------------------------------------------------------- the prover
class Prover:
    def __init__(self, prog, producers):
        self.p, self.isa = prog, prog.isa
        self.fns = {k: Fn(k, rec) for k, rec in prog.funcs.items()}
        self.prod_f = {s['symbol']: s for s in producers if s['kind'] == 'function'}
        self.prod_d = {s['symbol'] for s in producers if s['kind'] == 'data'}
        self.reasons, self.callers, self.values = set(), set(), set()
        self.params_done = set()
        self.cuts = 0

    # ------------------------------------------------------------ values of a register before instruction n of function f
    def value(self, f, n, r, depth=0):
        """-> (set of values, set of fail reasons); values: ('prod', callee) ('page', sym, add) ('addr', sym, off) ('data', sym, off) ('param', j) ('slot', S)."""
        vals, fails, seen, work = set(), set(), set(), [(n, r)]
        while work:
            i, rr = work.pop()
            if (i, rr) in seen:
                continue
            seen.add((i, rr))
            if i == 0:
                if rr in self.isa.args:
                    vals.add(('param', self.isa.args.index(rr)))
                else:
                    fails.add('uninitialized')
            for q in f.preds[i]:
                ins = f.insns[q]
                if ins.op == 'call':
                    if rr not in self.isa.caller_saved:
                        work.append((q, rr))              # callee-saved registers survive the call
                    elif ins.sym and rr == self.isa.ret:
                        vals.add(('prod', self.p.resolve(f.key[0], ins.sym)[1]))
                    else:
                        fails.add('clobbered' if rr != self.isa.ret else 'call-result')
                    continue
                if not reaches_sp(ins, rr):
                    work.append((q, rr))
                    continue
                self.defn(f, q, ins, rr, vals, fails, work, depth)
        return vals, fails

    def defn(self, f, q, ins, rr, vals, fails, work, depth):
        op = ins.op
        if op == 'mov':
            work.append((q, ins.src))
        elif op == 'page':
            vals.add(('page', ins.sym, ins.add))
        elif op == 'addr':
            vals.add(('addr', ins.sym, ins.add))
        elif op == 'addlo':
            base, bf = self.value(f, q, ins.src, depth + 1)
            fails |= bf
            for v in base:
                if v[0] == 'page' and v[1] == ins.sym:
                    vals.add(('addr', ins.sym, ins.add))
                else:
                    fails.add('computed')
        elif op == 'addimm':
            base, bf = self.value(f, q, ins.src, depth + 1)
            fails |= bf
            for v in base:
                if v[0] == 'addr':
                    vals.add(('addr', v[1], v[2] + ins.imm))
                else:
                    fails.add('computed')
        elif op == 'sp_addr':
            if f.delta[q] is None:
                fails.add('sp-unstable')
            else:
                vals.add(('slot', f.delta[q] + ins.pre + ins.imm))
        elif op == 'load':
            self.load(f, q, ins, vals, fails, depth)
        elif op in ('movi',):
            fails.add('constant')
        else:
            fails.add('computed')

    def load(self, f, q, ins, vals, fails, depth):
        if ins.base == 'sp':
            self.slot_load(f, q, ins, vals, fails)
            return
        if ins.base == 'rip':                                          # x64 rip-relative load from a symbol at a constant offset
            vals.add(('data', ins.sym, ins.add))
            return
        if ins.base is None:
            fails.add('load')
            return
        bv, bf = self.value(f, q, ins.base, depth + 1)
        fails |= bf
        if ins.idx is not None:
            self.indexed(f, q, ins, bv, vals, fails)
            return
        for v in bv:
            if v[0] == 'addr':
                vals.add(('data', v[1], v[2] + ins.off))
            elif v[0] == 'page' and ins.rel and 'LDST' in ins.rel[0] and 'LO12' in ins.rel[0] and v[1] == ins.rel[1]:
                vals.add(('data', v[1], ins.rel[2]))
            elif v[0] == 'slot':
                self.slot_load_at(f, q, ins, v[1] + ins.off, vals, fails)
            else:
                fails.add('load')

    # ------------------------------------------------------------ a variable index into a producer table
    def indexed(self, f, q, ins, bv, vals, fails):
        """load from base + index*elt: accepted iff the index is proven in [0, count), count = (st_size(sym) - off) / elt."""
        where = '%s+0x%x' % (f.key[1], ins.addr)
        idx = ins.idx
        need = 'w' if ins.w == 'w' else ('w' if self.zext32(f, q, idx) else 'x')
        okform = isinstance(idx, int) and (ins.ext in (None, 'lsl', 'uxtw', 'sxtw'))
        for v in bv:
            if v[0] != 'addr' or not okform:
                fails.add('load')
                continue
            sym = self.sym(f.key[0], v[1])
            u = self.bound(f, q, idx, need, set(), {}) if sym is not None else None
            if u is not None and ins.ext == 'sxtw' and u > 0x80000000:
                u = None
            elt = 1 << (ins.sh or 0)
            if u is None or u < 1 or v[2] + ins.off + (u - 1) * elt + elt > sym['size']:
                fails.add('index-unbounded:%s' % where)
            else:
                vals.add(('data', v[1], v[2] + ins.off + (u - 1) * elt))

    def zext32(self, f, n, r):
        """True iff every definition of register r reaching instruction n writes its 32-bit half (the upper half is zero)."""
        seen, work = set(), [(n, r)]
        while work:
            i, rr = work.pop()
            if (i, rr) in seen:
                continue
            seen.add((i, rr))
            if i == 0:
                return False
            for q in f.preds[i]:
                ins = f.insns[q]
                if ins.op == 'call' and rr in self.isa.caller_saved:
                    return False
                if reaches_sp(ins, rr) and ins.op != 'call':
                    if not ins.dst32:
                        return False
                    continue
                work.append((q, rr))
        return True

    def bound(self, f, n, r, need, stack, memo):
        """Exclusive unsigned upper bound U of register r (read as `need`, 'w' or 'x') before instruction n on EVERY path to it, or None (not provable)."""
        key = (n, r, need)
        if key in memo:
            return memo[key]
        if key in stack:
            self.cuts += 1
            return 0                       # a loop back edge adds no new information (least fixed point; the entry and exit edges carry the bounds)
        if n == 0:
            return None
        stack.add(key)
        best, cuts = 0, self.cuts
        for q in sorted(f.preds[n]):
            u = self.edge_bound(f, q, n, r, need)
            if u is None:
                u = self.through(f, q, r, need, stack, memo)
            if u is None:
                best = None
                break
            best = max(best, u)
        stack.discard(key)
        if self.cuts == cuts:              # a result that used the loop assumption is not final: not memoised
            memo[key] = best
        return best

    def edge_bound(self, f, q, n, r, need):
        """The bound that the branch q gives on the edge q -> n: q is a conditional branch right after `cmp r, #C` (the only way into q)."""
        ins = f.insns[q]
        if ins.op != 'jcc' or ins.cond not in ('lo', 'hs', 'ls', 'hi') or q == 0 or f.preds[q] != {q - 1} or f.insns[q - 1].op != 'cmp' or ins.target not in f.at:
            return None
        c = f.insns[q - 1]
        taken, fall = f.at[ins.target], q + 1
        if taken == fall or n not in (taken, fall) or c.dst != r or not (c.w == need or c.w == 'x' and need == 'w') or c.imm < 0:
            return None
        edge = 'taken' if n == taken else 'fall'
        if (ins.cond == 'lo' and edge == 'taken') or (ins.cond == 'hs' and edge == 'fall'):
            return c.imm
        if (ins.cond == 'ls' and edge == 'taken') or (ins.cond == 'hi' and edge == 'fall'):
            return c.imm + 1
        return None

    def through(self, f, q, r, need, stack, memo):
        """The bound of r after instruction q: unchanged when q does not write r, a copy/extend/mask/constant otherwise."""
        ins = f.insns[q]
        if ins.op == 'call':
            return self.bound(f, q, r, need, stack, memo) if r not in self.isa.caller_saved else None
        if not reaches_sp(ins, r):
            return self.bound(f, q, r, need, stack, memo)
        if ins.op == 'movi':
            return ins.imm + 1 if ins.imm >= 0 else None
        if ins.op == 'mov':
            if ins.sext:
                u = self.bound(f, q, ins.src, 'w', stack, memo)
                return u if u is not None and u <= 0x80000000 else None
            return self.bound(f, q, ins.src, 'w' if ins.w == 'w' else need, stack, memo)
        if ins.op == 'andi' and ins.imm >= 0:
            return ins.imm + 1
        return None

    # ------------------------------------------------------------ the out-parameter shape
    def slot_load(self, f, q, ins, vals, fails):
        if f.delta[q] is None:
            fails.add('sp-unstable')
            return
        self.slot_load_at(f, q, ins, f.delta[q] + ins.pre + ins.off, vals, fails)

    def slot_load_at(self, f, q, ins, off, vals, fails):
        size = ins.size or 4
        local = set()
        if ins.idx is not None:
            local.add('stack-load')
        elif any(d is None for d in f.delta):
            local.add('sp-unstable')
        else:
            callees = set()
            if self.out_paths(f, q, off, size, callees, local) and self.escape_ok(f, off, local):
                for c in sorted(callees):
                    vals.add(('prod', self.p.resolve(f.key[0], c)[1]))
                return
            local = local or {'stack-load'}
        fails |= local

    def out_paths(self, f, n, off, size, callees, fails):
        """Every path from the entry to instruction n passes a producer call that wrote [off, off+size) and no other store touches it afterwards."""
        seen, work, ok = set(), [n], True
        while work:
            i = work.pop()
            if i in seen:
                continue
            seen.add(i)
            if i == 0:
                fails.add('out-uninit')
                ok = False
            for q in f.preds[i]:
                ins = f.insns[q]
                if ins.op == 'call' and ins.sym:
                    callee = self.p.resolve(f.key[0], ins.sym)[1]
                    if callee in self.prod_f:
                        hit = False
                        for o in self.prod_f[callee].get('out', []):
                            if not (isinstance(o.get('arg'), int) and 0 <= o['arg'] < len(self.isa.args)):
                                continue
                            vs, _ = self.value(f, q, self.isa.args[o['arg']])
                            slots = {v[1] for v in vs if v[0] == 'slot'}
                            if len(vs) == 1 and len(slots) == 1:
                                s0 = next(iter(slots))
                                if s0 <= off and off + size <= s0 + o['size']:
                                    hit = True
                                elif self.overlap(s0, o['size'], off, size):
                                    fails.add('out-type')
                                    ok = False
                        if hit:
                            callees.add(ins.sym)
                            continue
                if ins.op == 'store' and not self.store_clear(f, q, ins, off, size):
                    fails.add('out-store')
                    ok = False
                work.append(q)
        return ok

    def store_clear(self, f, q, ins, off, size):
        """True iff the store at q cannot write [off, off+size): a known slot elsewhere (through sp, or a register holding a frame address)."""
        if ins.idx is not None:
            return False
        if ins.base == 'sp':
            return f.delta[q] is not None and not self.overlap(f.delta[q] + ins.pre + ins.off, ins.size or 8, off, size)
        if ins.base is None:
            return False
        bv, bf = self.value(f, q, ins.base)
        if bf or any(v[0] == 'slot' and self.overlap(v[1] + ins.off, ins.size or 8, off, size) for v in bv):
            return False
        return True             # a register that holds no frame address (the escape rule below keeps frame addresses out of other registers)

    @staticmethod
    def overlap(a, an, b, bn):
        return a < b + bn and b < a + an

    def escape_ok(self, f, off, fails):
        """The address of every frame object at or below `off` flows only into the out argument of a producer call (a copy is followed), and no instruction
        of an unknown form reads the stack pointer."""
        for q, ins in enumerate(f.insns):
            if ins.op == 'other' and not (ins.pre or ins.post) and re.search(r'\b(?:r|e)?sp\b', ins.ops) and ins.mn != 'nop':
                fails.add('out-escape')
                return False
            if ins.op != 'sp_addr':
                continue
            if f.delta[q] is None:
                fails.add('sp-unstable')
                return False
            if f.delta[q] + ins.pre + ins.imm > off:
                continue
            if not self.address_flows_ok(f, q, ins.dst):
                fails.add('out-escape')
                return False
        return True

    def address_flows_ok(self, f, q, reg0):
        seen, work = set(), [(s, reg0) for s in f.succs[q]]
        while work:
            n, r = work.pop()
            if (n, r) in seen:
                continue
            seen.add((n, r))
            ins = f.insns[n]
            if ins.op == 'call':
                callee = self.p.resolve(f.key[0], ins.sym)[1] if ins.sym else None
                spec = self.prod_f.get(callee)
                if r in self.isa.args and not (spec and any(o.get('arg') == self.isa.args.index(r) for o in spec.get('out', []))):
                    return False
                if r in self.isa.caller_saved:
                    continue
                work += [(s, r) for s in f.succs[n]]
                continue
            if ins.op == 'tail':
                if r in self.isa.args:
                    return False
                continue
            if ins.op == 'ret':
                continue
            if ins.wr == 'ALL':
                return False
            if ins.op in ('load', 'store'):
                if ins.idx == r or (ins.rd is None or r in ins.rd) or (ins.base == r and ins.idx is not None):
                    return False
            elif ins.rd is None or r in ins.rd:
                if ins.op == 'mov' and ins.src == r and not ins.sext:
                    work += [(s, ins.dst) for s in f.succs[n]]
                else:
                    return False
            if r in ins.wr:
                continue
            work += [(s, r) for s in f.succs[n]]
        return True

    # ------------------------------------------------------------ the proof
    def judge(self, key, vals, where):
        for v in sorted(vals, key=repr):
            if v[0] == 'prod':
                if v[1] in self.prod_f:
                    self.values.add('prod:%s' % v[1])
                else:
                    self.reasons.add(('not-producer', where, v[1]))
            elif v[0] in ('addr', 'data'):
                s = self.sym(key[0], v[1])
                if v[1] in self.prod_d and s is not None and 0 <= v[2] < s['size']:
                    self.values.add('%s:%s' % (v[0], v[1]))
                else:
                    self.reasons.add(('not-producer', where, '%s+%d' % (v[1], v[2])))
            elif v[0] == 'page':
                self.reasons.add(('computed', where, 'page of %s' % v[1]))
            elif v[0] == 'slot':
                self.reasons.add(('computed', where, 'stack address'))
            elif v[0] == 'param':
                self.param(key, v[1], where)

    def sym(self, obj, name):
        return self.p.syms.get(self.p.resolve(obj, name))

    def callsites(self, target):
        """[(function key, index)] of every call/jump relocation to the canonical symbol `target`."""
        out = []
        for k, f in sorted(self.fns.items()):
            for n, i in enumerate(f.insns):
                if i.op in ('call', 'tail') and i.sym and self.p.resolve(k[0], i.sym) == target:
                    out.append((k, n))
        return out

    def address_taken(self, target):
        """The relocations that take the address of target outside a call: [(where, inside a producer data symbol?)]."""
        out = []
        for k, f in sorted(self.p.funcs.items()):
            if f['section'].startswith(IGNORED_SECTIONS):
                continue
            for i in f['insns']:
                if i.rel and i.op not in ('call', 'tail') and self.p.resolve(k[0], i.rel[1]) == target:
                    out.append(('%s:%s+0x%x' % (k[0], k[1], i.addr), False))
        for obj, sec, off, typ, sym, add in self.p.datarel:
            if sec.startswith(IGNORED_SECTIONS) or self.p.resolve(obj, sym) != target:
                continue
            inside = any(s['section'] == sec and s['value'] <= off < s['value'] + s['size'] and k[1] in self.prod_d
                         for k, s in self.p.syms.items() if k[0] == obj and s['defined'])
            out.append(('%s:%s+0x%x' % (obj, sec, off), inside))
        return out

    def param(self, key, j, where):
        if (key, j) in self.params_done:
            return
        self.params_done.add((key, j))
        for w, inside in self.address_taken(key):
            if not inside:
                self.reasons.add(('address-taken', w, key[1]))
        sites = self.callsites(key)
        if not sites and not self.address_taken(key):
            self.reasons.add(('no-caller', where, key[1]))
        for ck, n in sites:
            at = '%s:%s+0x%x' % (ck[0], ck[1], self.fns[ck].insns[n].addr)
            self.callers.add(at)
            vals, fails = self.value(self.fns[ck], n, self.isa.args[j])
            for r in sorted(fails):
                self.reasons.add((r, at, key[1]))
            self.judge(ck, vals, '%s:%s' % ck)

    def prove(self, site, k):
        target = self.p.resolve('', site)
        sites = self.callsites(target)
        if not sites:
            self.reasons.add(('no-site', site, '-'))
        for w, inside in self.address_taken(target):
            if not inside:
                self.reasons.add(('address-taken', w, site))
        for ck, n in sites:
            at = '%s:%s+0x%x' % (ck[0], ck[1], self.fns[ck].insns[n].addr)
            self.callers.add(at)
            vals, fails = self.value(self.fns[ck], n, self.isa.args[k])
            for r in sorted(fails):
                self.reasons.add((r, at, site))
            self.judge(ck, vals, at)
        return sorted(self.reasons), sorted(self.callers), sorted(self.values)


def prove(prog, site, k, producers):
    return Prover(prog, producers).prove(site, k)
