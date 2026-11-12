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
    __slots__ = ('r', 'f')

    def __init__(s, r, f): s.r = r; s.f = f      # r: {reg: interval} (absent = top); f: None | (kind, reg, pre interval, imm)
    def cp(s): return St(dict(s.r), s.f)
    def __eq__(s, o): return s.r == o.r and s.f == o.f
    def g(s, reg): return s.r.get(reg, TOP)


def setr(st, reg, v):
    if v is None or v == TOP: st.r.pop(reg, None)
    else: st.r[reg] = v


def joinst(p, q):
    r = {}
    for k in set(p.r) | set(q.r):
        v = join(p.g(k), q.g(k))
        if v != TOP: r[k] = v
    return St(r, p.f if p.f == q.f else None)


def val(st, ins, op):
    """interval of a source operand or None"""
    if op.type == X.X86_OP_IMM: return (op.imm & M32, 0)
    if op.type == X.X86_OP_REG and op.size == 4: return st.g(FULL.get(ins.reg_name(op.reg)))
    return TOP


def step(ins, st, u8):
    """transfer function (st modified in place)"""
    mn = ins.mnemonic.split()[-1]; ops = ins.operands; n = len(ops); prev = st.f
    if prev is not None:
        try: W = {FULL.get(ins.reg_name(x)) for x in ins.regs_access()[1]}
        except Exception: W = {prev[1]}
        if prev[1] in W: st.f = None      # the compared register is overwritten by an instruction that keeps the flags
    if mn not in KEEP and mn[0] != 'j': st.f = None
    d = ops[0] if n else None
    r32 = d is not None and d.type == X.X86_OP_REG and d.size == 4
    reg = FULL.get(ins.reg_name(d.reg)) if r32 else None
    if mn == 'call':
        for k in list(st.r): del st.r[k]
        st.f = None; return
    if mn == 'mov' and r32 and n == 2: setr(st, reg, val(st, ins, ops[1]) if ops[1].type in (X.X86_OP_IMM, X.X86_OP_REG) else TOP); return
    if mn in ('xor', 'sub') and r32 and n == 2 and ops[1].type == X.X86_OP_REG and ops[1].reg == d.reg: setr(st, reg, (0, 0)); return
    if mn in ('add', 'sub') and r32 and n == 2 and ops[1].type == X.X86_OP_IMM:
        c = ops[1].imm & M32; a = st.g(reg)
        if mn == 'sub': st.f = ('sub', reg, a, c); setr(st, reg, add(a, -c))
        else: setr(st, reg, add(a, c))
        return
    if mn == 'cmp' and n == 2 and r32 and ops[1].type == X.X86_OP_IMM: st.f = ('cmp', reg, st.g(reg), ops[1].imm & M32); return
    if mn == 'add' and r32 and n == 2 and ops[1].type == X.X86_OP_REG and ops[1].size == 4:
        a = st.g(reg); b = st.g(FULL.get(ins.reg_name(ops[1].reg)))
        if a[0] + a[1] < N and b[0] + b[1] < N and a[0] + a[1] + b[0] + b[1] < N: setr(st, reg, (a[0] + b[0], a[1] + b[1]))
        else: setr(st, reg, TOP)
        return
    if mn == 'and' and r32 and n == 2:
        b = val(st, ins, ops[1]); a = st.g(reg); hi = M32
        for v in (a, b):
            if v[0] + v[1] < N: hi = min(hi, v[0] + v[1])
        setr(st, reg, (0, hi)); return
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
    try: wr = [FULL.get(ins.reg_name(x)) for x in ins.regs_access()[1]]
    except Exception: st.r.clear(); st.f = None; return
    for w in wr:
        if w: st.r.pop(w, None)


def refine(ins, st, taken):
    """the state on one edge of the conditional branch ins (st = the state before it); None if the edge is infeasible"""
    mn = ins.mnemonic
    f = st.f
    if f is None: return st
    kind, reg, pre, c = f
    cc = {'jb': 'b', 'jc': 'b', 'jnae': 'b', 'jae': 'ae', 'jnb': 'ae', 'jnc': 'ae', 'jbe': 'be', 'jna': 'be', 'ja': 'a', 'jnbe': 'a', 'je': 'e', 'jz': 'e', 'jne': 'ne', 'jnz': 'ne'}.get(mn)
    if cc is None: return st
    if not taken: cc = {'b': 'ae', 'ae': 'b', 'be': 'a', 'a': 'be', 'e': 'ne', 'ne': 'e'}[cc]
    rng = {'b': (0, c - 1) if c > 0 else None, 'ae': (c, M32), 'be': (0, c), 'a': (c + 1, M32) if c < M32 else None, 'e': (c, c), 'ne': None}[cc]
    if cc == 'ne': p = pre
    elif rng is None: return None
    else:
        p = meet(pre, *rng)
        if p is None: return None
    o = st.cp()
    if cc != 'ne' or True: setr(o, reg, add(p, -c) if kind == 'sub' else p)
    return o


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
    for x in cone:
        if seeded(x): IN[x] = St({}, None); work.append(x)
    while work:
        x = work.pop()
        if x == site: continue
        i = ins_c(x)
        if i is None: continue
        sz, mn, ft, fall, tg = dec[x]; o = IN[x].cp(); step(i, o, u8)
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
    r = Tg(out); r.addrs = tuple(addrs); r.base = T; r.lo = v[0] if v[0] < N // 2 else v[0] - N; r.hi = r.lo + v[1]; return r, None
