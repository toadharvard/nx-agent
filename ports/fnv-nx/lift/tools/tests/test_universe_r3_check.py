#!/usr/bin/env python3
"""Gate R3 (exe universe):  universe_r3_check.py [FACTS_DIR] [--exe PATH] [--base HEX] [--x86sys FILE] [--fpstate DIR] [--fpdepth FILE] [--no-c]
Defaults: FACTS_DIR and the exe are the declared inputs of this test (inputs.json: the gate gives them as FNVNX_INPUT_FACTS / FNVNX_INPUT_EXE, the checkouts pinned in
verify/contract_inputs.json); --exe/--base name the image under test (modules_check passes a module and the base the loader assigned); fpstate and fpdepth default to
FACTS_DIR/fpstate and FACTS_DIR/fpmodes/depth.tsv; --x86sys (the platform's X86SYS output) is required by (c).
Recomputes from the exe alone the set of relocated pointer targets in executable sections (PE .reloc HIGHLOW fixups whose stored value lies
in an executable section) and checks (a) every target is an entry of universe.tsv; (b) gate_codemap.json is green and codemap.tsv classifies
every byte of the executable sections exactly once (contiguous runs, no UNEXPLAINED); (c) closure under direct targets: lifting every entry with fnvlift
(lift_entry over FACTS_DIR/universe.tsv) the direct call targets and the jmp/jcc/loop targets leaving the range (meta calls, tails) are all entries
(entries whose lift raises Unsupported are counted by reason class and do not enter (c); --no-c skips it). Prints counts only. Exit 0 iff all hold."""
import os, sys, re, multiprocessing as mp
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import json, pemod
def opt(name, default=None):
    a = sys.argv; return a[a.index(name) + 1] if name in a else default
def conf():
    """(exe path, base or None, facts dir) from argv, else the declared inputs of the gate"""
    pos = [x for i, x in enumerate(sys.argv[1:], 1) if not x.startswith('-') and sys.argv[i - 1] not in ('--exe', '--base', '--x86sys', '--fpstate', '--fpdepth')]
    fd = pos[0] if pos else os.environ['FNVNX_INPUT_FACTS']      # declared input of this test (inputs.json)
    exe = opt('--exe') or os.path.join(os.environ['FNVNX_INPUT_EXE'], 'FalloutNV.exe')
    b = opt('--base'); return exe, int(b, 16) if b else None, fd
def lift_part(arg):
    """worker: lift entries[k::n] -> (missing target -> number of entries naming it, reason class -> count, entries lifted, direct targets seen)"""
    exe, base, fd, x86sys, fps, fpd, k, n = arg; sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
    import fnvlift as L
    L.inputs.configure(universe=fd + '/universe.tsv', fpstate=fps or fd + '/fpstate', fpdepth=fpd or fd + '/fpmodes/depth.tsv', x86sys=x86sys)
    img = L.Image(exe, base); ent = [int(l.split('\t')[0], 16) for l in open(fd + '/universe.tsv') if l.strip() and not l.startswith('#')]; es = set(ent)
    miss = Counter(); why = Counter(); ne = Counter(); ok = seen = 0
    for a in ent[k::n]:
        try: t, m = L.lift_entry(img, a)
        except L.Unsupported as e: why['unsupported: ' + re.sub(r'[0-9a-f]{5,8}', 'ADDR', str(e))] += 1; continue
        ok += 1; ts = set(m['calls']) | set(m['tails']); seen += len(ts)
        for x in ts:
            if x not in es: miss[x] += 1
        ne.update(m['noentry'])
    return miss, why, ok, seen, ne
def check_c(exe, base, fd, ix):
    x86sys = opt('--x86sys')
    if not x86sys: raise SystemExit('(c) needs --x86sys (the platform X86SYS output); --no-c leaves (c) out of the run')
    with mp.Pool(2) as p: rs = p.map(lift_part, [(exe, base, fd, x86sys, opt('--fpstate'), opt('--fpdepth'), k, 2) for k in range(2)])
    miss = Counter(); why = Counter(); ne = Counter()
    for m, w, o, s, n in rs: miss.update(m); why.update(w); ne.update(n)
    ok = sum(r[2] for r in rs); seen = sum(r[3] for r in rs)
    print('(c) closure under direct targets: %d entries lifted, %d direct call/tail references, targets that are not entries: %d (named by %d lifted entries)' % (ok, seen, len(miss), sum(miss.values())))
    print('(c) lift raised (counted apart, not in (c)): %d; by reason class: %s' % (sum(why.values()), dict(why.most_common(8))))
    out = [v for v in miss if not ix(v)]; ins = sorted(v for v in miss if ix(v))
    print('(c) of them outside every executable section: %d %s; inside executable sections: %d %s' % (len(out), ' '.join('%08x' % v for v in sorted(out)[:6]), len(ins), ' '.join('%08x' % v for v in ins[:6])))
    nin = sorted(v for v in ne if ix(v))
    print('(c) direct targets (calls, jumps, fall-throughs) the lifter routed to the dispatcher because they are not entries: %d distinct (%d references), inside executable sections: %d %s' % (len(ne), sum(ne.values()), len(nin), ' '.join('%08x' % v for v in nin[:6])))
    return miss + Counter(dict.fromkeys(nin, 1))
def main():
    EXE, base, fd = conf()
    M = pemod.load(EXE, base); xs = [(a, a + z) for n, a, z, ro in M.xs]
    ix = lambda v: any(a <= v < b for a, b in xs)
    tg = {M.u32(r) for r in (pemod.relocs(M) or ())}; tg = {v for v in tg if ix(v)}
    ent = {int(l.split('\t')[0], 16) for l in open(fd + '/universe.tsv') if l.strip() and not l.startswith('#')}
    miss = sorted(tg - ent); bad = []
    print('relocated pointer targets in executable sections: %d, entries %d, missing %d' % (len(tg), len(tg) - len(miss), len(miss)))
    if miss: bad.append('missing entries')
    g = json.load(open(fd + '/gate_codemap.json')); print('gate_codemap.json: status %s, universe %d, certified %d, uncertified %d' % (g['status'], g['universe'], g['certified'], g['uncertified']))
    if g['status'] != 'green' or g['uncertified']: bad.append('gate not green')
    T0 = min(a for a, b in xs); T1 = max(b for a, b in xs); pos = T0; tot = unx = gaps = ovl = 0
    for l in open(fd + '/codemap.tsv'):
        a, n, c, o = l.rstrip('\n').split('\t'); a = int(a, 16); n = int(n)
        if a < pos: ovl += 1
        elif a > pos and any(ix(x) for x in (pos, a - 1)): gaps += 1
        pos = max(pos, a + n)
        if c != 'hole': tot += n
        if c == 'UNEXPLAINED': unx += n
    need = sum(b - a for a, b in xs)
    print('codemap.tsv: classified %d of %d executable bytes, unexplained %d, overlapping runs %d, gaps %d' % (tot, need, unx, ovl, gaps))
    if tot != need or unx or ovl or gaps: bad.append('codemap not total')
    if '--no-c' not in sys.argv and check_c(EXE, base, fd, ix): bad.append('targets that are not entries')
    print('RESULT: ' + ('FAIL (%s)' % ', '.join(bad) if bad else 'PASS')); return 1 if bad else 0
if __name__ == '__main__': sys.exit(main())
