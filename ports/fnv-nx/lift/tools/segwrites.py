#!/usr/bin/env python3
"""segwrites: per image, the write sites of each data segment register (es ds fs gs) and, for each, whether the selector it loads is PROVEN NON-NULL, by rule over the decoded runs of the facts
(format fnvnx-segwrites/2).

    segwrites.py --out segwrites.json --facts FACTS --modules MODULES_TSV --exe EXE --moddirs D1:D2 --x86sys X86SYS [--lift DIR]

WRITE SITES: `mov S, r/m16`, `pop S`, `lds/les/lfs/lgs` (S = the register the mnemonic names). cs and ss are not analysed: loading them with null is #GP (x86sys mov_to_sreg), so they are never null.
SOURCE of a write, found by a backward walk (at most DEPTH instructions) along the control-flow graph of the decoded runs where each instruction has exactly ONE predecessor and is no universe entry
(a join, or an entry that is called from elsewhere, ends the walk):
  * `mov S, r16/r32`: the last instruction before the site that writes the register family r. Non-null iff it is `mov r, imm` with imm & 0xfffc != 0 (a loadable selector, x86sys mov_to_sreg),
    `mov r, S'` (a read of a segment register: non-null iff S' is non-null in the image, below), `mov r, r2` (continue with r2), or `pop r` (continue with the stack walk);
  * `pop S`: the matching `push X` found by the stack walk (depth counted over push/pop/pushfd/popfd; any other write of esp ends it): X is `push imm` / `push S'` / `push r` as above;
  * every other source (a memory operand, a call/int/sysenter or any instruction between that may write the register, a walk that ends, lds/les/lfs/lgs: a far pointer from memory) is POSSIBLY NULL.
S' NON-NULL IN THE IMAGE (greatest fixpoint): S' starts non-null iff its initial selector (x86sys selectors) is non-null (ds es fs); S' stays non-null iff every write of S' is proven non-null given the
S'' that are non-null. cs and ss are always non-null.
PROOF ARGUMENT (soundness): a write is non-null only when the walk reaches a defining instruction that is a nonzero immediate, a read of a segment register that is itself non-null (fixpoint), a register move
whose source register is recursively proven by the same walk, or a pop whose matching push is proven by the same walk. `mov r, r2` and `pop r` carry no value of their own. Every way the walk can stop short
(the DEPTH bound, a join, an entry, a barrier, an unknown instruction, a memory operand) returns possibly-null, so the bound and the cut-offs cost precision and never soundness; a "guard" flag is only ever
cleared by a proof.
SELECTOR SETS (field `selectors`, per es ds fs gs ss; absent or null = unbounded): the least fixpoint, from the declared initial selector (x86sys selectors), of the sets of selectors the register can hold: a write whose value the same walk bounds
contributes its constants (`mov r, imm`, `push imm`, a read of cs: its declared value) and the whole set of a register it reads (`mov r, S'`, `push S'`); every other source is unbounded, and an unbounded write makes the set unbounded.
Context restores load captured selectors (the platform's rule, as above). The lifter proves with them that the code-selector arm of a store through S is dead (proof/lift_dsl.py).
The lifter (core.gs_guard) guards an access through the effective segment S iff S is not non-null in the image (its initial selector is null, or some write of S is possibly null).
Context restores (SetThreadContext, exception continuation) load the selectors a context holds, which are the captured ones: the platform's rule, not this tool's."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = sys.argv[sys.argv.index('--lift') + 1]      # the lift/ directory: a declared input, always an argument
sys.path[:0] = [HERE, LIFT]
from capstone import x86 as X
from fnvlift.image import Image
from proof import census, shape



def universe(path):
    out = {}
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); out[int(f[0], 16)] = f[1]
    return out


def universe(path):
    out = {}
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); out[int(f[0], 16)] = f[1]
    return out


def decode(img, ranges):
    """the decoded instructions of the facts' runs, one at a time (a generator: the exe has millions)"""
    base, rngs = census.read_ranges(ranges); md = shape.decoder(); seen = set()
    for lo, hi in rngs:
        for g in md.disasm(img.bytes(lo, hi - lo), lo):
            if g.address not in seen:
                seen.add(g.address); yield g


STOP = ('ret', 'retn', 'int3', 'ud2', 'hlt')
BARRIER = ('call', 'int', 'into', 'sysenter', 'syscall', 'iret', 'iretd', 'leave', 'enter', 'pushal', 'popal', 'pushad', 'popad', 'rep', 'xchg', 'cmpxchg', 'xadd')
DEPTH = 32
FAMILY = {}
for _f, _n in (('eax', 'eax ax al ah'), ('ecx', 'ecx cx cl ch'), ('edx', 'edx dx dl dh'), ('ebx', 'ebx bx bl bh'), ('esp', 'esp sp'), ('ebp', 'ebp bp'), ('esi', 'esi si'), ('edi', 'edi di')):
    for _x in _n.split(): FAMILY[_x] = _f
SEGREG = ('es', 'cs', 'ss', 'ds', 'fs', 'gs')
LXS = {'les': 'es', 'lds': 'ds', 'lfs': 'fs', 'lgs': 'gs'}
SEGS = ('es', 'ds', 'fs', 'gs')
VSEGS = SEGS + ('ss',)          # the registers whose SELECTOR SET is derived: ss too, because `mov ds, ss` reads it (an ss write is a write site of the set domain only)
YES = (True, frozenset())
NO = (False, frozenset())


class NullDomain:
    """value = (proven non-null, the segment registers its non-nullness depends on)"""
    top = NO
    def __init__(self, sel): pass
    def imm(self, v): return YES if (v & 0xfffc) != 0 else NO      # a loaded selector with a nonzero index (a loadable one, else the load is #GP and no value is loaded)
    def seg(self, n): return YES if n in ('cs', 'ss') else (True, frozenset([n]))


class SetDomain:
    """value = (the selectors it can be, a frozenset of 16-bit values, or None = unbounded; the segment registers whose set it also includes).  A write the walk cannot bound is None."""
    top = (None, frozenset())
    def __init__(self, sel): self.cs = frozenset([sel['cs']['value'] & 0xffff])      # cs is never loaded by a mov: its value is the declared one
    def imm(self, v): return (frozenset([v & 0xffff]), frozenset())
    def seg(self, n): return (self.cs, frozenset()) if n == 'cs' else (frozenset(), frozenset([n]))


def write_site(g, segs=SEGS):
    """-> (S, kind) for a write of a register of `segs`, else None"""
    m = g.mnemonic.split(' ')[-1]; o = g.operands
    if m in LXS and LXS[m] in segs: return LXS[m], 'far'
    if m == 'lss' and 'ss' in segs: return 'ss', 'far'
    if m in ('mov', 'pop') and o and o[0].type == X.X86_OP_REG:
        n = g.reg_name(o[0].reg)
        if n in segs: return n, m
    return None


def succs(g, tables):
    a = g.address; m = g.mnemonic.split(' ')[-1]; nxt = a + g.size; o = g.operands; s = []
    imm = o[0].imm & 0xffffffff if o and o[0].type == X.X86_OP_IMM else None
    if m == 'jmp':
        if imm is not None: s.append(imm)
        else: s += tables.get(a, [])
    elif m in STOP: pass
    elif m == 'call':
        if imm is not None: s.append(imm)
        s.append(nxt)
    elif (m.startswith('j') or m in ('loop', 'loope', 'loopne')) and imm is not None: s += [imm, nxt]
    else: s.append(nxt)
    return s


class Walk:
    """backward walks over a decoded image: pred(a) is the one predecessor of a, or None (a join, an entry, no predecessor)"""
    def __init__(self, img, addrs, npred, pred1, dom):
        self.img, self.addrs, self.npred, self.pred1, self.D = img, addrs, npred, pred1, dom; self.md = shape.decoder()

    def at(self, a):
        return next(iter(self.md.disasm(self.img.bytes(a, 16), a, 1)))

    def pred(self, a):
        return self.pred1.get(a) if self.npred.get(a) == 1 else None

    def imm_val(self, v): return self.D.imm(v)

    def reg_val(self, a, r, n=DEPTH):
        """the selector value in the register family r just BEFORE instruction a: (proven non-null, segment registers its non-nullness depends on)"""
        while n > 0:
            p = self.pred(a)
            if p is None: return self.D.top
            g = self.at(p); m = g.mnemonic.split(' ')[-1]; n -= 1
            if m in BARRIER or g.mnemonic.split(' ')[0] in ('rep', 'repe', 'repne'): return self.D.top
            wr = {FAMILY.get(g.reg_name(x), g.reg_name(x)) for x in g.regs_access()[1]}
            if r not in wr: a = p; continue
            o = g.operands
            if m == 'mov' and len(o) == 2 and o[0].type == X.X86_OP_REG and FAMILY.get(g.reg_name(o[0].reg)) == r and o[0].size >= 2:
                if o[1].type == X.X86_OP_IMM: return self.imm_val(o[1].imm)
                if o[1].type == X.X86_OP_REG:
                    n1 = g.reg_name(o[1].reg)
                    if n1 in SEGREG: return self.D.seg(n1)
                    if n1 in FAMILY and o[1].size == o[0].size: r = FAMILY[n1]; a = p; continue
                return self.D.top
            if m == 'pop' and len(o) == 1 and o[0].type == X.X86_OP_REG and FAMILY.get(g.reg_name(o[0].reg)) == r and o[0].size >= 2:
                return self.stack_val(p, 0, n)
            return self.D.top
        return self.D.top

    def stack_val(self, a, d, n=DEPTH):
        """the selector value of the stack word at depth d (0 = top) just BEFORE instruction a"""
        while n > 0:
            p = self.pred(a)
            if p is None: return self.D.top
            g = self.at(p); m = g.mnemonic.split(' ')[-1]; n -= 1; o = g.operands
            if m == 'push' and len(o) == 1:
                if d == 0:
                    if o[0].type == X.X86_OP_IMM: return self.imm_val(o[0].imm)
                    if o[0].type == X.X86_OP_REG:
                        n1 = g.reg_name(o[0].reg)
                        if n1 in SEGREG: return self.D.seg(n1)
                        if n1 in FAMILY and o[0].size >= 2: return self.reg_val(p, FAMILY[n1], n)
                    return self.D.top
                d -= 1
            elif m == 'pop' and len(o) == 1: d += 1
            elif m in ('pushfd', 'pushf'):
                if d == 0: return self.D.top
                d -= 1
            elif m in ('popfd', 'popf'): d += 1
            elif m in BARRIER or 'esp' in {FAMILY.get(g.reg_name(x), g.reg_name(x)) for x in g.regs_access()[1]}: return self.D.top
            a = p
        return self.D.top


def analyse(img, ranges, tables, uni, init_null, sel):
    """-> {S: {initial_null, guard, nonnull, maynull, maynull_sites, selectors, selector_sources}} for the image (see the module doc).  `selectors`: the sorted list of the selectors S can hold (its initial
    selector and every value a write site loads), or None = unbounded (a write the walk cannot bound); `selector_sources`: the initial row and one row per write site with the values it contributes."""
    addrs = set(); sites = []
    for g in decode(img, ranges):
        addrs.add(g.address)
        if write_site(g, VSEGS): sites.append(g.address)
    npred = {}; pred1 = {}
    for g in decode(img, ranges):
        for t in succs(g, tables):
            if t in addrs: npred[t] = npred.get(t, 0) + 1; pred1[t] = g.address
    for e in uni:
        if e in addrs: npred[e] = npred.get(e, 0) + 1          # an entry is also entered from outside the graph
    def collect(dom):
        w = Walk(img, addrs, npred, pred1, dom); rows = {s: [] for s in VSEGS}
        for a in sorted(sites):
            g = w.at(a); s, kind = write_site(g, VSEGS); o = g.operands
            if kind == 'far': r = dom.top
            elif kind == 'pop': r = w.stack_val(a, 0)
            elif len(o) == 2 and o[1].type == X.X86_OP_REG and g.reg_name(o[1].reg) in FAMILY: r = w.reg_val(a, FAMILY[g.reg_name(o[1].reg)])
            else: r = dom.top
            rows[s].append((a, '%s %s' % (g.mnemonic, g.op_str), r))
        return rows
    rows = collect(NullDomain(sel))
    rows = {s: rows[s] for s in SEGS}
    nn = {s for s in SEGS if not init_null[s]}                  # greatest fixpoint: the registers proven non-null in the image
    while True:
        bad = {s for s in nn if any(not ok or not d <= nn for _, _, (ok, d) in rows[s])}
        if not bad: break
        nn -= bad
    vrows = collect(SetDomain(sel))
    cur = {s: frozenset([sel[s]['value'] & 0xffff]) for s in VSEGS}       # least fixpoint of the selector sets, from the declared initial selectors; None = unbounded
    while True:
        new = {}
        for s in VSEGS:
            v = cur[s]
            for _, _, (vals, deps) in vrows[s]:
                if v is None: break
                if vals is None or any(cur[d] is None for d in deps): v = None
                else: v = v | vals | frozenset().union(*[cur[d] for d in deps])
            new[s] = v
        if new == cur: break
        cur = new
    out = {}
    for s in VSEGS:
        d = {'selectors': None if cur[s] is None else sorted(cur[s]),
             'selector_sources': ['initial %#06x' % (sel[s]['value'] & 0xffff)] + ['%08x %s -> %s' % (a, t, 'unbounded' if v[0] is None else ' '.join('%#06x' % x for x in sorted(v[0])) + (' + set of ' + ','.join(sorted(v[1])) if v[1] else '')) for a, t, v in vrows[s]]}
        if s in SEGS:
            good = [x for x in rows[s] if x[2][0] and x[2][1] <= nn]; bad = [x for x in rows[s] if x not in good]
            d.update(initial_null=init_null[s], guard=s not in nn, nonnull=len(good), maynull=len(bad), maynull_sites=['%08x %s' % (a, t) for a, t, _ in bad])
        out[s] = d
    return out


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--out', required=True); A('--facts', required=True); A('--modules', required=True); A('--lift'); A('--exe', required=True); A('--moddirs', required=True); A('--x86sys', required=True)
    a = ap.parse_args(); import pemod
    sel = json.load(open(a.x86sys))['selectors']
    init_null = {s: (sel[s]['value'] & 0xfffc) == 0 for s in SEGS}; sel_all = {s: sel[s] for s in VSEGS + ('cs',)}       # an undecided selector is a KeyError: no value is guessed
    imgs = [('exe', a.exe, None, a.facts)]
    for n, row in sorted(pemod.modules(a.modules).items()):
        path, _ = pemod.locate(n, a.modules, a.moddirs.split(':'))
        imgs.append((os.path.splitext(n)[0].lower(), path, row.base, os.path.join(a.facts, os.path.splitext(n)[0].lower())))
    res = {}
    for name, path, base, d in imgs:
        tables = {}; jt = os.path.join(d, 'ehtables.json')
        if os.path.isfile(jt):
            for t in json.load(open(jt)).get('jtab_tables', []): tables[int(t['site'], 16)] = [int(x, 16) for x in t['targets']]
        res[name] = analyse(Image(path, base), os.path.join(d, 'ranges.tsv'), tables, universe(os.path.join(d, 'universe.tsv')), init_null, sel_all)
        print(name, {s: (v['nonnull'], v['maynull'], v['guard']) for s, v in res[name].items() if s in SEGS}, {s: v['selectors'] for s, v in res[name].items()})
    with open(a.out, 'w') as f: json.dump({'format': 'fnvnx-segwrites/2', 'images': res}, f, indent=1, sort_keys=True); f.write('\n')


if __name__ == '__main__': main()
