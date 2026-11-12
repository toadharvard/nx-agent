#!/usr/bin/env python3
"""Chain gate: multi-function call chains, x86 (Unicorn, real image, real callees) against the lifted closure (AArch64 ILP32, Unicorn).
    chain.py [--n 300] [--seeds 2] [--jobs 3] [--cap 60] [--lifter DIR] [--work DIR] [--phf-full]
A chain = a decomp root + the closure of its direct calls and tail jumps (<= 40 functions, every one lifts, no indirect call/jump, no import, no fs:).
Compared per run: call trace (entry address, esp at entry), 8 registers, x87 top/ST0, every written byte, the return address of the root.
Everything exe-derived goes to --work (default /home/user/l3work/chain/run), never the repo. Inputs FNVNX_EXE, FNVNX_UNIVERSE."""
import argparse, collections, hashlib, multiprocessing as mp, os, pickle, statistics, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(LIFT, 'tools'))      # phf lives in the tool root
G = {}
EXCL = ('indcall', 'import', 'jmp-indirect-untabled', 'fs')
CHKSTK = 0xec8660                      # __chkstk / _alloca_probe: returns with a changed esp
LIBC = ('.text\n.weak fnvl_raise_av\nfnvl_raise_av: ret\n.globl memcpy\nmemcpy: mov x3,x0\n1: cbz x2,2f\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memset\nmemset: mov x3,x0\n1: cbz x2,2f\n strb w1,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memmove\nmemmove: cmp x0,x1\n b.ls 3f\n add x3,x0,x2\n add x1,x1,x2\n1: cbz x2,2f\n ldrb w4,[x1,#-1]!\n strb w4,[x3,#-1]!\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '3: mov x3,x0\n4: cbz x2,2b\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 4b\n')


def load_universe(path):
    ent, kids = {}, collections.defaultdict(list)
    for l in open(path):
        if l[0] == '#': continue
        a, kind, ext, fl = l.rstrip('\n').split('\t'); a = int(a, 16); par = None
        for f in fl.split(','):
            if f.startswith('parent='): par = int(f[7:], 16)
        ent[a] = (kind, int(ext), par)
    for a, (k, e, p) in ent.items():
        if p is not None: kids[p].append(a)
    return ent, kids


def lift_args(a):
    kind, ext, par = G['ent'][a]; base, size = (par, G['ent'][par][1]) if par is not None else (a, ext)
    return dict(addr=base, size=size, start=a, owns=G['kids'].get(base, ()))


def meta_one(a):
    L = G['L']
    try:
        t, m = L.lift(G['img'], **lift_args(a)); return a, None, m['calls'], m['tails'], tuple(m['kinds']), m['n'], m['tabtails']
    except L.Unsupported as e: return a, str(e)[:60], (), (), (), 0, ()
    except Exception as e: return a, 'error ' + type(e).__name__, (), (), (), 0, ()


def load_meta(a):
    src = b''.join(open(os.path.join(a.lifter, p), 'rb').read() for p in sorted(['fnvlift/core.py', 'fnvlift/f_flow.py', 'fnvlift/f_int.py', 'fnvlift/f_x87.py', 'fnvlift/f_x87_ctl.py', 'rt/fnvl.h', 'rt/fnvl_x87.h']))
    f = '%s/meta-%s.pkl' % (a.work, hashlib.sha1(src + open(__file__, 'rb').read() + str(os.stat(a.universe).st_mtime).encode()).hexdigest()[:12])
    if os.path.exists(f): return pickle.load(open(f, 'rb'))
    t0 = time.time()
    with mp.get_context('fork').Pool(a.jobs) as p: res = {r[0]: r[1:] for r in p.imap_unordered(meta_one, sorted(G['ent']), 300)}
    pickle.dump(res, open(f, 'wb')); print('meta of %d entries in %.0fs' % (len(res), time.time() - t0), file=sys.stderr); return res


def closure(root, meta, ent, limit=40):
    """-> (nodes in BFS order, depth, why-not)"""
    seen = {root: 0}; q = [root]; i = 0
    while i < len(q):
        u = q[i]; i += 1; m = meta.get(u)
        if m is None: return None, 0, 'target not an entry'
        if m[0]: return None, 0, 'unlifted: ' + m[0].split(' at ')[0]
        bad = [k for k in EXCL if k in m[3]]
        if bad: return None, 0, 'has ' + bad[0]
        for t in m[1] + m[2] + m[5]:
            if t not in seen:
                seen[t] = seen[u] + 1; q.append(t)
                if len(q) > limit: return None, 0, 'closure > %d' % limit
    return q, max(seen.values()), None


def features(q, meta):
    f = set(); ix = {a: i for i, a in enumerate(q)}
    adj = {a: [t for t in meta[a][1] if t in ix] for a in q}; tl = {a: [t for t in meta[a][2] if t in ix] for a in q}
    if any(tl[a] for a in q): f.add('tail')
    if any('retn' in meta[a][3] for a in q): f.add('retn')
    if any('jumptable' in meta[a][3] for a in q): f.add('jumptable')
    if CHKSTK in ix: f.add('chkstk')
    def reach(a):
        s, st = set(), list(adj[a] + tl[a])
        while st:
            u = st.pop()
            if u not in s: s.add(u); st += adj[u] + tl[u]
        return s
    if any(a in reach(a) for a in q): f.add('recursion')
    return f


def spread(xs, n):
    return xs if len(xs) <= n else [xs[i * len(xs) // n] for i in range(n)]


def select(a, meta, ent):
    acc, why, forest = [], collections.Counter(), {}
    for r in sorted(x for x, e in ent.items() if e[0] == 'decomp'):
        q, d, w = closure(r, meta, ent)
        if q is None: why[w] += 1; continue
        forest[r] = dict(q=q, depth=d, f=features(q, meta)); acc.append(r)
    pick = []
    nb = a.n
    for i in range(nb):
        b = acc[i * len(acc) // nb:(i + 1) * len(acc) // nb]
        if b: pick.append(max(b, key=lambda r: (forest[r]['depth'], len(forest[r]['q']), -r)) if i % 3 else b[0])
    extra = {}
    for ft in ('tail', 'retn', 'recursion', 'jumptable', 'chkstk'):
        cand = [r for r in acc if ft in forest[r]['f']]
        if ft == 'chkstk': cand = sorted(cand, key=lambda r: (CHKSTK not in meta[r][1], r))[:max(20, a.cap)]; cand = sorted(cand)
        extra[ft] = (len(cand), spread(cand, max(a.cap, 20) if ft == 'chkstk' else a.cap))
    sel = list(dict.fromkeys(pick + [r for ft in extra for r in extra[ft][1]]))
    q, d, w = closure(CHKSTK, meta, ent)       # the changed-esp helper as a root of its own (20 seeds: different sizes), few real roots call it
    if q: forest[CHKSTK] = dict(q=q, depth=d, f={'chkstk'}); sel.append(CHKSTK)
    return acc, why, forest, sel, extra


def compile_all(a, funcs):
    """funcs: sorted addresses -> (elf, bad {addr: reason})"""
    import chrun; T = chrun.T
    flags = [f for f in T.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-DFNVL_TRACE', '-I', a.lifter + '/rt']; od = a.work + '/obj'; os.makedirs(od, exist_ok=True)
    texts = {}
    for f in funcs: texts[f] = G['L'].lift(G['img'], **lift_args(f))[0]
    chunks = [funcs[i:i + 40] for i in range(0, len(funcs), 40)]
    with mp.get_context('fork').Pool(a.jobs) as p: outs = p.map(comp_chunk, [(G['L'].PRELUDE + '\n'.join(texts[f] for f in c), flags, od) for c in chunks])
    bad, objs = {}, []
    for c, (o, err) in zip(chunks, outs):
        if o: objs.append(o); continue
        for f in c:     # a chunk failed: find the functions that do not compile
            o2, e2 = comp_chunk((G['L'].PRELUDE + texts[f], flags, od))
            if o2: objs.append(o2)
            else: bad[f] = e2[:70]
    good = [f for f in funcs if f not in bad]
    syms = {f: 'L_%08x' % f for f in good}
    return objs, syms, bad, flags


def comp_chunk(t):
    text, flags, od = t; h = hashlib.sha1((text + ' '.join(flags)).encode()).hexdigest()[:16]; o = '%s/%s.o' % (od, h)
    if os.path.exists(o): return o, ''
    open(o[:-2] + '.cpp', 'w').write(text)
    r = subprocess.run(['aarch64-linux-gnu-g++'] + flags + [o[:-2] + '.cpp', '-o', o], capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
    if r.returncode: return None, (r.stderr.split('error:', 1) + [''])[1].strip().split('\n')[0]
    os.remove(o[:-2] + '.cpp'); return o, ''


def link(a, objs, syms, flags, ldextra=()):
    import phf
    CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-w', '-c']
    d = a.work; h = phf.build(list(syms))
    open(d + '/tab.S', 'w').write(phf.table_asm(syms, h)); open(d + '/libc.S', 'w').write(LIBC)
    for src, extra in (('%s/rt/rt.c' % a.lifter, []), ('%s/rt/x87.c' % a.lifter, ['-I', '%s/rt' % a.lifter]), ('%s/rt/rcptab.c' % a.lifter, []), (d + '/tab.S', []), (d + '/libc.S', []), (HERE + '/harness.S', [])):
        subprocess.check_call(['aarch64-linux-gnu-gcc'] + CF + extra + [src, '-o', '%s/%s.o' % (d, os.path.basename(src)[:-2])])
    elf = d + '/chain.elf'; open(d + '/objs.txt', 'w').write('\n'.join(sorted(objs)))
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0x%x' % 0xE0000000, '-e', '0', '-o', elf] + list(ldextra) + [d + '/rt.o', d + '/x87.o', d + '/rcptab.o', d + '/tab.o', d + '/libc.o', d + '/harness.o'] + sorted(objs), capture_output=True, text=True)
    if r.returncode: sys.exit('link failed: ' + r.stderr[:300])
    return elf


def work(job):
    import chrun
    root, ents, seeds = job
    try: return root, chrun.run_chain(G['im'], root, ents, seeds)
    except Exception as e: return root, [('harness', '%s: %s' % (type(e).__name__, e), 0, 0, 0, 0, 0)]


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--n', type=int, default=300); A('--seeds', type=int, default=2); A('--jobs', type=int, default=3); A('--cap', type=int, default=60)
    A('--lifter', default=LIFT); A('--work', default='/home/user/l3work/chain/run'); A('--phf-full', action='store_true')
    A('--exe', default=os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); A('--universe', default=os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv'))
    a = ap.parse_args(); t0 = time.time(); os.makedirs(a.work, exist_ok=True); sys.path.insert(0, a.lifter)
    import fnvlift as L, chrun
    G.update(L=L, img=L.Image(a.exe)); G['ent'], G['kids'] = load_universe(a.universe)
    chrun.T.G['img'] = G['img']
    meta = load_meta(a); ent = G['ent']
    undec = sorted(x for x, m in meta.items() if m[0] and 'undecoded' in m[0])
    acc, why, forest, sel, extra = select(a, meta, ent)
    funcs = sorted(set(f for r in sel for f in forest[r]['q']))
    print('universe %d entries: lifted %d, unlifted %d (%d fall into undecoded bytes)' % (len(meta), sum(1 for m in meta.values() if not m[0]), sum(1 for m in meta.values() if m[0]), len(undec)))
    print('roots with an accepted closure: %d of %d decomp; rejected: %s' % (len(acc), sum(1 for e in ent.values() if e[0] == 'decomp'), dict(why.most_common(6))))
    sz = collections.Counter(min(len(forest[r]['q']), 41) for r in acc); bins = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 20), (21, 40)]
    print('closure size, accepted roots: ' + ' '.join('%d-%d:%d' % (lo, hi, sum(v for k, v in sz.items() if lo <= k <= hi)) for lo, hi in bins))
    dp = collections.Counter(forest[r]['depth'] for r in acc); print('depth (BFS levels), accepted roots: ' + ' '.join('%d:%d' % kv for kv in sorted(dp.items())[:10]))
    print('available by feature: ' + ' '.join('%s %d' % (k, v[0]) for k, v in extra.items()) + '; selected %d chains, %d functions' % (len(sel), len(funcs)))
    objs, syms, bad, flags = compile_all(a, funcs)
    G['im'] = chrun.Image(link(a, objs, syms, flags)); print('built %d functions (%d do not compile: %s) in %.0fs' % (len(syms), len(bad), dict(collections.Counter(bad.values()).most_common(3)), time.time() - t0))
    run = [r for r in sel if all(f in syms for f in forest[r]['q'])]
    with mp.get_context('fork').Pool(a.jobs) as p: res = dict(p.imap_unordered(work, [(r, set(forest[r]['q']), 20 if r == CHKSTK else a.seeds) for r in run], 1))
    report(a, sel, run, forest, res, syms, t0)


def report(a, sel, run, forest, res, syms, t0):
    im = G['im']; c = collections.Counter(); diffs = []; ratios = {}; fe = collections.defaultdict(collections.Counter); nx = na = nd = ndisp = ntr = 0
    for r in run:
        pr = []
        for v, d, x, y, dd, nc, tr in res[r]:
            k = v if v != 'skip' else 'skip: ' + d; c[k if v != 'diff' else 'different'] += 1
            for ft in forest[r]['f']: fe[ft][v] += 1
            if v == 'diff' or v == 'harness': diffs.append('%x %s' % (r, d))
            if v == 'equal': y2 = y - 4 * tr; nx += x; na += y2; nd += dd; ndisp += nc; ntr += tr; pr.append(y2 / x)
        if pr: ratios[r] = statistics.median(pr)
    print('chains selected %d, run %d, functions covered %d; runs %d: equal %d, different %d, skipped %d (%s)' % (len(sel), len(run), len(syms), sum(c.values()), c['equal'], c['different'] + c['harness'], sum(v for k, v in c.items() if k.startswith('skip')), '; '.join('%s %d' % kv for kv in sorted(c.items()) if kv[0].startswith('skip'))))
    for d in diffs[:int(os.environ.get('CHAIN_DIFFS', 5))]: print('  DIFF ' + d[:190])
    print('features (chains in run; runs equal/diff/skip): ' + '; '.join('%s %d (%d/%d/%d)' % (ft, sum(1 for r in run if ft in forest[r]['f']), fe[ft]['equal'], fe[ft]['diff'], fe[ft]['skip']) for ft in ('tail', 'retn', 'recursion', 'jumptable', 'chkstk')))
    sx = sum(v[1] for v in (G['ent'][f] for f in syms)); sa = sum(im.sym[s][1] for s in syms.values())
    if nx: print('executed insns, equal runs: x86 %d, A64 lifted %d + dispatcher %d (x%.2f lifted only, x%.2f total; trace calls subtracted), median per chain x%.2f; dispatcher %.1f insns per call (%d calls); bytes: x86 %d, A64 %d (x%.2f)' % (nx, na, nd, na / nx, (na + nd) / nx, statistics.median(ratios.values()), nd / max(ndisp, 1), ndisp, sx, sa, sa / sx))
    print('time %.0fs' % (time.time() - t0))


if __name__ == '__main__': main()
