#!/usr/bin/env python3
"""Value range of the index of an indexed store [disp + idx*scale], by the jtab interval domain (tools/jtab.py: wrapped intervals mod 2^32, branch refinement on the reached flags definition, join over ALL predecessors,
widening at loop heads, no step cap), carried across calls: a function entered only by direct calls has, for each incoming parameter n (stack slot esp_entry + 4 + 4n), the JOIN over every call site of the interval of
the pushed argument at that site (computed by the same analysis in the caller, whose own parameters are joined the same way). A function that can be entered otherwise (a relocated or exported address, an entry reached by a
jump from another function, no decoded call site, a recursive chain, an exception-handler entry) has unknown parameters. A value loaded from memory that is not a tracked frame cell is unknown: the rule never narrows a range
that the code does not bound. Result per store: (lo, hi) mod 2^32 or None with the reason; the source of the index is classified from its defining instruction (param#n@entry, ebp-slot@va, reg@va).
Premises of "entered only by direct calls" (each is a named fact; the entry is unknown, never joined, if any is open):
  (a) no relocated value names the entry (R3 after the displacement-only refinement; here ANY relocated dword whose value is the entry makes it unknown, which is a superset);
  (b) no PIC / computed code address: the exe-checked fact `no PIC`; (c) no escape-open path: the gate `no_s4_arith` (pcsrc escapes) must have no OPEN item that can form the entry's address:
  `escape_open(e)` supplies it; while no such fact is supplied the closed intervals are labelled `cond:no_s4_arith` in the why column, so a consumer sees the premise it depends on;
  (d) no jump or fall-through predecessor (the entry is not also entered from another function), no exception-handler / TLS / export kind: checked by `pure_entry`.
Basis: closure completeness (every call site is a decoded edge, ASM-202); frame cells are tracked only when no instruction of the cone forms the frame address (jtab.flow)."""
import re
import jtab as JT
from jtab import St, TOP, N, M32, FULL

NP = 8


class Ranger:
    def __init__(s, dec, ins_at, preds, callers_of, entry_of, pure_entry, seed_other, u8, escape_open=None):
        """dec/ins_at/preds as for jtab.resolve; callers_of(e) -> decoded call-site addresses of the entry e; entry_of(a) -> the entry of the function containing a; pure_entry(e) -> None if e is entered only by
        direct calls, else the reason it is not; u8 as for jtab.resolve."""
        s.dec = dec; s.ins_at = ins_at; s.preds = preds; s.callers_of = callers_of; s.entry_of = entry_of; s.pure = pure_entry; s.seed_other = seed_other; s.u8 = u8; s.esc = escape_open
        s.P = {}; s.A = {}; s.busy = set()

    def seedfn(s, e):
        pv = s.params(e)
        def seed(x):
            if x == e:
                st = St({}, None)
                for n, v in enumerate(pv[0]):
                    if v != TOP: st.r[('c', 'esp0', 4 + 4 * n)] = v
                for n, w in enumerate(pv[1]): st.why['p%d' % n] = w
                return st
            if s.is_seed(x): return St({}, None, why={'*': 'entry %x entered from outside the decoded edges' % x})
            return None
        return seed

    def is_seed(s, x):
        return s.seed_other(x)

    def params(s, e):
        """-> ((interval per parameter), (reason per parameter))"""
        if e in s.P: return s.P[e]
        top = ((TOP,) * NP, ('unknown',) * NP)
        why = s.pure(e)
        if why is None and s.esc is not None and s.esc(e): why = 'an escape-open path (no_s4_arith OPEN) can form its address'
        if why is not None: return top[0], ('entry %x: %s' % (e, why),) * NP
        if e in s.busy: return top[0], ('recursive call chain at %x' % e,) * NP
        s.busy.add(e)
        try:
            cs = s.callers_of(e); acc = [None] * NP; rs = [''] * NP
            if not cs: return top[0], ('entry %x has no decoded call site' % e,) * NP
            for c in cs:
                av, aw = s.args(c)
                for n in range(NP):
                    acc[n] = av[n] if acc[n] is None else JT.join(acc[n], av[n])
                    if av[n] == TOP and not rs[n]: rs[n] = aw[n]
            res = (tuple(a if a is not None else TOP for a in acc), tuple(rs[n] or 'join over %d call sites' % len(cs) for n in range(NP)))
        finally:
            s.busy.discard(e)
        s.P[e] = res
        return res

    def args(s, c):
        """-> (interval, reason) per outgoing argument at the call site c"""
        if c in s.A: return s.A[c]
        e = s.entry_of(c)
        st, cells = JT.flow(c, s.dec, s.ins_at, s.preds, s.seedfn(e), s.u8)
        iv = []; ws = []
        for n in range(NP):
            v = TOP; w = 'call site %x: argument %d is not a tracked stack value' % (c, n)
            if st is not None:
                if n < len(st.pk): v = st.pk[-1 - n]; w = 'pushed argument at %x' % c
                elif st.espd is not None and cells and ('c', 'esp0', st.espd + 4 * n) in st.r: v = st.r[('c', 'esp0', st.espd + 4 * n)]; w = 'stack cell argument at %x' % c
            iv.append(v); ws.append(w if v == TOP else 'bound')
        s.A[c] = (tuple(iv), tuple(ws))
        return s.A[c]

    def index_range(s, va, reg):
        """-> (lo, hi, source, why)"""
        e = s.entry_of(va)
        st, cells = JT.flow(va, s.dec, s.ins_at, s.preds, s.seedfn(e), s.u8)
        if st is None: return None, None, 'reg@%x' % va, 'the store is unreachable from any entry in the decoded edges'
        v = st.g(reg); w = st.why.get(reg, '')
        src = s.source(reg, w, va, e)
        if v == TOP: return None, None, src, 'unknown: ' + (w or 'no definition in the cone') + ('; ' + st.why['*'] if '*' in st.why else '')
        return v[0], (v[0] + v[1]) % N, src, ('' if s.esc is not None else 'cond:no_s4_arith ') + 'interval by the jtab domain; defined by ' + (w or 'seed')

    def source(s, reg, w, va, e):
        m = re.search(r'\[ebp \+ (0x[0-9a-f]+|\d+)\]', w)
        if m and int(m.group(1), 0) >= 8 and (int(m.group(1), 0) - 8) % 4 == 0 and w.startswith('mov'): return 'param#%d@%x' % ((int(m.group(1), 0) - 8) // 4, e)
        m = re.search(r'\[ebp - (0x[0-9a-f]+|\d+)\]', w)
        if m: return 'ebp-slot@%x' % int(w.rsplit(' at ', 1)[1], 16)
        return 'reg@%x' % va


def run(rows, rg, out, image):
    """rows: [(store_va, index_reg)] -> idxrange.tsv lines `store_va index_src lo hi why`; returns the counts (known, unknown)"""
    lines = []; k = u = 0
    for va, reg in rows:
        lo, hi, src, why = rg.index_range(va, FULL[reg])
        if lo is None: u += 1; lines.append('%08x\t%s\tunknown\tunknown\t%s' % (va, src, why))
        else: k += 1; lines.append('%08x\t%s\t%08x\t%08x\t%s' % (va, src, lo, hi, why))
    open(out, 'w').write(''.join(l + '\n' for l in lines))
    return k, u
