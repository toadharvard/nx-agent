#!/usr/bin/env python3
"""Reachability of sticky-bit READERS after an import returns (item 29 (d)) and after a native body exits (native_fpflags), by a call-graph reachability over the image, no clear in between.

    fpreach.py <pe image> <facts dir> [--native <file of function addresses>]       (run fpstate.py scan on the same image + facts dir first: it writes fpstate/readers.tsv)

Graph (context-insensitive, an over-approximation of the control flow):
  * every instruction of every entry's decoded range (x86flow.decode: intra-function successors, jump tables, tails to other entries);
  * a direct call c -> T: edge to T's entry E(T) and to the call's post point P(c) (the callee may return; the post point is also the return target);
  * an indirect call (register / memory that is not an import slot): edges to every ADDRESS-TAKEN entry (an entry whose address is a relocated value, or an export / TLS callback / the image entry) and to P(c);
  * a ret (or an import thunk `jmp [slot]`) of function g: edges to P(c) of every direct call c of a function f from which g is reachable by tail jumps, and of every indirect call if such an f is address-taken;
  * a call through an import slot: edge to P(c) only (the import itself is the boundary; its own FP effects are the boundary model's, ASM-207).
CLEAR (the path stops): fnclex / fclex / fninit / finit, and the imports _clearfp / _fpreset. READER (the hit): a row of fpstate/readers.tsv of class `escapes` (the sticky bits flow into the value: fnstsw that escapes,
fnstenv, fnsave, fxsave, stmxcsr, the imports _statusfp / _status87 / _clearfp); `cc-only` fnstsw reads observe only C0-C3 and are no hit.
STARTS: P(c) of every call c through an import slot, and of every direct call c to an import thunk; with --native: P(c) of every direct call c to a listed function (all indirect P if one is address-taken).
Output (<facts dir>/fpstate/): reach_imports.tsv (reader, shortest witness path from a start), reach_starts.tsv (start, import, first reader or '-'); counts on stdout."""
import os, sys, bisect
from collections import defaultdict, deque
HERE = os.path.dirname(os.path.abspath(__file__))
if __name__ == '__main__':
    os.environ['FNVNX_FACTS'] = os.path.abspath(sys.argv[2])
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from multiprocessing import Pool
import x86flow as W
from x86flow import X, M32, mn, Fn, load_universe
CLEAR_M = ('fnclex', 'fclex', 'fninit', 'finit')
CLEAR_IMP = ('_clearfp', '_fpreset')
READ_IMP = ('_statusfp', '_status87', '_clearfp')
RD = set()          # reader instruction addresses (set before the pool forks)


def iat_name(i):
    o = i.operands
    if o and o[0].type == X.X86_OP_MEM and not o[0].mem.base and not o[0].mem.index and not o[0].mem.segment:
        return W.IATADDR.get(o[0].mem.disp & M32)
    return None


def work(args):
    a, ext, kind = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, None
    key, kind_of = {}, {}
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands
        nm = iat_name(i) if m in ('call', 'jmp') else None
        if x in RD or (nm in READ_IMP):
            key[x] = ('X', x)
        elif m in CLEAR_M or (nm in CLEAR_IMP):
            key[x] = None          # absorbing: the path stops (a clear that is also a reader (_clearfp) is a reader first)
            kind_of[x] = 'K'
        elif m == 'call':
            key[x] = ('M', x) if nm is not None else (('C', x) if o[0].type == X.X86_OP_IMM else ('I', x))
        elif m == 'ret':
            key[x] = ('RG', a)
        elif m == 'jmp' and nm is not None:
            key[x] = ('RG', a)
        else:
            key[x] = False         # not interesting: passes through
    # S[x] = first interesting nodes reached from x (x included when interesting); clear -> empty
    S = {}
    order = sorted(F.ins)
    for x in order:
        k = key[x]
        S[x] = set() if k is None else ({k} if k else set())
    ch = True
    while ch:
        ch = False
        for x in reversed(order):
            k = key[x]
            if k is not False:
                continue
            acc = set()
            for t in F.succ[x]:
                acc |= S[t]
            for t in F.tails.get(x, ()):
                acc.add(('E', t))
            if acc != S[x]:
                S[x] = acc; ch = True
    out = defaultdict(set); posts = {}
    for x in order:
        k = key[x]
        if k in (None, False):
            continue
        if k[0] in ('C', 'I', 'M'):
            n = x + F.ins[x].size
            out[('P', x)] |= S.get(n, set()) if n in F.ins else set()
            if k[0] == 'C':
                out[k].add(('E', F.ins[x].operands[0].imm & M32))
            out[k].add(('P', x))
    out[('E', a)] |= S[a] if a in S else set()
    tails = sorted({t for x in F.tails for t in F.tails[x]})
    calls = sorted((x, F.ins[x].operands[0].imm & M32) for x in order if key[x] and key[x][0] == 'C')
    imps = sorted((x, iat_name(F.ins[x])) for x in order if key[x] and key[x][0] == 'M')
    thunk = F.ins.get(a) is not None and mn(F.ins[a]) == 'jmp' and iat_name(F.ins[a]) is not None
    inds = sorted(x for x in order if key[x] and key[x][0] == 'I')
    return a, (dict(out), tails, calls, imps, thunk, inds)


def main():
    exe, facts = sys.argv[1], sys.argv[2]
    nat = []
    if '--native' in sys.argv:
        nat = [int(l.split()[0], 16) for l in open(sys.argv[sys.argv.index('--native') + 1]) if l.strip() and not l.startswith('#')]
    raw, rows = load_universe(exe)
    W.parse_imports()
    for l in open(os.path.join(facts, 'fpstate', 'readers.tsv')):
        f = l.rstrip('\n').split('\t')
        if f[2] == 'escapes':
            RD.add(int(f[0], 16))
    ent = {a for a, e, k in rows}
    res = {}
    with Pool(2) as pool:
        for a, r in pool.imap(work, [(a, e, k) for a, e, k in rows], chunksize=100):
            res[a] = r
    adj = defaultdict(set); tail_rev = defaultdict(set); callsites = defaultdict(list); inds = []; thunks = {}
    impsites = []
    for a, r in res.items():
        if r is None:
            continue
        out, tails, calls, imps, th, ix = r
        for k, v in out.items():
            adj[k] |= v
        for t in tails:
            if t in ent:
                tail_rev[t].add(a)
        for x, t in calls:
            callsites[t].append(x)
        impsites += imps
        if th:
            thunks[a] = True
        inds += ix
    thname = {}
    for a in thunks:
        i = next(iter(W.md.disasm(W.IMG.bytes(a, 16), a, 1)))
        thname[a] = iat_name(i)
    tk = set(W.IMG.taken)
    atk = {a for a in ent if a in tk} | {a for a, e, k in rows if k in ('export', 'tls', 'entry')}
    # address-taken closure for indirect calls; return edges of g
    for k in [k for k in list(adj) if k[0] == 'P']:
        pass
    indirect_posts = [('P', x) for x in inds]
    HUB_A, HUB_P = ('IA', 0), ('IP', 0)          # hubs: one edge set instead of (indirect calls x address-taken entries)
    for x in inds:
        adj[('I', x)].add(HUB_A)
    adj[HUB_A] |= {('E', g) for g in atk}
    adj[HUB_P] |= set(indirect_posts)
    rets = set()
    for a, r in res.items():
        if r is not None:
            rets.add(a)
    for g in sorted(rets):
        Q, st = {g}, [g]
        while st:
            f = st.pop()
            for h in tail_rev.get(f, ()):
                if h not in Q:
                    Q.add(h); st.append(h)
        tgt = set()
        for f in Q:
            for c in callsites.get(f, ()):
                tgt.add(('P', c))
        if Q & atk:
            tgt.add(HUB_P)
        adj[('RG', g)] |= tgt
    starts = {}
    for x, nm in impsites:
        starts[('P', x)] = nm
    for t, nm in thname.items():
        for c in callsites.get(t, ()):
            starts[('P', c)] = nm
    nstarts = {}
    for f in nat:
        for c in callsites.get(f, ()):
            nstarts[('P', c)] = 'native:%08x' % f
        if f in atk:
            for p in indirect_posts:
                nstarts[p] = 'native:%08x' % f
    use = nstarts if nat else starts
    # forward multi-source BFS with parents
    par = {}; dq = deque()
    for s in sorted(use):
        par[s] = None; dq.append(s)
    hits = []
    while dq:
        n = dq.popleft()
        if n[0] == 'X':
            hits.append(n); continue
        for m in sorted(adj.get(n, ())):
            if m not in par:
                par[m] = n; dq.append(m)
    def path(n):
        p = []
        while n is not None:
            p.append(n); n = par[n]
        p.reverse()
        return [('%s%08x' % (k[0], k[1]) if isinstance(k[1], int) else str(k)) for k in p if k[0] in ('P', 'E', 'X', 'RG')]
    # per start: does it reach a reader at all (backward from readers)
    radj = defaultdict(set)
    for k, v in adj.items():
        for m in v:
            radj[m].add(k)
    xs = {n for n in par if n[0] == 'X'}
    back = set(xs); dq = deque(sorted(xs))
    while dq:
        n = dq.popleft()
        for m in radj.get(n, ()):
            if m not in back:
                back.add(m); dq.append(m)
    bad = sorted(s for s in use if s in back)
    od = os.path.join(facts, 'fpstate'); tag = 'native' if nat else 'imports'
    with open(os.path.join(od, 'reach_%s.tsv' % tag), 'w') as f:
        for n in sorted(hits):
            f.write('%08x\t%s\n' % (n[1], ' > '.join(path(n))))
    with open(os.path.join(od, 'reach_%s_starts.tsv' % tag), 'w') as f:
        for s in sorted(use):
            f.write('%08x\t%s\t%s\n' % (s[1], use[s], 'reaches-reader' if s in back else '-'))
    print('%s: image %s: starts %d (import call sites %d, calls to import thunks %d); readers (escapes) %d; starts that reach a reader without a clear: %d; distinct readers reachable: %d' % (
        tag, os.path.basename(exe), len(use), len({x for x, _ in impsites}), len(starts) - len({x for x, _ in impsites}), len(RD), len(bad), len(hits)))


if __name__ == '__main__':
    main()
