#!/usr/bin/env python3
"""Gate R3 (exe universe):  universe_r3_check.py [FACTS_DIR]   (default env FNVNX_FACTS_OUT or /home/user/l3facts; exe env FNVNX_EXE)
Recomputes from the exe alone the set of relocated pointer targets in executable sections (PE .reloc HIGHLOW fixups whose stored value lies
in an executable section) and checks (a) every target is an entry of universe.tsv; (b) gate_codemap.json is green and codemap.tsv classifies
every byte of the executable sections exactly once (contiguous runs, no UNEXPLAINED). Prints counts only. Exit 0 iff all hold."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import json, pemod
def main():
    fd = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts')
    M = pemod.load(os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); xs = [(a, a + z) for n, a, z, ro in M.xs]
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
    print('RESULT: ' + ('FAIL (%s)' % ', '.join(bad) if bad else 'PASS')); return 1 if bad else 0
sys.exit(main())
