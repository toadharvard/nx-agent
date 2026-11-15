#!/usr/bin/env python3
"""pointsto: whole-image points-to over abstract objects, the data-pointer dataflow behind the write-set facts (design: journal/inbox/l3a-lift-w4-001.md).
    pointsto.run(img, M, entries, summ) -> PT ; PT.reach(lo, hi) -> writers of [lo, hi) and the open stores ; PT.report() -> counts

Values (frozenset of tokens):  (obj, off)  a pointer into object obj at constant byte offset off, or off None = ANY offset of obj (OWNER 6: arithmetic on a pointer to O stays in O);
  (None, c)  the integer c ; (None, None) an arbitrary integer ; TOP = ('TOP', None) an unbounded value (sources below).
Objects:  ('S', section)   a static object of the image (a section; a relocated word's value is a pointer into the section it lies in);
          ('F', entry)     the stack frame of an entry (cells at esp-relative offsets of its tracked esp, incoming argument slots included);
          ('H', site)      a heap or handle object: one per call site of an import (documented returned pointers: OWNER 3).
FACTS USED (each by its registry record): OWNER 6 (no wild accesses): a value derived from O points into O; a deref of an integer touches no object.
  Relocation completeness: an image address originates from a relocated word or a relocated immediate; an unrelocated constant is an integer.
  OWNER 3: an import writes only through its pointer arguments (their objects, ANY offset); with no contract entry the content written is TOP (a GAP, counted per import).
  An indirect call whose target set holds no known entry calls the escaping entries (kind ptr / export / entry), never an unknown callee.
TOP SOURCES (counted): import-gap writes, loads from TOP cells, esp lost (frame ANY is still an object: not TOP).
Memory: cells (obj, off) -> value, one global map (Andersen: flow-insensitive per cell, monotone); a write with ANY offset is kept per object (`anyw`). Registers and stack slots are flow-sensitive inside one entry.
Termination: objects are finite; per object the offsets of a set are widened to ANY above WIDTH distinct offsets, integers above WIDTH constants to the arbitrary integer; values only grow; the global fixpoint ends when a pass changes no cell, no input, no output."""
import os, sys, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capstone
from capstone import x86 as X
import pcsrc
from collections import Counter, defaultdict

M32 = 0xffffffff
WIDTH = 8
STKW = 256                       # stack slots of a callee's frame above this byte offset are one slot (DEEP): bounds the table forwarded around a call cycle (a recursion adds a slot per pass otherwise)
OBJW = 8
UNK = (None, None); TOP = ('TOP', None)
U = frozenset([UNK]); TOPSET = frozenset([TOP])
R32 = pcsrc.R32


class NS(frozenset):
    """a token set in canonical (normalised) form: norm() of it is itself. Only norm() makes one; every set stored in a cell or state is one."""
    __slots__ = ()


def norm(ts):
    """the canonical form of a token set: per object more than WIDTH offsets -> ANY; more than WIDTH integer constants -> the arbitrary integer; more than OBJW frame or heap objects of a kind -> the kind's summary object;
    TOP absorbs nothing (kept as a token). norm(norm(x)) == norm(x), and x is contained in norm(x) as a set of concrete values. No memo table (a memo keyed by the wide input sets held 1.3 GB at 3M joins on binkw32).
    Canonical forms are what makes the fixpoint terminate: two sets with the same meaning compare equal, so a join that adds nothing is seen as unchanged."""
    if type(ts) is NS: return ts
    return NS(_norm(ts))


def _norm(ts):
    """canonical form, in this order: (1) per object more than WIDTH offsets -> ANY; (2) more than WIDTH integer constants -> the arbitrary integer; (3) more than OBJW frame or heap objects of a kind -> the kind's
    summary object (kind, '*') with ANY offset; (4) SUBSUMPTION: a token that another token of the set already covers is dropped: (o, c) under (o, None), (F|H obj, c) under ((kind,'*'), None), (None, c) under UNK.
    Without (4) one meaning has several forms and a loop that adds an offset to an object that already has ANY, then collapses at WIDTH, alternates between them forever."""
    byo = defaultdict(set)
    for o, c in ts: byo[o].add(c)
    out = set()
    for o, cs in byo.items():
        if o is None:
            if None in cs or len(cs) > WIDTH: out.add(UNK)
            else: out |= {(None, c) for c in cs}
        elif o == 'TOP': out.add(TOP)
        elif None in cs or len(cs) > WIDTH: out.add((o, None))
        else: out |= {(o, c) for c in cs}
    kinds = defaultdict(set)
    for o, c in out:
        if o is not None and o != 'TOP' and o[0] in ('F', 'H') and o[1] != '*': kinds[o[0]].add(o)
    for k, objs in kinds.items():
        if len(objs) > OBJW:
            out = {(o, c) for o, c in out if not (o is not None and o != 'TOP' and o[0] == k and o[1] != '*')}
            out.add(((k, '*'), None))
    for o, c in list(out):                                   # (4) subsumption by a summary object
        if o is not None and o != 'TOP' and o[0] in ('F', 'H') and o[1] != '*' and ((o[0], '*'), None) in out: out.discard((o, c))
    return frozenset(out)


def join(a, b):
    """the canonical union; every result is canonical (norm), also when one side contains the other: returning the raw wider side would give the same meaning two forms and the dataflow of a loop would alternate between them"""
    if a is None: return norm(b) if b is not None else None
    if b is None or a is b: return norm(a)
    if b <= a: return norm(a)
    if a <= b: return norm(b)
    return norm(a | b)


def add(A, B, sub=False):
    out = set()
    for ta in A:
        for tb in B:
            (oa, ca), (ob, cb) = ta, tb
            if oa == 'TOP' or ob == 'TOP': out.add(TOP)
            elif oa is None and ob is None:
                out.add(UNK if ca is None or cb is None else (None, ((ca - cb) if sub else (ca + cb)) & M32))
            elif oa is None or ob is None:
                po, pc, ic = (oa, ca, cb) if ob is None else (ob, cb, ca)
                if sub and oa is None: out.add(UNK)                       # integer - pointer: an integer
                else: out.add((po, None if pc is None or ic is None else (pc - ic if sub else pc + ic) & M32))
            else:
                if sub: out.add(UNK)                                      # pointer - pointer: an integer
                else: out.add((oa, None)); out.add((ob, None))
    return norm(frozenset(out))


def other(A, B=frozenset()):
    """any other arithmetic (and/or/xor/shift/mul/div...): pointers of the operands stay in their objects (OWNER 6); constants only combine to an arbitrary integer"""
    out = {UNK}
    for t in A | B:
        if t[0] == 'TOP': out.add(TOP)
        elif t[0] is not None: out.add((t[0], None))
    return norm(frozenset(out))


class PT:
    def __init__(self, img, M, summ=None, imports=None, contracts=None):
        self.img, self.M, self.summ = img, M, summ or {}
        self.imports = imports or {}; self.contracts = contracts or {}
        self.cells = {}; self.anyw = defaultdict(lambda: None)
        self.inp = {}; self.out = {}; self.changed = False
        self.stores = []; self.gaps = Counter(); self.top_stores = []; self.int_stores = 0
        self.escset = set(); self.esc = sorted(e for e, k in img.entries.items() if k & {'ptr', 'export', 'entry'})
        self.escset = set(self.esc); self.rdw = M.u32; self.ver = 0; self.oc = {}; self.byobj = defaultdict(dict); self.initall = {}; self.agg = {}

    # ---- memory
    def sobj(self, a):
        s = self.M.sec(a); return ('S', s) if s is not None else None

    def init_cell(self, obj, off):
        """the value a static cell holds before any write: the image word"""
        if obj[0] != 'S' or off is None: return None
        a = off
        if a in self.img.rset:
            w = self.rdw(a); o = self.sobj(w)
            if o is not None: return frozenset([(o, w)])
            return U
        return U

    def objall(self, o):
        """the union of every value a cell of object o can hold: kept incrementally (`agg`, joined at each write) plus the static initial words"""
        v = self.agg.get(o)
        if o[0] == 'S':
            iv = self.initall.get(o)
            if iv is None:
                iv = U
                for a in self.img.rset:
                    if self.sobj(a) == o: iv = join(iv, self.init_cell(o, a))
                self.initall[o] = iv
            v = join(v, iv)
        return join(v, self.anyw[o]) or U

    def summ_of(self, o):
        """the summary object (kind, '*') of a frame or heap object, else None"""
        return (o[0], '*') if o[0] in ('F', 'H') and o[1] != '*' else None

    def read(self, toks):
        out = None
        for o, c in toks:
            if o is None: continue
            if o == 'TOP': out = join(out, TOPSET); continue
            if c is None:
                out = join(out, self.objall(o))
            else:
                v = self.cells.get((o, c))
                if v is None:
                    for d in (1, 2, 3):
                        v = join(v, self.cells.get((o, c - d)))
                    v = join(v, self.init_cell(o, c))
                out = join(out, v)
            out = join(out, self.anyw[o])
            so = self.summ_of(o) if o != 'TOP' else None
            if so is not None: out = join(out, self.anyw.get(so))       # a write through the kind's summary pointer reaches every object of the kind
        return out if out is not None else U

    def write(self, toks, val, site, kind='store'):
        top = False; wrote = []
        for o, c in toks:
            if o is None: self.int_stores += 1; continue
            if o == 'TOP': top = True; continue
            wrote.append((o, c))
            so = self.summ_of(o)
            if so is not None: self.agg[so] = join(self.agg.get(so), val)       # the summary object holds the union of every object of its kind
            if c is None:
                old = self.anyw[o]; new = join(old, val)
                if new != old: self.anyw[o] = new; self.agg[o] = join(self.agg.get(o), val); self.changed = True; self.ver += 1
            else:
                old = self.cells.get((o, c)); new = join(old, val)
                if new != old: self.cells[(o, c)] = new; self.agg[o] = join(self.agg.get(o), val); self.changed = True; self.ver += 1
        self.stores.append((site, kind, frozenset(wrote), top))

    # ---- entry analysis
    def analyse(self, e):
        img = self.img; pcs = pcsrc
        inr, ins = self.inp.get(e, ({}, {}))
        if e in self.escset and 'ESC' in self.inp:
            er, es = self.inp['ESC']
            inr = {k: join(inr.get(k), er.get(k)) for k in set(inr) | set(er)}; ins = {k: join(ins.get(k), es.get(k)) for k in set(ins) | set(es)}
        states = {e: (dict(inr), {('F', e), k}if False else dict(ins), 0)}
        work = [e]; seen = {}
        fr = ('F', e)
        outregs = None; pops = set()
        while work:
            a = work.pop()
            regs0, st0, esp0 = states[a]
            if not img.reached(a): continue
            g = img.dec(a)
            if not g: continue
            regs = dict(regs0); st = dict(st0); esp = esp0; mn = g.mnemonic; ops = g.operands; nxt = a + g.size; succ = [nxt]; term = False

            def regv(n):
                if n == 'esp': return frozenset([(fr, esp)]) if esp is not None else frozenset([(fr, None)])
                return regs.get(n, U)

            def setr(op, v):
                n = R32.get(op.reg)
                if n is None or n == 'esp': return
                if op.size != 4: v = other(regs.get(n, U), v) if False else U
                regs[n] = v

            def addr_of(op):
                m = op.mem
                disp = m.disp
                if not m.base and not m.index:
                    ra = a + g.disp_offset
                    if g.disp_size == 4 and ra in img.rset:
                        o = self.sobj(disp & M32)
                        return frozenset([(o, disp & M32)]) if o is not None else U
                    return U
                v = frozenset([(None, disp & M32)])
                if m.base:
                    bn = R32.get(m.base)
                    if bn == 'esp' and esp is not None:
                        return self.stack_addr(fr, esp + disp) if not m.index else frozenset([(fr, None)])
                    v = add(regv(bn), v)
                if m.index:
                    iv = regv(R32.get(m.index))
                    if m.scale != 1: iv = other(iv)
                    v = add(v, iv)
                return v

            def rdmem(op):
                if op.type == X.X86_OP_MEM:
                    ad = addr_of(op); v = self.read(ad)
                    for o_, c_ in ad:                                  # incoming slots (and pushed ones) of this frame: the values the caller passed (feed), joined to the cells
                        if o_ == fr:
                            if c_ is None:
                                for sv in st.values(): v = join(v, sv)
                            else:
                                sv = st.get(min(c_, STKW) if c_ >= 0 else c_)
                                if sv is not None: v = join(v, sv)
                    if op.size and op.size != 4: v = U if not any(t[0] == 'TOP' for t in v) else TOPSET
                    return v
                return None

            def val(op):
                if op.type == X.X86_OP_REG: return regv(R32.get(op.reg)) if op.size == 4 else U
                if op.type == X.X86_OP_IMM:
                    if g.imm_size == 4 and (a + g.imm_offset) in img.rset:
                        o = self.sobj(op.imm & M32)
                        if o is not None: return frozenset([(o, op.imm & M32)])
                    return frozenset([(None, op.imm & M32)])
                if op.type == X.X86_OP_MEM: return rdmem(op)
                return U

            def putmem(op, v):
                ad = addr_of(op); self.write(ad, v, a)

            if mn == 'mov':
                d, s = ops
                if d.type == X.X86_OP_REG:
                    if R32.get(d.reg) == 'esp': esp = None;
                    else: setr(d, val(s))
                else: putmem(d, val(s))
            elif mn == 'lea':
                d, s = ops; m = s.mem; v = frozenset([(None, m.disp & M32)])
                if m.base:
                    bn = R32.get(m.base)
                    v = add(regv(bn), v) if not (bn == 'esp' and esp is not None) else (self.stack_addr(fr, esp + m.disp))
                if m.index:
                    iv = regv(R32.get(m.index))
                    if m.scale != 1: iv = other(iv)
                    v = add(v, iv)
                if not m.base and not m.index and g.disp_size == 4 and (a + g.disp_offset) in img.rset:
                    o = self.sobj(m.disp & M32); v = frozenset([(o, m.disp & M32)]) if o is not None else U
                setr(d, v)
            elif mn == 'push':
                v = val(ops[0])
                if esp is not None: esp -= 4; self.write(frozenset([(fr, esp)]), v, a, 'push'); st[esp] = v
                else: self.write(frozenset([(fr, None)]), v, a, 'push')
            elif mn == 'pop':
                if esp is not None: v = st.pop(esp, None) or self.read(frozenset([(fr, esp)])); esp += 4
                else: v = self.read(frozenset([(fr, None)]))
                if ops[0].type == X.X86_OP_REG: setr(ops[0], v)
                else: putmem(ops[0], v)
            elif mn in ('add', 'sub', 'and', 'or', 'xor', 'imul', 'shl', 'shr', 'sar', 'sal', 'rol', 'ror', 'adc', 'sbb', 'neg', 'not', 'inc', 'dec') and ops and ops[0].type in (X.X86_OP_REG, X.X86_OP_MEM):
                d = ops[0]
                if mn in ('xor', 'sub') and len(ops) == 2 and ops[1].type == X.X86_OP_REG and d.type == X.X86_OP_REG and ops[1].reg == d.reg: nv = frozenset([(None, 0)])
                else:
                    A = val(d) if not (d.type == X.X86_OP_REG and R32.get(d.reg) == 'esp') else frozenset([(None, 0)])
                    B = val(ops[1]) if len(ops) > 1 else frozenset([(None, 1)])
                    if len(ops) == 3: B = val(ops[2]); A = val(ops[1])
                    if mn in ('add', 'inc'): nv = add(A, B if mn == 'add' else frozenset([(None, 1)]))
                    elif mn in ('sub', 'dec'): nv = add(A, B if mn == 'sub' else frozenset([(None, 1)]), True)
                    else: nv = other(A, B if mn not in ('neg', 'not') else frozenset())
                if d.type == X.X86_OP_REG:
                    if R32.get(d.reg) == 'esp':
                        if mn in ('add', 'sub') and len(ops) == 2 and ops[1].type == X.X86_OP_IMM and esp is not None: esp += ops[1].imm if mn == 'add' else -ops[1].imm
                        else: esp = None
                    else: setr(d, nv)
                else: putmem(d, nv)
            elif mn == 'xchg' and len(ops) == 2:
                va, vb = val(ops[0]), val(ops[1])
                for o_, v_ in ((ops[0], vb), (ops[1], va)):
                    if o_.type == X.X86_OP_REG:
                        if R32.get(o_.reg) == 'esp': esp = None
                        else: setr(o_, v_)
                    else: putmem(o_, v_)
            elif mn in ('movzx', 'movsx', 'cmovo', 'cmovb') or mn.startswith('cmov') or mn == 'bswap':
                d = ops[0]
                if d.type == X.X86_OP_REG and R32.get(d.reg) != 'esp':
                    if mn.startswith('cmov'): setr(d, join(regs.get(R32.get(d.reg), U), val(ops[1])))
                    else: setr(d, U)
            elif mn == 'call':
                succ = [nxt]; self.do_call(g, a, ops[0], regs, st, esp, fr, val)
                esp2 = self.call_effect(g, a, regs, esp)
                esp = esp2
            elif mn == 'ret':
                n = ops[0].imm & 0xffff if ops and ops[0].type == X.X86_OP_IMM else 0
                pops.add(None if esp is None else esp + n)
                outregs = {k: join(outregs.get(k) if outregs else None, regs.get(k, U)) for k in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp')} if outregs is None else {k: join(outregs.get(k), regs.get(k, U)) for k in outregs}
                term = True; succ = []
            elif mn == 'jmp':
                o = ops[0]
                if o.type == X.X86_OP_IMM:
                    t = o.imm & M32
                    if t in img.entries and pcs.owner(img, t) and t != e: self.feed(t, regs, st, esp, fr, tail=True); term = True; succ = []
                    else: succ = [t]
                else:
                    ts = self.targets(val(o)); [self.feed(t, regs, st, esp, fr, tail=True) for t in ts]
                    if o.type == X.X86_OP_MEM and o.mem.index and not o.mem.base: succ = [w for w in self.table_succ(o)]
                    else: succ = []
                    term = not succ
                    for w in succ: pass
            elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne', 'jecxz'):
                succ = [nxt, ops[0].imm & M32] if ops and ops[0].type == X.X86_OP_IMM else [nxt]
            elif mn in ('hlt', 'ud2', 'int3', 'iret', 'iretd', 'retf'): term = True; succ = []
            elif mn in ('cmp', 'test', 'nop', 'wait', 'fwait', 'leave') or mn.startswith('set'):
                if mn == 'leave':
                    ebp = regs.get('ebp', U); esp = None
                    for o_, c_ in ebp:
                        if o_ == fr and c_ is not None: esp = c_ + 4
                    regs['ebp'] = self.read(frozenset([(fr, esp - 4)])) if esp is not None else U
                if mn.startswith('set'): setr(ops[0], U) if ops[0].type == X.X86_OP_REG else putmem(ops[0], U)
            else:
                # any other instruction: written registers get an arbitrary integer, a written memory operand an arbitrary value (string moves copy: below)
                for o in ops:
                    if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_READ and not mn.startswith(('movs', 'cmps', 'lods', 'scas')): self.read(addr_of(o))
                rd, wr = g.regs_access()
                for r in wr:
                    n = R32.get(r)
                    if n and n != 'esp' and n in regs: regs[n] = U
                    elif n == 'esp': esp = None
                if mn.startswith('movs') and not mn.startswith(('movsx', 'movsd', 'movss')) or mn in ('movsd',) and len(ops) == 2 and ops[0].type == ops[1].type == X.X86_OP_MEM:
                    src = [o for o in ops if o.access & capstone.CS_AC_READ and o.type == X.X86_OP_MEM]; dst = [o for o in ops if o.access & capstone.CS_AC_WRITE and o.type == X.X86_OP_MEM]
                    v = self.read(addr_of(src[0])) if src else U
                    for o in dst: putmem(o, join(v, U))
                else:
                    for o in ops:
                        if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_WRITE: putmem(o, U)
            if term: continue
            for t in succ:
                if not (img.T0 <= t < img.T1) or not img.reached(t): continue
                ns = (regs, st, esp)
                if t not in states: states[t] = ns; work.append(t)
                else:
                    o = states[t]
                    r2 = {k: join(o[0].get(k), regs.get(k)) for k in set(o[0]) | set(regs)}
                    s2 = {k: join(o[1].get(k), st.get(k)) for k in set(o[1]) | set(st)}
                    e2 = o[2] if o[2] == esp else None
                    if (r2, s2, e2) != o: states[t] = (r2, s2, e2); work.append(t)
        if outregs is not None:
            old = self.out.get(e)
            new = outregs if old is None else {k: join(old.get(k), outregs.get(k)) for k in outregs}
            if new != old: self.out[e] = new; self.changed = True
        self.pop[e] = (next(iter(pops)) if len(pops) == 1 else None) if pops else None

    def stack_addr(self, fr, k): return frozenset([(fr, k)])

    def table_succ(self, o):
        t = o.mem.disp & M32; out = []; n = 0
        while t in self.img.rset and n < 4096:
            w = self.rdw(t)
            if not (self.img.T0 <= w < self.img.T1 and self.img.reached(w)): break
            out.append(w); t += 4; n += 1
        return out

    def targets(self, v):
        """known entries of a value; none known -> the escaping entries (R3's set)"""
        ts = [c for o, c in v if o is not None and o != 'TOP' and c is not None and c in self.img.entries]
        return ts if ts else ['ESC']

    def feed(self, t, regs, st, esp, fr, tail=False):
        old = self.inp.get(t, ({}, {}))
        r = {k: join(old[0].get(k), regs.get(k, U)) for k in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp')}
        s = dict(old[1])
        if esp is not None:
            base = esp if tail else esp - 4
            for k, v in st.items():
                if k >= esp:
                    kk = min(k - base, STKW) if k - base >= 0 else k - base      # offsets from the callee's entry esp; at STKW and above one slot (DEEP): a join, sound
                    s[kk] = join(s.get(kk), v)
        new = (r, s)
        if new != old: self.inp[t] = new; self.changed = True

    def do_call(self, g, a, o, regs, st, esp, fr, val):
        if o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and g.disp_size == 4 and (a + g.disp_offset) in self.img.rset and (o.mem.disp & M32) in self.imports:
            self.import_call(a, self.imports[o.mem.disp & M32], regs, st, esp, fr); return
        tg = self.img.calltg.get(a)
        ts = [tg] if tg is not None and tg in self.img.entries else self.targets(val(o) if o.type != X.X86_OP_IMM else frozenset())
        for t in ts: self.feed(t, regs, st, esp, fr)

    def import_call(self, a, name, regs, st, esp, fr):
        """an import with no contract: it writes TOP through every pointer in its argument registers and the outgoing argument slots (a GAP, counted per import); returns a fresh heap object"""
        self.gaps[name] += 1
        ptrs = set()
        for r in ('eax', 'ecx', 'edx'):
            ptrs |= {t for t in regs.get(r, U) if t[0] is not None and t[0] != 'TOP'}
        for k, v in st.items():
            if esp is not None and esp <= k < esp + 64: ptrs |= {t for t in v if t[0] is not None and t[0] != 'TOP'}
        self.write(frozenset((o, None) for o, c in ptrs), TOPSET, a, 'import-gap:' + name)

    def call_effect(self, g, a, regs, esp):
        img = self.img; tg = img.calltg.get(a)
        o = g.operands[0]
        written = {'eax', 'ecx', 'edx'}
        if o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and (o.mem.disp & M32) in self.imports:
            regs['eax'] = frozenset([(('H', a), 0), UNK]); regs['ecx'] = U; regs['edx'] = U
            pop = None
            return None if esp is None else esp          # stdcall pop of an import is its contract's; unknown here: the esp stays (see report)
        ts = [tg] if tg in img.entries else sorted(self.escset)
        outs = [self.out.get(t) for t in ts]
        for r in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp'):
            v = None
            for ou in outs:
                if ou is not None: v = join(v, ou.get(r))
            regs[r] = v if v is not None else U
        pops = {self.summ[t][1]['pop'] if t in self.summ else None for t in ts} if len(ts) < 64 else {None}
        if esp is not None and len(pops) == 1 and None not in pops: return esp + next(iter(pops))
        return None

    # ---- driver
    def run(self):
        img = self.img; ents = [e for e in sorted(img.entries) if pcsrc.owner(img, e)]
        self.pop = {}; passes = 0
        for e in ents:
            if not img.entries[e] - {'tail', 'inner', 'eh-unwind', 'eh-catch', 'eh-cont', 'eh-throw'} or e in self.esc: self.inp.setdefault(e, ({k: U for k in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp')}, {}))
        while True:
            self.changed = False; self.stores = []; self.gaps = Counter(); self.int_stores = 0; passes += 1
            for e in ents:
                if e in self.inp: self.analyse(e)
            if not self.changed: break
        self.passes = passes
        return self

    # ---- results
    def reach(self, lo, hi):
        """the stores whose address set can contain a byte of [lo, hi): exact (constant offset in range), anyoff (ANY offset of the object holding the range) and TOP (open) stores"""
        ob = self.sobj(lo); ex = []; anyo = []; top = []
        for site, kind, toks, t in self.stores:
            if t: top.append((site, kind)); continue
            for o, c in toks:
                if o == ob and c is not None and c < hi and lo < c + 4: ex.append((site, kind, c))
                elif o == ob and c is None: anyo.append((site, kind))
        return {'exact': sorted(set(ex)), 'anyoff': sorted(set(anyo)), 'top': sorted(set(top))}

    def report(self):
        c = Counter()
        for site, kind, toks, t in self.stores:
            c['top' if t else 'static-any' if any(o is not None and o[0] == 'S' and cc is None for o, cc in toks) else 'static' if any(o is not None and o[0] == 'S' for o, cc in toks) else 'frame/heap' if toks else 'none'] += 1
        return dict(stores=len(self.stores), by_class=dict(c), int_stores=self.int_stores, import_gaps=dict(self.gaps), passes=self.passes)


def load_image(pe, facts):
    """-> (pcsrc.Img with its summaries, M): the PE image `pe` (the stored file, preferred base) with the facts' universe.tsv / ranges.tsv; the driver of the dataflow"""
    import pemod
    nb = None
    for l in open(os.path.join(facts, 'universe.tsv')):
        if l.startswith('#') and 'base=' in l: nb = int(l.split('base=')[1].split(':')[0].split()[0], 16); break
    M = pemod.load(pe, nb)             # the facts speak the load base of the image (modlift); the exe's is its preferred base
    nm, va, sz, ro = M.xs[0]; text = M.d[ro:ro + sz]
    ent, rg = {}, []
    for l in open(os.path.join(facts, 'universe.tsv')):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); ent[int(f[0], 16)] = set(f[1].split(','))
    for l in open(os.path.join(facts, 'ranges.tsv')):
        if l[0] == '#' or not l.strip(): continue
        for r in l.rstrip('\n').split('\t')[1].split(','):
            if '-' in r: a, b = r.split('-'); rg.append((int(a, 16), int(b, 16)))
    img = pcsrc.Img(bytes(text), va, ent, rg, M.u32, set(pemod.relocs(M) or ()))
    pcsrc.analyse(img, ent, {})
    return img, M


def main(argv):
    import json, time
    if len(argv) != 2: print('usage: pointsto.py IMAGE FACTS_DIR', file=sys.stderr); return 2
    t0 = time.time(); img, M = load_image(argv[0], argv[1]); t1 = time.time()
    import pemod
    imp = pemod.imports(M)
    pt = PT(img, M, img.summ, imp).run()
    r = pt.report(); r['seconds_pcsrc'] = round(t1 - t0, 1); r['seconds_pointsto'] = round(time.time() - t1, 1)
    print(json.dumps(r, sort_keys=True)); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
