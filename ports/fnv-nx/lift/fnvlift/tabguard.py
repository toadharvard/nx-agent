"""The index range of a jump-table dispatch `jmp [base + idx*4 + disp]`, proved from the function's own code (rule TAB-GUARD).

The table slots a dispatch can read are disp + 4*i for the values i that the index register can hold at the jmp.  That set is not read off
the data (a run of relocated dwords says what is in the image, not what the code can select) and not defaulted: it is the interval that a
forward interval analysis over the function's instructions proves for the index register at the jmp site, from the guards the code itself
carries (cmp/test + jcc edges, and/shr/add/sub/lea/movzx/mov transfers, a byte table read through `movzx r, byte [r2 + tab]`).

Domain: per 32-bit register, and per frame cell `dword [ebp+k]` / `dword [esp+k]` (the debug-build `cmp dword [ebp-0x14], 4` guards), an unsigned
interval [lo, hi] (absent = [0, 2^32-1]); per register a set of masks m with `value & m != 0`.  A cell is dropped by every instruction that can
write memory other than a modelled 4-byte store to a cell (any other store, push, call, string stores, ...), by a store to an overlapping or other-base
cell, and by a write to its base register; the rest of memory is never tracked.  (A thread that changes a local between the guard and its use makes
the exe index outside its own table, which is a wild access: ruled out by OWNER 6, "the exe makes no wild accesses".)
A push/pop stack of intervals models `push imm / pop reg` (the constant load of size-optimized code); any memory write other than a push, any other change of
esp, a call, an int clears it.  Join = interval hull, mask sets intersect.  Edges: fall-through, jmp/jcc targets, the in-function entries of a table jmp; every
address-taken label and every entry of the range starts at top (it is entered from outside with unknown registers); a call, int, syscall,
sysenter leaves every register at top (no calling-convention fact is used).  An instruction whose effect on a register is not modelled
sets every register it writes (capstone regs_access, implicit writes included) to top.  Loops stop by widening: a register whose interval
changed on the 6th visit of a node goes to top.  A byte read through `movzx r, byte [reg + tab]` is evaluated from the image only when the
bytes lie in a section mapped without write permission (Image.readonly); otherwise it is [0, 255].
The result at a jmp is the interval of the index register.  A site whose register is not bounded (no guard, a guard the analysis does not
model, a base register that is not one constant) raises Unsupported: there is no default and no bias.

Facts used: the decoded bytes are the code; the image's read-only sections do not change; the relocation list is the list of code-pointer slots.  Scope: one entry's
own range.  A table whose guard lies in another entry (a fragment the universe split off a function) is red until entry states are propagated across entries.
"""
from capstone import CS_AC_WRITE, x86 as X
from .core import SUB, md

M32 = 0xffffffff
S32 = 0x80000000
WIDEN = 6
MAXSLOTS = 1 << 16


class TableUnbounded(Exception):
    """a jump-table site whose index range no guard proves"""


def _reg32(name):
    t = SUB.get(name)
    return t[0] if t else None


def _pieces(cc, imm):
    """the unsigned intervals of the values v with `v cc imm` (cc after the compare of v with imm); imm unsigned 32-bit"""
    s = imm - (1 << 32) if imm >= S32 else imm

    def signed(a, b):
        if a > b:
            return []
        if a >= 0:
            return [(a, b)]
        if b < 0:
            return [(a + (1 << 32), b + (1 << 32))]
        return [(0, b), (a + (1 << 32), M32)]
    return {
        'e': [(imm, imm)],
        'ne': [(0, imm - 1)] * (imm > 0) + [(imm + 1, M32)] * (imm < M32),
        'a': [(imm + 1, M32)] if imm < M32 else [],
        'ae': [(imm, M32)],
        'b': [(0, imm - 1)] if imm > 0 else [],
        'be': [(0, imm)],
        'g': signed(s + 1, S32 - 1),
        'ge': signed(s, S32 - 1),
        'l': signed(-S32, s - 1),
        'le': signed(-S32, s),
    }[cc]


NEG = {'e': 'ne', 'ne': 'e', 'a': 'be', 'be': 'a', 'ae': 'b', 'b': 'ae', 'g': 'le', 'le': 'g', 'ge': 'l', 'l': 'ge'}
CC = {'je': 'e', 'jz': 'e', 'jne': 'ne', 'jnz': 'ne', 'ja': 'a', 'jnbe': 'a', 'jae': 'ae', 'jnb': 'ae', 'jnc': 'ae', 'jb': 'b', 'jnae': 'b', 'jc': 'b',
      'jbe': 'be', 'jna': 'be', 'jg': 'g', 'jnle': 'g', 'jge': 'ge', 'jnl': 'ge', 'jl': 'l', 'jnge': 'l', 'jle': 'le', 'jng': 'le'}


def _refine(iv, cc, imm):
    """iv (or top) intersected with {v: v cc imm}: the hull of the nonempty pieces, None when empty"""
    lo, hi = iv if iv else (0, M32)
    out = [(max(a, lo), min(b, hi)) for a, b in _pieces(cc, imm) if max(a, lo) <= min(b, hi)]
    return (min(a for a, _ in out), max(b for _, b in out)) if out else None


def _clip(iv, lo, hi):
    """iv (or top) intersected with [lo, hi]; None when empty"""
    a, b = iv if iv else (0, M32)
    a, b = max(a, lo), min(b, hi)
    return (a, b) if a <= b else None


class _St:
    """abstract state: iv[key] = (lo, hi) (absent: top); nz[reg] = frozenset of masks m with value & m != 0; al = pairs of keys known to hold equal values"""
    __slots__ = ('iv', 'nz', 'al', 'stk')

    def __init__(self, iv=None, nz=None, al=None, stk=()):
        self.iv = iv or {}
        self.nz = nz or {}
        self.al = al or frozenset()
        self.stk = stk             # the intervals of the values pushed by this function and not yet popped (top last), while esp moves only by push/pop

    def copy(self):
        return _St(dict(self.iv), dict(self.nz), self.al, self.stk)

    def kill(self, r):
        self.iv.pop(r, None)
        self.nz.pop(r, None)
        if self.al:
            self.al = frozenset(p for p in self.al if r not in p)

    def setiv(self, r, iv):
        """assign: r takes a new value"""
        self.kill(r)
        if iv is not None and iv != (0, M32):
            self.iv[r] = iv

    def link(self, a, b):
        if a != b:
            self.al = self.al | {(min(a, b), max(a, b))}

    def same(self, k):
        g, work = {k}, [k]
        while work:
            x = work.pop()
            for p in self.al:
                if x in p:
                    y = p[0] if p[1] == x else p[1]
                    if y not in g:
                        g.add(y)
                        work.append(y)
        return g

    def narrow(self, k, fn, mask=None):
        """the value of k is known to satisfy fn (an interval transformer, None = impossible) and `value & mask != 0`: apply it to k and every key equal to it; False when infeasible"""
        for m in sorted(self.same(k)):
            v = fn(self.iv.get(m))
            if v is None:
                return False
            if v == (0, M32):
                self.iv.pop(m, None)
            else:
                self.iv[m] = v
            if mask is not None and m[0] != '[':
                self.nz[m] = self.nz.get(m, frozenset()) | {mask}
        return True

    def join(self, o):
        iv = {r: (min(a[0], o.iv[r][0]), max(a[1], o.iv[r][1])) for r, a in self.iv.items() if r in o.iv}
        iv = {r: v for r, v in iv.items() if v != (0, M32)}
        nz = {r: s & o.nz[r] for r, s in self.nz.items() if r in o.nz and s & o.nz[r]}
        stk = tuple((min(a[0], b[0]), max(a[1], b[1])) for a, b in zip(self.stk, o.stk)) if len(self.stk) == len(o.stk) else ()
        return _St(iv, nz, self.al & o.al, stk)

    def key(self):
        return (tuple(sorted(self.iv.items())), tuple(sorted((r, tuple(sorted(s))) for r, s in self.nz.items())), tuple(sorted(self.al)), self.stk)


def _simm(v):
    v &= M32
    return v - (1 << 32) if v >= S32 else v


def _add(iv, k):
    """the interval iv shifted by the signed constant k; top when any value wraps"""
    lo, hi = iv if iv else (0, M32)
    return (lo + k, hi + k) if lo + k >= 0 and hi + k <= M32 else None


class Analysis:
    def __init__(self, img, insns, tables, entries):
        self.img, self.insns, self.tables = img, insns, tables
        order = sorted(insns)
        self.order = order
        preds = {a: set() for a in order}
        self.succ_static = {}
        for a in order:
            ins = insns[a]
            m = ins.mnemonic.split(' ')[-1]
            nxt = a + ins.size
            ops = ins.operands
            s = []
            if m == 'jmp':
                if ops[0].type == X.X86_OP_IMM:
                    s.append(ops[0].imm & M32)
                elif a in tables:
                    s.extend(tables[a])
            elif m in ('ret', 'retf', 'hlt', 'ud2', 'iret', 'iretd'):
                pass
            else:
                if m.startswith('j') or m in ('loop', 'loope', 'loopne'):
                    s.append(ops[0].imm & M32)
                s.append(nxt)
            self.succ_static[a] = [t for t in s if t in insns]
            for t in self.succ_static[a]:
                preds[t].add(a)
        self.preds = preds
        self.ext = set(entries) & set(insns)       # entered from outside the function: top
        self.prev = {}                              # address -> the instruction that falls into it (the only way in when the address is no jump target and no entry)
        for a in order:
            ins = insns[a]
            t = a + ins.size
            if t in insns and a in preds.get(t, ()):
                self.prev.setdefault(t, a)

    FLAGSRC_DEPTH = 32

    def _writes(self, ins):
        try:
            _, wr = ins.regs_access()
        except Exception:
            return None
        return wr

    def _flagsrc(self, a):
        """-> (src, touched, memwrite): the flag-writing instruction whose flags the jcc at a consumes, found by walking back the unique fall-through predecessors
        (an address with another predecessor or entered from outside ends the walk: None) over instructions that do not write EFLAGS; touched = the 32-bit
        registers those instructions write, memwrite = whether any of them writes memory (the compared register / cell must be unchanged to refine it)"""
        touched, memwrite, cur = set(), False, a
        for _ in range(self.FLAGSRC_DEPTH):
            p = self.prev.get(cur)
            if cur in self.ext or p is None or self.preds[cur] != {p}:
                return None
            ins = self.insns[p]
            wr = self._writes(ins)
            if wr is None:
                return None
            if X.X86_REG_EFLAGS in wr:
                return ins, touched, memwrite
            for rid in wr:
                t = _reg32(self._name(rid))
                if t:
                    touched.add(t)
            m = ins.mnemonic.split(' ')[-1]
            if any(m.startswith(q) for q in self.IMPLICIT_STORES) or any(o.type == X.X86_OP_MEM and o.access & CS_AC_WRITE for o in ins.operands):
                memwrite = True
            cur = p
        return None

    def _cell(self, op):
        """the cell key of a 4-byte `[ebp+k]` / `[esp+k]` memory operand, else None"""
        if op.type != X.X86_OP_MEM or op.size != 4:
            return None
        m = op.mem
        if m.index or m.segment or not m.base:
            return None
        b = self._name(m.base)
        return '[%s%+d' % (b, _simm(m.disp)) if b in ('ebp', 'esp') else None

    def _kill_cells(self, s, keep=None):
        for c in [c for c in s.iv if c[0] == '[' and c != keep]:
            s.kill(c)

    def _regop(self, ins, i):
        o = ins.operands
        if len(o) > i and o[i].type == X.X86_OP_REG:
            return SUB.get(ins.reg_name(o[i].reg))
        return None

    def transfer(self, a, st):
        """-> {successor: state} for the instruction at a with the in-state st"""
        ins = self.insns[a]
        m = ins.mnemonic.split(' ')[-1]
        ops = ins.operands
        out = {}
        nxt = a + ins.size
        if m in CC and len(ops) == 1 and ops[0].type == X.X86_OP_IMM:
            cc = CC[m]
            tg = ops[0].imm & M32
            src = self._flagsrc(a)
            for tgt, c in ((tg, cc), (nxt, NEG[cc])):
                if tgt not in self.insns:
                    continue
                s = st.copy()
                ok = self._edge(s, src, c)
                if ok:
                    out[tgt] = s if tgt not in out else out[tgt].join(s)
            return out
        s = st.copy()
        self._apply(ins, m, ops, s)
        for t in self.succ_static[a]:
            out[t] = s
        return out

    def _edge(self, s, src, cc):
        """refine s on the edge where the flags of src satisfy cc; False when the edge is infeasible"""
        if src is None:
            return True
        src, touched, memwrite = src
        sm = src.mnemonic.split(' ')[-1]
        o = src.operands
        r = self._regop(src, 0)
        key = r[0] if r and r[2] == 32 and r[1] == 0 else self._cell(o[0]) if o else None
        if key and (key in touched or key[0] == '[' and (memwrite or key[1:4] in touched)):
            return True                  # the compared register / cell may have changed since the flags were set
        if sm == 'cmp' and len(o) == 2 and key:
            if o[1].type == X.X86_OP_IMM:
                imm = o[1].imm & M32
            else:
                r2 = self._regop(src, 1)
                iv2 = s.iv.get(r2[0]) if r2 and r2[2] == 32 and r2[1] == 0 else None
                if iv2 is None or iv2[0] != iv2[1]:
                    return True
                imm = iv2[0]                 # the other operand is the one constant its interval holds
            return s.narrow(key, lambda iv: _refine(iv, cc, imm))
        if sm == 'sub' and len(o) == 2 and key and o[1].type == X.X86_OP_IMM and cc in ('b', 'ae', 'a', 'e', 'ne') and 0 < (o[1].imm & M32):
            k = o[1].imm & M32           # the flags of `sub r, k` are those of `cmp r_old, k`; r now holds r_old - k mod 2^32
            piece = {'b': (M32 + 1 - k, M32), 'ae': (0, M32 - k), 'a': (1, M32 - k), 'e': (0, 0)}.get(cc)
            return s.narrow(key, lambda iv: _clip(iv, *piece)) if piece else True
        if sm == 'test' and len(o) == 2 and cc in ('e', 'ne') and r and r[2] == 32 and r[1] == 0:
            if o[1].type == X.X86_OP_REG and src.reg_name(o[1].reg) == src.reg_name(o[0].reg):
                mask = M32
            elif o[1].type == X.X86_OP_IMM:
                mask = o[1].imm & M32
            else:
                return True
            if cc == 'ne':               # value & mask != 0
                return s.narrow(key, lambda iv: _refine(iv, 'ae', 1), mask)
            if mask == M32:               # value == 0
                return s.narrow(key, lambda iv: _refine(iv, 'e', 0))
        return True

    def _apply(self, ins, m, ops, s):
        old = s.copy()
        try:
            _, wr = ins.regs_access()
        except Exception:
            wr = ()
        written = set()
        for rid in wr:
            t = _reg32(ins.reg_name(rid))
            if t:
                written.add(t)
        if m in ('call', 'int', 'syscall', 'sysenter', 'int3', 'into'):
            s.iv.clear()
            s.nz.clear()
            s.al = frozenset()
            s.stk = ()
            return
        for r in written:
            s.kill(r)
            for c in [c for c in s.iv if c[0] == '[' and c[1:4] == r]:
                s.kill(c)
        self._memory(ins, m, ops, s, old)
        d = self._regop(ins, 0)
        if m == 'push' and len(ops) == 1 and ops[0].size == 4:
            if ops[0].type == X.X86_OP_IMM:
                v = (ops[0].imm & M32, ops[0].imm & M32)
            else:
                v = old.iv.get(d[0]) if d and d[2] == 32 and d[1] == 0 else None
            s.stk = s.stk + (v or (0, M32),)
            return
        if m == 'pop' and len(ops) == 1 and d and d[2] == 32 and d[1] == 0:
            v = s.stk[-1] if s.stk else None
            s.stk = s.stk[:-1]
            s.setiv(d[0], v)
            return
        if 'esp' in written:                       # any other change of esp
            s.stk = ()
        if not (d and d[2] == 32 and d[1] == 0 and len(ops) >= 1):
            return
        dr = d[0]
        two = len(ops) == 2
        src = self._regop(ins, 1) if two else None
        imm = ops[1].imm if two and ops[1].type == X.X86_OP_IMM else None
        if m == 'mov' and two:
            if imm is not None:
                s.setiv(dr, (imm & M32, imm & M32))
            elif src and src[2] == 32 and src[1] == 0:
                v = old.iv.get(src[0])
                s.setiv(dr, v)
                if src[0] in old.nz:
                    s.nz[dr] = old.nz[src[0]]
                s.link(dr, src[0])
            elif self._cell(ops[1]):
                s.setiv(dr, old.iv.get(self._cell(ops[1])))
                s.link(dr, self._cell(ops[1]))
        elif m in ('xor', 'sub') and two and src and src[0] == dr and src[2] == 32:
            s.setiv(dr, (0, 0))
        elif m == 'and' and imm is not None:
            im = imm & M32
            lo, hi = old.iv.get(dr, (0, M32))
            nlo = 1 if any(k & ~im & M32 == 0 for k in old.nz.get(dr, ())) else 0
            s.setiv(dr, (nlo, min(hi, im)))
        elif m in ('add', 'sub') and imm is not None:
            k = _simm(imm) if m == 'add' else -_simm(imm)
            s.setiv(dr, _add(old.iv.get(dr), k))
        elif m in ('inc', 'dec') and not two:
            s.setiv(dr, _add(old.iv.get(dr), 1 if m == 'inc' else -1))
        elif m == 'shr' and imm is not None and 0 < imm < 32:
            lo, hi = old.iv.get(dr, (0, M32))
            s.setiv(dr, (lo >> imm, hi >> imm))
        elif m == 'shl' and imm is not None and 0 < imm < 32:
            lo, hi = old.iv.get(dr, (0, M32))
            s.setiv(dr, (lo << imm, hi << imm) if hi << imm <= M32 else None)
        elif m == 'lea' and two and ops[1].type == X.X86_OP_MEM:
            mm = ops[1].mem
            if mm.index == 0 and mm.base:
                b = _reg32(ins.reg_name(mm.base))
                if b:
                    s.setiv(dr, _add(old.iv.get(b), _simm(mm.disp)))
        elif m == 'movzx' and two:
            if src:
                s.setiv(dr, (0, 0xff if src[2] == 8 else 0xffff))
            elif ops[1].type == X.X86_OP_MEM:
                s.setiv(dr, self._load(old, ops[1].mem, ops[1].size))

    IMPLICIT_STORES = ('push', 'call', 'movsb', 'movsw', 'movsd', 'movsl', 'stosb', 'stosw', 'stosd', 'insb', 'insw', 'insd', 'enter', 'int')

    def _slot(self, op):
        """(base, disp, size) of a `[ebp+k]` / `[esp+k]` memory operand, else None"""
        m = op.mem
        if m.index or m.segment or not m.base:
            return None
        b = self._name(m.base)
        return (b, _simm(m.disp), op.size) if b in ('ebp', 'esp') else None

    def _memory(self, ins, m, ops, s, old):
        """the effect of the instruction's memory writes on the cells: a store to a `[ebp+k]` / `[esp+k]` slot drops the cells it may overlap (same base: by byte
        range; the other base: all) and, for a modelled 4-byte store, sets that cell; any other write drops every cell"""
        wr = [i for i, o in enumerate(ops) if o.type == X.X86_OP_MEM and o.access & CS_AC_WRITE]
        implicit = any(m.startswith(p) for p in self.IMPLICIT_STORES)
        if (implicit or wr) and m != 'push':
            s.stk = ()
        if implicit:
            self._kill_cells(s)
            return
        if not wr:
            return
        sl = self._slot(ops[wr[0]]) if wr == [0] else None
        if sl is None:
            self._kill_cells(s)
            return
        base, disp, size = sl
        for c in [c for c in s.iv if c[0] == '[']:
            if not (c[1:4] == base and (int(c[4:]) + 4 <= disp or disp + size <= int(c[4:]))):
                s.kill(c)
        for c in sorted({k for pr in s.al for k in pr if k[0] == '[' and not (k[1:4] == base and (int(k[4:]) + 4 <= disp or disp + size <= int(k[4:])))}):
            s.kill(c)
        if size != 4:
            return
        key = '[%s%+d' % (base, disp)
        iv, src = None, None
        if m == 'mov' and len(ops) == 2:
            if ops[1].type == X.X86_OP_IMM:
                iv = (ops[1].imm & M32, ops[1].imm & M32)
            elif ops[1].type == X.X86_OP_REG:
                t = SUB.get(ins.reg_name(ops[1].reg))
                if t and t[2] == 32 and t[1] == 0:
                    iv, src = old.iv.get(t[0]), t[0]
        elif m in ('add', 'sub') and len(ops) == 2 and ops[1].type == X.X86_OP_IMM:
            iv = _add(old.iv.get(key), _simm(ops[1].imm) * (1 if m == 'add' else -1))
        elif m in ('inc', 'dec') and len(ops) == 1:
            iv = _add(old.iv.get(key), 1 if m == 'inc' else -1)
        s.setiv(key, iv)
        if src:
            s.link(key, src)

    def _load(self, st, mm, size):
        """the interval of a zero-extended load of `size` bytes: [0, 2^(8*size)-1], the exact byte range when the address is a table that the image fixes"""
        top = (0, (1 << (8 * size)) - 1)
        if size != 1 or mm.segment:
            return top
        regs = [r for r in (mm.base, mm.index) if r]
        if len(regs) != 1 or (mm.index and mm.scale != 1):
            return top
        iv = st.iv.get(_reg32(self._name(regs[0])))
        if iv is None:
            return top
        base = mm.disp & M32
        a0, a1 = base + iv[0], base + iv[1]
        if a1 > M32 or a1 - a0 >= MAXSLOTS or not self.img.readonly(a0, a1 - a0 + 1):
            return top
        bs = self.img.bytes(a0, a1 - a0 + 1)
        return (min(bs), max(bs))

    def _name(self, rid):
        return md.reg_name(rid)

    def run(self):
        ins_ = self.insns
        st = {}
        count = {}
        work = []
        for a in sorted(self.ext):
            st[a] = _St()
            work.append(a)
        if not work and self.order:
            st[self.order[0]] = _St()
            work.append(self.order[0])
        import heapq
        heapq.heapify(work)
        queued = set(work)
        while work:
            a = heapq.heappop(work)
            queued.discard(a)
            for t, s in self.transfer(a, st[a]).items():
                if t in self.ext:
                    continue                                   # an entry stays top
                if t not in st:
                    new = s
                else:
                    new = st[t].join(s)
                    if new.key() == st[t].key():
                        continue
                    count[t] = count.get(t, 0) + 1
                    if count[t] >= WIDEN:                      # widening: what still grows goes to top
                        for r in list(new.iv):
                            if r not in st[t].iv or new.iv[r] != st[t].iv[r]:
                                new.iv.pop(r)
                                new.nz.pop(r, None)
                if t not in st or new.key() != st[t].key():
                    st[t] = new
                    if t not in queued:
                        heapq.heappush(work, t)
                        queued.add(t)
        self.state = st
        return st

    def index_range(self, a):
        """(lo, hi, disp) of the dispatch `jmp [base + idx*4 + disp]` at a: the values of idx the code can reach it with"""
        ins = self.insns[a]
        mm = ins.operands[0].mem
        st = self.state.get(a)
        if st is None:
            raise TableUnbounded('table jump at %x is not reached by the analysis' % a)
        idx = _reg32(self._name(mm.index))
        iv = st.iv.get(idx)
        if iv is None:
            raise TableUnbounded('table jump at %x: no guard bounds %s' % (a, self._name(mm.index)))
        disp = mm.disp & M32
        if mm.base:
            b = st.iv.get(_reg32(self._name(mm.base)))
            if b is None or b[0] != b[1]:
                raise TableUnbounded('table jump at %x: base register %s is not one constant' % (a, self._name(mm.base)))
            disp = (disp + b[0]) & M32
        if iv[1] - iv[0] >= MAXSLOTS:
            raise TableUnbounded('table jump at %x: index range %d..%d is larger than %d slots' % (a, iv[0], iv[1], MAXSLOTS))
        return iv[0], iv[1], disp


def table_slots(img, an, a):
    """-> the code pointers (slot values) the dispatch at a can load, one per proved index value; a slot that is not a relocated dword raises TableUnbounded"""
    lo, hi, disp = an.index_range(a)
    out = []
    for i in range(lo, hi + 1):
        slot = (disp + 4 * i) & M32
        if slot not in img.rset:
            raise TableUnbounded('table jump at %x: slot %d (%x) lies in the proved index range %d..%d but is not a relocated dword' % (a, i, slot, lo, hi))
        out.append(img.u32(slot))
    return out
