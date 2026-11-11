#!/usr/bin/env python3
"""Independent frame-escape analysis (function-level, conservative).  Output: per entry a PRIVATE/ESC verdict
plus the escape events (condition 1..4, tag, address).  Usage: frameesc2.py OUTDIR [nproc]"""
import struct, sys, collections
from multiprocessing import Pool
import capstone
from capstone import x86 as X

EXE, FACTS = '/home/user/game/FalloutNV.exe', '/home/user/l3facts/'
FULL = {'eax': 0, 'ecx': 1, 'edx': 2, 'ebx': 3, 'esi': 4, 'edi': 5, 'ebp': 6}
FAM = {}
for n, fam in (('a', 'eax'), ('c', 'ecx'), ('d', 'edx'), ('b', 'ebx')):
    for s in ('x', 'l', 'h'):
        FAM[n + s] = fam
for r in FULL:
    FAM[r] = r
    FAM[r[1:]] = r
for r in ('si', 'di', 'bp'):
    FAM[r] = 'e' + r
    FAM[r + 'l'] = 'e' + r
    FAM['e' + r] = 'e' + r
CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True
IMG = None
SECS = []


def load_pe():
    global IMG, SECS
    d = open(EXE, 'rb').read()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    ns = struct.unpack_from('<H', d, pe + 6)[0]
    osz = struct.unpack_from('<H', d, pe + 20)[0]
    base = struct.unpack_from('<I', d, pe + 52)[0]
    off = pe + 24 + osz
    for i in range(ns):
        vs, va, rs, ro = struct.unpack_from('<4I', d, off + 8 + i * 40)
        SECS.append((base + va, base + va + max(vs, rs), ro, rs))
    IMG = d


def read(va, n):
    for lo, hi, ro, rs in SECS:
        if lo <= va < hi:
            o = ro + va - lo
            return IMG[o:o + n]
    return b''


def parse_ranges(path):
    out = {}
    for ln in open(path):
        if ln.startswith('#'):
            continue
        e, r = ln.split()[:2]
        out[int(e, 16)] = [tuple(int(x, 16) for x in p.split('-')) for p in r.split(',')]
    return out


def decode(ranges):
    ins = {}
    for lo, hi in ranges:
        for i in CS.disasm(read(lo, hi - lo), lo):
            ins[i.address] = i
    return ins


def reg(i, r):
    return i.reg_name(r)


class Fn:
    """Facts about the whole family (root + funclets)."""
    pass


CALLEE = {}   # entry -> (esp_odd, retimm or None)
RANGES = {}


def callee_info(t):
    """(modifies esp oddly, ret imm) of a direct callee; None if unknown."""
    if t in CALLEE:
        return CALLEE[t]
    res = None
    if t in RANGES:
        odd, rets = False, set()
        for a, i in decode(RANGES[t]).items():
            if i.mnemonic == 'ret':
                rets.add(i.operands[0].imm if i.operands else 0)
            if esp_write_odd(i):
                odd = True
        res = (odd, rets.pop() if len(rets) == 1 else None)
    CALLEE[t] = res
    return res


def esp_write_odd(i):
    """Writes esp other than push/pop/call/ret/add|sub esp,imm and the ebp restores (leave counted odd here)."""
    m = i.mnemonic
    if m == 'leave':
        return False
    for o in i.operands:
        if o.type == X.X86_OP_REG and reg(i, o.reg) == 'esp' and (o.access & capstone.CS_AC_WRITE):
            if m in ('add', 'sub') and len(i.operands) == 2 and i.operands[1].type == X.X86_OP_IMM:
                return False
            if m == 'mov' and i.operands[1].type == X.X86_OP_REG and reg(i, i.operands[1].reg) == 'ebp':
                return False
            if m == 'lea' and i.operands[1].mem.base == X.X86_REG_EBP and not i.operands[1].mem.index:
                return False
            return True
    return False


def is_fp(root_ins, root, fam_ins):
    """ebp is a frame pointer: push ebp; mov ebp,esp at entry and no other write of ebp but pop ebp / leave."""
    a = sorted(root_ins)
    if len(a) < 2 or a[0] != root:
        return False
    i0, i1 = root_ins[a[0]], root_ins[a[1]]
    if not (i0.mnemonic == 'push' and i0.op_str == 'ebp' and i1.mnemonic == 'mov' and i1.op_str == 'ebp, esp'):
        return False
    for ad, i in fam_ins.items():
        if ad == a[1]:
            continue
        if i.mnemonic in ('pop', 'leave'):
            continue
        rd, wr = i.regs_access()
        if any(i.reg_name(r) == 'ebp' or FAM.get(i.reg_name(r)) == 'ebp' for r in wr):
            return False
    return True


class Ana:
    def __init__(self, ins, fp, root, funclet):
        self.ins, self.fp, self.root, self.funclet = ins, fp, root, funclet
        self.ev = []
        self.order = sorted(ins)
        self.nxt = {a: b for a, b in zip(self.order, self.order[1:])}

    def e(self, cond, tag, i):
        self.ev.append((cond, tag, i.address))

    def isframe(self, r):
        """capstone reg id -> True if esp or frame-pointer ebp."""
        n = CS.reg_name(r) if r else ''
        return n == 'esp' or (n == 'ebp' and self.fp)

    def slot(self, o, d):
        """Constant-offset frame slot key (entry-relative) of a memory operand, else None."""
        m = o.mem
        if m.index or m.base == 0:
            return None
        b = CS.reg_name(m.base)
        if b == 'esp':
            return None if d is None else d + m.disp
        if b == 'ebp' and self.fp:
            return m.disp - 4
        return None

    def run(self, start, st0):
        states, work = {}, [(start, st0)]
        self.reached = set()
        while work:
            a, st = work.pop()
            if a not in self.ins:
                continue
            if a in states:
                o = states[a]
                n = (o[0] if o[0] == st[0] else None, o[1] | st[1], o[2] | st[2])
                if n == o:
                    continue
                st = n
            states[a] = st
            for s in self.step(self.ins[a], st):
                work.append(s)
        return states

    def step(self, i, st):
        d, regs, slots = st
        m, ops, nx = i.mnemonic, i.operands, self.nxt.get(i.address)
        taint = {r for r, t in regs}
        ttag = dict(regs)
        out = [(nx, None)]
        rd, wr = i.regs_access()
        full_w = {FAM[reg(i, r)] for r in wr if reg(i, r) in FAM and reg(i, r) in FULL}
        # (3) computed frame access
        for o in ops:
            if o.type == X.X86_OP_MEM:
                if o.mem.index and self.isframe(o.mem.base):
                    self.e(3, 'index', i)
                if slots and ((o.mem.base and FAM.get(reg(i, o.mem.base)) in taint) or (o.mem.index and FAM.get(reg(i, o.mem.index)) in taint)):
                    self.e(2, 'ptr-mem-with-slots', i)
                if slots and self.isframe(o.mem.base) and d is None and not o.mem.index and CS.reg_name(o.mem.base) == 'esp':
                    self.e(2, 'untrackable-esp', i)
        # (4) odd esp writes
        if esp_write_odd(i) or (not self.fp and any(o.type == X.X86_OP_REG and reg(i, o.reg) == 'esp' and (o.access & capstone.CS_AC_WRITE) and m in ('mov', 'lea') for o in ops)):
            self.e(4, 'esp-write', i)
            d = None
        if m == 'leave':
            if self.fp:
                d = 0
            else:
                self.e(4, 'leave-nonfp', i)
                d = None
            taint.discard('ebp')
        elif m in ('pushad', 'pushal'):
            if taint:
                self.e(1, 'push', i)
            d = None if d is None else d - 32
        elif m in ('popad', 'popal'):
            taint.clear()
            d = None if d is None else d + 32
        elif m in ('pushfd', 'pushf'):
            d = None if d is None else d - 4
        elif m in ('popfd', 'popf'):
            d = None if d is None else d + 4
        elif m == 'push':
            o = ops[0]
            if o.type == X.X86_OP_REG:
                n = reg(i, o.reg)
                first = self.fp and not self.funclet and i.address == self.root
                if (FAM.get(n) in taint) or (n == 'esp') or (n == 'ebp' and self.fp and not first):
                    self.e(1, 'push', i)
            elif o.type == X.X86_OP_MEM:
                k = self.slot(o, d)
                if k is not None and k in slots:
                    self.e(1, 'slot-push', i)
            if d is not None:
                slots = slots - {d - 4}
                d -= 4
        elif m == 'pop':
            o = ops[0]
            top = slots and d is not None and d in slots
            if o.type == X.X86_OP_REG:
                n = reg(i, o.reg)
                if n == 'esp':
                    self.e(4, 'pop-esp', i)
                elif n in FULL or FAM.get(n) in FULL:
                    taint.discard(FAM.get(n))
                    if top:
                        taint.add(FAM[n])
                        ttag[FAM[n]] = 's'
            d = None if d is None else d + 4
        elif m == 'call':
            o = ops[0]
            direct = o.type == X.X86_OP_IMM
            ci = callee_info(o.imm) if direct else None
            if ci and ci[0]:
                self.e(4, 'alloca-callee', i)
            if taint:
                self.e(1, 'call-reg' + ('' if direct else '-indirect'), i)
            if slots:
                self.e(2, 'call-slots', i)
            taint -= {'eax', 'ecx', 'edx'}
            d = (d + ci[1]) if (direct and ci and ci[1] is not None and d is not None) else None
        elif m == 'ret' or m == 'retn':
            if taint:
                self.e(1, 'ret', i)
            out = []
        elif m == 'jmp':
            o = ops[0]
            if o.type == X.X86_OP_IMM:
                out = [(o.imm, None)]
                if o.imm not in self.ins and (taint or slots):
                    self.e(1, 'jmp-out', i)
            else:
                out = []
                if taint:
                    self.e(1, 'jmpreg', i)
        elif m.startswith('j') or m.startswith('loop'):
            out = [(nx, None)]
            if ops and ops[0].type == X.X86_OP_IMM:
                out.append((ops[0].imm, None))
        elif m in ('ud2', 'int3', 'hlt'):
            out = []
        elif m == 'mov' and len(ops) == 2:
            dst, src = ops
            if dst.type == X.X86_OP_REG:
                n = reg(i, dst.reg)
                if n == 'esp':
                    d = -4 if (src.type == X.X86_OP_REG and reg(i, src.reg) == 'ebp') else None
                elif n in FULL or (n in FAM and dst.size == 4):
                    f = FAM[n]
                    taint.discard(f)
                    if src.type == X.X86_OP_REG:
                        sn = reg(i, src.reg)
                        if FAM.get(sn) in taint and sn in FULL:
                            taint.add(f)
                            ttag[f] = ttag.get(FAM[sn], 'd')
                        elif sn == 'esp' or (sn == 'ebp' and self.fp):
                            if not (n == 'ebp' and self.fp and not self.funclet and self.prologue(i)):
                                taint.add(f)
                                ttag[f] = 'd'
                    elif src.type == X.X86_OP_MEM:
                        k = self.slot(src, d)
                        if k is not None and k in slots:
                            taint.add(f)
                            ttag[f] = 's'
                        elif k is not None and any(s != k and abs(s - k) < 4 for s in slots):
                            self.e(2, 'partial-slot-read', i)
            elif dst.type == X.X86_OP_MEM:
                k = self.slot(dst, d)
                val = None
                if src.type == X.X86_OP_REG:
                    sn = reg(i, src.reg)
                    if FAM.get(sn) in taint and sn in FULL:
                        val = ttag.get(FAM[sn], 'd')
                    elif sn == 'esp' or (sn == 'ebp' and self.fp):
                        val = 'd'
                if val is not None:
                    if k is not None and k < 0 and not self.funclet:
                        slots = slots | {k}
                    elif k is not None:
                        self.e(1, 'store-callerframe' if k >= 0 else 'store-slot-in-funclet', i)
                    else:
                        self.e(1, 'store-ptr' if dst.mem.base else 'store-global', i)
                elif k is not None and dst.size == 4:
                    slots = slots - {k}
                elif k is None and slots and dst.mem.base and not self.isframe(dst.mem.base):
                    self.e(2, 'ptr-store-with-slots', i)
        elif m == 'lea':
            dst, src = ops
            n = reg(i, dst.reg)
            f = FAM.get(n)
            mm = src.mem
            b = reg(i, mm.base) if mm.base else ''
            bi = reg(i, mm.index) if mm.index else ''
            if n == 'esp':
                if b == 'ebp' and self.fp and not mm.index:
                    d = -4 + mm.disp
                else:
                    self.e(4, 'lea-esp', i)
                    d = None
            else:
                tainted_src = False
                if self.isframe(mm.base) and not mm.index:
                    tainted_src = True
                elif FAM.get(b) in taint and not mm.index:
                    tainted_src = True
                elif FAM.get(bi) in taint or FAM.get(b) in taint:
                    self.e(1, 'lea-op', i)
                if f in FULL:
                    taint.discard(f)
                    if tainted_src:
                        taint.add(f)
                        ttag[f] = ttag.get(FAM.get(b), 'd')
        elif m in ('cmp', 'test'):
            pass
        elif m in ('add', 'sub', 'inc', 'dec') and ops and ops[0].type == X.X86_OP_REG and (len(ops) == 1 or ops[1].type == X.X86_OP_IMM):
            n = reg(i, ops[0].reg)
            if n == 'esp':
                if d is not None and len(ops) == 2:
                    d += ops[1].imm if m == 'add' else -ops[1].imm
                else:
                    d = None
        elif m in ('xor', 'sub') and len(ops) == 2 and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
            taint.discard(FAM.get(reg(i, ops[0].reg)))
        elif m == 'xchg' and len(ops) == 2 and ops[0].type == ops[1].type == X.X86_OP_REG:
            a, b = FAM.get(reg(i, ops[0].reg)), FAM.get(reg(i, ops[1].reg))
            ta, tb = a in taint, b in taint
            taint.discard(a)
            taint.discard(b)
            if tb:
                taint.add(a)
            if ta:
                taint.add(b)
        else:
            self.generic(i, taint, slots, d, rd, full_w)
        newregs = frozenset((r, ttag.get(r, 'd')) for r in taint)
        res = []
        for t, _ in out:
            res.append((t, (d, newregs, slots)))
        return res

    def prologue(self, i):
        return i.address == self.second

    def generic(self, i, taint, slots, d, rd, full_w):
        """Any other instruction: reading a frame address or tainted reg (except cmp/test, memory base) escapes; writes kill."""
        mem_regs = set()
        for o in i.operands:
            if o.type == X.X86_OP_MEM:
                mem_regs |= {o.mem.base, o.mem.index}
        explicit = set()
        for o in i.operands:
            if o.type == X.X86_OP_REG and (o.access & capstone.CS_AC_READ):
                n = reg(i, o.reg)
                explicit.add(n)
                if n == 'esp' or (n == 'ebp' and self.fp):
                    self.e(1, 'copy-esp', i)
                elif FAM.get(n) in taint:
                    self.e(1, 'op-on-tainted', i)
        for r in rd:
            n = reg(i, r)
            if r in mem_regs or n in explicit or n == 'esp':
                continue
            if FAM.get(n) in taint and n in FULL:
                if i.mnemonic.startswith(('movs', 'cmps', 'scas', 'lods', 'ins', 'outs')) and FAM[n] in ('esi', 'edi'):
                    if slots:
                        self.e(2, 'string-op-with-slots', i)
                else:
                    self.e(1, 'implicit-op', i)
        for o in i.operands:
            if o.type == X.X86_OP_MEM and slots and (o.access & capstone.CS_AC_READ):
                k = self.slot(o, d)
                if k is not None and any(abs(s - k) < 4 for s in slots):
                    self.e(2, 'slot-read-nonmov', i)
        for f in full_w:
            taint.discard(f)


def analyse_family(root, members, ranges):
    """members: list of entries (root first).  Returns (events, privflag)."""
    ev = []
    allins = {}
    insl = {}
    for e in members:
        insl[e] = decode(ranges[e])
        allins.update(insl[e])
    fp = is_fp(insl[root], root, allins)
    for e in members:
        ana = Ana(insl[e], fp, root, e != root)
        a = sorted(insl[e])
        ana.second = a[1] if len(a) > 1 else -1
        if not a:
            continue
        # root starts at its entry with delta 0; funclets start with unknown delta
        start = a[0] if e == root else a[0]
        init = (0 if e == root else None, frozenset(), frozenset())
        states = ana.run(start, init)
        # seed code not reached (jump tables, ptr targets): worst case state
        ever = set()
        for s in states.values():
            ever |= s[1]
        for ad in a:
            if ad not in states:
                top = (None, frozenset(ever), frozenset())
                states.update(ana.run(ad, top))
        ev += [(c, t, ad, 'F' if e != root else 'R', e) for c, t, ad in ana.ev]
    return ev, fp


def worker(args):
    fams = args
    out = []
    for root, members in fams:
        ev, fp = analyse_family(root, members, RANGES)      # an analysis failure is a bug of the analysis: it propagates (no EXC event, no verdict from a crash)
        out.append((root, members, ev, fp))
    return out


def main():
    global RANGES
    outdir = sys.argv[1]
    nproc = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    load_pe()
    RANGES = parse_ranges(FACTS + 'ranges.tsv')
    parent, kinds = {}, {}
    for ln in open(FACTS + 'universe.tsv'):
        if ln.startswith('#'):
            continue
        f = ln.rstrip('\n').split('\t')
        e = int(f[0], 16)
        kinds[e] = f[1]
        for p in f[3].split(','):
            if p.startswith('parent='):
                parent[e] = int(p[7:], 16)
    fam = collections.defaultdict(list)
    for e in kinds:
        r = e
        seen = 0
        while r in parent and seen < 10:
            r = parent[r]
            seen += 1
        fam[r].append(e)
    roots = sorted(fam)
    chunks = [[(r, [r] + [m for m in fam[r] if m != r]) for r in roots[k::200]] for k in range(200)]
    with Pool(nproc) as p, open(outdir + '/verdicts.tsv', 'w') as fo:
        fo.write('entry\tkind\troot\tpriv\tfp\tconds\tfirst_events\n')
        for res in p.imap_unordered(worker, chunks):
            for root, members, ev, fp in res:
                conds = ''.join(sorted({str(c) for c, *_ in ev}))
                pr = 0 if ev else 1
                fe = ';'.join('%d/%s/%x/%s/%x' % (c, t, ad, w, en) for c, t, ad, w, en in sorted(ev, key=lambda x: x[2])[:40])
                for m in members:
                    fo.write('%08x\t%s\t%08x\t%d\t%d\t%s\t%s\n' % (m, kinds[m], root, pr, fp, conds or '-', fe or '-'))


if __name__ == '__main__':
    main()
