#!/usr/bin/env python3
"""Shared x86-32 flow code for fpmodes.py: PE image, recursive-descent decoder with CFG, TOTAL esp/ebp tracking
(symbolic callee pops solved from join/ret constraints; every instruction ends with a constant or a named reason), frame-address
events, x87 stack-depth dataflow. No clocks, no entropy; every iteration order is sorted."""
import os, struct, bisect, itertools
from collections import defaultdict
import capstone
from capstone import x86 as X

FACTS = os.environ.get('FNVNX_FACTS', '/home/user/l3facts')       # facts root: every input / output path below derives from it
UNI = FACTS + '/universe.tsv'
EHJ = FACTS + '/ehtables.json'
M32 = 0xffffffff
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']
SUB = {}
for _r in R32:
    SUB[_r] = (_r, 0, 32)
    SUB[_r[1:]] = (_r, 0, 16)
for _r in 'abcd':
    SUB[_r + 'l'] = ('e' + _r + 'x', 0, 8)
    SUB[_r + 'h'] = ('e' + _r + 'x', 8, 8)
RETS = ('ret', 'retf', 'int3', 'hlt', 'ud2')
FUNK = ('inner', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'eh-throw')
STARTS = ('decomp', 'call', 'entry', 'tail', 'export', 'tls')
IMG = None
KINDS, EST, FLAGS = {}, [], {}
RETN, ALLOCA = {}, []          # entry -> callee-popped bytes (None = several different N); entries that allocate stack (esp -= eax)
IATADDR = {}                   # IAT slot address -> import name


class Image:
    def __init__(s, path):
        d = s.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        s.base = struct.unpack_from('<I', d, pe + 52)[0]
        s.secs, s.pe = [], pe
        o = pe + 24 + opt
        for i in range(n):
            name = d[o:o + 8].rstrip(b'\0').decode()
            vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            s.secs.append((name, s.base + va, vs, ro, rs, struct.unpack_from('<I', d, o + 36)[0]))
            o += 40
        rr, rsz = struct.unpack_from('<II', d, pe + 24 + 96 + 40)
        rel = []
        p = s.off(s.base + rr)
        end = p + rsz
        while p < end:
            page, bs = struct.unpack_from('<II', d, p)
            if not bs:
                break
            for i in range((bs - 8) // 2):
                e = struct.unpack_from('<H', d, p + 8 + 2 * i)[0]
                if e >> 12 == 3:
                    rel.append(s.base + page + (e & 0xfff))
            p += bs
        rel.sort()
        s.rset = frozenset(rel)
        s.taken = sorted(s.u32(r) for r in rel)

    def off(s, a):
        for n, va, vs, ro, rs, ch in s.secs:
            if va <= a < va + vs and a - va < rs:
                return ro + a - va
        return None

    def u32(s, a):
        o = s.off(a)
        return struct.unpack_from('<I', s.d, o)[0] if o is not None else 0

    def bytes(s, a, n):
        o = s.off(a)
        return s.d[o:o + n]


def explore(img, lo, hi, code):
    """recursive descent over [lo, hi) -> (insns by address, jump tables by jmp address, address-taken labels)."""
    insns, work, tables = {}, [lo], {}
    taken = sorted(set(img.taken[bisect.bisect_left(img.taken, lo):bisect.bisect_left(img.taken, hi)]))
    while work:
        a = work.pop()
        while lo <= a < hi and a not in insns:
            g = list(md.disasm(code[a - lo:a - lo + 16], a, 1))
            if not g:
                raise ValueError('undecodable')
            ins = insns[a] = g[0]
            m = ins.mnemonic.split(' ')[-1]      # the instruction under its prefix words (fnvlift.core.explore)
            ops = ins.operands
            if m == 'jmp':
                if ops[0].type == X.X86_OP_IMM:
                    work.append(ops[0].imm & M32)
                elif ops[0].type == X.X86_OP_MEM and ops[0].mem.scale == 4 and ops[0].mem.index and (a + ins.size - 4) in img.rset:
                    t, ent = ops[0].mem.disp & M32, []
                    while t in img.rset and lo <= img.u32(t) < hi:
                        ent.append(img.u32(t)); t += 4
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
                work.append(ops[0].imm & M32)
            a += ins.size
    return insns, tables, taken


def mn(i):
    return i.mnemonic.split()[-1]


def rn(i, op):
    return i.reg_name(op.reg) if op.type == X.X86_OP_REG else None


def base(n):
    return SUB[n][0] if n in SUB else n


def memslot(i, o, sz):
    m = o.mem
    if m.index or (m.segment and i.reg_name(m.segment) != 'ds'):
        return None
    b = i.reg_name(m.base) if m.base else None
    return ('s', m.disp, sz) if b == 'esp' else ('b', m.disp, sz) if b == 'ebp' else ('g', m.disp & M32) if b is None else None


def load_universe(exe):
    global IMG
    IMG = Image(exe)
    raw = open(UNI, 'rb').read()
    rows = []
    for ln in raw.decode().splitlines():
        f = ln.split('\t')
        if len(f) >= 3 and f[2].isdigit() and int(f[2]) > 0:
            rows.append((int(f[0], 16), int(f[2]), f[1]))
            KINDS[rows[-1][0]] = f[1]
            FLAGS[rows[-1][0]] = f[3] if len(f) > 3 else ''
    EST[:] = sorted((a, e) for a, e, k in rows)
    return raw, rows


def pmap(pool, fn, items, chunk=100):
    """ordered parallel map (results merged in input order)."""
    return list(pool.imap(fn, items, chunksize=chunk))


def decode(a, ext, kind='decomp'):
    """descent over [a, a+ext); non-decomp extents are byte sums, so the range is widened (up to +512) while a jump leaves it towards
    a non-start target and on undecodable tails. -> (insns, succ, tails) or None."""
    hi = a + ext
    for _ in range(40):
        try:
            ins, tb, tk = explore(IMG, a, hi, IMG.bytes(a, hi - a))
        except ValueError:
            if kind == 'decomp' or hi >= a + ext + 512:
                return None
            hi += 16
            continue
        succ, tails = {}, {}
        for x in sorted(ins):
            i = ins[x]; m = mn(i); n = x + i.size; o = i.operands
            if m == 'jmp':
                s = [o[0].imm & M32] if o[0].type == X.X86_OP_IMM else tb.get(x, list(tk))
            elif m in RETS:
                s = []
            else:
                s = [n] + ([o[0].imm & M32] if m[0] == 'j' or m.startswith('loop') else [])
            succ[x] = [t for t in s if t in ins]
            out = [t for t in s if t not in ins and (m[0] == 'j' or m.startswith('loop') or t == n)]
            if out and (m != 'jmp' or o[0].type == X.X86_OP_IMM):
                tails[x] = out
        far = [t for x in sorted(tails) for t in tails[x] if t != x + ins[x].size and hi <= t < a + ext + 512 and KINDS.get(t) not in STARTS]
        if kind != 'decomp' and far:
            hi = max(far) + 16
            continue
        return ins, succ, tails
    return None


class Fn:
    def __init__(s, a, ext, kind):
        s.a, s.kind, s.memo, s.steps, s.w = a, kind, {}, 0, 2
        r = decode(a, ext, kind)
        s.ok = r is not None
        if not r:
            return
        s.ins, s.succ, s.tails = r
        s.preds = defaultdict(list)
        for x in sorted(s.succ):
            for t in s.succ[x]:
                s.preds[t].append(x)


def callpop(F, x):
    """callee-popped bytes of the call at x: ('c', n) or ('v', kind) unknown, ('a',) alloca-style helper."""
    o = F.ins[x].operands[0]
    if o.type == X.X86_OP_IMM:
        t = o.imm & M32
        if t in ALLOCA:
            return ('a',)
        return ('c', RETN[t]) if RETN.get(t) is not None else ('v', 'pop-ambiguous-callee' if t in RETN else 'pop-unknown-callee')
    if o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and (o.mem.disp & M32) in IATADDR:
        return ('v', 'pop-import')
    return ('v', 'pop-indirect')


def eaxconst(F, x):
    """eax value from a `mov eax, imm` in the straight line before x, else None."""
    for _ in range(8):
        ps = F.preds.get(x, [])
        if len(ps) != 1:
            return None
        x = ps[0]; i = F.ins[x]
        if mn(i) == 'mov' and rn(i, i.operands[0]) == 'eax':
            return i.operands[1].imm & M32 if i.operands[1].type == X.X86_OP_IMM else None
        if mn(i) in ('call', 'jmp') or (i.operands and rn(i, i.operands[0]) == 'eax'):
            return None
    return None


def track(F):
    """TOTAL esp/ebp tracking. Sets F.sp[x] / F.bp[x] (esp / ebp offset from entry esp before insn x, or None), F.why (sorted reasons
    why some instruction is None), F.multi (more than one esp epoch: slot offsets not comparable). Callee pops are symbolic variables
    solved from join / ret constraints."""
    if hasattr(F, 'sp'):
        return
    st, eqs, kinds, why = {F.a: (F.a, 0, (), (F.a, 0, ()) if F.kind in FUNK else None)}, [], {}, set()   # funclets: ebp = parent's frame pointer, offset 0
    work, multi = [F.a], False
    funclet = F.kind in ('inner', 'eh-cont')
    while work:
        a = work.pop(); ep, k, vs, bp = st[a]; i = F.ins[a]; m = mn(i); o = i.operands
        r0 = rn(i, o[0]) if o else None; r1 = rn(i, o[1]) if len(o) > 1 else None
        nbp = bp
        wr = {base(i.reg_name(r)) for r in i.regs_access()[1]}       # every register the instruction writes, explicit and implicit (xchg esp, popal, xadd ..., ebp as a second operand)
        hs = m in ('ret', 'retf')                                    # True: the esp effect is modelled (here or in the chain below)
        if m in ('push', 'pushfd'):
            k -= 4; hs = True
        elif m == 'pop' and r0 == 'esp':
            ep, k, vs, multi = a, 0, (), True; why.add('esp-pop'); hs = True
        elif m in ('pop', 'popfd'):
            k += 4; hs = True
            nbp = None if r0 == 'ebp' else bp
        elif m in ('pushal', 'popal'):
            k += -32 if m == 'pushal' else 32; hs = True
            if m == 'popal':
                nbp = None                                           # popal reloads ebp from the stack
        elif m == 'enter':
            ep, k, vs, multi = a, 0, (), True; why.add('enter'); hs = True
        elif m == 'leave':
            hs = True
            if bp is None:
                ep, k, vs, multi = a, 0, (), True; why.add('esp-from-unknown-ebp')
            else:
                ep, k, vs = bp[0], bp[1] + 4, bp[2]
            nbp = None
        elif m == 'call':
            hs = True
            cp = callpop(F, a)
            if cp[0] == 'c':
                k += cp[1]
            elif cp[0] == 'a':
                n = eaxconst(F, a)
                if n is None:
                    ep, k, vs, multi = a, 0, (), True; why.add('alloca-size')
                else:
                    k -= n
            else:
                nx = F.ins.get(a + i.size)
                kinds[a] = cp[1]; vs = tuple(sorted(vs + (a,)))
                if nx is not None and mn(nx) == 'add' and rn(nx, nx.operands[0]) == 'esp':
                    eqs.append((((a, 1),), 0))                  # cdecl: the compiler cleans up itself
        elif m == 'mov' and r0 == 'esp':
            if r1 == 'ebp' and bp is not None:
                ep, k, vs = bp
            else:
                ep, k, vs, multi = a, 0, (), True; why.add('esp-from-reg')
        elif m == 'lea' and r0 == 'esp':
            if o[1].mem.base and i.reg_name(o[1].mem.base) == 'ebp' and not o[1].mem.index and bp is not None:
                ep, k, vs = bp[0], bp[1] + o[1].mem.disp, bp[2]
            else:
                ep, k, vs, multi = a, 0, (), True; why.add('esp-from-lea')
        elif m == 'mov' and r0 == 'ebp':
            nbp = (ep, k, vs) if r1 == 'esp' else None
        elif m in ('add', 'sub') and r0 == 'esp':
            if o[1].type == X.X86_OP_IMM:
                k += o[1].imm if m == 'add' else -o[1].imm
            else:
                ep, k, vs, multi = a, 0, (), True; why.add('esp-arith-reg')
        elif r0 == 'esp' and m not in ('cmp', 'test', 'push'):
            ep, k, vs, multi = a, 0, (), True; why.add('esp-align' if m == 'and' else 'esp-other')
        elif r0 == 'ebp' and m not in ('cmp', 'test', 'push'):
            nbp = None
        if 'esp' in wr and not hs and r0 != 'esp':                    # an esp write the chain above does not model (xchg reg, esp; xadd ...): a new epoch
            ep, k, vs, multi = a, 0, (), True; why.add('esp-implicit')
        if 'ebp' in wr and not (m == 'mov' and r0 == 'ebp' and r1 == 'esp'):
            nbp = None                                                # any other write of ebp, whatever the operand position
        if (m in ('ret', 'retf') or (m == 'jmp' and a in F.tails)) and ep == F.a and not funclet:
            eqs.append((tuple((v, 1) for v in vs), k))
        for t in reversed(F.succ[a]):
            ns = (ep, k, vs, nbp)
            if t not in st:
                st[t] = ns; work.append(t)
            else:
                e0, k0, v0, b0 = st[t]
                if (e0, k0, v0) != (ep, k, vs):
                    if e0 == ep:
                        c = defaultdict(int)
                        for v in vs:
                            c[v] += 1
                        for v in v0:
                            c[v] -= 1
                        eqs.append((tuple(sorted((v, n) for v, n in c.items() if n)), k - k0))
                    else:
                        why.add('epoch-join'); multi = True
                if b0 != nbp and b0 is not None:
                    st[t] = (e0, k0, v0, None); work.append(t)
    sol, bad = {}, False
    for _ in range(30):
        prog = False
        for cs, c0 in eqs:
            rem = [(v, n) for v, n in cs if v not in sol]
            c1 = c0 + sum(n * sol[v] for v, n in cs if v in sol)
            if not rem:
                bad = bad or c1 != 0
            elif len(rem) == 1:
                v, n = rem[0]
                q = -c1 / n
                if q != int(q) or q < 0 or q > 4096 or int(q) % 4:
                    bad = True
                else:
                    sol[v] = int(q); prog = True
        if not prog:
            break
    if bad:
        why.add('contradiction')
    F.sp, F.bp = {}, {}
    for x in sorted(st):
        ep, k, vs, bp = st[x]
        un = [v for v in vs if v not in sol]
        for v in un:
            why.add(kinds[v])
        F.sp[x] = None if un or bad else k + sum(sol[v] for v in vs)
        F.bp[x] = None if bp is None or bad or any(v not in sol for v in bp[2]) else bp[1] + sum(sol[v] for v in bp[2])
        if bp is not None and F.bp[x] is not None and bp[0] != ep:
            F.bp[x] = None
    F.why, F.multi = sorted(why), multi or bad


def frame_events(F):
    """frame-address events: (insn, coordinate or None, indexed, destination register or None, frame slot written or None)."""
    track(F)
    out = []
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands; s = F.sp.get(x); b = F.bp.get(x)
        r0 = rn(i, o[0]) if o else None; r1 = rn(i, o[1]) if len(o) > 1 else None
        if m == 'lea' and o[1].mem.base and i.reg_name(o[1].mem.base) in ('esp', 'ebp') and r0 not in ('esp', 'ebp'):
            bs = i.reg_name(o[1].mem.base); v = s if bs == 'esp' else b
            out.append((x, None if v is None else v + o[1].mem.disp, bool(o[1].mem.index), r0, None))
        elif m == 'mov' and r1 in ('esp', 'ebp') and o[0].type == X.X86_OP_REG and r0 not in ('esp', 'ebp'):
            out.append((x, s if r1 == 'esp' else b, False, r0, None))
        elif m == 'push' and r0 in ('esp', 'ebp') and (r0 == 'esp' or b is not None):
            out.append((x, s if r0 == 'esp' else b, False, None, None))
        elif m == 'mov' and r1 in ('esp', 'ebp') and o[0].type == X.X86_OP_MEM and not (o[0].mem.segment and i.reg_name(o[0].mem.segment) == 'fs'):
            mm = o[0].mem; v = s if mm.base and i.reg_name(mm.base) == 'esp' else b if mm.base and i.reg_name(mm.base) == 'ebp' else None
            out.append((x, s if r1 == 'esp' else b, False, None, None if v is None or mm.index or F.multi else v + mm.disp))
    return out


def conformance(F):
    """-> (ret_ok, ebp_ok) of a non-funclet entry, PROVED from F.sp / F.bp (call track(F) first):
    ret_ok: every `ret` / tail jump is reached with esp = entry esp exactly (F.sp == 0), the entry is single-epoch and its stack constraints are consistent;
    ebp_ok: on every path to a `ret` / tail jump ebp holds its entry value: the value is saved by `push ebp` (or `mov [esp+k], ebp`) in a slot of known entry-relative
    coordinate and reloaded from that very slot (`pop ebp`, `leave` through a proven ebp, `mov ebp, [..]`); any other write of ebp loses it; a store to the slot kills the save.
    Lattice (eo, sv): eo = ebp holds the entry value, sv = coordinate of the save slot or None; join = (and, equal or None). Callees are not looked at here
    (the direct-call graph closes both properties: a caller treats its callee's result as that callee's own)."""
    if F.kind in FUNK:
        return True, True
    ends = [x for x in F.ins if mn(F.ins[x]) in ('ret', 'retf') or (mn(F.ins[x]) == 'jmp' and x in F.tails)]
    ret_ok = not F.multi and all(F.sp.get(x) == 0 for x in ends)
    st, work, ebp_ok = {F.a: (True, None)}, [F.a], True
    while work:
        a = work.pop(); eo, sv = st[a]; i = F.ins[a]; m = mn(i); o = i.operands
        s, b = F.sp.get(a), F.bp.get(a)
        if a in ends and not eo:
            ebp_ok = False
        r0 = rn(i, o[0]) if o else None
        coord = lambda p: None if p.type != X.X86_OP_MEM or not p.mem.base or p.mem.index or F.multi else (None if i.reg_name(p.mem.base) not in ('esp', 'ebp') else (lambda v: None if v is None else v + p.mem.disp)(s if i.reg_name(p.mem.base) == 'esp' else b))
        wr = {base(i.reg_name(r)) for r in i.regs_access()[1]}
        if m == 'push' and r0 == 'ebp':
            sv = (s - 4 if s is not None else None) if eo else sv
        elif m == 'pop' and r0 == 'ebp':
            eo = s is not None and s == sv
        elif m == 'leave':
            eo = b is not None and b == sv
        elif m == 'mov' and r0 == 'ebp' and len(o) == 2 and o[1].type == X.X86_OP_MEM:
            c = coord(o[1]); eo = c is not None and c == sv
        elif m == 'mov' and len(o) == 2 and o[0].type == X.X86_OP_MEM and rn(i, o[1]) == 'ebp':
            c = coord(o[0]); sv = c if eo and c is not None else sv
        elif 'ebp' in wr:
            eo = False
        elif sv is not None and any(p.type == X.X86_OP_MEM and p.access & 2 and coord(p) is not None and coord(p) <= sv < coord(p) + max(p.size, 1) for p in o):
            sv = None
        for t in F.succ[a]:
            ns = (eo, sv)
            if t not in st:
                st[t] = ns; work.append(t)
            else:
                e0, v0 = st[t]; j = (e0 and eo, v0 if v0 == sv else None)
                if j != (e0, v0):
                    st[t] = j; work.append(t)
    return ret_ok, ebp_ok


# ---- x87 stack depth ----
PUSH = frozenset('fld fild fld1 fldz fldpi fldl2e fldl2t fldlg2 fldln2 fbld fsincos fptan fxtract fdecstp'.split())
POP = frozenset('fstp fistp fisttp fbstp fcomp fucomp ficomp fcomip fucomip faddp fsubp fsubrp fmulp fdivp fdivrp fpatan fyl2x fyl2xp1 fincstp'.split())
NOTX87 = frozenset(('fxsave', 'fxrstor', 'femms', 'fs'))
UNC = set()                    # entries whose x87 depth is undecidable: callers treat their result class as unknown
TC, FC = {}, {}


def isx87(m):
    return (m[0] == 'f' and m not in NOTX87) or m == 'wait'


def depth1(F, tabl, ov, start=None):
    start = F.a if start is None else start
    d0 = {start: 0}; work = [start]; conf = set(); mx = mi = 0; rets = set(); unk = ind = 0; x87 = False; sites = []; indpos = []
    while work:
        a = work.pop(); d = d0[a]; i = F.ins[a]; m = mn(i); e = 0
        if isx87(m):
            x87 = True
            e = 1 if m in PUSH else -1 if m in POP else -2 if m in ('fcompp', 'fucompp') else 0
            if m in ('fninit', 'finit', 'fnsave', 'fsave', 'emms'):
                d = 0
            elif m in ('frstor', 'fldenv', 'fxrstor'):
                d = None
        elif m == 'call':
            o = i.operands[0]
            if o.type == X.X86_OP_IMM:
                t = o.imm & M32
                if t in UNC:
                    sites.append(a); e = ov.get(a, 0)
                else:
                    e = tabl.get(t, 0)
            elif not (o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index):
                ind += 1; indpos.append(a); sites.append(a); e = ov.get(a, 0)
        nd = None if d is None else d + e
        if nd is None:
            unk = 1
        else:
            mx = max(mx, nd); mi = min(mi, nd)
        if m in ('ret', 'retf'):
            rets.add(nd)
        for t in F.tails.get(a, ()):
            if t in UNC:
                sites.append(a)
            rets.add(None if nd is None else nd + (ov.get(a, 0) if t in UNC else tabl.get(t, 0) if t in KINDS else tailret(t, tabl)))
        for s_ in F.succ[a]:
            if s_ not in d0:
                d0[s_] = nd; work.append(s_)
            elif d0[s_] != nd and d0[s_] is not None and nd is not None:
                conf.add((s_, d0[s_], nd))
    rd = sorted(r for r in rets if r is not None)
    return dict(x87=x87, conf=sorted(conf)[:3], nconf=len(conf) + (len(set(rd)) > 1) + (mi < 0 and F.kind == 'decomp'), mx=mx, mi=mi,
                ret=rd[0] if len(set(rd)) == 1 else 0, nodepth=bool(unk), ind=ind, sites=sites,
                inddep=sum(1 for a in indpos if x87 and F.succ[a] and isx87(mn(F.ins[F.succ[a][0]]))))


def tailret(t, tabl):
    """net depth change at return of code starting at t, a jump target inside another entry (not an entry start)."""
    if t in TC:
        return TC[t]
    TC[t] = 0
    k = bisect.bisect_right(EST, (t, M32)) - 1
    if k >= 0 and t < EST[k][0] + EST[k][1] + 512:
        if EST[k][0] not in FC:
            if len(FC) > 40:
                FC.clear()
            FC[EST[k][0]] = Fn(EST[k][0], EST[k][1], KINDS[EST[k][0]])
        F = FC[EST[k][0]]
        if F.ok and t in F.ins:
            TC[t] = depth1(F, tabl, {}, t)['ret']
    return TC[t]


def depth(F, tabl):
    """static depth; unknown-callee result classes (indirect calls, callees with a conflict) inferred only if the all-zero run fails."""
    r = depth1(F, tabl, {})
    r['status'] = 'static'
    st = sorted(set(r['sites']))
    if r['nconf'] and 0 < len(st) <= 10:
        for combo in itertools.islice(sorted(itertools.product((0, 1), repeat=len(st)), key=sum), 150):
            r2 = depth1(F, tabl, dict(zip(st, combo)))
            if not r2['nconf']:
                r2['status'] = 'inferred'; r = r2
                break
    if r['nconf']:
        r['status'] = 'conflict' if len(st) <= 10 else 'conflict-undecided'
    r['ret'] = r['ret'] if r['status'] != 'conflict' else 0
    return r


# ---- whole-program prerequisites: imports, callee pops (ret N, jmp-thunk chains), alloca helpers ----
THUNK = {}                     # entry -> IAT slot of a `jmp [iat]` stub
CACHE = FACTS + '/flow/retn.tsv'


def parse_imports():
    d, pe = IMG.d, IMG.pe
    rva = struct.unpack_from('<I', d, pe + 24 + 96 + 8)[0]
    while rva:
        oft, _, _, nm, ft = struct.unpack_from('<IIIII', IMG.bytes(IMG.base + rva, 20), 0)
        if not nm:
            break
        for k in range(100000):
            v = IMG.u32(IMG.base + (oft or ft) + 4 * k)
            if not v:
                break
            IATADDR[IMG.base + ft + 4 * k] = IMG.bytes(IMG.base + v + 2, 64).split(b'\0')[0].decode('latin1') if not v & 0x80000000 else '#%d' % (v & 0xffff)
        rva += 20


def phase0(args):
    a, ext, kind = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, 'x', None, 0
    rets = sorted({(i.operands[0].imm if i.operands else 0) for i in F.ins.values() if mn(i) == 'ret'})
    tl = sorted({t for x in sorted(F.tails) for t in F.tails[x]})
    f = F.ins.get(a)
    th = None
    if f is not None and mn(f) == 'jmp' and f.operands[0].type == X.X86_OP_MEM and not f.operands[0].mem.base and not f.operands[0].mem.index:
        th = f.operands[0].mem.disp & M32
    al = int(ext <= 128 and any(mn(i) == 'mov' and rn(i, i.operands[0]) == 'esp' and rn(i, i.operands[1]) not in (None, 'ebp') for i in F.ins.values()))
    return a, (rets[0] if len(rets) == 1 else None) if rets else (('t', tl[0]) if len(tl) == 1 else 0), th, al


def prepare(pool, rows, key):
    """fills RETN / THUNK / ALLOCA / IATADDR; cached in CACHE (header = key = exe+universe digest)."""
    parse_imports()
    res = None
    if os.path.exists(CACHE):
        L = open(CACHE).read().splitlines()
        if L and L[0] == key:
            res = [(int(f[0], 16), None if f[1] == '-' else 'x' if f[1] == 'x' else int(f[1]), None if f[2] == '-' else int(f[2], 16), int(f[3]))
                   for f in (ln.split('\t') for ln in L[1:])]
    if res is None:
        res = [(a, r, th, al) for a, r, th, al in pmap(pool, phase0, [(a, e, k) for a, e, k in rows], 300)]
        res = [(a, ('t', r[1]) if isinstance(r, tuple) else r, th, al) for a, r, th, al in res]
        pend = dict((a, r) for a, r, th, al in res)
        for a in sorted(pend):
            r = pend[a]
            for _ in range(6):
                if isinstance(r, tuple):
                    r = pend.get(r[1], 0)
            pend[a] = 0 if isinstance(r, tuple) else r
        res = [(a, pend[a], th, al) for a, r, th, al in res]
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, 'w') as f:
            f.write(key + '\n' + ''.join('%08x\t%s\t%s\t%d\n' % (a, '-' if r is None else r, '-' if th is None else '%x' % th, al) for a, r, th, al in res))
    for a, r, th, al in res:
        if r != 'x':
            RETN[a] = r
        if th is not None:
            THUNK[a] = th
        if al:
            ALLOCA.append(a)
