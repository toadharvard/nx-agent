"""argflow - backward data-flow of one stdcall argument of a call site over the control-flow graph of its function (L1 CONTRACT).

    img = Image(rec)                               rec: imagerec.ImageRec(pe_bytes, name, runs), runs {entry: [(start, end)]} (code facts, absolute VAs at the image base)
    derive_arg(img, call_va, k)     -> Result      value set of stdcall argument k (k = 0 is pushed last) at the call at call_va
    callee(img, call_va)            -> ('import', dll, name) | ('direct', va) | ('unknown', reason)
    imports_called(img, name)       -> every call site of an imported function (all forms below)

Result: .closed (bool), .leaves = sorted [(kind, value, chain)], .breaks = [(va, reason)].  Leaf kinds:
  str   (D1) value = text: an immediate that is a relocated address of a NUL-terminated string in a section without
        IMAGE_SCN_MEM_WRITE (char or wide, as the caller asks);
  ord   (D3) value = n: a non-relocated immediate below 0x10000;
  imm   any other non-relocated immediate, or a relocated one that is not a read-only string (value = int);
  tbl   (D2) a load `mov r,[disp+idx*scale]` from a table in a read-only section whose index is itself a proven constant on
        all paths: one entry per index; the table extent is the relocation-delimited run of the table (see table_run);
  ret   the eax result of the call at va: value = callee(...) (the caller derives its arguments);
  param (D4) value = (entry, i): stack parameter i of the function entered at entry; expanded to the call sites when the
        entry is not address-taken (relocated word or immediate equal to the entry) and every caller is a direct call;
  imp   the loaded value is the IAT slot of an import: value = (dll, name).
`chain` is the machine-checkable proof: the defining instruction addresses (and table address / index constants).

Rule.  Locations are a 32-bit register, a stack slot at byte offset s from esp, a frame slot [ebp+d], a global [A].  Walking
the predecessors of an instruction backwards (all paths, a visited set of (instruction, location) closes loops), an
instruction either leaves the location unchanged, moves it (mov reg,reg / mov reg,mem / push / pop / esp arithmetic, which
shift s by the instruction's esp delta), or defines it.  Everything else is a break, reported with its address.
Assumptions (stated, not measured): (A1) ebx, esi, edi, ebp survive a call (callee-saved); eax, ecx, edx do not; (A2) the (ASM-010, ASM-011, ASM-012, ASM-013, ASM-014, ASM-015, ASM-016, ASM-017)
outgoing-argument area and a frame slot whose address is never taken (no lea of it in the function) are written only by the
instructions that name them; (A3) the only way code obtains a code address is a relocated word (no PIC); (A4) the facts'
ranges contain every instruction of the function and every target of an indirect jump of the function is a relocated word
into the ranges (a table entry); (A5) a call to a direct callee removes the callee's uniform `ret n` bytes;
(A6) memory [R+d] through a pointer register is not a slot of the current frame;
(A7, retired) a call between a store through R and a load through R does not write that memory: handle_origin / derive_arg with
`img.mx` (memfx.Memfx) use the rule `call_keeps` instead - the call writes [R+d] only if a value derived from the address of R's
object (frame of a function / image section; origins are derived backwards through callers) reaches the call, or that address
escaped; the rule rests on the provenance premise P stated in memfx.py (A8); mode 'open' (no rule, calls transparent) is kept
only for comparison and is the assumption A7.
Also with `img.mx`: a load of a writable global, and a string in a writable section, are constants of the image bytes when
memfx proves nothing writes them (memfx.const_range); a load [R+d] whose pointer is the exact address of a global at every
origin yields the image bytes plus the value stored by every writer of that dword (memfx.words).
"""
import bisect
import struct

import imagerec
from imagerec import X

PARENT = {}
for _n in ('ax', 'bx', 'cx', 'dx', 'si', 'di', 'bp', 'sp'):
    PARENT[_n] = 'e' + _n
for _n in 'abcd':
    PARENT[_n + 'l'] = 'e' + _n + 'x'
    PARENT[_n + 'h'] = 'e' + _n + 'x'
    PARENT['e' + _n + 'x'] = 'e' + _n + 'x'
for _n in ('si', 'di', 'bp', 'sp'):
    PARENT['e' + _n] = 'e' + _n
for _n in ('sil', 'dil', 'bpl', 'spl'):
    PARENT[_n] = 'e' + _n[:2]
CALLER_SAVED = ('eax', 'ecx', 'edx')
WRITE = 0x80000000
LOADERS = {'LoadLibraryA': 'a', 'LoadLibraryW': 'w', 'LoadLibraryExA': 'a', 'LoadLibraryExW': 'w',
           'GetModuleHandleA': 'a', 'GetModuleHandleW': 'w'}


class Result:
    def __init__(self):
        self.leaves, self.breaks = set(), set()

    @property
    def closed(self):
        return not self.breaks

    def add(self, kind, value, chain):
        self.leaves.add((kind, value, tuple(chain)))

    def brk(self, va, why):
        self.breaks.add((va, why))

    def merge(self, o):
        self.leaves |= o.leaves
        self.breaks |= o.breaks

    def sorted_leaves(self):
        return sorted(self.leaves, key=repr)


class Image:
    def __init__(self, rec, exe_name=None):
        """rec: imagerec.ImageRec, the one input (no file is opened here; the ranges are the record's runs)."""
        self.rec = rec
        self.name = rec.name
        self.pe = rec.pe
        self.base = rec.base
        self.exe_name = exe_name or rec.name
        self.ranges = rec.runs
        self.relocs = rec.relocs
        self.imports = rec.imports
        self._ins, self._funcs, self._ctx = {}, {}, None
        self.flat = sorted((a, b, e) for e, rs in self.ranges.items() for a, b in rs)
        self._callers = None
        self._relvals = None
        self.mx = None              # memfx.Memfx: the may-write rule (None = writable memory is never constant)
        self.import_stack = {}      # (dll lower with .dll, name) -> bytes the callee removes (stack_bytes of bridge.tsv)

    # ---- bytes
    def sec_of(self, va):
        for rva, vs, ro, rs, ch in self.pe.secs:
            if self.base + rva <= va < self.base + rva + max(vs, rs):
                return rva, vs, ro, rs, ch
        return None

    def readonly(self, va, n=1):
        s = self.sec_of(va)
        e = self.sec_of(va + n - 1)
        return bool(s) and s == e and not s[4] & WRITE and va - self.base < s[0] + s[3]

    def word(self, va):
        return struct.unpack('<I', self.pe.rd(va - self.base, 4))[0]

    def cstr(self, va, wide=False):
        """The NUL-terminated string at va, or None (not printable ASCII / unterminated / in a writable section whose bytes the
        may-write rule (memfx) does not prove constant)."""
        step = 2 if wide else 1
        sec = self.sec_of(va)
        if not sec or not self.in_raw(va, step):
            return None
        ro = self.readonly(va, step)
        out, a = [], va
        while self.in_raw(a, step) and self.sec_of(a) == sec:
            b = self.pe.rd(a - self.base, step)
            if wide:
                if b[1]:
                    return None
                c = b[0]
            else:
                c = b[0]
            if c == 0:
                if ro or (self.mx is not None and self.mx.const_range(va, a + step).ok):
                    return ''.join(out)
                return None
            if not 0x20 <= c < 0x7f or len(out) > 256:
                return None
            out.append(chr(c))
            a += step
        return None

    def in_raw(self, va, n=1):
        s = self.sec_of(va)
        return bool(s) and va + n - 1 - self.base < s[0] + s[3] and self.sec_of(va + n - 1) == s

    def insn(self, va):
        i = self._ins.get(va)
        if i is None:
            for i in imagerec.disasm(self.pe.rd(va - self.base, 16), va, 1):
                break
            else:
                i = False
            self._ins[va] = i
        return i or None

    def entries_containing(self, va):
        return sorted({e for a, b, e in self.flat if a <= va < b})

    def func(self, entry):
        f = self._funcs.get(entry)
        if f is None:
            f = self._funcs[entry] = Func(self, entry)
        return f

    def callers(self, target):
        """[(site va, 'call'|'jmp')] of the direct calls / jumps to target in every range of the facts."""
        if self._callers is None:
            self._callers = {}
            merged = []
            for a, b in sorted({(a, b) for a, b, e in self.flat}):
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            for a, b in merged:          # linear decode of the union of the ranges; no instruction object is kept
                for i in imagerec.disasm(self.pe.rd(a - self.base, b - a), a):
                    if i.mnemonic in ('call', 'jmp') and i.operands[0].type == X.X86_OP_IMM:
                        self._callers.setdefault(i.operands[0].imm, set()).add((i.address, i.mnemonic))
        return sorted(self._callers.get(target, ()))

    def reloc_values(self):
        """Sorted values of the relocated words of the image that point into it (code addresses, A3), computed once."""
        if self._relvals is None:
            hi = self.base + self.pe.size_of_image if hasattr(self.pe, 'size_of_image') else 1 << 32
            vs = set()
            for r in self.relocs:
                if self.in_raw(r, 4):
                    v = self.word(r)
                    if self.base <= v < hi:
                        vs.add(v)
            self._relvals = sorted(vs)
        return self._relvals

    def address_taken(self, va):
        """Relocated words whose value is va (A3: the only way code gets a code address)."""
        out = []
        for r in sorted(self.relocs):
            if self.word(r) == va:
                out.append(r)
        return out

    def callee_clean(self, target):
        """Uniform `ret n` of the function entered at target (n = 0 for a bare ret), or None."""
        if target not in self.ranges:
            return None
        ns = set()
        for a, b in self.ranges[target]:
            v = a
            while v < b:
                i = self.insn(v)
                if not i:
                    return None
                if i.mnemonic == 'ret':
                    ns.add(i.operands[0].imm if i.operands else 0)
                v += i.size
        return ns.pop() if len(ns) == 1 else None


class Func:
    """The instructions of one entry (the facts' ranges) and their edges."""

    def __init__(self, img, entry):
        self.img, self.entry = img, entry
        self.nodes = {}
        for a, b in img.ranges[entry]:
            v = a
            while v < b:
                i = img.insn(v)
                if not i:
                    break
                self.nodes[v] = i
                v += i.size
        vals = img.reloc_values()
        taken = set()
        for a, b in img.ranges[entry]:        # relocated words whose value is an instruction of this function
            lo = bisect.bisect_left(vals, a)
            while lo < len(vals) and vals[lo] < b:
                if vals[lo] in self.nodes:
                    taken.add(vals[lo])
                lo += 1
        self.pred = {v: set() for v in self.nodes}
        self.succ = {v: set() for v in self.nodes}
        for v, i in self.nodes.items():
            nxt = v + i.size
            m = i.mnemonic
            out = set()
            if m in ('ret', 'int3', 'ud2', 'hlt') or m == 'jmp':
                pass
            else:
                out.add(nxt)
            if m == 'jmp' or m.startswith('j') and m != 'jmp':
                op = i.operands[0]
                if op.type == X.X86_OP_IMM:
                    out.add(op.imm)
                elif m == 'jmp':
                    out |= taken          # A4: an indirect jump reaches a relocated code word of the function
            for t in out:
                if t in self.nodes:
                    self.succ[v].add(t)
                    self.pred[t].add(v)
        self._ebp = None
        self._lea = {}

    def ebp_frame(self):
        """ebp is a frame pointer: written only by `mov ebp,esp`, pop ebp, leave (and the push ebp before it)."""
        if self._ebp is None:
            ok = True
            for v, i in self.nodes.items():
                r, w = i.regs_access()
                if 'ebp' in {PARENT.get(i.reg_name(x), i.reg_name(x)) for x in w}:
                    if not (i.mnemonic in ('pop', 'leave') or (i.mnemonic == 'mov' and i.op_str == 'ebp, esp')):
                        ok = False
            self._ebp = ok
        return self._ebp

    def lea_sites(self, d):
        """Instructions taking the address of a frame slot overlapping [ebp+d, ebp+d+4) (or an indexed frame address)."""
        out = []
        for v, i in sorted(self.nodes.items()):
            if i.mnemonic == 'lea' and i.operands[1].type == X.X86_OP_MEM:
                m = i.operands[1].mem
                if m.base and i.reg_name(m.base) == 'ebp' and (m.index or d - 3 <= m.disp <= d + 3):
                    out.append(v)
        return out

    def reach(self, v):
        """Instructions reachable from v (v excluded unless on a cycle)."""
        r = self._lea.get(v)
        if r is None:
            r, work = set(), sorted(self.succ[v])
            while work:
                x = work.pop()
                if x not in r:
                    r.add(x)
                    work += sorted(self.succ[x])
            self._lea[v] = r
        return r

    def frame_setup(self):
        """The entry starts (after an optional mov edi,edi) with push ebp; mov ebp,esp: [ebp+8+4i] is stack parameter i."""
        seq = []
        v = self.entry
        while v in self.nodes and len(seq) < 4:
            i = self.nodes[v]
            seq.append(i.mnemonic + ' ' + i.op_str)
            v += i.size
        if seq[:1] == ['mov edi, edi']:
            seq = seq[1:]
        return seq[:2] == ['push ebp', 'mov ebp, esp']


def rn(i, r):
    n = i.reg_name(r)
    return PARENT.get(n, n) if n else None


def esp_delta(img, i):
    """esp_after - esp_before for the instruction, None when not statically known."""
    m = i.mnemonic
    if m == 'push':
        return -4 if i.operands[0].size == 4 else None
    if m == 'pop':
        return 4 if i.operands[0].size == 4 else None
    if m == 'call':
        c = callee(img, i.address)
        if c[0] == 'import':
            d = c[1].lower()
            return img.import_stack.get((d if '.' in d else d + '.dll', c[2]))
        if c[0] == 'direct':
            return img.callee_clean(c[1])
        return None
    if m in ('add', 'sub') and i.operands[0].type == X.X86_OP_REG and rn(i, i.operands[0].reg) == 'esp':
        if i.operands[1].type == X.X86_OP_IMM:
            return i.operands[1].imm if m == 'add' else -i.operands[1].imm
        return None
    r, w = i.regs_access()
    if 'esp' in {rn(i, x) for x in w}:
        return None
    return 0


def stack_bytes(conv, args):
    """Bytes a call of a bridge.tsv function removes from the stack: stdcall 4 per i/p/f, 8 per d/q; cdecl/vararg/data 0; else None."""
    if conv in ('cdecl', 'vararg', 'data'):
        return 0
    if conv != 'stdcall' or any(c not in 'ipfdq' for c in args):
        return None
    return sum(8 if c in 'dq' else 4 for c in args)


def callee(img, call_va, _seen=None):
    """Who the call at call_va calls: ('import', dll, name) / ('direct', va) / ('unknown', reason)."""
    i = img.insn(call_va)
    if not i or i.mnemonic not in ('call', 'jmp'):
        return ('unknown', 'not a call at %x' % call_va)
    op = i.operands[0]
    if op.type == X.X86_OP_IMM:
        t = img.insn(op.imm)
        if t and t.mnemonic == 'jmp' and t.operands[0].type == X.X86_OP_MEM:
            m = t.operands[0].mem
            if not m.base and not m.index and m.disp in img.imports:
                return ('import',) + img.imports[m.disp]
        return ('direct', op.imm)
    if op.type == X.X86_OP_MEM:
        m = op.mem
        if not m.base and not m.index and m.disp in img.imports:
            return ('import',) + img.imports[m.disp]
        return ('unknown', 'indirect call through memory at %x' % call_va)
    es = entries_for(img, call_va)
    if not es:
        return ('unknown', 'no function contains %x' % call_va)
    res = Result()
    for e in es:
        res.merge(Flow(img, img.func(e)).run(call_va, ('reg', rn(i, op.reg))))
    imps = {l[1] for l in res.leaves if l[0] == 'imp'}
    if res.breaks or len(imps) != 1 or any(l[0] != 'imp' for l in res.leaves):
        return ('unknown', 'register callee at %x: %s' % (call_va, sorted(res.breaks) or sorted(res.leaves, key=repr)))
    return ('import',) + next(iter(imps))


def entries_for(img, va):
    return [e for e in img.entries_containing(va) if va in img.func(e).nodes]


class Flow:
    """One backward walk over one function."""

    def __init__(self, img, fn, depth=0, mem=None):
        # mem: None = a load through a register pointer is a break; 'strict' = tracked, a call between store and load is a
        # break; 'open' = tracked, calls are transparent (reported as conditional on A7)
        self.img, self.fn, self.depth, self.mem = img, fn, depth, mem

    def run(self, at, loc):
        """Value set of `loc` at the point before the instruction at `at`."""
        res = Result()
        seen = set()
        work = [(at, loc, ())]
        while work:
            at, loc, chain = work.pop()
            if (at, loc) in seen:
                continue
            seen.add((at, loc))
            preds = self.fn.pred[at]
            if not preds:
                self.entry(res, loc, chain)
                continue
            for p in sorted(preds):
                self.step(res, work, self.fn.nodes[p], loc, chain)
        return res

    # ---- the entry of the function reached with a pending location
    def entry(self, res, loc, chain):
        if loc[0] == 'sk' and loc[1] >= 4 and loc[1] % 4 == 0:
            res.add('param', (self.fn.entry, (loc[1] - 4) // 4), chain)
        elif loc[0] == 'bp' and loc[1] >= 8 and loc[1] % 4 == 0 and self.fn.ebp_frame() and self.fn.frame_setup():
            res.add('param', (self.fn.entry, (loc[1] - 8) // 4), chain)
        else:
            res.brk(self.fn.entry, 'live-in %r at function entry' % (loc,))

    def define(self, res, work, i, loc, chain, src):
        """The instruction i defines loc from operand src (capstone operand) evaluated before i."""
        chain = chain + (i.address,)
        a = i.address
        if src.type == X.X86_OP_IMM:
            v = src.imm & 0xffffffff
            fld = i.address + i.size - 4
            if fld in self.img.relocs:
                s = self.img.cstr(v)
                w = self.img.cstr(v, True)
                if s is not None or w is not None:
                    res.add('str', (s, w), chain)
                else:
                    res.add('imm', v, chain)
            elif v < 0x10000:
                res.add('ord', v, chain)
            else:
                res.add('imm', v, chain)
        elif src.type == X.X86_OP_REG:
            work.append((a, ('reg', rn(i, src.reg)), chain))
        else:
            self.load(res, work, i, src.mem, chain)

    def load(self, res, work, i, m, chain):
        """Location after a memory read `[m]` evaluated at instruction i (before it)."""
        a = i.address
        base, idx = rn(i, m.base) if m.base else None, rn(i, m.index) if m.index else None
        if not base and not idx:
            d = m.disp & 0xffffffff
            if d in self.img.imports:
                res.add('imp', self.img.imports[d], chain)
            elif d in self.img.relocs or self.img.sec_of(d):
                if self.img.readonly(d, 4):
                    res.add('imm', self.img.word(d), chain + ('ro@%x' % d,))
                elif self.img.mx is not None and self.img.in_raw(d, 4) and self.img.mx.const_range(d, d + 4).ok:
                    res.add('imm', self.img.word(d), chain + ('const@%x' % d,))
                else:
                    res.brk(a, 'load of writable global %x' % d)
            else:
                res.brk(a, 'absolute load %x' % d)
        elif base == 'ebp' and not idx:
            if not self.fn.ebp_frame():
                res.brk(a, 'ebp is not a frame pointer')
            elif any(a in self.fn.reach(l) for l in self.fn.lea_sites(m.disp)):
                res.brk(a, 'frame slot [ebp%+d] may be written through its address taken earlier' % m.disp)
            else:
                work.append((a, ('bp', m.disp), chain))
        elif base == 'esp' and not idx:
            work.append((a, ('sk', m.disp), chain))
        elif not base and idx and m.disp in self.img.relocs | {m.disp} and self.img.readonly(m.disp & 0xffffffff, 4):
            self.table(res, i, m, chain)
        elif base and not idx and self.mem == 'rule' and base not in ('esp', 'ebp') and self.global_load(res, work, i, base, m.disp, chain):
            pass
        elif base and not idx and self.mem and base not in ('esp', 'ebp'):
            work.append((a, ('mem', base, m.disp), chain))
        else:
            res.brk(a, 'load through a register pointer')

    def global_load(self, res, work, i, base, disp, chain):
        """A load [base+disp] whose pointer is the exact address of a global at every origin (memfx.origins): the value set is the
        image bytes plus the value stored by every writer of that dword (memfx.words).  False: not that case (the caller
        falls back to the store walk)."""
        mx = self.img.mx
        a = i.address
        orgs = mx.origins(self.fn, a, base)
        if not orgs or any(o[0] != 'sec' or o[1] is None for o in orgs):
            return False
        for o in sorted(orgs):
            addr = (o[1] + disp) & 0xffffffff
            key = ('words', addr)
            if key in mx.busy:
                continue
            mx.busy.add(key)
            try:
                leaves, writers, why = mx.words(addr)
                if why:
                    res.brk(a, 'global %x: %s' % (addr, why[0]))
                    continue
                ch = chain + ('g@%x' % addr,)
                for _, v, reloc in leaves:
                    self.imm_leaf(res, v, reloc, ch)
                for va, w in writers:
                    self.writer_value(res, va, addr, ch + (va,))
            finally:
                mx.busy.discard(key)
        return True

    def imm_leaf(self, res, v, reloc, chain):
        if reloc:
            s, w = self.img.cstr(v), self.img.cstr(v, True)
            if s is not None or w is not None:
                res.add('str', (s, w), chain)
                return
            res.add('imm', v, chain)
        elif v < 0x10000:
            res.add('ord', v, chain)
        else:
            res.add('imm', v, chain)

    def writer_value(self, res, va, addr, chain):
        """The value stored by the instruction at va (a 4-byte mov [..], src)."""
        i = self.img.insn(va)
        if not i or i.mnemonic != 'mov' or i.operands[0].type != X.X86_OP_MEM or i.operands[0].size != 4:
            res.brk(va, 'writer of %x is %s' % (addr, i.mnemonic + ' ' + i.op_str if i else '?'))
            return
        src = i.operands[1]
        if src.type == X.X86_OP_IMM:
            self.define(res, [], i, ('reg', None), chain, src)
            return
        if src.type != X.X86_OP_REG:
            res.brk(va, 'writer of %x stores %s' % (addr, i.op_str))
            return
        es = entries_for(self.img, va)
        if not es:
            res.brk(va, 'no function contains the writer %x' % va)
        for e in es:
            res.merge(Flow(self.img, self.img.func(e), self.depth, self.mem).run(va, ('reg', rn(i, src.reg))))

    def table(self, res, i, m, chain):
        """D2 with a proven constant index: one table entry per index value."""
        a = i.address
        idx = Flow(self.img, self.fn).run(a, ('reg', rn(i, m.index)))
        if idx.breaks or any(l[0] not in ('imm', 'ord') for l in idx.leaves):
            res.brk(a, 'table index of %x not a constant on all paths (loop bound proof not implemented): %s' % (
                m.disp, sorted(idx.breaks) or sorted(l[:2] for l in idx.leaves)))
            return
        run = table_run(self.img, m.disp & 0xffffffff, m.scale)
        for _, k, ch in idx.leaves:
            if not 0 <= k < len(run):
                res.brk(a, 'index %d outside the table run of %d entries at %x' % (k, len(run), m.disp))
                continue
            v = run[k]
            res.add('str', (self.img.cstr(v), self.img.cstr(v, True)), chain + (a, 'tbl@%x[%d]' % (m.disp, k)) + ch)

    # ---- one backward step over the predecessor instruction p of the point
    def step(self, res, work, p, loc, chain):
        img = self.img
        a = p.address
        m = p.mnemonic
        kind = loc[0]
        ops = p.operands
        if kind == 'reg':
            R = loc[1]
            if m == 'call':
                if R == 'eax':
                    res.add('ret', a, chain + (a,))
                    return
                if R in CALLER_SAVED:
                    res.brk(a, 'caller-saved %s clobbered by the call' % R)
                    return
                work.append((a, loc, chain))
                return
            r, w = p.regs_access()
            if R not in {rn(p, x) for x in w}:
                work.append((a, loc, chain))
                return
            if m == 'mov' and len(ops) == 2 and ops[0].type == X.X86_OP_REG and rn(p, ops[0].reg) == R and ops[0].size == 4:
                self.define(res, work, p, loc, chain, ops[1])
            elif m in ('xor', 'sub') and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg and ops[0].size == 4:
                res.add('imm', 0, chain + (a,))
            elif m == 'lea' and not ops[1].mem.base and not ops[1].mem.index:
                v = ops[1].mem.disp & 0xffffffff
                res.add('imm', v, chain + (a,))
            elif m == 'pop' and ops[0].type == X.X86_OP_REG and ops[0].size == 4:
                work.append((a, ('sk', 0), chain + (a,)))
            else:
                res.brk(a, 'register %s defined by an unhandled instruction (%s %s)' % (R, m, p.op_str))
            return
        if kind == 'sk':
            s = loc[1]
            if s < 0:
                res.brk(a, 'slot below esp')
                return
            if m == 'push':
                if s == 0:
                    if ops[0].size != 4:
                        res.brk(a, 'push of a non-dword')
                    else:
                        self.define(res, work, p, loc, chain, ops[0])
                else:
                    work.append((a, ('sk', s - 4), chain))
                return
            d = esp_delta(img, p)
            if d is None:
                res.brk(a, 'esp effect of %s %s not statically known' % (m, p.op_str))
                return
            if m in ('pushad', 'popad', 'pushfd', 'popfd', 'leave', 'enter'):
                res.brk(a, 'implicit stack instruction %s' % m)
                return
            # a store to the slot through esp
            if ops and ops[0].type == X.X86_OP_MEM and m in ('mov',) and ops[0].mem.base and rn(p, ops[0].mem.base) == 'esp' \
                    and not ops[0].mem.index:
                k = ops[0].mem.disp
                if k == s:
                    self.define(res, work, p, loc, chain, ops[1])
                    return
                if s - 3 <= k <= s + 3:
                    res.brk(a, 'overlapping store to the stack slot')
                    return
            elif ops and ops[0].type == X.X86_OP_MEM and ops[0].mem.base and rn(p, ops[0].mem.base) == 'esp' and m not in ('cmp', 'test', 'push', 'call', 'jmp'):
                if s - 3 <= ops[0].mem.disp <= s + 3:
                    res.brk(a, 'unhandled store to the stack slot (%s %s)' % (m, p.op_str))
                    return
            work.append((a, ('sk', s + d), chain))
            return
        if kind == 'bp':
            d = loc[1]
            if ops and ops[0].type == X.X86_OP_MEM and m not in ('cmp', 'test', 'push', 'call', 'jmp') and ops[0].mem.base \
                    and rn(p, ops[0].mem.base) == 'ebp' and not ops[0].mem.index and d - 3 <= ops[0].mem.disp <= d + 3:
                if ops[0].mem.disp == d and m == 'mov' and ops[0].size == 4:
                    self.define(res, work, p, loc, chain, ops[1])
                else:
                    res.brk(a, 'unhandled store to the frame slot (%s %s)' % (m, p.op_str))
                return
            if m in ('pop',) and ops[0].type == X.X86_OP_MEM:
                res.brk(a, 'pop into memory')
                return
            work.append((a, loc, chain))
            return
        if kind == 'mem':
            R, d = loc[1], loc[2]
            r, w = p.regs_access()
            if R in {rn(p, x) for x in w}:
                res.brk(a, 'pointer register %s rewritten' % R)
            elif m == 'call':
                if self.mem == 'strict':
                    res.brk(a, 'call at %x may write [%s%+d]' % (a, R, d))
                elif self.mem == 'rule':
                    ok, why = self.img.mx.call_keeps(self.fn, p.address, R)
                    if ok:
                        work.append((a, loc, chain + (('call-keeps', a),)))
                    else:
                        res.brk(a, 'call at %x may write [%s%+d]: %s' % (a, R, d, why))
                else:
                    work.append((a, loc, chain + (('call-transparent', a),)))
            elif m in ('rep movsd', 'movsd', 'movsb', 'stosd', 'stosb') or m.startswith('rep') or m.startswith('movs') or m.startswith('stos'):
                res.brk(a, 'string store')
            elif ops and ops[0].type == X.X86_OP_MEM and m not in ('cmp', 'test', 'push', 'call', 'jmp'):
                mm = ops[0].mem
                b = rn(p, mm.base) if mm.base else None
                if b == R and not mm.index and mm.disp == d and m == 'mov' and ops[0].size == 4:
                    self.define(res, work, p, loc, chain, ops[1])
                elif b in ('esp', 'ebp') and not mm.index:
                    work.append((a, loc, chain))        # A6: [R+d] is not a slot of this frame
                else:
                    res.brk(a, 'store through another pointer (%s %s) may alias [%s%+d]' % (m, p.op_str, R, d))
            else:
                work.append((a, loc, chain))
            return
        res.brk(a, 'unknown location kind %r' % (kind,))


def table_run(img, disp, scale):
    """The relocation-delimited run of string pointers at disp: entries up to the first word that is not a relocated pointer
    to a NUL-terminated read-only string (D2 structure; the terminator word is not a relocation)."""
    out, a = [], disp
    while a in img.relocs and scale == 4:
        v = img.word(a)
        if img.cstr(v) is None and img.cstr(v, True) is None:
            break
        out.append(v)
        a += 4
    return out


def expand_params(img, res, k, depth=0, seen=None, ctx=None):
    """Replace `param` leaves by the values at every call site of the function (D4); open when the entry is address-taken
    or reached by a jump that is not a call."""
    seen = seen or set()
    out = Result()
    out.breaks |= res.breaks
    for kind, val, chain in res.leaves:
        if kind != 'param':
            out.leaves.add((kind, val, chain))
            continue
        ent, i = val
        if ctx and ctx[0] == ent:       # reached through the call at ctx[1]: only that call's argument flows in
            out.merge(derive_arg(img, ctx[1], i, depth + 1, seen, ctx[2]))
            continue
        if (ent, i) in seen:
            continue
        seen.add((ent, i))
        taken = img.address_taken(ent)
        if taken:
            out.brk(ent, 'function %x is address-taken at %s: an indirect caller can pass anything' % (ent, ','.join('%x' % t for t in taken[:3])))
            continue
        cs = img.callers(ent)
        if not cs:
            out.brk(ent, 'function %x has no direct caller' % ent)
        for site, how in cs:
            if how != 'call':
                out.brk(site, 'tail jump to %x' % ent)
                continue
            if depth > 6:
                out.brk(site, 'caller chain deeper than 6')
                continue
            r = derive_arg(img, site, i, depth + 1, seen)
            out.merge(r)
    return out


def derive_arg(img, call_va, k, depth=0, seen=None, ctx=None):
    """Value set of stdcall argument k at the call at call_va."""
    es = entries_for(img, call_va)
    res = Result()
    if not es:
        res.brk(call_va, 'no function of the facts contains %x' % call_va)
        return res
    for e in es:
        res.merge(Flow(img, img.func(e), depth, 'rule' if img.mx is not None else None).run(call_va, ('sk', 4 * k)))
    return expand_params(img, res, k, depth, seen, ctx)


def call_result_modules(img, call_va, ctx=None, depth=0):
    """Module names the eax result of the call at call_va can denote: ({name: chain}, breaks, null).
    Import LoadLibrary*/GetModuleHandle*: the string argument (GetModuleHandle(NULL) is the exe); a direct callee: the
    values at its rets, its parameters bound to the arguments of this call (context-sensitive, depth <= 6)."""
    names, brk, null = {}, set(), False
    c = callee(img, call_va)
    if c[0] == 'import' and c[2] in LOADERS:
        wide = LOADERS[c[2]] == 'w'
        a = derive_arg(img, call_va, 0, 0, None, ctx)
        brk |= a.breaks
        for k2, v2, ch2 in a.sorted_leaves():
            if k2 == 'str' and v2[1 if wide else 0] is not None:
                names[v2[1 if wide else 0]] = ch2 + (call_va,)
            elif k2 == 'imm' and v2 == 0 and c[2].startswith('GetModuleHandle'):
                names[img.exe_name] = ch2 + (call_va,)
            else:
                brk.add((call_va, 'module name argument is %s %r' % (k2, v2)))
    elif c[0] == 'direct' and depth < 6 and c[1] in img.ranges:
        f = img.func(c[1])
        for v, i in sorted(f.nodes.items()):
            if i.mnemonic != 'ret':
                continue
            r = Flow(img, f).run(v, ('reg', 'eax'))
            brk |= r.breaks
            for kind, val, ch in r.sorted_leaves():
                if kind == 'ret':
                    n2, b2, z2 = call_result_modules(img, val, (c[1], call_va, ctx), depth + 1)
                    names.update(n2)
                    brk |= b2
                    null |= z2
                elif kind == 'imm' and val == 0:
                    null = True
                else:
                    brk.add((v, 'return value of %x is %s %r' % (c[1], kind, val)))
    else:
        brk.add((call_va, 'hModule is the result of %r' % (c,)))
    return names, brk, null


def module_names(img, call_va):
    """Module set of the hModule argument of the GetProcAddress call at call_va: ({name: chain}, breaks).
    A NULL handle makes GetProcAddress fail (no pair), so an immediate 0 contributes nothing."""
    r = derive_arg(img, call_va, 0)
    names, brk = {}, set(r.breaks)
    for kind, val, chain in r.sorted_leaves():
        if kind == 'ret':
            n, b, z = call_result_modules(img, val)
            for k, v in n.items():
                names[k] = chain + v
            brk |= b
        elif kind == 'imm' and val == 0:
            pass
        else:
            brk.add((chain[-1] if chain and isinstance(chain[-1], int) else call_va, 'hModule is %s %r' % (kind, val)))
    return names, brk


def name_set(img, call_va):
    """Name set of the lpProcName argument: ([(kind, text|ordinal, chain)], breaks)."""
    r = derive_arg(img, call_va, 1)
    names, brk = [], set(r.breaks)
    for kind, val, chain in r.sorted_leaves():
        if kind == 'str' and val[0] is not None:
            names.append(('name', val[0], chain))
        elif kind == 'ord':
            names.append(('ord', '#%d' % val, chain))
        else:
            brk.add((chain[-1] if chain and isinstance(chain[-1], int) else call_va, 'lpProcName is %s %r' % (kind, val)))
    return names, brk


def imports_called(img, imp_name):
    """Every call of an imported function: [(call va, slot-reference va)] over the forms call [slot], call thunk (jmp [slot]),
    and call reg after mov reg,[slot]; plus [(va, why)] for the forms that make the set open (escape, address-taken thunk)."""
    slots = {s for s, (d, n) in img.imports.items() if n == imp_name}
    calls, open_ = [], []
    refs = sorted(r for r in img.relocs if img.word(r) in slots)
    for r in refs:
        c, o = uses_of_slot_ref(img, r)
        calls += c
        open_ += o
    return sorted(set(calls)), sorted(set(open_))


def uses_of_slot_ref(img, ref):
    """The calls that use the import slot referenced by the relocated operand at ref: ([call va], [(va, why)])."""
    calls, open_ = [], []
    ia = None
    for e in img.entries_containing(ref):
        for back in range(1, 12):
            i = img.func(e).nodes.get(ref - back)
            if i and i.size >= back + 4:
                ia = i
                break
        if ia:
            break
    if not ia:
        if unreached(img, ref):
            return calls, []
        return calls, [(ref, 'no instruction contains the slot reference')]
    i = ia
    m = i.mnemonic
    ops = i.operands
    if m == 'call':
        return [i.address], []
    if m == 'jmp':
        taken = img.address_taken(i.address)
        if taken:
            open_.append((i.address, 'thunk address-taken at %x' % taken[0]))
        for site, how in img.callers(i.address):
            if how == 'call':
                calls.append(site)
            else:
                open_.append((site, 'tail jump to the thunk'))
        if not img.callers(i.address) and not taken:
            open_.append((i.address, 'thunk has no caller'))
        return calls, open_
    if m == 'mov' and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM and ops[0].size == 4:
        es = entries_for(img, i.address)
        if not es:
            return calls, [(i.address, 'no function contains the load')]
        for e in es:
            c, o = forward_uses(img, img.func(e), i.address, rn(i, ops[0].reg))
            calls += c
            open_ += o
        return calls, open_
    return calls, [(i.address, 'slot referenced by %s %s' % (m, i.op_str))]


def unreached(img, va):
    """No range of any entry of the facts covers va: the facts are closed under direct targets and relocated code pointers, so
    no call reaches the bytes (A3); a slot reference there has no uses."""
    return not any(a <= va < b for a, b, e in img.flat)


def forward_uses(img, fn, at, reg):
    """Calls through reg (or a copy of it) from the load at `at`, over all forward paths; escapes are reported."""
    calls, open_ = [], []
    seen = set()
    work = [(n, frozenset([reg])) for n in sorted(fn.succ[at])]
    while work:
        v, held = work.pop()
        if (v, held) in seen:
            continue
        seen.add((v, held))
        i = fn.nodes[v]
        m = i.mnemonic
        nh = set(held)
        r, w = i.regs_access()
        rd = {rn(i, x) for x in r}
        wr = {rn(i, x) for x in w}
        ops = i.operands
        used = held & rd
        if m == 'call':
            if ops[0].type == X.X86_OP_REG and rn(i, ops[0].reg) in held:
                calls.append(v)
            elif used:
                open_.append((v, 'held register read by %s %s' % (m, i.op_str)))
            nh -= set(CALLER_SAVED)
        elif m == 'ret':
            if 'eax' in held:
                open_.append((v, 'value returned in eax'))
            continue
        elif m == 'mov' and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and ops[0].size == 4 and ops[1].size == 4 \
                and rn(i, ops[1].reg) in held:
            nh.add(rn(i, ops[0].reg))
            nh -= (wr - {rn(i, ops[0].reg)})
        elif m in ('cmp', 'test'):
            pass
        elif m == 'xor' and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
            nh -= wr
        elif used:
            open_.append((v, 'held register read by %s %s' % (m, i.op_str)))
            nh -= used
        else:
            nh -= wr
        if not nh:
            continue
        for s in sorted(fn.succ[v]):
            work.append((s, frozenset(nh)))
    return sorted(set(calls)), sorted(set(open_))


def handle_origin(img, call_va, k, mem):
    """Where the stdcall argument k of the call at call_va (a handle) comes from, following stores through a pointer register
    (mem 'strict': a call between store and load breaks; 'open': calls are transparent, the answer is conditional on A7).
    -> (closed, [(callee ('import', dll, name), call va, chain)], breaks)."""
    es = entries_for(img, call_va)
    res = Result()
    for e in es:
        res.merge(Flow(img, img.func(e), 0, mem).run(call_va, ('sk', 4 * k)))
    origins, brk = [], set(res.breaks)
    for kind, val, chain in res.sorted_leaves():
        if kind == 'ret':
            origins.append((callee(img, val), val, chain))
        else:
            brk.add((call_va, 'handle is %s %r' % (kind, val)))
    return not brk, origins, sorted(brk)


# ---------------------------------------------------------------- path predicate on a call result
M32 = 0xffffffff
# jcc mnemonics (capstone spelling and synonyms) -> which edge is the `result != K` edge, per flag source.
#   cmp r,K : ZF = (r == K), CF = (r <u K); test r,r (K = 0): ZF = (r == 0), CF = 0; inc r / add r,1 (K = 0xffffffff): ZF = (r == K).
#   jb: CF=1 <=> r <u 0xffffffff <=> r != 0xffffffff;  ja: CF=0 and ZF=0 <=> r != 0 (r >=u 0 always).
_EQ_TAKEN, _NE_TAKEN = 'eq', 'ne'
_JCC_ZF = {'je': _EQ_TAKEN, 'jz': _EQ_TAKEN, 'jne': _NE_TAKEN, 'jnz': _NE_TAKEN}
_JCC_MAX = {'jb': _NE_TAKEN, 'jnae': _NE_TAKEN, 'jc': _NE_TAKEN, 'jae': _EQ_TAKEN, 'jnb': _EQ_TAKEN, 'jnc': _EQ_TAKEN}
_JCC_ZERO = {'ja': _NE_TAKEN, 'jnbe': _NE_TAKEN, 'jbe': _EQ_TAKEN, 'jna': _EQ_TAKEN}


def _jcc_kind(kind, K, m):
    """'eq' / 'ne' (the edge taken when the compared copy equals / differs from K), or None (not exactly equivalent)."""
    if m in _JCC_ZF:
        return _JCC_ZF[m]
    if kind == 'cmp' and K == M32:
        return _JCC_MAX.get(m)
    if kind in ('cmp', 'test') and K == 0:
        return _JCC_ZERO.get(m)
    return None


def _path_pred(img, fn, C, S, K):
    """Every path of fn from the call at C to the instruction at S crosses an edge of a conditional branch that is decided by flags
    from a compare of a copy of the result of C with K and takes the `!= K` side.  -> (ok, [(compare va, branch va)], [(va, why)])."""
    import memfx
    fb = fn.ebp_frame() and not memfx.frame_exposed(fn)
    brk = {}

    def slot(i, op):
        m = op.mem
        if fb and m.base and rn(i, m.base) == 'ebp' and not m.index and not m.segment and op.size == 4:
            return ('bp', m.disp)
        return None

    def after(v, st):
        i = fn.nodes[v]
        copies, flags, chk = st
        if v == C:
            return (frozenset([('reg', 'eax')]), None, False)
        m = i.mnemonic
        ops = i.operands
        r, w = i.regs_access()
        wn = {rn(i, x) for x in w}
        nf = flags
        new = None
        if m == 'cmp' and len(ops) == 2 and ops[1].type == X.X86_OP_IMM and (ops[1].imm & M32) == K and (v + i.size - 4) not in img.relocs \
                and ops[0].size == 4:
            loc = ('reg', rn(i, ops[0].reg)) if ops[0].type == X.X86_OP_REG else slot(i, ops[0]) if ops[0].type == X.X86_OP_MEM else None
            if loc in copies:
                new = ('cmp', v)
        elif m == 'test' and K == 0 and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg and ops[0].size == 4 \
                and ('reg', rn(i, ops[0].reg)) in copies:
            new = ('test', v)
        elif K == M32 and ops and ops[0].type == X.X86_OP_REG and ops[0].size == 4 and ('reg', rn(i, ops[0].reg)) in copies \
                and (m == 'inc' or (m == 'add' and ops[1].type == X.X86_OP_IMM and ops[1].imm == 1)):
            new = ('inc', v)
        if new is not None:
            nf = new
        elif 'eflags' in wn:
            nf = None
        nc = set(copies)
        for x in wn:
            nc.discard(('reg', x))
        if m == 'call':
            for x in CALLER_SAVED:
                nc.discard(('reg', x))
        for o in ops:
            if o.type == X.X86_OP_MEM and o.access & 2:
                s = slot(i, o)
                mm = o.mem
                framey = s is not None or (mm.base and rn(i, mm.base) in ('esp', 'ebp')) or (mm.index and rn(i, mm.index) in ('esp', 'ebp'))
                for loc in [c for c in nc if c[0] == 'bp']:
                    if framey and (s is None or abs(loc[1] - s[1]) < 4):
                        nc.discard(loc)
        if m == 'mov' and len(ops) == 2 and ops[0].size == 4:
            if ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and ('reg', rn(i, ops[1].reg)) in copies:
                nc.add(('reg', rn(i, ops[0].reg)))
            elif ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM and slot(i, ops[1]) in copies:
                nc.add(('reg', rn(i, ops[0].reg)))
            elif ops[0].type == X.X86_OP_MEM and ops[1].type == X.X86_OP_REG and ('reg', rn(i, ops[1].reg)) in copies \
                    and slot(i, ops[0]) is not None:
                nc.add(slot(i, ops[0]))
        return (frozenset(nc), nf, chk)

    def edge(v, st, s):
        i = fn.nodes[v]
        copies, flags, chk = st
        m = i.mnemonic
        if chk or not (m.startswith('j') and m != 'jmp'):
            return st
        if flags is None:
            brk[v] = 'branch at %x is not decided by a compare of a copy of the result with 0x%x (flags unset or clobbered)' % (v, K)
            return st
        kind = _jcc_kind(flags[0], K, m)
        if kind is None:
            brk[v] = 'condition %s after the %s at %x is not exactly equivalent to != 0x%x' % (m, flags[0], flags[1], K)
            return st
        tgt = i.operands[0].imm if i.operands[0].type == X.X86_OP_IMM else None
        fall = v + i.size
        if tgt == fall:
            return st
        ne_target = kind == _NE_TAKEN
        if (s == tgt and ne_target) or (s == fall and not ne_target):
            return (copies, flags, True)
        return st

    ins = {}
    work = []
    for s in sorted(fn.succ[C]):
        ins[s] = (frozenset([('reg', 'eax')]), None, False)
        work.append(s)
    outs = {}
    while work:
        v = work.pop()
        st = ins[v]
        a = after(v, st)
        for s in sorted(fn.succ[v]):
            e = edge(v, a, s)
            cur = ins.get(s)
            if s == C:
                continue
            if cur is None:
                ins[s] = e
                work.append(s)
            else:
                n = (cur[0] & e[0], cur[1] if cur[1] == e[1] else None, cur[2] and e[2])
                if n != cur:
                    ins[s] = n
                    work.append(s)
    if S not in ins:
        return False, [], [(S, 'the site is not reachable from the call at %x' % C)]
    if ins[S][2]:
        dec = []
        for v in sorted(ins):
            i = fn.nodes[v]
            if i.mnemonic.startswith('j') and i.mnemonic != 'jmp' and ins[v][1] is not None and not ins[v][2]:
                dec.append((ins[v][1][1], v))
        return True, dec, []
    b = sorted(brk.items())
    b.append((S, 'a path from the call at %x reaches the site with no `!= 0x%x` edge crossed' % (C, K)))
    return False, [], b


def result_predicate(img, call_site_va, k, callee_set, relation, constant):
    """Path predicate on the argument k of the call at call_site_va: the argument is, on all paths, the result of a call of an
    import in callee_set ({(dll, name)}, dll normalised), AND on every path from that call to the site the result was compared
    (a still-valid copy: register or private frame slot) with `constant` and the path crossed the exactly-equivalent `relation`
    ('!=') edge of the branch deciding it.  Equivalences used: cmp r,K / test r,r (K = 0) with je/jne (any K), jb/jae (K = 0xffffffff)
    and ja/jbe (K = 0); inc r / add r,1 with je/jne (K = 0xffffffff).  The flags must not be redefined between the compare and
    the branch.  Anything else is reported as a break.  -> Result: .closed, .leaves ('pred', (dll, name, call va), chain of
    (compare va, branch va)), .breaks."""
    import iat
    res = Result()
    if relation != '!=':
        res.brk(call_site_va, 'unsupported relation %r' % relation)
        return res
    K = constant & M32
    ok, origins, brk = handle_origin(img, call_site_va, k, 'strict')
    if not ok and img.mx is not None:
        ok, origins, brk = handle_origin(img, call_site_va, k, 'rule')
    if not ok:
        for b in brk:
            res.breaks.add(b)
        return res
    for c, va, chain in origins:
        if c[0] != 'import' or (iat.norm_dll(c[1]), c[2]) not in callee_set:
            res.brk(va, 'origin %r is not in the callee set' % (c,))
            continue
        entries = [e for e in entries_for(img, call_site_va) if va in img.func(e).nodes]
        if not entries:
            res.brk(va, 'no function contains both the call and the site')
        for e in entries:
            good, dec, bad = _path_pred(img, img.func(e), va, call_site_va, K)
            for b in bad:
                res.breaks.add(b)
            if good:
                res.add('pred', (iat.norm_dll(c[1]), c[2], va), tuple(x for p in dec for x in p))
    return res
