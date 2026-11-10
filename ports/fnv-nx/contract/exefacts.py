#!/usr/bin/env python3
"""exefacts - the one fact base read from the player's executable (L1 CONTRACT, M0).

    exefacts.py --exe FalloutNV.exe --units units.json --out facts.pkl [--jobs N]

Everything a boundary needs is a fact of the exe: base relocations, imports, RTTI vftables, and per function
the `ret N` set, registers live on entry, direct and indirect calls, vptr stores (in order, with base and
displacement), data references and code pointers.  The output is exe-derived: it stays in the container or in
fnv-nx-build, never in this repository (docs/ARCHITECTURE.md, Legal).

Facts (pickle, one dict):
  meta      sha256 of the exe and of this file, counts
  base, secs{name: (va, vsize, raw_off, raw_size)}, relocs[sorted va]
  imports   {iat_slot_va: (dll, name)}
  vft       {vftable_va: (rtti_name, col_offset, [slot targets], col_va)}; a vftable without RTTI that the
            decomp names has (its `??_7` symbol, 0, slots, 0)
  ptrs      [(va, target)]   every relocated code pointer held in data (slots, callbacks, __xc ...)
  xc        [target ...]     the C++ initializer table, in order
  fn        {va: F}          (F['cleanup'] = [(call site, bytes the caller pops right after the call; 0 = none)])
  fn        {va: F}          F as returned by analyze() plus 'live' (interprocedural) and 'sym', 'decomp'
"""
import argparse, bisect, collections, hashlib, json, pickle, struct, sys
import capstone
from capstone import x86_const as X

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')
BIT = {r: 1 << i for i, r in enumerate(REGS)}
ALL = (1 << len(REGS)) - 1
_SUB = {'al': 'eax', 'ah': 'eax', 'ax': 'eax', 'cl': 'ecx', 'ch': 'ecx', 'cx': 'ecx', 'dl': 'edx', 'dh': 'edx',
        'dx': 'edx', 'bl': 'ebx', 'bh': 'ebx', 'bx': 'ebx', 'si': 'esi', 'di': 'edi', 'bp': 'ebp'}
_SUB.update({r: r for r in REGS})
VERSION = 7
CALLEE_SAVED = BIT['ebx'] | BIT['esi'] | BIT['edi'] | BIT['ebp']


class Image:
    """A PE32 image addressed by virtual address."""

    def __init__(self, path):
        self.d = d = open(path, 'rb').read()
        self.sha256 = hashlib.sha256(d).hexdigest()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec, = struct.unpack_from('<H', d, pe + 6)
        opt, = struct.unpack_from('<H', d, pe + 20)
        self.base, = struct.unpack_from('<I', d, pe + 24 + 28)
        self.dd = [struct.unpack_from('<II', d, pe + 24 + 96 + 8 * i) for i in range(16)]
        self.secs = {}
        o = pe + 24 + opt
        for _ in range(nsec):
            name = d[o:o + 8].rstrip(b'\0').decode()
            vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            self.secs[name] = (self.base + va, vs, ro, rs)
            o += 40
        self._spans = sorted((va, vs, ro, rs, n) for n, (va, vs, ro, rs) in self.secs.items())
        self.text = self.secs['.text'][:2]
        self.relocs = self._relocs()
        self.rset = set(self.relocs)
        self.imports, self.iat = self._imports()

    def sec(self, a):
        for va, vs, ro, rs, n in self._spans:
            if va <= a < va + vs:
                return n
        return None

    def off(self, a):
        for va, vs, ro, rs, n in self._spans:
            if va <= a < va + vs:
                return ro + a - va if a - va < rs else None
        return None

    def u32(self, a):
        o = self.off(a)
        return struct.unpack_from('<I', self.d, o)[0] if o is not None else 0

    def cstr(self, a):
        o = self.off(a)
        return self.d[o:self.d.index(b'\0', o)].decode('latin1') if o is not None else ''

    def intext(self, a):
        return self.text[0] <= a < self.text[0] + self.text[1]

    def _relocs(self):
        rva, size = self.dd[5]
        out = []
        p = self.off(self.base + rva)
        end = p + size
        while p < end:
            page, bs = struct.unpack_from('<II', self.d, p)
            if bs == 0:
                break
            for i in range((bs - 8) // 2):
                e, = struct.unpack_from('<H', self.d, p + 8 + 2 * i)
                if e >> 12 == 3:
                    out.append(self.base + page + (e & 0xfff))
            p += bs
        return sorted(out)

    def _imports(self):
        rva, size = self.dd[1]
        out = {}
        p = self.base + rva
        while True:
            ilt, _, _, name, iat = struct.unpack_from('<IIIII', self.d, self.off(p))
            if not name:
                break
            dll = self.cstr(self.base + name).lower()
            t = self.base + (ilt or iat)
            s = self.base + iat
            while True:
                v = self.u32(t)
                if not v:
                    break
                out[s] = (dll, '#%d' % (v & 0xffff) if v >> 31 else self.cstr(self.base + v + 2))
                t += 4
                s += 4
            p += 20
        return out, set(out)


def rtti_vftables(img):
    """{vftable: (name, COL offset, slots, COL)} - a vftable is the word run after a relocated pointer to a COL."""
    rva, rvs = img.secs['.rdata'][:2]
    dva, dvs = img.secs['.data'][:2]
    inr = lambda a: rva <= a < rva + rvs

    def is_col(c):
        if not inr(c) or img.u32(c) != 0 or (c + 12) not in img.rset:
            return None
        td = img.u32(c + 12)
        if not (dva <= td < dva + dvs):
            return None
        n = img.cstr(td + 8)
        return n if n.startswith('.?A') else None
    vft = {}
    for r in img.relocs:
        if not inr(r):
            continue
        col = img.u32(r)
        name = is_col(col)
        if not name:
            continue
        slots = []
        a = r + 4
        while a in img.rset and img.intext(img.u32(a)):
            if a != r + 4 and (a - 4) in img.rset and is_col(img.u32(a - 4)):
                break
            slots.append(img.u32(a))
            a += 4
        if slots:
            vft[r + 4] = (name, img.u32(col + 4), slots, col)
    return vft


def named_vftables(img, units, vft):
    """Add the vftables the decomp names (`??_7...` in units.json) that carry no RTTI: (symbol, 0, slots, 0)."""
    names = {}
    for un in units['units']:
        for sym, a in un.get('symbols', {}).items():
            if sym.startswith('??_7'):
                names.setdefault(int(a, 16), sym)
    starts = set(vft) | set(names)
    out = dict(vft)
    for v, sym in names.items():
        if v in vft:
            continue
        slots = []
        a = v
        while a in img.rset and img.intext(img.u32(a)) and (a == v or a not in starts):
            slots.append(img.u32(a))
            a += 4
        if slots:
            out[v] = (sym, 0, slots, 0)
    return out


def data_code_pointers(img):
    """Every relocated word outside .text whose value is an address in .text."""
    t0, ts = img.text
    return [(r, v) for r in img.relocs if not (t0 <= r < t0 + ts) for v in (img.u32(r),) if t0 <= v < t0 + ts]


def xc_table(img, ptrs):
    """The longest run of consecutive code pointers bounded by null words is the CRT's __xc_a..__xc_z table."""
    best, cur = [], []
    for a, v in ptrs:
        if cur and a == cur[-1][0] + 4:
            cur.append((a, v))
        else:
            if len(cur) > len(best) and img.u32(cur[0][0] - 4) == 0 and img.u32(cur[-1][0] + 4) == 0:
                best = cur
            cur = [(a, v)]
    if len(cur) > len(best) and img.u32(cur[0][0] - 4) == 0 and img.u32(cur[-1][0] + 4) == 0:
        best = cur
    return [v for _, v in best], (best[0][0] if best else 0)


class Analyzer:
    """Per-function facts by recursive descent from the entry."""

    def __init__(self, img, vft):
        self.img = img
        self.vft = vft
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True
        self.rb = {}
        for i in range(1, X.X86_REG_ENDING):
            n = self.md.reg_name(i)
            if n in _SUB:
                self.rb[i] = BIT[_SUB[n]]
        self.t0, self.ts = img.text
        self.traw = img.secs['.text'][2]

    def _mask(self, ids):
        m = 0
        for i in ids:
            m |= self.rb.get(i, 0)
        return m

    def analyze(self, a, size, bound):
        """size: decomp size or 0 (then the function ends before `bound`, the next known entry)."""
        img, md = self.img, self.md
        lo, hi = a, (a + size if size else bound)
        d, t0, traw, relocs = img.d, self.t0, self.traw, img.relocs
        ins = {}          # addr -> [size, reads, writes, succs, calltarget/None]
        work = [a]
        F = dict(a=a, size=size, ret=set(), calls=[], tails=[], icalls=[], itails=[], stores=[], vrefs=[],
                 data=[], codeptrs=[], imports=[], jt=[], cleanup=[], fs=0, x87=0, bad=0)
        pend = {}         # reg bit -> vftable loaded by `mov reg, imm`
        last = {}         # reg id -> displacement of the last `mov reg, [reg2+disp]`
        while work:
            p = work.pop()
            if p in ins or not (lo <= p < hi):
                continue
            prev_call = None
            for i in md.disasm(d[traw + p - t0:traw + hi - t0], p):
                p = i.address
                if p in ins:
                    break
                rd, wr = i.regs_access()
                R, W = self._mask(rd), self._mask(wr)
                ops = i.operands
                nx = p + i.size
                succ = [nx]
                ct = None
                iid = i.id
                # relocated operand words of this instruction
                k = bisect.bisect_left(relocs, p)
                rv = []
                while k < len(relocs) and relocs[k] < nx:
                    rv.append((relocs[k], img.u32(relocs[k])))
                    k += 1
                jt_base = None
                if i.prefix[1] == X.X86_PREFIX_FS or any(o.type == X.X86_OP_MEM and o.mem.segment == X.X86_REG_FS for o in ops):
                    F['fs'] = 1
                if X.X86_GRP_FPU in i.groups:
                    F['x87'] = 1
                if prev_call is not None:      # caller clean-up right after a call: `add esp, n` or `pop ecx`
                    if iid == X.X86_INS_ADD and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP and ops[1].type == X.X86_OP_IMM:
                        F['cleanup'].append((prev_call, ops[1].imm))
                        prev_call = None
                    elif iid == X.X86_INS_POP and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ECX:
                        F['cleanup'].append((prev_call, 4))
                        prev_call = None
                    else:
                        if iid != X.X86_INS_CALL:
                            F['cleanup'].append((prev_call, 0))
                        prev_call = None
                if iid == X.X86_INS_CALL:
                    prev_call = p
                    o = ops[0]
                    if o.type == X.X86_OP_IMM:
                        ct = o.imm & 0xffffffff
                        F['calls'].append((p, ct))
                    elif o.type == X.X86_OP_MEM:
                        m = o.mem
                        if m.base == 0 and m.index == 0:
                            s = m.disp & 0xffffffff
                            (F['imports'] if s in img.iat else F['icalls']).append((p, s, 'call') if s in img.iat else (p, 'abs', 0, s))
                        else:
                            F['icalls'].append((p, 'mem', md.reg_name(m.base) if m.base else '', m.disp))
                        ct = -1
                    else:
                        F['icalls'].append((p, 'reg', i.op_str, last.get(o.reg)))
                        ct = -1
                    W |= BIT['eax'] | BIT['ecx'] | BIT['edx']
                    last = {}
                    pend = {}
                elif iid == X.X86_INS_JMP:
                    o = ops[0]
                    succ = []
                    if o.type == X.X86_OP_IMM:
                        t = o.imm & 0xffffffff
                        if lo <= t < hi:
                            succ = [t]
                        else:
                            F['tails'].append((p, t))
                            ct = t
                    elif o.type == X.X86_OP_MEM:
                        m = o.mem
                        s = m.disp & 0xffffffff
                        if m.index and m.scale == 4 and (nx - 4) in img.rset:
                            jt_base = s
                            n = 0
                            while (s + 4 * n) in img.rset and lo <= img.u32(s + 4 * n) < hi:
                                succ.append(img.u32(s + 4 * n))
                                n += 1
                            F['jt'].append((p, s, n))
                        elif m.base == 0 and m.index == 0 and s in img.iat:
                            F['imports'].append((p, s, 'jmp'))
                        else:
                            F['itails'].append((p, 'mem', md.reg_name(m.base) if m.base else '', m.disp))
                    else:
                        F['itails'].append((p, 'reg', i.op_str, last.get(o.reg)))
                elif iid in (X.X86_INS_RET, X.X86_INS_RETF):
                    F['ret'].add(ops[0].imm if ops else 0)
                    succ = []
                elif iid == X.X86_INS_INT3 or iid == X.X86_INS_HLT or iid == X.X86_INS_UD2:
                    succ = []
                elif X.X86_GRP_JUMP in i.groups:      # jcc, loop, jecxz
                    t = ops[0].imm & 0xffffffff
                    if lo <= t < hi:
                        succ.append(t)
                    else:
                        F['tails'].append((p, t))
                elif iid == X.X86_INS_PUSH and ops[0].type == X.X86_OP_REG:
                    b = self.rb.get(ops[0].reg, 0)
                    R &= ~b
                    if b:
                        ins.setdefault(('push', p), b)
                elif len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                    o0, o1 = ops
                    b = self.rb.get(o0.reg, 0)
                    same = o1.type == X.X86_OP_REG and o1.reg == o0.reg
                    if same and iid in (X.X86_INS_XOR, X.X86_INS_SUB, X.X86_INS_SBB, X.X86_INS_PXOR, X.X86_INS_XORPS):
                        R &= ~b
                    elif same and iid == X.X86_INS_MOV:
                        R &= ~b
                        W &= ~b
                    elif o1.type == X.X86_OP_IMM and ((iid == X.X86_INS_OR and o1.imm & 0xffffffff == 0xffffffff)
                                                      or (iid == X.X86_INS_AND and o1.imm == 0)) and o0.size == 4:
                        R &= ~b
                    if iid == X.X86_INS_LEA and o1.mem.base == o0.reg and not o1.mem.index and not o1.mem.disp:
                        R &= ~b               # padding nop
                        W &= ~b
                    if iid == X.X86_INS_MOV:
                        if o1.type == X.X86_OP_MEM and o1.mem.index == 0 and o1.mem.base:
                            last[o0.reg] = o1.mem.disp
                        else:
                            last.pop(o0.reg, None)
                        if o1.type == X.X86_OP_IMM and (o1.imm & 0xffffffff) in self.vft and rv:
                            pend[o0.reg] = o1.imm & 0xffffffff
                            F['vrefs'].append((p, 'mov-reg', pend[o0.reg]))
                            rv = [(r_, v_) for r_, v_ in rv if v_ != pend[o0.reg]]
                        else:
                            pend.pop(o0.reg, None)
                    else:
                        last.pop(o0.reg, None)
                        pend.pop(o0.reg, None)
                elif iid == X.X86_INS_MOV and len(ops) == 2 and ops[0].type == X.X86_OP_MEM:
                    m = ops[0].mem
                    base = md.reg_name(m.base) if m.base else ''
                    idx = md.reg_name(m.index) if m.index else ''
                    if ops[1].type == X.X86_OP_IMM and (ops[1].imm & 0xffffffff) in self.vft and rv:
                        v = ops[1].imm & 0xffffffff
                        F['stores'].append((p, 'imm', base + ('+' + idx if idx else ''), m.disp, v))
                        rv = [(r_, v_) for r_, v_ in rv if v_ != v]
                    elif ops[1].type == X.X86_OP_REG and ops[1].reg in pend:
                        F['stores'].append((p, 'reg', base + ('+' + idx if idx else ''), m.disp, pend[ops[1].reg]))
                    elif ops[1].type == X.X86_OP_REG and m.base in (X.X86_REG_ESP, X.X86_REG_EBP) and not m.index:
                        b = self.rb.get(ops[1].reg, 0) & CALLEE_SAVED   # saving one to the frame is weak evidence, like push
                        R &= ~b
                        if b:
                            ins[('push', p)] = b
                for r_, v_ in rv:
                    if v_ == jt_base:
                        continue
                    if v_ in self.vft:
                        F['vrefs'].append((p, i.mnemonic, v_))
                    elif self.t0 <= v_ < self.t0 + self.ts:
                        F['codeptrs'].append((p, v_))
                    elif v_ in img.iat:
                        if iid not in (X.X86_INS_CALL, X.X86_INS_JMP):
                            F['imports'].append((p, v_, 'ref'))
                    else:
                        F['data'].append((p, v_))
                ins[p] = [i.size, R, W, succ, ct]
                if not succ or succ[0] != nx:
                    work.extend(succ)
                    break
                work.extend(succ[1:])
                if not (lo <= nx < hi):
                    break
            else:
                if lo <= p < hi and p not in ins:
                    F['bad'] = 1      # undecodable bytes on a reached path
        # forward "still holds its entry value on some path" analysis
        pushes = {k[1]: v for k, v in ins.items() if isinstance(k, tuple)}
        addrs = sorted(k for k in ins if not isinstance(k, tuple))
        IN = dict.fromkeys(addrs, 0)
        if a in IN:
            IN[a] = ALL
        chg = True
        while chg:
            chg = False
            for p in addrs:
                out = IN[p] & ~ins[p][2]
                for s in ins[p][3]:
                    if s in IN and out & ~IN[s]:
                        IN[s] |= out
                        chg = True
        live = push = ic = 0
        callin = collections.Counter()
        for p in addrs:
            sz, R, W, succ, ct = ins[p]
            live |= R & IN[p]
            push |= pushes.get(p, 0) & IN[p]
            if ct is not None:
                if ct == -1:
                    ic |= IN[p]
                else:
                    callin[ct] |= IN[p]
        for p, t in F['tails']:
            callin[t] |= IN.get(p, 0)
        F.update(n=len(addrs), bytes=sum(ins[p][0] for p in addrs), end=(max(p + ins[p][0] for p in addrs) if addrs else a),
                 local=live, pushin=push, icin=ic, callin=dict(callin), ret=tuple(sorted(F['ret'])))
        return F


_AN = None


def _init(exe, units):
    global _AN
    img = Image(exe)
    _AN = Analyzer(img, named_vftables(img, json.load(open(units)), rtti_vftables(img)))


def _run(job):
    return [_AN.analyze(*j) for j in job]


def interprocedural_live(fn):
    """live(F) = local(F) | U over direct calls and tail jumps (live(target) & entry-valued-at-site)."""
    for F in fn.values():
        # a body that reads five or more registers before writing them captures a context (CRT failure paths);
        # it takes no register arguments
        F['ctx'] = bin(F['local']).count('1') >= 5
        F['live'] = 0 if F['ctx'] else F['local']
    chg = True
    while chg:
        chg = False
        for F in fn.values():
            v = F['live']
            for t, m in F['callin'].items():
                T = fn.get(t)
                if T is not None and not F['ctx']:
                    v |= T['live'] & m
            if v != F['live']:
                F['live'] = v
                chg = True


def build(exe, units, jobs=4, log=print):
    import multiprocessing
    img = Image(exe)
    u = json.load(open(units))
    rtti = rtti_vftables(img)
    vft = named_vftables(img, u, rtti)
    ptrs = data_code_pointers(img)
    xc, xc_va = xc_table(img, ptrs)
    dec = {}
    for un in u['units']:
        for f in un['functions']:
            dec[int(f['address'], 16)] = (f['size'], f['symbol'], un['source'])
    known = dict.fromkeys(dec, 0)
    for a in dec:
        known[a] = dec[a][0]
    fn = {}
    todo = set(dec)
    for _, v in ptrs:
        todo.add(v)
    rnd = 0
    with multiprocessing.Pool(jobs, _init, (exe, units)) as pool:
        while todo:
            rnd += 1
            starts = sorted(set(known) | todo | set(fn))
            jobs_ = []
            for a in sorted(todo):
                k = bisect.bisect_right(starts, a)
                bound = starts[k] if k < len(starts) else img.text[0] + img.text[1]
                jobs_.append((a, known.get(a, 0), bound))
            chunks = [jobs_[i:i + 400] for i in range(0, len(jobs_), 400)]
            new = set()
            for res in pool.imap_unordered(_run, chunks):
                for F in res:
                    fn[F['a']] = F
                    for _, t in F['calls'] + F['tails'] + F['codeptrs']:
                        if img.intext(t) and t not in fn and t not in todo:
                            new.add(t)
            log('round %d: analyzed %d, new targets %d' % (rnd, len(jobs_), len(new)))
            # a target inside an already analyzed decomp function is a mid-function entry, not a function
            ds = sorted(dec)
            keep = set()
            for t in new:
                k = bisect.bisect_right(ds, t) - 1
                if k >= 0 and t < ds[k] + dec[ds[k]][0]:
                    continue
                keep.add(t)
            todo = keep
    for a, F in fn.items():
        F['decomp'] = a in dec
        F['sym'] = dec[a][1] if a in dec else ''
        F['unit'] = dec[a][2] if a in dec else ''
    interprocedural_live(fn)
    meta = dict(version=VERSION, exe_sha256=img.sha256, tool_sha256=hashlib.sha256(open(__file__, 'rb').read()).hexdigest(),
                relocs=len(img.relocs), vftables=len(vft), vftables_rtti=len(rtti), functions=len(fn), decomp=len(dec), ptrs=len(ptrs),
                xc=len(xc), xc_va=xc_va, imports=len(img.imports))
    return dict(meta=meta, base=img.base, secs=img.secs, relocs=img.relocs, imports=img.imports, vft=vft, ptrs=ptrs,
                xc=xc, fn=fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()
    facts = build(a.exe, a.units, a.jobs, log=lambda s: print(s, file=sys.stderr))
    with open(a.out, 'wb') as fh:
        pickle.dump(facts, fh, protocol=4)
    print(json.dumps(facts['meta'], indent=1))


if __name__ == '__main__':
    main()
