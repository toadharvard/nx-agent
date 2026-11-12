#!/usr/bin/env python3
"""Jump-table edges by ONE rule: a forward interval analysis over decoded code (modlift.py and universe.py supply the same accessors: the decoded instructions and their predecessors).
A site is `jmp [T + r*4]` (T the displacement, a relocated dword; no base register, no segment). Its edges are the words T + 4*v for every v the interval of r at the site contains.
Domain: per 32-bit register a wrapped interval {lo + k mod 2^32 : 0 <= k <= len} (top = every value); the flags of the last flag-setting `cmp r, imm` / `sub r, imm`.
Transfer (exactly these; every other write of a register makes it top, every other flag-writing instruction forgets the flags; a call makes every register top):
  mov r, imm|r2; xor/sub r, r; add/sub r, imm; add r, r2 (no overflow); and r, imm|r2 (<= the smaller bound); shr r, imm; neg r; not r; lea; movzx r, byte|word
  (a byte table `movzx r, byte [D + q]` reads D[lo(q)..hi(q)] from the image: the image is mapped read-only at its own addresses, README "Calls and code pointers").
Branches refine the interval of the register the reached flags definition compared: after `cmp r, imm` or `sub r, imm`, jb/jae/jbe/ja/je/jne give r < imm, r >= imm, r <= imm, r > imm,
r == imm on the edge; for `sub` the PRE value is refined and the post value recomputed (so `sub ecx, 4; jb` gives ecx in [-4, -1] on the taken edge).
Merge: the join (smallest covering arc) over ALL predecessors, where predecessors are the decoded edges of the closure (fall-through, jumps, branches and the table edges resolved so far).
A seeded instruction (call target, relocated code address, PE entry or export) is entered from outside the decoded edges: its state is top. Widening: a register whose interval changed at a
node more than WIDEN times becomes top there (loop heads), so the analysis terminates.
The table is the words T + 4*v, v over the interval: every word must be a relocated dword whose value is the address of an executable instruction; the extent may lie below T. The number of words
is at most the image's size / 4 (more cannot be relocated code addresses). Anything else is RED with the reason.
Basis: closure completeness (the preds are every edge of the closure, seeds are top: no assumption beyond the manifest of ASM-202); intervals and flags are an over-approximation."""
from capstone import x86 as X

N = 1 << 32; M32 = N - 1; TOP = (0, M32); WIDEN = 4
FULL = {f: f for f in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp')}
for _f, _s in {'eax': 'ax al ah', 'ebx': 'bx bl bh', 'ecx': 'cx cl ch', 'edx': 'dx dl dh', 'esi': 'si', 'edi': 'di', 'ebp': 'bp', 'esp': 'sp'}.items():
    for _x in _s.split(): FULL[_x] = _f
KEEP = {'mov', 'movzx', 'movsx', 'lea', 'push', 'pop', 'nop', 'xchg', 'jmp', 'cdq', 'cwde', 'bswap', 'movs', 'stos', 'lods', 'leave', 'pushal', 'popal', 'pushfd', 'fld', 'fstp', 'fild'}


def span(a):
    lo, ln = a; return (lo, ln)


def seg(a):
    """the non-wrapped segments [x, y] of an interval"""
    lo, ln = a; hi = lo + ln
    return [(lo, hi)] if hi < N else [(lo, M32), (0, hi - N)]


def join(a, b):
    """smallest arc covering both"""
    if a == b: return a
    best = None
    for p, q in ((a, b), (b, a)):
        ln = max(p[1], ((q[0] - p[0]) % N) + q[1])
        if ln >= M32: ln = M32
        if best is None or ln < best[1]: best = (p[0], ln)
    return TOP if best[1] >= M32 else best


def meet(a, lo, hi):
    """a intersected with the non-wrapped range [lo, hi]; None if empty (the hull when two pieces remain)"""
    out = None
    for x, y in seg(a):
        x = max(x, lo); y = min(y, hi)
        if x <= y: out = (x, y - x) if out is None else join(out, (x, y - x))
    return out


def add(a, c): return ((a[0] + c) % N, a[1])


class St:
    """r: {loc: interval} (absent = top), loc = a register name or a frame cell ('c', base, disp); f: None | (kind, loc, pre interval, imm);
    al: {loc: class id} (locations that hold the same value); nz: {loc: set of masks m with loc & m != 0}; pk: tuple of pushed intervals (top last), espd: esp offset since the seed (None = unknown)."""
    __slots__ = ('r', 'f', 'al', 'nz', 'pk', 'espd')

    def __init__(s, r, f, al=None, nz=None, pk=(), espd=0): s.r = r; s.f = f; s.al = al or {}; s.nz = nz or {}; s.pk = pk; s.espd = espd
    def cp(s): return St(dict(s.r), s.f, dict(s.al), {k: set(v) for k, v in s.nz.items()}, s.pk, s.espd)
    def __eq__(s, o): return s.r == o.r and s.f == o.f and s.al == o.al and s.nz == o.nz and s.pk == o.pk and s.espd == o.espd
    def g(s, loc): return s.r.get(loc, TOP)


def setr(st, loc, v):
    """loc := v (a fresh value: its aliases, facts and flags on it are dropped)"""
    st.al.pop(loc, None); st.nz.pop(loc, None)
    if st.f is not None and st.f[1] == loc: st.f = None
    if v is None or v == TOP: st.r.pop(loc, None)
    else: st.r[loc] = v


def copy(st, dst, src):
    """dst := src (same value: aliased, facts copied)"""
    v = st.g(src); cid = st.al.get(src)
    if cid is None: cid = st.al[src] = id(object())
    f = st.f; setr(st, dst, v); st.f = f if f is None or f[1] != dst else None
    st.al[dst] = cid
    if src in st.nz: st.nz[dst] = set(st.nz[src])


def kill_cells(st, lo=None, hi=None):
    for k in [k for k in list(st.r) + list(st.al) if isinstance(k, tuple)]:
        if lo is None or lo <= k[2] <= hi: st.r.pop(k, None); st.al.pop(k, None); st.nz.pop(k, None)
    if st.f is not None and isinstance(st.f[1], tuple): st.f = None


def joinst(p, q):
    r = {}
    for k in set(p.r) | set(q.r):
        v = join(p.g(k), q.g(k))
        if v != TOP: r[k] = v
    cls = {}; al = {}
    for k in set(p.al) & set(q.al): cls.setdefault((p.al[k], q.al[k]), []).append(k)
    for ks in cls.values():
        if len(ks) > 1:
            c = id(object())
            for k in ks: al[k] = c
    nz = {}
    for k in set(p.nz) & set(q.nz):
        m = p.nz[k] & q.nz[k]
        if m: nz[k] = m
    pk = p.pk if p.pk == q.pk else (tuple(join(a, b) for a, b in zip(p.pk, q.pk)) if len(p.pk) == len(q.pk) else ())
    e = p.espd if p.espd == q.espd else None
    o = St(r, p.f if p.f == q.f else None, al, nz, pk, e)
    if e is None: kill_cells(o, None, None)
    return o


def cell(ins, op, st, cells):
    """the frame cell an operand names: ('c', 'ebp'|'esp', disp) for a plain [ebp+k] / [esp+k] dword, else None"""
    if not cells or op.type != X.X86_OP_MEM: return None
    m = op.mem
    if m.segment or m.index or op.size != 4: return None
    b = FULL.get(ins.reg_name(m.base)) if m.base else None
    if b == 'ebp': return ('c', 'ebp', m.disp)
    if b == 'esp' and st.espd is not None: return ('c', 'esp', m.disp + st.espd)
    return None


def val(st, ins, op, cells):
    """interval of a source operand or None"""
    if op.type == X.X86_OP_IMM: return (op.imm & M32, 0)
    if op.type == X.X86_OP_REG and op.size == 4: return st.g(FULL.get(ins.reg_name(op.reg)))
    c = cell(ins, op, st, cells)
    if c is not None: return st.g(c)
    return TOP


def locof(st, ins, op, cells):
    if op.type == X.X86_OP_REG and op.size == 4: return FULL.get(ins.reg_name(op.reg))
    return cell(ins, op, st, cells)


def forms_frame(ins):
    """True if the instruction makes a frame address (esp/ebp as a value) available other than by the frame instructions: lea of an esp/ebp based operand, a copy of esp/ebp into anything but the prologue/epilogue forms,
    push esp, push ebp not followed by mov ebp, esp (checked by the caller), arithmetic whose source is esp/ebp."""
    mn = ins.mnemonic.split()[-1]; ops = ins.operands
    if mn == 'lea': return any(ops[1].mem.base and FULL.get(ins.reg_name(ops[1].mem.base)) in ('esp', 'ebp') for _ in (0,))
    if mn in ('mov', 'xchg', 'add', 'sub', 'and', 'or', 'xor', 'adc', 'sbb', 'cmp', 'test') and len(ops) == 2:
        s = ops[1]
        if s.type == X.X86_OP_REG and FULL.get(ins.reg_name(s.reg)) in ('esp', 'ebp'):
            d = ops[0]
            if mn == 'mov' and d.type == X.X86_OP_REG and FULL.get(ins.reg_name(d.reg)) in ('esp', 'ebp'): return False
            if mn in ('cmp', 'test'): return False
            return True
        if mn == 'xchg' and ops[0].type == X.X86_OP_REG and FULL.get(ins.reg_name(ops[0].reg)) in ('esp', 'ebp'): return True
    if mn == 'push' and ops[0].type == X.X86_OP_REG and FULL.get(ins.reg_name(ops[0].reg)) == 'esp': return True
    return False


def step(ins, st, u8, cells=True):
    """transfer function (st modified in place)"""
    mn = ins.mnemonic.split()[-1]; ops = ins.operands; n = len(ops); prev = st.f
    if mn not in KEEP and mn[0] != 'j': st.f = None
    d = ops[0] if n else None
    r32 = d is not None and d.type == X.X86_OP_REG and d.size == 4
    reg = FULL.get(ins.reg_name(d.reg)) if r32 else None
    if mn == 'call':
        for k in list(st.r): del st.r[k]
        st.f = None; st.al.clear(); st.nz.clear(); st.pk = (); st.espd = None; return
    if mn == 'push' and n == 1:
        v = val(st, ins, d, cells) if d.type in (X.X86_OP_IMM, X.X86_OP_REG) else TOP
        st.pk = st.pk + (v,); st.espd = None if st.espd is None else st.espd - 4; return
    if mn == 'pop' and r32:
        v = st.pk[-1] if st.pk else TOP; st.pk = st.pk[:-1]; st.espd = None if st.espd is None else st.espd + 4
        if reg == 'esp': st.espd = None; return
        setr(st, reg, v); return
    if mn in ('add', 'sub') and r32 and reg == 'esp' and n == 2 and ops[1].type == X.X86_OP_IMM:
        st.pk = (); st.espd = None if st.espd is None else st.espd + (ops[1].imm if mn == 'add' else -ops[1].imm); return
    wesp = False
    try: wr = [FULL.get(ins.reg_name(x)) for x in ins.regs_access()[1]]
    except Exception: st.r.clear(); st.f = None; st.al.clear(); st.nz.clear(); st.pk = (); st.espd = None; return
    if 'esp' in wr and mn not in ('push', 'pop'): st.espd = None; st.pk = ()
    if 'ebp' in wr:
        for k in [k for k in list(st.r) + list(st.al) if isinstance(k, tuple) and k[1] == 'ebp']: st.r.pop(k, None); st.al.pop(k, None); st.nz.pop(k, None)
    if n >= 1 and d.type == X.X86_OP_MEM and mn not in ('cmp', 'test'):           # a store: a frame cell, or an unknown address (every cell may be overwritten)
        c = cell(ins, d, st, cells); m = d.mem
        if mn == 'mov' and c is not None and n == 2:
            src = ops[1]
            for k in [k for k in list(st.r) + list(st.al) if isinstance(k, tuple) and k[1] == c[1] and k != c and abs(k[2] - c[2]) < 4]: st.r.pop(k, None); st.al.pop(k, None); st.nz.pop(k, None)
            if src.type == X.X86_OP_REG and src.size == 4: copy(st, c, FULL.get(ins.reg_name(src.reg)))
            else: setr(st, c, (src.imm & M32, 0) if src.type == X.X86_OP_IMM else TOP)
            return
        b = FULL.get(ins.reg_name(m.base)) if m.base else None
        if c is not None: setr(st, c, TOP)
        elif cells and b in ('ebp', 'esp') and not m.index and not m.segment and (b == 'ebp' or st.espd is not None):
            o = m.disp + (st.espd if b == 'esp' else 0); kill_cells(st, o - 3, o + d.size - 1)
        else: kill_cells(st)
    if mn == 'mov' and n == 2 and r32:
        s = ops[1]
        if s.type == X.X86_OP_REG and s.size == 4: copy(st, reg, FULL.get(ins.reg_name(s.reg))); return
        if s.type == X.X86_OP_IMM: setr(st, reg, (s.imm & M32, 0)); return
        c = cell(ins, s, st, cells)
        if c is not None: copy(st, reg, c); return
        setr(st, reg, TOP); return
    if mn in ('xor', 'sub') and r32 and n == 2 and ops[1].type == X.X86_OP_REG and ops[1].reg == d.reg: setr(st, reg, (0, 0)); return
    if mn in ('add', 'sub') and r32 and n == 2 and ops[1].type == X.X86_OP_IMM:
        c = ops[1].imm & M32; a = st.g(reg)
        if mn == 'sub': setr(st, reg, add(a, -c)); st.f = ('sub', reg, a, c)
        else: setr(st, reg, add(a, c))
        return
    if mn == 'cmp' and n == 2:
        l = locof(st, ins, d, cells) if d.type != X.X86_OP_IMM else None
        if l is not None:
            s = ops[1]
            if s.type == X.X86_OP_IMM: st.f = ('cmp', l, st.g(l), s.imm & M32)
            else:
                b = val(st, ins, s, cells)
                if b[1] == 0: st.f = ('cmp', l, st.g(l), b[0])                      # a location holding one constant value
                else: st.f = None
        else: st.f = None
        return
    if mn == 'test' and n == 2:
        l = locof(st, ins, d, cells); s = ops[1]
        if l is not None and s.type == X.X86_OP_IMM and s.imm: st.f = ('test', l, None, s.imm & M32)
        elif l is not None and s.type == X.X86_OP_REG and ops[0].type == X.X86_OP_REG and ops[0].reg == s.reg: st.f = ('test', l, None, M32)
        else: st.f = None
        return
    if mn == 'add' and r32 and n == 2 and ops[1].type == X.X86_OP_REG and ops[1].size == 4:
        a = st.g(reg); b = st.g(FULL.get(ins.reg_name(ops[1].reg)))
        if a[0] + a[1] < N and b[0] + b[1] < N and a[0] + a[1] + b[0] + b[1] < N: setr(st, reg, (a[0] + b[0], a[1] + b[1]))
        else: setr(st, reg, TOP)
        return
    if mn == 'and' and r32 and n == 2:
        b = val(st, ins, ops[1], cells); a = st.g(reg); hi = M32; lo = 0
        for v in (a, b):
            if v[0] + v[1] < N: hi = min(hi, v[0] + v[1])
        m = b[0] if b[1] == 0 else None
        if m is not None and m in st.nz.get(reg, ()): lo = 1                              # reg & m != 0 is a fact of the value
        setr(st, reg, (lo, hi - lo) if lo <= hi else TOP); return
    if mn == 'shr' and r32 and n == 2 and ops[1].type == X.X86_OP_IMM:
        k = ops[1].imm & 31; a = st.g(reg)
        if k == 0: return
        setr(st, reg, (a[0] >> k, (a[0] + a[1] >> k) - (a[0] >> k)) if a[0] + a[1] < N else (0, (M32 >> k))); return
    if mn == 'neg' and r32 and n == 1: a = st.g(reg); setr(st, reg, ((-(a[0] + a[1])) % N, a[1])); return
    if mn == 'not' and r32 and n == 1: a = st.g(reg); setr(st, reg, ((M32 - (a[0] + a[1])) % N, a[1])); return
    if mn == 'lea' and n == 2 and r32 and ops[1].type == X.X86_OP_MEM:
        m = ops[1].mem; lo = hi = m.disp; ok = not m.segment
        for rr, sc in ((m.base, 1), (m.index, m.scale)):
            if rr and ok:
                v = st.g(FULL.get(ins.reg_name(rr)))
                if v[0] + v[1] >= N: ok = False
                else: lo += v[0] * sc; hi += (v[0] + v[1]) * sc
        setr(st, reg, ((lo % N), hi - lo) if ok and hi - lo < M32 else TOP); return
    if mn == 'movzx' and r32 and n == 2:
        s = ops[1]
        if s.type == X.X86_OP_REG: setr(st, reg, (0, 255 if s.size == 1 else 65535)); return
        if s.type == X.X86_OP_MEM and s.size == 1 and not s.mem.segment and bool(s.mem.base) != bool(s.mem.index) and (not s.mem.index or s.mem.scale == 1):
            q = st.g(FULL.get(ins.reg_name(s.mem.base or s.mem.index))); D = s.mem.disp & M32
            if q[1] < (1 << 16):
                vs = [u8((D + q[0] + k) % N) for k in range(q[1] + 1)]
                if None not in vs: setr(st, reg, (min(vs), max(vs) - min(vs))); return
        setr(st, reg, (0, 255 if s.size == 1 else 65535)); return
    for w in wr:
        if w: setr(st, w, TOP)
    if 'esp' in wr and mn in ('push', 'pop'): pass


SIGNED = {'jl': 'l', 'jnge': 'l', 'jge': 'ge', 'jnl': 'ge', 'jle': 'le', 'jng': 'le', 'jg': 'g', 'jnle': 'g'}
UNS = {'jb': 'b', 'jc': 'b', 'jnae': 'b', 'jae': 'ae', 'jnb': 'ae', 'jnc': 'ae', 'jbe': 'be', 'jna': 'be', 'ja': 'a', 'jnbe': 'a', 'je': 'e', 'jz': 'e', 'jne': 'ne', 'jnz': 'ne'}
NEG = {'b': 'ae', 'ae': 'b', 'be': 'a', 'a': 'be', 'e': 'ne', 'ne': 'e', 'l': 'ge', 'ge': 'l', 'le': 'g', 'g': 'le'}


def arc(a, b):
    """the arc of values from a to b (mod 2^32), a and b taken mod 2^32; None if empty (b is a-1 or less: handled by the caller)"""
    return (a % N, (b - a) % N)


def refine(ins, st, taken):
    """the state on one edge of the conditional branch ins (st = the state before it); None if the edge is infeasible"""
    mn = ins.mnemonic
    f = st.f
    if f is None: return st
    kind, loc, pre, c = f
    if kind == 'test':
        cc = UNS.get(mn)
        if cc not in ('e', 'ne'): return st
        if not taken: cc = NEG[cc]
        o = st.cp()
        if cc == 'ne' and c != M32: o.nz.setdefault(loc, set()).add(c)
        elif cc == 'ne' and c == M32:
            v = o.g(loc); r = meet(v, 1, M32) if v != TOP else (1, M32 - 1)
            if r is None: return None
            setr_keep(o, loc, r)
        elif cc == 'e' and c == M32: setr_keep(o, loc, (0, 0))
        return o
    cc = UNS.get(mn); sg = False
    if cc is None: cc = SIGNED.get(mn); sg = cc is not None
    if cc is None: return st
    if not taken: cc = NEG[cc]
    S = 1 << 31; cs = c - N if c >= S else c
    if not sg:
        rng = {'b': (0, c - 1) if c > 0 else None, 'ae': (c, M32), 'be': (0, c), 'a': (c + 1, M32) if c < M32 else None, 'e': (c, c), 'ne': None}[cc]
        if cc == 'ne': p = pre
        elif rng is None: return None
        else:
            p = meet(pre, *rng)
            if p is None: return None
    else:
        # signed ranges as arcs: [-2^31, c-1], [c, 2^31-1], [-2^31, c], [c+1, 2^31-1]
        ar = {'l': (-S, cs - 1), 'ge': (cs, S - 1), 'le': (-S, cs), 'g': (cs + 1, S - 1)}[cc]
        if ar[0] > ar[1]: return None
        p = meet_arc(pre, ar)
        if p is None: return None
    o = st.cp()
    if kind == 'sub': setr_keep(o, loc, add(p, -c)); return o
    cid = o.al.get(loc)
    for k in [loc] + [k for k, v in o.al.items() if cid is not None and v == cid and k != loc]:
        q = join_meet(o.g(k), p) if o.g(k) != TOP else p
        setr_keep(o, k, q)
    return o


def setr_keep(st, loc, v):
    """narrow loc to v keeping its aliases and facts"""
    if v == TOP: st.r.pop(loc, None)
    else: st.r[loc] = v


def meet_arc(a, ar):
    """a intersected with the signed range [ar[0], ar[1]] (as an arc starting at ar[0] mod 2^32); None if empty (hull of two pieces)"""
    lo, hi = ar; b = (lo % N, hi - lo)
    out = None
    for x, y in seg(a):
        for u, w in seg(b):
            p = max(x, u); q = min(y, w)
            if p <= q: out = (p, q - p) if out is None else join(out, (p, q - p))
    return out


def join_meet(a, p):
    """a intersected with the interval p (hull)"""
    out = None
    for x, y in seg(a):
        for u, w in seg(p):
            lo = max(x, u); hi = min(y, w)
            if lo <= hi: out = (lo, hi - lo) if out is None else join(out, (lo, hi - lo))
    return out if out is not None else p


class Tg(tuple):
    pass


def resolve(site, dec, ins_at, preds, seeded, rset, u32, u8, intext, maxwords):
    """-> (targets tuple, None) or (None, why). dec[x] = (size, mnemonic, feat, falls through, direct targets); preds(x) = the decoded predecessors of x."""
    ins = ins_at(site); op = ins.operands[0]
    if op.mem.base or op.mem.segment: return None, 'table jump with a base register or segment'
    if (site + ins.disp_offset) not in rset: return None, 'table displacement is not relocated'
    T = op.mem.disp & M32; ireg = FULL.get(ins.reg_name(op.mem.index))
    cone = {site}; stack = [site]
    while stack:
        x = stack.pop()
        if seeded(x): continue
        for p in preds(x):
            if p not in cone: cone.add(p); stack.append(p)
    IN = {}; cnt = {}; work = []; cache = {}
    def ins_c(a):
        if a not in cache: cache[a] = ins_at(a)
        return cache[a]
    def esc(x):
        i = ins_c(x)
        if i is None: return False
        if forms_frame(i): return True
        if i.mnemonic == 'push' and i.operands[0].type == X.X86_OP_REG and FULL.get(i.reg_name(i.operands[0].reg)) == 'ebp':
            j = ins_c(x + i.size)
            return not (j is not None and j.mnemonic == 'mov' and len(j.operands) == 2 and all(o.type == X.X86_OP_REG for o in j.operands) and
                        FULL.get(j.reg_name(j.operands[0].reg)) == 'ebp' and FULL.get(j.reg_name(j.operands[1].reg)) == 'esp')
        return False
    cells = not any(esc(x) for x in cone)                  # a frame cell is tracked only if no instruction of the cone forms the frame address (then no pointer can name it)
    for x in cone:
        if seeded(x): IN[x] = St({}, None); work.append(x)
    while work:
        x = work.pop()
        if x == site: continue
        i = ins_c(x)
        if i is None: continue
        sz, mn, ft, fall, tg = dec[x]; o = IN[x].cp(); step(i, o, u8, cells)
        cond = i.mnemonic[0] == 'j' and i.mnemonic != 'jmp'
        outs = []
        for y in tg:
            if y in cone: outs.append((y, refine(i, o, True) if cond else o))
        if fall and (x + sz) in cone: outs.append((x + sz, refine(i, o, False) if cond else o))
        for y, s in outs:
            if s is None or seeded(y): continue
            if y not in IN: IN[y] = s.cp(); work.append(y); cnt[y] = 0
            else:
                m = joinst(IN[y], s)
                if m != IN[y]:
                    cnt[y] = cnt.get(y, 0) + 1
                    if cnt[y] > WIDEN:
                        for k in list(m.r):
                            if m.r[k] != IN[y].g(k): del m.r[k]
                    IN[y] = m; work.append(y)
    st = IN.get(site)
    if st is None: return None, 'the site is unreachable from any entry in the decoded edges'
    v = st.g(ireg)
    if v[1] + 1 > maxwords: return None, 'index register %s unbounded (interval of %d values)' % (ireg, v[1] + 1)
    out = []; addrs = []
    for k in range(v[1] + 1):
        a = (T + 4 * ((v[0] + k) % N)) % N
        if a not in rset: return None, 'table word %x (index %d) is not a relocated dword' % (a, (v[0] + k) % N if (v[0] + k) % N < N // 2 else (v[0] + k) % N - N)
        w = u32(a)
        if not intext(w): return None, 'table word %x -> %x is no code address' % (a, w)
        out.append(w); addrs.append(a)
    r = Tg(out); r.addrs = tuple(addrs); r.base = T; r.dsite = site + ins.disp_offset; r.lo = v[0] if v[0] < N // 2 else v[0] - N; r.hi = r.lo + v[1]; return r, None


def exclusive(ok, svals):
    """-> the set of table-word addresses whose only readers are resolved jump sites: svals = sorted [(value, reloc site)] of every relocated dword; a word range [lo, hi) of a resolved table is read by
    the relocated references whose value lies in it, and those must all be the displacement fields of resolved jump sites. A word of a table that anything else can read may reach code through data: it is not exclusive.
    Argument: the table object is the proved extent [lo, hi) (the words T + 4v over the proved interval of the index; the displacement T may lie above the extent, e.g. T-8, T-16). By OWNER 6 (ASM-006: the exe makes no wild
    accesses, in-bounds indices) a read of a byte of the object goes through a pointer derived from a reference to the object, and every reference to an address in the image is a relocated value (the loader's relocation set,
    s1): the reference may be the base T itself or a derived base such as T-8 (inside or outside the extent, the value is then still an address the relocation names; the reference whose value is in [lo, hi) is the one counted here,
    a derived base below lo is a reference to the neighbouring object, whose own extent is read the same way). So if every relocated reference into [lo, hi) is the displacement of a resolved jump site, no other read reaches the
    words, and the targets are entered only through the table edges. Code reachability does not change (the edges reach the targets); only the entry split does: such targets are not seeded."""
    import bisect
    allowed = {t.dsite for t in ok.values()}; out = set()
    for t in ok.values():
        lo = min(t.addrs); hi = max(t.addrs) + 4
        i = bisect.bisect_left(svals, (lo, -1))
        good = True
        while i < len(svals) and svals[i][0] < hi:
            if svals[i][1] not in allowed: good = False; break
            i += 1
        if good: out.update(t.addrs)
    return out
