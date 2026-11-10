#!/usr/bin/env python3
"""vtid - table identity (L1 CONTRACT, M2): every native vtable-pointer value is one exe vftable address.

    vtid.py --facts facts.pkl --obj objfacts.pkl --units units.json --out vtid.pkl [--list N]

A key is (object, `_ZTV` symbol, addend): one sub-vtable of one class as one unit sees it.  Route N is the
binding rule; P and S are set comparisons that verify it and must agree with it:

  P  position: in a function whose native and exe stores have the same shape (same count per object offset),
     the k-th native store at an offset is the k-th exe store at that offset.  Then, with the keys known so
     far as anchors, a function of a different shape binds a key when exactly one order-preserving embedding
     of its native stores into the exe stores exists.
  N  identity by the compiler (the rule): L2 emits, per unit, the pair Itanium `_ZTV` name <-> MSVC
     `??_7Class@@6B...@` names of each polymorphic class (--pairs: object <tab> _ZTV symbol <tab> ??_7 symbol).
     units.json gives the address of the unit's `??_7`; the sub-vtable is the one whose COL offset equals the
     Itanium offset-to-top of the address point.  No name is translated here.  Without --pairs N binds nothing.

  S  slots: the one exe vftable whose slot targets are the exe addresses of the functions the native
     sub-vtable holds (through the address map), allowing the one-slot shift of a doubled destructor.

Verdicts per key: bound (with the routes that voted), CONFLICT (P gives two tables), DISAGREE (routes differ),
unbound.
The gate is: CONFLICT == 0, DISAGREE == 0, unbound == 0; anything else is a BODYSEL row for the functions
that hold the key.  Output is exe- and decomp-derived: container and fnv-nx-build only.
"""
import argparse, collections, json, pickle, re

STK = ('esp', 'ebp')
BR = re.compile(r'__fnv(?:nx_br|nx_vflag)?_([0-9a-f]{8})')


def slot_address(amap, ob, name):
    if not name:
        return None
    m = BR.match(name)
    if m:
        return int(m.group(1), 16)
    return amap.get((ob, name))


def exe_groups(Fx):
    g = collections.defaultdict(list)
    for (site, form, base, disp, V) in sorted(Fx['stores']):
        g[('stk', 0) if base.split('+')[0] in STK else ('obj', disp)].append(V)
    stored = {s[0] for s in Fx['stores']}
    for (site, how, V) in sorted(Fx['vrefs']):
        g[('val', 0)].append(V)
    return g


def nat_groups(vt):
    g = collections.defaultdict(list)
    for (io, sym, add, base, off) in sorted(vt):
        k = ('stk', 0) if base == 'stk' else ('obj', off) if base == 'obj' else ('val', 0)
        g[k].append((sym, add))
    return g


def embeddings(nat, exe, known, limit=2):
    """Order-preserving injections of the native list into the exe list that respect known keys; at most `limit`."""
    out = []

    def rec(i, j, cur):
        if len(out) >= limit:
            return
        if i == len(nat):
            out.append(tuple(cur))
            return
        if len(exe) - j < len(nat) - i:
            return
        for k in range(j, len(exe)):
            v = known.get(nat[i])
            if v is None or v == exe[k]:
                cur.append(exe[k])
                rec(i + 1, k + 1, cur)
                cur.pop()
    rec(0, 0, [])
    return out


def solve(facts, obj, units, amap, pairs):
    fn, vft = facts['fn'], facts['vft']
    unit_of = {}
    usym = {}
    for un in units['units']:
        ob = un['source'].replace('/', '_') + '.o'
        d = {s: int(a, 16) for s, a in un.get('symbols', {}).items() if s.startswith('??_7')}
        usym[ob] = d
    ztv = {k[0]: v for k, v in obj.items() if k[1] == 'ztv'}
    gz = collections.defaultdict(set)
    for ob, d in ztv.items():
        for s, pts in d.items():
            gz[s].add(tuple((x[0], x[1]) for x in pts))
    funcs = {k: v for k, v in obj.items() if k[1] != 'ztv' and v[0]}
    keys = set()
    for (ob, a), v in funcs.items():
        for (io, sym, add, base, off) in v[0]:
            if sym.startswith('_ZTV'):
                keys.add((ob, sym, add))
    st = collections.Counter()
    # route P, pass 1: shape-equal functions
    eq = collections.defaultdict(lambda: collections.defaultdict(set))
    rest = []
    for (ob, a), v in funcs.items():
        Fx = fn.get(a)
        if Fx is None or not (Fx['stores'] or Fx['vrefs']):
            st['functions: exe stores no table'] += 1
            continue
        ex, nat = exe_groups(Fx), nat_groups(v[0])
        if {k: len(x) for k, x in ex.items()} != {k: len(x) for k, x in nat.items()}:
            rest.append((ob, a, ex, nat))
            continue
        st['functions: same shape'] += 1
        for k in ex:
            for V, (sym, add) in zip(ex[k], nat[k]):
                if sym.startswith('_ZTV'):
                    eq[(ob, sym, add)][V].add(a)
                else:
                    st['image-address sites: equal to the exe store' if int(sym[-8:], 16) + (add or 0) == V
                       else 'image-address sites: DIFFER from the exe store'] += 1
    known = {K: next(iter(v)) for K, v in eq.items() if len(v) == 1}
    # route P, pass 2: unique embeddings with anchors, to a fixpoint
    st['functions: different shape'] = len(rest)
    chg = True
    while chg:
        chg = False
        for (ob, a, ex, nat) in rest:
            for k, nl in nat.items():
                xl = ex.get(k)
                if not xl:
                    continue
                kn = {}
                seq = []
                for (sym, add) in nl:
                    if sym.startswith('_ZTV'):
                        K = (ob, sym, add)
                        seq.append(K)
                        if K in known:
                            kn[K] = known[K]
                    else:
                        K = ('img', sym, add)
                        seq.append(K)
                        kn[K] = int(sym[-8:], 16) + (add or 0)
                if all(K in kn for K in seq):
                    continue
                em = embeddings(seq, xl, kn)
                if len(em) == 1:
                    for K, V in zip(seq, em[0]):
                        if K[0] != 'img' and K not in known and V not in eq[K]:
                            eq[K][V].add(a)
                            if len(eq[K]) == 1:
                                known[K] = V
                                chg = True
    P = {K: set(v) for K, v in eq.items() if v}
    # route N: the compiler's own pair (object, `_ZTV` symbol) -> MSVC `??_7...@@6B...@` names, from L2
    Nn = {}
    for K in keys:
        ob, sym, add = K
        if add is None:
            continue
        pts = {x[0]: x[1] for x in (ztv.get(ob, {}).get(sym) or (next(iter(gz[sym])) if len(gz.get(sym, ())) == 1 else ()))}
        if add not in pts:
            continue
        c = {usym[ob][m] for m in pairs.get((ob, sym), ()) if m in usym.get(ob, {})}
        c = {a for a in c if a in vft and vft[a][1] == pts[add]}
        if len(c) == 1:
            Nn[K] = next(iter(c))
    # route S: the exe vftable whose slots are the functions the native sub-vtable holds
    by_slot = collections.defaultdict(set)
    for V, (_, _, slots, _) in vft.items():
        for i, t in enumerate(slots):
            by_slot[(i, t)].add(V)
    S = {}
    for K in keys:
        ob, sym, add = K
        grp = [x for x in ztv.get(ob, {}).get(sym, ()) if x[0] == add]
        if not grp:
            continue
        addrs = [slot_address(amap, ob, n) for n in grp[0][2]]
        score = collections.Counter()
        nk = 0
        for shift in (0, 1):                 # 1: two Itanium destructor slots stand where the exe has one
            known = [(i - shift, t) for i, t in enumerate(addrs) if t is not None and i - shift >= 0]
            nk = max(nk, len(known))
            sc = collections.Counter()
            for it in known:
                for V in by_slot.get(it, ()):
                    sc[V] += 1
            for V, n in sc.items():
                score[V] = max(score[V], n)
        top = score.most_common(2)
        # the table that holds every known slot, and no other table does
        if top and top[0][1] == nk and (len(top) == 1 or top[1][1] < nk):
            S[K] = top[0][0]
    verdict = {}
    bind = {}
    for K in keys:
        votes = {}
        if K in P and len(P[K]) > 1:
            verdict[K] = 'CONFLICT'
            continue
        for r, v in (('P', next(iter(P[K])) if K in P else None), ('N', Nn.get(K)), ('S', S.get(K))):
            if v is not None:
                votes.setdefault(v, []).append(r)
        if len(votes) == 1:
            V, rs = next(iter(votes.items()))
            verdict[K] = 'bound: ' + '+'.join(rs)
            bind[K] = V
        elif votes:
            verdict[K] = 'DISAGREE: ' + ' vs '.join(sorted('+'.join(rs) for rs in votes.values()))
        else:
            verdict[K] = 'unbound' if K[2] is not None else 'unbound: value not stored plainly'
    return dict(keys=keys, P=P, N=Nn, S=S, verdict=verdict, bind=bind, stats=st, funcs=funcs)


def report(R, facts, lst=0):
    vft = facts['vft']
    for k, v in sorted(R['stats'].items()):
        print('%7d  %s' % (v, k))
    c = collections.Counter(R['verdict'].values())
    print('keys (object, _ZTV, addend): %d' % len(R['keys']))
    for k, v in sorted(c.items()):
        print('%7d  %s' % (v, k))
    sites = collections.Counter()
    fbad = set()
    for (ob, a), v in R['funcs'].items():
        for (io, sym, add, base, off) in v[0]:
            if sym.startswith('_ZTV'):
                ok = (ob, sym, add) in R['bind']
                sites['bound' if ok else 'not bound'] += 1
                if not ok:
                    fbad.add(a)
    print('native _ZTV sites: %s; functions with an unbound site (BODYSEL rows): %d' % (dict(sites), len(fbad)))
    if lst:
        for tag in ('CONFLICT', 'DISAGREE'):
            n = 0
            for K, v in sorted(R['verdict'].items(), key=str):
                if v.startswith(tag) and n < lst:
                    n += 1
                    d = lambda x: x and (hex(x), vft[x][0], vft[x][1])
                    print(v, K, 'P', [d(x) for x in R['P'].get(K, ())], 'N', d(R['N'].get(K)), 'S', d(R['S'].get(K)))
    return c, fbad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--obj', required=True)
    ap.add_argument('--units', required=True)
    ap.add_argument('--link', required=True, help='link dir (out/addrmap.tsv)')
    ap.add_argument('--pairs', help='L2: object, _ZTV symbol, ??_7 symbol per line')
    ap.add_argument('--out')
    ap.add_argument('--list', type=int, default=0)
    a = ap.parse_args()
    facts = pickle.load(open(a.facts, 'rb'))
    import objfacts
    pairs = collections.defaultdict(set)
    if a.pairs:
        for ln in open(a.pairs):
            p = ln.rstrip('\n').split('\t')
            if len(p) >= 3:
                pairs[(p[0], p[1])].add(p[2])
    R = solve(facts, pickle.load(open(a.obj, 'rb')), json.load(open(a.units)), objfacts.load_addrmap(a.link), pairs)
    c, fbad = report(R, facts, a.list)
    if a.out:
        pickle.dump(dict(bind=R['bind'], verdict=R['verdict'], P=R['P'], N=R['N'], S=R['S'], bodysel=sorted(fbad)), open(a.out, 'wb'), protocol=4)


if __name__ == '__main__':
    main()
