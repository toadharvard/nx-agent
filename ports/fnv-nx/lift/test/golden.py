#!/usr/bin/env python3
"""Golden gate for lifter refactors: lift every function of funcs.tsv and print one digest of all outputs.

    golden.py <module dir> [--jobs N]     module dir holds the fnvlift package

Prints: functions, lifted, the failure-reason census, and sha256 over (address, C text | failure reason) in address
order. Two lifters are output-identical exactly when the digests are equal. Inputs: FNVNX_EXE (the player's exe),
FNVNX_FUNCS (address<TAB>size list; exe-derived, lives only in fnv-nx-build)."""
import sys, os, hashlib, collections, multiprocessing as mp
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
FUNCS = os.environ.get('FNVNX_FUNCS', '/home/user/l3facts/funcs.tsv')
UNIVERSE = os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv')   # default mode: every universe entry (lift_entry); --decomp: the decomp functions of funcs.tsv only (comparable with older digests)

def load(moddir):
    sys.path.insert(0, moddir)
    import fnvlift as L
    return L

def work(arg):
    moddir, k, n, whole = arg
    L = load(moddir); img = L.Image(EXE); out = []
    rows = [l.split('\t')[:2] for l in open(UNIVERSE if whole else FUNCS) if l[0] != '#']
    for a, s in rows[k::n]:
        a = int(a, 16); s = int(s) if not whole else a
        try: t, m = L.lift_entry(img, a) if whole else L.lift(img, a, s); out.append((a, 1, t))
        except L.Unsupported as e: out.append((a, 0, 'unsupported: ' + str(e)))
    return out

if __name__ == '__main__':
    moddir = os.path.abspath(sys.argv[1]); whole = '--decomp' not in sys.argv; n = int(sys.argv[sys.argv.index('--jobs') + 1]) if '--jobs' in sys.argv else 3
    with mp.Pool(n) as p: rs = p.map(work, [(moddir, k, n, whole) for k in range(n)])
    rows = sorted(r for part in rs for r in part); h = hashlib.sha256(); c = collections.Counter()
    for a, ok, t in rows:
        h.update(b'%08x %d ' % (a, ok)); h.update(t.encode()); h.update(b'\n')
        if not ok: c[t] += 1
    print('%s %d lifted %d failed %d reasons %d' % ('entries' if whole else 'functions', len(rows), sum(r[1] for r in rows), sum(c.values()), len(c)))
    for k, v in c.most_common(): print('%6d %s' % (v, k))
    print('sha256', h.hexdigest())
