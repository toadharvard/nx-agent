"""memfx - may-write analysis of an x86 PE image (L1 CONTRACT): which writes can reach a memory location?

    mx = Memfx(img, imports=ImportInfo(...))          img: argflow.Image
    mx.const_range(lo, hi)        -> Verdict            no write in the process changes [lo, hi) after load (case: global constancy)
    mx.call_keeps(fn, call_va, R) -> (bool, why)        the call at call_va does not write [R+d] of the object R points to
    mx.words(addr)                -> (leaves, writers, why)   value set of the global dword at addr (initial bytes + every writer)

The question and the rule.  A write modifies location L only through (W1) an instruction that names L (an absolute operand
[disp] or [disp+idx*s] whose address range covers L), or (W2) an instruction whose address register holds a pointer value.
Rule (P, provenance - the one memory-model premise of this file): a pointer value is built from an address constant of
its object (a relocated immediate / lea of a section address, the address of a stack frame) by copies, constant offsets and
arithmetic, and a memory access through it stays inside the object that address belongs to.  Object = one image section
(a global), or the frame of one function activation (a frame slot).  Under P the writers of L through pointers are exactly
the stores through values of the forward flow of the address constants of L's object, so the whole question is a taint
closure (class Closure) of that object's address values over the image's direct call graph:
  * state: registers, dword stack slots relative to the entry esp, the numeric esp/ebp; a tainted value is a set of
    absolute addresses (section objects), the marker 'F' (frame) and ANY (unknown offset: arithmetic, an index);
  * every instruction not modelled that reads a tainted value is an ESCAPE (the closure is then open); a tainted value
    stored to memory that is not a tracked slot, returned in eax/edx to a caller the analysis cannot see (address-taken
    function), passed to an indirect call, passed to an import whose escape letter is not n/r, left in a callee-saved
    register at ret, or stored into a slot of a function whose frame address is taken (class exposed) is an ESCAPE;
  * a store through a tainted base is a WRITE event (address set, disp, size); a call that receives a tainted register or
    stack slot (direct callee: the closure continues in the callee with that state; import: pointer arguments) is a CALL
    event; an import receives tainted data = it may write through it (ImportInfo.may_write, default yes).
The location L of an object is not written by a call c iff the object's closure has no ESCAPE and c is not a CALL event
(it receives no value derived from the object's address; it cannot get one except through memory, and a tainted value
reaches memory only through an ESCAPE).  For a global, additionally (W1) no direct store anywhere in the image covers L
(exact for [disp]; [disp+idx*s] covers [disp, disp+max*s+size) when the index maximum is proven (and-mask, movzx, constant)
and else the whole section), no relocated data word points into the section (that word is a pointer stored in memory: an
ESCAPE of the section's closure), and the loader does not write L (base relocations, import slots, IAT / TLS-index of the
PE directories).  Assumptions (stated, not proven): P; the image's code ranges are instruction-aligned and complete (A4); a
callee preserves ebx/esi/edi/ebp (A1) and reads no register of an import call (the platform's calling convention); the
platform implementation writes through a pointer argument only when ImportInfo says so (default: every pointer argument
may be written); no code outside the images (the loader, the OS) writes L except through those imports.
"""
import bisect
import struct

import capstone
from capstone import x86_const as X

import argflow
from argflow import rn

ANY = 'A'
FRAME = 'F'
SAVED = ('ebx', 'esi', 'edi', 'ebp')
ALL_REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')
MAXSET = 12
EMPTY = frozenset()


def norm_imp(dll, name):
    d = dll.lower()
    return (d if '.' in d else d + '.dll', name)


class ImportInfo:
    """Per import: bytes the callee removes, bridge argument letters, escape letters, and the write facts.
    escape: one letter per argument (n not captured, r may be returned, e escapes); no row = all e.
    writes: {(dll, name): letters} one per argument, n = never written through, w = may be written; no row = all w."""

    def __init__(self, bridge_text='', escape_texts=(), writes_text=''):
        self.stack, self.letters, self.escape, self.writes = {}, {}, {}, {}
        for line in bridge_text.splitlines()[1:]:
            f = line.split('\t')
            if len(f) > 5:
                k = norm_imp(f[0], f[1])
                n = argflow.stack_bytes(f[3], f[5])
                if n is not None:
                    self.stack[k] = n
                self.letters[k] = f[5]
        for t in escape_texts:
            for line in t.splitlines():
                f = line.split('\t')
                if len(f) < 3 or line[0] == '#' or f[0] == 'dll':
                    continue
                k = norm_imp(f[0], f[1])
                old = self.escape.get(k)
                new = f[2]
                if old is not None:
                    new = ''.join('e' if 'e' in (a, b) else 'r' if 'r' in (a, b) else 'n' for a, b in zip(old.ljust(len(new), 'e'), new.ljust(len(old), 'e')))
                self.escape[k] = new
        for line in writes_text.splitlines():
            f = line.split('\t')
            if len(f) >= 3 and line[0] != '#' and f[0] != 'dll':
                self.writes[norm_imp(f[0], f[1])] = f[2]

    def esc_letter(self, k, idx):
        s = self.escape.get(k)
        if s is None or idx >= len(s):
            return 'e'
        return s[idx]

    def may_write(self, k, idx):
        s = self.writes.get(k)
        return not (s is not None and idx < len(s) and s[idx] == 'n')


class Verdict:
    def __init__(self, ok, reasons, counts):
        self.ok, self.reasons, self.counts = ok, reasons, counts

    def __repr__(self):
        return 'Verdict(%s, %s, %s)' % (self.ok, self.reasons[:6], self.counts)


def tjoin(a, b):
    if not b or a >= b:
        return a
    s = a | b
    if len(s) > MAXSET or ANY in s:
        s = frozenset([ANY]) | {x for x in s if x == FRAME}
    return s


def tshift(t, k):
    return frozenset(x + k if isinstance(x, int) else x for x in t)


def wrap(t):
    return frozenset(x & 0xffffffff if isinstance(x, int) else x for x in t)


class Scan:
    """One linear pass over the code ranges: direct stores, address values, data relocations (no instruction is kept)."""

    def __init__(self, img):
        self.img = img
        self.direct = []        # (va, lo, hi) [disp, disp+size) named by an absolute operand
        self.indexed = []       # (va, disp, scale, idxreg, size)
        self.addr_values = []   # (va, value, exact) relocated immediates and lea disp (value uses)
        self.ptr_stores = 0
        self.nins = 0
        ivs = sorted({(a, b) for a, b, e in img.flat})
        merged = []
        for a, b in ivs:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.code = merged
        cs = img.cs
        for a, b in merged:
            try:
                data = img.pe.rd(a - img.base, b - a)
            except Exception:
                continue
            for i in cs.disasm(data, a):
                self.nins += 1
                self.one(i)
        starts = [a for a, b in merged]
        self.code_cells = lambda va: self._in_code(va)
        self._starts = starts

    def _in_code(self, va):
        k = bisect.bisect_right(self._starts, va) - 1
        return k >= 0 and va < self.code[k][1]

    def one(self, i):
        img = self.img
        m = i.mnemonic
        end4 = i.address + i.size - 4
        for op in i.operands:
            if op.type == X.X86_OP_IMM and end4 in img.relocs and m not in ('call', 'jmp') and not m.startswith('j'):
                self.addr_values.append((i.address, op.imm & 0xffffffff, True))
            elif op.type == X.X86_OP_MEM:
                mm = op.mem
                writes = bool(op.access & 2)
                if m == 'lea':
                    if mm.disp and not mm.base and not mm.index and (i.address + i.size - 4) in img.relocs:
                        self.addr_values.append((i.address, mm.disp & 0xffffffff, True))
                    elif mm.disp and (mm.base or mm.index) and self.disp_reloc(i):
                        self.addr_values.append((i.address, mm.disp & 0xffffffff, False))
                    continue
                if not writes:
                    continue
                if mm.segment in (X.X86_REG_FS, X.X86_REG_GS):
                    continue
                if not mm.base and not mm.index:
                    d = mm.disp & 0xffffffff
                    self.direct.append((i.address, d, d + op.size))
                elif not mm.base and mm.index:
                    self.indexed.append((i.address, mm.disp & 0xffffffff, mm.scale, rn(i, mm.index), op.size))
                elif rn(i, mm.base) != 'esp':
                    self.ptr_stores += 1

    def disp_reloc(self, i):
        """The displacement of a memory operand is a relocated field of the instruction (searched over its bytes)."""
        return any(r in self.img.relocs for r in range(i.address + 1, i.address + i.size - 3))


UNSIGNED_JCC = {'ja': ('f', 0), 'jnbe': ('f', 0), 'jbe': ('t', 0), 'jna': ('t', 0),
                'jae': ('f', -1), 'jnb': ('f', -1), 'jnc': ('f', -1), 'jb': ('t', -1), 'jnae': ('t', -1), 'jc': ('t', -1)}


def edge_bound(fn, p, at, reg):
    """The bound on `reg` that the edge p -> at gives: p is an unsigned conditional jump whose only predecessor is `cmp reg, imm`
    (the flags of every path to p come from that compare), and the edge is the one on which the compare's unsigned relation holds
    (jbe taken / ja not taken: reg <= imm; jb taken / jae not taken: reg <= imm - 1).  None if the edge gives no bound."""
    i = fn.nodes[p]
    ent = UNSIGNED_JCC.get(i.mnemonic)
    if ent is None or i.operands[0].type != X.X86_OP_IMM:
        return None
    taken = i.operands[0].imm
    fall = p + i.size
    if taken == fall or at not in (taken, fall):
        return None
    cps = fn.pred[p]
    if len(cps) != 1:
        return None
    c = fn.nodes[next(iter(cps))]
    ops = c.operands
    if c.mnemonic != 'cmp' or ops[0].type != X.X86_OP_REG or ops[0].size != 4 or rn(c, ops[0].reg) != reg \
            or ops[1].type != X.X86_OP_IMM or (c.address + c.size - 4) in fn.img.relocs or ops[1].imm < 0:
        return None
    edge, adj = ent
    if (edge == 't') != (at == taken):
        return None
    v = ops[1].imm + adj
    return v if v >= 0 else None


def umax(fn, at, reg, seen=None):
    """Largest unsigned value `reg` can hold before the instruction at `at` over all paths of fn, or None (unbounded)."""
    seen = seen if seen is not None else set()
    if (at, reg) in seen:
        return 0
    seen.add((at, reg))
    preds = fn.pred[at]
    if not preds:
        return None
    best = 0
    for p in sorted(preds):
        i = fn.nodes[p]
        r, w = i.regs_access()
        if reg not in {rn(i, x) for x in w}:
            if i.mnemonic == 'call' and reg in argflow.CALLER_SAVED:
                return None
            v = umax(fn, p, reg, seen)
            eb = edge_bound(fn, p, at, reg)
            if eb is not None:
                v = eb if v is None else min(v, eb)
        else:
            ops = i.operands
            m = i.mnemonic
            v = None
            if m == 'mov' and ops[0].size == 4 and rn(i, ops[0].reg) == reg:
                if ops[1].type == X.X86_OP_IMM and (p + i.size - 4) not in fn.img.relocs and ops[1].imm >= 0:
                    v = ops[1].imm
                elif ops[1].type == X.X86_OP_REG and ops[1].size == 4:
                    v = umax(fn, p, rn(i, ops[1].reg), seen)
            elif m == 'xor' and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
                v = 0
            elif m == 'and' and ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) == reg and ops[0].size == 4 \
                    and ops[1].type == X.X86_OP_IMM and 0 <= ops[1].imm:
                v = ops[1].imm
            elif m == 'movzx' and rn(i, ops[0].reg) == reg:
                v = {1: 0xff, 2: 0xffff}.get(ops[1].size)
            elif m == 'shr' and ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) == reg and ops[0].size == 4 \
                    and ops[1].type == X.X86_OP_IMM and 0 < ops[1].imm < 32:
                v = 0xffffffff >> ops[1].imm
        if v is None:
            return None
        best = max(best, v)
    return best


class Closure:
    """Forward taint closure of the address values of one object over the image's direct call graph."""

    def __init__(self, mx, kind, lo=0, hi=0, root=None):
        self.mx, self.img, self.kind, self.lo, self.hi, self.root = mx, mx.img, kind, lo, hi, root
        self.esc, self.writes, self.calls, self.imps = {}, {}, {}, {}
        self.edges, self.fw = {}, set()       # tainted call edges per function; functions with a write event
        self.entry, self.ret, self.callers_of, self.done = {}, {}, {}, set()
        self.work = []
        self.nfuncs = 0
        self.run()

    # ---- bookkeeping
    def writes_at(self, va):
        """Can the call at va write through a value derived from the object?  (tainted state passed: the callees' closure)"""
        ts = self.calls.get(va, 0)
        if ts is None:
            return True
        if any(k[0] == va and mw for k, (mw, esc) in self.imps.items()):
            return True
        seen, work = set(), list(ts or ())
        while work:
            f = work.pop()
            if f in seen:
                continue
            seen.add(f)
            if f in self.fw:
                return True
            work += list(self.edges.get(f, ()))
        return False

    def e(self, va, why):
        self.esc.setdefault((va, why), True)

    def queue(self, entry):
        if entry not in self.work:
            self.work.append(entry)

    def join_entry(self, entry, st):
        old = self.entry.get(entry)
        if old is None:
            self.entry[entry] = dict(st)
            self.queue(entry)
            return
        ch = False
        for k, v in st.items():
            o = old.get(k, EMPTY)
            n = tjoin(o, v)
            if n != o:
                old[k] = n
                ch = True
        if ch:
            self.queue(entry)

    def run(self):
        img = self.img
        self.partial = False
        if self.kind == 'sec':
            for r in img.relocs:           # a relocated data word that is a pointer into the object sits in memory
                v = img.word(r)
                if self.lo <= v < self.hi and not self.mx.scan._in_code(r):
                    self.e(r, 'data word %x holds the address %x' % (r, v))
            if self.esc:                   # the closure is open for every question: its events are not needed (not computed)
                self.partial = True
                return
            seeds = set()
            for va, v, exact in self.mx.scan.addr_values:
                if self.lo <= v < self.hi:
                    for e in argflow.entries_for(img, va):
                        seeds.add(e)
            for e in sorted(seeds):
                self.join_entry(e, {'sp': 0})
        else:
            self.join_entry(self.root, {'sp': 0})
        while self.work:
            e = self.work.pop()
            self.analyze(e)
            if self.esc:                   # open for every question (an escaped address may be written by anything)
                self.partial = True
                break
        self.nfuncs = len(self.entry)

    # ---- one function
    def analyze(self, entry):
        img = self.img
        if entry not in img.ranges:
            self.e(entry, 'function %x has no facts' % entry)
            return
        fn = img.func(entry)
        exposed = self.mx.exposed(fn)
        est = self.entry[entry]
        ins = {entry: dict(est)}
        work = [entry]
        retv = {'eax': EMPTY, 'edx': EMPTY}
        while work:
            v = work.pop()
            st = ins[v]
            i = fn.nodes[v]
            out, rets = self.step(fn, i, dict(st), exposed, est)
            for k in retv:
                retv[k] = tjoin(retv[k], rets.get(k, EMPTY))
            if out is None:
                continue
            for s in fn.succ[v]:
                cur = ins.get(s)
                if cur is None:
                    ins[s] = dict(out)
                    work.append(s)
                else:
                    ch = False
                    for k in sorted(set(cur) | set(out), key=str):
                        if k in ('sp', 'bp'):
                            if cur.get(k) != out.get(k):
                                if cur.get(k) is not None:
                                    cur[k] = None
                                    ch = True
                        else:
                            n = tjoin(cur.get(k, EMPTY), out.get(k, EMPTY))
                            if n != cur.get(k, EMPTY):
                                cur[k] = n
                                ch = True
                    if ch:
                        work.append(s)
        old = self.ret.get(entry, {'eax': EMPTY, 'edx': EMPTY})
        new = {k: tjoin(old[k], retv[k]) for k in retv}
        if new != old:
            self.ret[entry] = new
            if new['eax'] or new['edx']:
                self.returned(entry)
            for c in sorted(self.callers_of.get(entry, ())):
                self.queue(c)
        else:
            self.ret.setdefault(entry, new)

    def returned(self, entry):
        """A function returns a tainted value: every direct caller must see it."""
        img = self.img
        if self.kind == 'frame' and entry == self.root:
            self.e(entry, 'the frame address is returned')
            return
        if img.address_taken(entry):
            self.e(entry, 'tainted return of address-taken function %x' % entry)
            return
        cs = img.callers(entry)
        if not cs:
            self.e(entry, 'tainted return of %x with no direct caller' % entry)
        for site, how in cs:
            for e in argflow.entries_for(img, site):
                self.callers_of.setdefault(entry, set()).add(e)
                if e not in self.entry:
                    self.join_entry(e, {'sp': 0})
                else:
                    self.queue(e)

    # ---- values
    def slot_ok(self, fn, exposed, t):
        return (not exposed) or (self.kind == 'frame' and fn.entry == self.root and t <= {FRAME})

    def slot_of(self, st, i, m):
        if m.index or not m.base or m.segment in (X.X86_REG_FS, X.X86_REG_GS):
            return None
        b = rn(i, m.base)
        if b == 'esp' and st.get('sp') is not None:
            return st['sp'] + m.disp
        if b == 'ebp' and st.get('bp') is not None:
            return st['bp'] + m.disp
        return None

    def is_gen_frame(self, fn):
        return self.kind == 'frame' and fn.entry == self.root

    def tv(self, fn, st, i, op):
        """Taint of the value a read operand yields."""
        t = op.type
        if t == X.X86_OP_REG:
            r = rn(i, op.reg)
            if r == 'esp' or (r == 'ebp' and st.get('bp') is not None):
                return frozenset([FRAME]) if self.is_gen_frame(fn) else EMPTY
            v = st.get(r, EMPTY)
            if v and op.size < 4:
                return frozenset([ANY])
            return v
        if t == X.X86_OP_IMM:
            if self.kind == 'sec' and (i.address + i.size - 4) in self.img.relocs and self.lo <= (op.imm & 0xffffffff) < self.hi:
                return frozenset([op.imm & 0xffffffff])
            return EMPTY
        if t == X.X86_OP_MEM:
            n = self.slot_of(st, i, op.mem)
            if n is None:
                return EMPTY
            if op.size == 4 and n in st:
                return st[n]
            if any(k in st for k in (n - 3, n - 2, n - 1, n, n + 1, n + 2, n + 3) if isinstance(k, int) and st.get(k)):
                return frozenset([ANY])
        return EMPTY

    def addr_taint(self, fn, st, i, m):
        """Taint of the address a memory operand computes (None when untainted)."""
        base = rn(i, m.base) if m.base else None
        idx = rn(i, m.index) if m.index else None
        out = EMPTY
        if base == 'esp' or (base == 'ebp' and st.get('bp') is not None):
            if self.is_gen_frame(fn):
                out = frozenset([FRAME])
        elif base:
            out = st.get(base, EMPTY)
            if out:
                out = tshift(out, m.disp)
        if idx:
            if st.get(idx):
                out = frozenset([ANY])
            elif out:
                out = frozenset([ANY]) | {x for x in out if x == FRAME}
        if not base and not idx and self.kind == 'sec' and (i.address + i.size - 4) in self.img.relocs:
            d = m.disp & 0xffffffff
            if self.lo <= d < self.hi:
                out = frozenset([d])
        elif (idx or base) and self.kind == 'sec' and m.disp and self.mx.scan.disp_reloc(i) and self.lo <= (m.disp & 0xffffffff) < self.hi:
            out = frozenset([ANY])
        return wrap(out)

    # ---- writes
    def write_mem(self, fn, st, i, op, val, exposed, size=None, rep=False):
        m = op.mem
        if m.segment in (X.X86_REG_FS, X.X86_REG_GS):
            if val:
                self.e(i.address, 'tainted value stored through a segment')
            return
        n = self.slot_of(st, i, m)
        size = size or op.size
        if n is not None:
            for k in range(n - 3, n + size):
                st.pop(k, None) if k != n or not (size == 4 and val) else None
            if val:
                if size == 4 and self.slot_ok(fn, exposed, val):
                    st[n] = val
                else:
                    self.e(i.address, 'tainted value stored into a slot of an exposed function' if size == 4 else 'partial store of a tainted value')
            return
        ta = self.addr_taint(fn, st, i, m)
        if ta:
            offs = ta
            if rep:
                offs = frozenset([ANY]) | {x for x in ta if x == FRAME}
            self.writes[(i.address, offs, m.disp, size)] = True
            self.fw.add(fn.entry)
        if val:
            self.e(i.address, 'tainted value stored to memory (%s %s)' % (i.mnemonic, i.op_str))

    def clear_reg(self, st, r):
        st.pop(r, None)

    # ---- the transfer function; returns (state or None, {'eax', 'edx'} at ret)
    def step(self, fn, i, st, exposed, est):
        img = self.img
        m = i.mnemonic
        ops = i.operands
        va = i.address
        rets = {}
        if m == 'ret':
            for k in ('eax', 'edx'):
                if st.get(k):
                    rets[k] = st[k]
            for r in SAVED:
                if not st.get(r, EMPTY) <= est.get(r, EMPTY):
                    self.e(va, 'tainted %s at ret' % r)
            return None, rets
        if m == 'jmp' and ops[0].type != X.X86_OP_IMM:
            if ops[0].type == X.X86_OP_MEM and not ops[0].mem.base and not ops[0].mem.index and ops[0].mem.disp in img.imports:
                return self.call(fn, i, st, True)
            if ops[0].type == X.X86_OP_REG and fn.succ[va]:
                return st, rets          # switch jump: the CFG edges are the relocated table words (A4)
            if fn.succ[va]:
                return st, rets
            return self.call(fn, i, st, True)
        if m == 'jmp' and ops[0].imm not in fn.nodes:
            return self.call(fn, i, st, True)
        if m == 'call':
            return self.call(fn, i, st, False)
        if m.startswith('j') or m in ('nop', 'int3', 'ud2', 'hlt', 'cmp', 'test', 'fnop', 'wait') or m.startswith('set'):
            if m.startswith('set') and ops[0].type == X.X86_OP_REG:
                pass
            if m.startswith('set') or m in ('cmp', 'test'):
                for o in ops:
                    if o.type == X.X86_OP_MEM:
                        self.addr_read_check(fn, st, i, o)
            return st, rets
        sp = st.get('sp')
        tot = [self.tv(fn, st, i, o) for o in ops]
        if m == 'push' and ops[0].size == 4:
            if sp is None:
                if tot[0]:
                    self.e(va, 'push with an unknown stack pointer')
                return st, rets
            st['sp'] = sp - 4
            self.put_slot(fn, st, i, sp - 4, tot[0], exposed)
            return st, rets
        if m == 'pop' and ops[0].size == 4:
            if sp is None:
                if ops[0].type == X.X86_OP_REG:
                    self.clear_reg(st, rn(i, ops[0].reg))
                return st, rets
            v = st.get(sp, EMPTY)
            st['sp'] = sp + 4
            st.pop(sp, None)
            if ops[0].type == X.X86_OP_REG:
                r = rn(i, ops[0].reg)
                if r == 'ebp':
                    st['bp'] = None
                self.set_reg(st, r, v)
            else:
                self.write_mem(fn, st, i, ops[0], v, exposed)
            return st, rets
        if m in ('pushal', 'pushfd', 'popfd', 'popal'):
            if sp is None:
                return st, rets
            if m in ('pushfd', 'popfd'):
                st['sp'] = sp + (-4 if m == 'pushfd' else 4)
                if m == 'popfd':
                    st.pop(sp, None)
                return st, rets
            order = ('eax', 'ecx', 'edx', 'ebx', None, 'ebp', 'esi', 'edi')
            if m == 'pushal':
                for k, r in enumerate(order):
                    n = sp - 4 * (k + 1)
                    st.pop(n, None)
                    v = st.get(r, EMPTY) if r else EMPTY
                    if v:
                        if self.slot_ok(fn, exposed, v):
                            st[n] = v
                        else:
                            self.e(va, 'tainted value pushed in an exposed function')
                st['sp'] = sp - 32
            else:
                for k, r in enumerate(order):
                    n = sp + 4 * (7 - k)
                    if r:
                        self.set_reg(st, r, st.get(n, EMPTY))
                    st.pop(n, None)
                st['sp'] = sp + 32
            return st, rets
        if m == 'leave':
            bp = st.get('bp')
            if bp is None:
                st['sp'] = None
                return st, rets
            v = st.get(bp, EMPTY)
            for k in [k for k in st if isinstance(k, int) and k <= bp + 3]:
                if k < bp:
                    st.pop(k, None)
            st.pop(bp, None)
            st['sp'] = bp + 4
            st['bp'] = None
            self.set_reg(st, 'ebp', v)
            return st, rets
        if m == 'mov' and ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) == 'esp':
            if ops[1].type == X.X86_OP_REG and rn(i, ops[1].reg) == 'ebp' and st.get('bp') is not None:
                st['sp'] = st['bp']
            else:
                st['sp'] = None
            return st, rets
        if m == 'mov' and ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) == 'ebp' and ops[1].type == X.X86_OP_REG \
                and rn(i, ops[1].reg) == 'esp' and ops[0].size == 4:
            st['bp'] = sp
            st.pop('ebp', None)
            return st, rets
        if m == 'mov' and len(ops) == 2:
            v = tot[1]
            if ops[1].size < 4 and v:
                v = frozenset([ANY])
            if ops[0].type == X.X86_OP_REG:
                self.set_reg(st, rn(i, ops[0].reg), v, ops[0].size)
                if rn(i, ops[0].reg) == 'ebp':
                    st['bp'] = None
            else:
                self.write_mem(fn, st, i, ops[0], v, exposed)
            return st, rets
        if m == 'lea' and ops[0].type == X.X86_OP_REG:
            self.set_reg(st, rn(i, ops[0].reg), self.addr_taint(fn, st, i, ops[1].mem), ops[0].size)
            return st, rets
        if m == 'xchg' and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].size == 4:
            a, b = rn(i, ops[0].reg), rn(i, ops[1].reg)
            va_, vb = st.get(a, EMPTY), st.get(b, EMPTY)
            self.set_reg(st, a, vb)
            self.set_reg(st, b, va_)
            return st, rets
        if m in ('add', 'sub', 'inc', 'dec', 'and', 'or', 'xor', 'adc', 'sbb', 'neg', 'not', 'imul', 'shl', 'shr', 'sar', 'sal',
                 'rol', 'ror', 'bts', 'btr', 'btc'):
            d = ops[0]
            if d.type == X.X86_OP_REG and rn(i, d.reg) == 'esp':
                dl = argflow.esp_delta(img, i)
                st['sp'] = sp + dl if (sp is not None and dl is not None) else None
                return st, rets
            if m in ('xor', 'sub') and len(ops) == 2 and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
                nv = EMPTY
            else:
                anyt = any(tot)
                nv = EMPTY
                if anyt:
                    if m in ('add', 'sub', 'inc', 'dec') and tot[0] and (len(ops) == 1 or ops[1].type == X.X86_OP_IMM) and d.size == 4:
                        k = 1 if m == 'inc' else -1 if m == 'dec' else (ops[1].imm if m == 'add' else -ops[1].imm)
                        nv = wrap(tshift(tot[0], k))
                    else:
                        nv = frozenset([ANY]) | {x for t in tot for x in t if x == FRAME}
            if d.type == X.X86_OP_REG:
                self.set_reg(st, rn(i, d.reg), nv, d.size)
                if rn(i, d.reg) == 'ebp':
                    st['bp'] = None
            else:
                self.write_mem(fn, st, i, d, nv, exposed)
            return st, rets
        # default: unmodelled
        reads = EMPTY
        for o, t in zip(ops, tot):
            if t and (o.access & 1 or not o.access):
                reads = reads | t
        if reads:
            self.e(va, 'unmodelled use of a tainted value (%s %s)' % (m, i.op_str))
        rep = m.startswith('rep') or m.startswith('movs') or m.startswith('stos')
        for o in ops:
            if o.type == X.X86_OP_MEM and (o.access & 2):
                self.write_mem(fn, st, i, o, EMPTY, exposed, rep=rep or None)
            elif o.type == X.X86_OP_MEM and rep and (o.access & 1):
                self.addr_read_check(fn, st, i, o)
        r, w = i.regs_access()
        for x in w:
            n = rn(i, x)
            if n == 'esp':
                st['sp'] = None
            elif n == 'ebp':
                st['bp'] = None
                st.pop('ebp', None)
            elif n in ALL_REGS:
                st.pop(n, None)
        if st.get('sp') is not None and any(k for k in st if isinstance(k, int) and st[k]) and rep:
            self.e(va, 'string instruction while a tainted slot is live')
        return st, rets

    def addr_read_check(self, fn, st, i, o):
        pass

    def put_slot(self, fn, st, i, n, val, exposed):
        for k in range(n - 3, n + 4):
            if k != n:
                st.pop(k, None)
        if val:
            if self.slot_ok(fn, exposed, val):
                st[n] = val
            else:
                self.e(i.address, 'tainted value pushed in an exposed function')
        else:
            st.pop(n, None)

    def set_reg(self, st, r, v, size=4):
        if r not in ALL_REGS:
            return
        if size < 4:
            if v:
                st[r] = tjoin(st.get(r, EMPTY), frozenset([ANY]))
            return
        if v:
            st[r] = v
        else:
            st.pop(r, None)

    # ---- calls
    def call(self, fn, i, st, tail):
        img = self.img
        va = i.address
        c = argflow.callee(img, va)
        sp = st.get('sp')
        regs = {r: st[r] for r in ALL_REGS if st.get(r)}
        slots = {k: v for k, v in st.items() if isinstance(k, int) and v and (sp is None or k >= sp)}
        anyt = bool(regs) or bool(slots)
        rets = {}
        out = dict(st)
        for k in [k for k in out if isinstance(k, int) and sp is not None and k >= sp and k < sp + 0x10000 and False]:
            pass
        if c[0] == 'import':
            key = norm_imp(c[1], c[2])
            nb = self.mx.imports.stack.get(key)
            letters = self.mx.imports.letters.get(key, '')
            eaxt = EMPTY
            for n, val in sorted(slots.items()):
                if sp is None:
                    break
                idx = (n - sp) // 4
                if nb is not None and n - sp >= nb:
                    continue
                esc = self.mx.imports.esc_letter(key, idx)
                mw = self.mx.imports.may_write(key, idx)
                self.imps[(va, key, idx)] = (mw, esc)
                if mw:
                    self.fw.add(fn.entry)
                if esc == 'e':
                    self.e(va, 'tainted argument %d of import %s!%s (platform escape letter %s)' % (idx, key[0], key[1], esc))
                elif esc == 'r':
                    eaxt = frozenset([ANY])
            if sp is None and anyt:
                self.e(va, 'import call with an unknown stack pointer')
            if nb is None:
                if slots:
                    self.e(va, 'import %s!%s: stack effect unknown' % key)
                out['sp'] = None
            elif sp is not None:
                for k in [k for k in out if isinstance(k, int) and sp <= k < sp + nb]:
                    out.pop(k)
                out['sp'] = sp + nb
            if tail:
                return None, {'eax': eaxt}
            for r in ('ecx', 'edx'):
                out.pop(r, None)
            self.set_reg(out, 'eax', eaxt)
            return out, rets
        if c[0] == 'direct' and c[1] in img.ranges:
            tgt = c[1]
            if anyt:
                self.calls.setdefault(va, set()).add(tgt)
                self.edges.setdefault(fn.entry, set()).add(tgt)
                if sp is None:
                    self.e(va, 'direct call with an unknown stack pointer')
                else:
                    est = dict(regs)
                    for n, val in slots.items():
                        est[n - sp + 4] = val
                    est['sp'] = 0
                    self.callers_of.setdefault(tgt, set()).add(fn.entry)
                    self.join_entry(tgt, est)
            else:
                self.callers_of.setdefault(tgt, set()).add(fn.entry)
                if tgt in self.entry:
                    pass
            clean = img.callee_clean(tgt)
            rt = self.ret.get(tgt, {'eax': EMPTY, 'edx': EMPTY})
            if tail:
                return None, {'eax': rt['eax'], 'edx': rt['edx']}
            if clean is None or sp is None:
                if slots:
                    self.e(va, 'callee %x: stack effect unknown' % tgt)
                out['sp'] = None
            else:
                for k in [k for k in out if isinstance(k, int) and sp <= k < sp + clean]:
                    out.pop(k)
                out['sp'] = sp + clean
            out.pop('ecx', None)
            out.pop('edx', None)
            self.set_reg(out, 'eax', rt['eax'])
            self.set_reg(out, 'edx', rt['edx'])
            return out, rets
        if anyt:
            self.e(va, 'tainted state at a call through %s' % (c[1] if c[0] == 'unknown' else c,))
            self.calls[va] = None
        if tail:
            return None, {}
        out['sp'] = None
        for r in ('eax', 'ecx', 'edx'):
            out.pop(r, None)
        return out, rets


def frame_exposed(fn):
    """The address of the frame of fn is taken somewhere in fn (see Memfx.exposed)."""
    mx = fn.img.mx
    if mx is None:
        mx = Memfx.__new__(Memfx)
        mx._exposed = {}
    return mx.exposed(fn)


class Memfx:
    def __init__(self, img, imports=None):
        self.img = img
        self.imports = imports or ImportInfo()
        img.mx = self
        self._scan = None
        self._clos = {}
        self._exposed = {}
        self._const = {}
        self.busy = set()
        self.in_analysis = False

    @property
    def scan(self):
        if self._scan is None:
            self._scan = Scan(self.img)
        return self._scan

    # ---- frame address taken?
    def exposed(self, fn):
        r = self._exposed.get(fn.entry)
        if r is None:
            r = False
            fb = ('esp', 'ebp') if fn.ebp_frame() else ('esp',)
            nodes = sorted(fn.nodes.items())
            for k, (v, i) in enumerate(nodes):
                m = i.mnemonic
                ops = i.operands
                if m == 'lea' and ops[1].type == X.X86_OP_MEM:
                    b = rn(i, ops[1].mem.base) if ops[1].mem.base else None
                    x = rn(i, ops[1].mem.index) if ops[1].mem.index else None
                    if b in fb or x in fb:
                        r = True
                        break
                    continue
                if m == 'mov' and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG \
                        and rn(i, ops[0].reg) in fb and rn(i, ops[1].reg) in fb:
                    continue
                if m == 'push' and ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) == 'ebp':
                    nxt = fn.nodes.get(v + i.size)
                    if nxt is not None and nxt.mnemonic == 'mov' and nxt.op_str == 'ebp, esp':
                        continue
                if m in ('cmp', 'test') or m.startswith('j'):
                    continue
                for k2, o in enumerate(ops):
                    if o.type == X.X86_OP_REG and rn(i, o.reg) in fb and (o.access & 1 or m in ('push',)) \
                            and not (k2 == 0 and m in ('add', 'sub', 'and') and o.access & 2 and rn(i, o.reg) == 'esp'):
                        r = True
                if r:
                    break
            self._exposed[fn.entry] = r
        return r

    def closure(self, kind, lo=0, hi=0, root=None):
        key = (kind, lo, hi, root)
        c = self._clos.get(key)
        if c is None:
            was, self.in_analysis = self.in_analysis, True
            try:
                c = self._clos[key] = Closure(self, kind, lo, hi, root)
            finally:
                self.in_analysis = was
        return c

    def section_range(self, va):
        s = self.img.sec_of(va)
        if not s:
            return None
        lo = self.img.base + s[0]
        return lo, lo + max(s[1], s[3])

    # ---- loader writes
    def loader_writes(self, lo, hi):
        img = self.img
        out = []
        for r in img.relocs:
            if r < hi and r + 4 > lo:
                out.append('base relocation applied to %x' % r)
        for slot in img.imports:
            if slot < hi and slot + 4 > lo:
                out.append('import slot %x' % slot)
        pe = img.pe
        for k, (rva, size) in enumerate(pe.dirs):
            if size and k not in (5,) and img.base + rva < hi and img.base + rva + size > lo:
                out.append('PE directory %d [%x,%x)' % (k, img.base + rva, img.base + rva + size))
        if len(pe.dirs) > 9 and pe.dirs[9][1]:
            try:
                t = struct.unpack('<IIIIII', pe.rd(pe.dirs[9][0], 24))
                if lo <= t[2] < hi:
                    out.append('TLS index %x' % t[2])
            except Exception:
                out.append('TLS directory unreadable')
        return out

    def direct_writers(self, lo, hi):
        """-> ([(va, why)], n_indexed_considered)"""
        sc = self.scan
        out = []
        for va, a, b in sc.direct:
            if a < hi and b > lo:
                out.append((va, 'direct store [%x,%x)' % (a, b)))
        sec = self.section_range(lo)
        nidx = 0
        for va, disp, scale, idx, size in sc.indexed:
            s2 = self.section_range(disp)
            if s2 is None or s2[0] >= hi or s2[1] <= lo:
                continue
            nidx += 1
            es = argflow.entries_for(self.img, va)
            us = [umax(self.img.func(e), va, idx) for e in es]
            mxv = None if (not us or any(u is None for u in us)) else max(us)
            if mxv is None:
                out.append((va, 'indexed store [%x+idx*%d] with an unbounded index covers the section %x-%x' % (disp, scale, s2[0], s2[1])))
            elif disp < hi and disp + mxv * scale + size > lo:
                out.append((va, 'indexed store [%x+idx*%d] idx<=%d covers [%x,%x)' % (disp, scale, mxv, disp, disp + mxv * scale + size)))
        return out, nidx

    # ---- case: global constancy
    def const_range(self, lo, hi):
        key = (lo, hi)
        v = self._const.get(key)
        if v is None:
            if self.in_analysis:       # the analysis resolves callees with argflow, which asks for constant strings: not constant here
                return Verdict(False, ['asked during the closure analysis'], {})
            self.in_analysis = True
            try:
                v = self._const[key] = self._const_range(lo, hi)
            finally:
                self.in_analysis = False
        return v

    def _const_range(self, lo, hi):
        why = []
        s = self.section_range(lo)
        if s is None or self.img.sec_of(hi - 1) != self.img.sec_of(lo):
            return Verdict(False, ['range %x-%x is not inside one section' % (lo, hi)], {})
        sec = self.img.sec_of(lo)
        if not sec[4] & argflow.WRITE:
            lw = self.loader_writes(lo, hi)
            return Verdict(not lw, ['loader: ' + x for x in lw], {'readonly': 1})
        for r in self.loader_writes(lo, hi):
            why.append('loader: ' + r)
        dw, nidx = self.direct_writers(lo, hi)
        for va, w in dw:
            why.append('%x %s' % (va, w))
        cl = self.closure('sec', s[0], s[1])
        for (va, w) in sorted(cl.esc):
            why.append('%x escape: %s' % (va, w))
        for (va, offs, disp, size) in sorted(cl.writes, key=repr):
            if ANY in offs or any(isinstance(o, int) and o < hi and o + size > lo for o in offs):
                why.append('%x write through a pointer into the section (offsets %s, disp %d, size %d)' % (va, sorted(map(str, offs)), disp, size))
        for (va, key, idx), (mw, esc) in sorted(cl.imps.items()):
            if mw:
                why.append('%x import %s!%s argument %d receives a pointer into the section and may write through it' % (va, key[0], key[1], idx))
        cnt = dict(direct_stores=len(self.scan.direct), indexed_in_section=nidx, addr_values=sum(1 for a in self.scan.addr_values if s[0] <= a[1] < s[1]),
                   closure_funcs=cl.nfuncs, escapes=len(cl.esc), writes=len(cl.writes), imports=len(cl.imps), instructions=self.scan.nins)
        return Verdict(not why, why, cnt)

    def exe_exports(self):
        return self.img.pe.dirs[0][1] if self.img.pe.dirs else 0

    # ---- case: a call between a store and a load through the pointer register R
    def origins(self, fn, at, reg, depth=0, seen=None):
        """Objects the pointer in `reg` before the instruction at `at` can point into: set of ('frame', entry) |
        ('sec', addr_or_None, offset_or_None) | ('unknown', why)."""
        seen = seen if seen is not None else set()
        img = self.img
        out = set()
        work = [(at, reg, 0)]
        vis = set()
        while work:
            a, r, off = work.pop()
            if (a, r, off) in vis:
                continue
            vis.add((a, r, off))
            preds = fn.pred[a]
            if not preds:
                if r in ('ecx', 'edx'):
                    out |= self.param_origins(fn, r, off, depth, seen)
                else:
                    out.add(('unknown', 'register %s live at the entry of %x' % (r, fn.entry)))
                continue
            for p in sorted(preds):
                i = fn.nodes[p]
                ops = i.operands
                rd, wr = i.regs_access()
                if r not in {rn(i, x) for x in wr}:
                    if i.mnemonic == 'call':
                        if r in argflow.CALLER_SAVED:
                            out.add(('unknown', 'caller-saved %s clobbered by the call at %x' % (r, p)))
                            continue
                    work.append((p, r, off))
                    continue
                m = i.mnemonic
                if m == 'mov' and ops[0].size == 4 and ops[0].type == X.X86_OP_REG:
                    s = ops[1]
                    if s.type == X.X86_OP_REG:
                        work.append((p, rn(i, s.reg), off))
                    elif s.type == X.X86_OP_IMM:
                        if (p + i.size - 4) in img.relocs:
                            out.add(('sec', (s.imm & 0xffffffff) + off, 0))
                        else:
                            out.add(('unknown', 'non-relocated immediate pointer at %x' % p))
                    else:
                        out.add(('unknown', 'pointer loaded from memory at %x' % p))
                elif m == 'lea' and ops[1].type == X.X86_OP_MEM:
                    mm = ops[1].mem
                    b = rn(i, mm.base) if mm.base else None
                    if mm.index:
                        out.add(('unknown', 'indexed lea at %x' % p))
                    elif not b:
                        if (p + i.size - 4) in img.relocs:
                            out.add(('sec', (mm.disp & 0xffffffff) + off, 0))
                        else:
                            out.add(('unknown', 'lea of a non-relocated address at %x' % p))
                    elif b == 'ebp' and fn.ebp_frame():
                        out.add(('frame', fn.entry))
                    elif b == 'esp':
                        out.add(('frame', fn.entry))
                    else:
                        work.append((p, b, off + mm.disp))
                elif m == 'add' and ops[0].size == 4 and ops[1].type == X.X86_OP_IMM and rn(i, ops[0].reg) == r:
                    work.append((p, r, off + ops[1].imm))
                else:
                    out.add(('unknown', 'register %s defined by %s %s at %x' % (r, m, i.op_str, p)))
        return out

    def param_origins(self, fn, r, off, depth, seen):
        img = self.img
        e = fn.entry
        if depth > 5 or (e, r) in seen:
            return set() if (e, r) in seen else {('unknown', 'caller chain deeper than 5')}
        seen = seen | {(e, r)}
        if img.address_taken(e):
            return {('unknown', 'function %x is address-taken' % e)}
        cs = img.callers(e)
        if not cs:
            return {('unknown', 'function %x has no direct caller' % e)}
        out = set()
        for site, how in cs:
            if how != 'call':
                out.add(('unknown', 'tail jump to %x' % e))
                continue
            es = argflow.entries_for(img, site)
            if not es:
                out.add(('unknown', 'no function contains %x' % site))
            for ce in es:
                out |= self.origins(img.func(ce), site, r, depth + 1, seen)
        res = set()
        for o in out:
            if o[0] == 'sec' and o[1] is not None:
                res.add(('sec', o[1] + off, 0))
            else:
                res.add(o)
        return res

    def call_keeps(self, fn, call_va, reg):
        """Does the call at call_va leave the memory [reg+d] (reg unchanged since the store) unmodified?  -> (bool, why)"""
        orgs = self.origins(fn, call_va, reg)
        for o in sorted(orgs, key=repr):
            if o[0] == 'unknown':
                return False, 'origin of %s unknown: %s' % (reg, o[1])
        for o in sorted(orgs, key=repr):
            if o[0] == 'frame':
                cl = self.closure('frame', root=o[1])
                if cl.esc:
                    return False, 'the frame address of %x escapes: %s' % (o[1], sorted(cl.esc)[0])
                if cl.writes_at(call_va):
                    return False, 'the call receives a pointer into the frame of %x and may write through it' % o[1]
            else:
                sec = self.section_range(o[1])
                if sec is None:
                    return False, 'pointer %x outside the image' % o[1]
                cl = self.closure('sec', sec[0], sec[1])
                if cl.esc:
                    return False, 'a pointer into section %x-%x escapes: %s' % (sec[0], sec[1], sorted(cl.esc)[0])
                if cl.writes_at(call_va):
                    return False, 'the call receives a pointer into the section %x-%x and may write through it' % sec
                dw, _ = self.direct_writers(o[1], o[1] + 4)
                if dw:
                    return False, 'direct store %x covers %x' % (dw[0][0], o[1])
        return True, 'no pointer into the object reaches the call'

    # ---- case: value set of a global dword
    def words(self, addr, size=4):
        """Value set of the dword at addr: ([leaf], [writer (va, why)], [reasons it is open]).  Leaves: ('init', value) from the
        image bytes, ('store', va, reg-or-imm) for each writer store through a pointer whose value the caller derives."""
        img = self.img
        sec = img.sec_of(addr)
        if sec is None:
            return [], [], ['address %x outside the image' % addr]
        lo = addr
        hi = addr + size
        why = []
        for r in self.loader_writes(lo, hi):
            if not r.startswith('base relocation'):
                why.append('loader: ' + r)
        leaves = []
        raw = sec[3]
        off = addr - img.base - sec[0]
        leaves.append(('init', img.word(addr) if off + 4 <= raw else 0, addr in img.relocs))
        writers = []
        dw, _ = self.direct_writers(lo, hi)
        for va, w in dw:
            writers.append((va, w))
        s = self.section_range(addr)
        cl = self.closure('sec', s[0], s[1])
        for (va, w) in sorted(cl.esc):
            why.append('%x escape: %s' % (va, w))
        for (va, offs, disp, sz) in sorted(cl.writes, key=repr):
            if ANY in offs:
                why.append('%x store through a pointer with an unknown offset into the section (disp %d)' % (va, disp))
            elif any(o < hi and o + sz > lo for o in offs):
                writers.append((va, 'store through a pointer to %x, disp %d, size %d' % (sorted(offs)[0], disp, sz)))
        for (va, key, idx), (mw, esc) in sorted(cl.imps.items()):
            if mw:
                why.append('%x import %s!%s argument %d receives a pointer into the section and may write through it' % (va, key[0], key[1], idx))
        return leaves, writers, why
