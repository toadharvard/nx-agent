#!/usr/bin/env python3
"""ehfacts - MSVC x86 C++ exception-handling tables of the exe, and what each data code pointer is.

    ehfacts.py --exe FalloutNV.exe --facts facts.pkl --out eh.pkl

parse(img, facts)    FuncInfo structures (found by magic, validated), their handler stubs and owner functions.
classify(facts, eh)  {data va: 'slot' | 'xc' | 'eh_unwind' | 'eh_catch' | 'other'} for every facts['ptrs'] entry.
The output is exe-derived: it stays out of the repository (docs/ARCHITECTURE.md, Legal).
"""
import argparse, collections, os, pickle, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exefacts

MAGICS = (0x19930520, 0x19930521, 0x19930522)
MAX_STATE = 1 << 12


def _insec(img, name, a):
    va, vs = img.secs[name][:2]
    return va <= a < va + vs


def _ptr(img, at, name='.rdata'):
    """Word at `at` is a relocated pointer into section `name`, or null (returns value), else None."""
    v = img.u32(at)
    if v == 0:
        return 0
    return v if at in img.rset and _insec(img, name, v) else None


def _code(img, at):
    v = img.u32(at)
    if v == 0:
        return 0
    return v if at in img.rset and img.intext(v) else None


def _funcinfo(img, a):
    magic, ms, pum, ntry, ptm, nip, pip = (img.u32(a + 4 * i) for i in range(7))
    if ms > MAX_STATE or ntry > MAX_STATE or nip > 4 * MAX_STATE:
        return None
    pum, ptm, pip = _ptr(img, a + 8), _ptr(img, a + 16), _ptr(img, a + 24)
    if None in (pum, ptm, pip) or bool(pum) != bool(ms) or bool(ptm) != bool(ntry) or bool(pip) != bool(nip):
        return None
    if not (ms or ntry or nip):
        return None
    unwind, ua, ca = [], [], []
    for i in range(ms):
        e = pum + 8 * i
        to, act = img.u32(e), _code(img, e + 4)
        if act is None or not (to == 0xffffffff or to < ms):
            return None
        unwind.append((to, act))
        ua.append(e + 4)
    tries = []
    for i in range(ntry):
        e = ptm + 20 * i
        lo, hi, ch, nc = (img.u32(e + 4 * k) for k in range(4))
        ph = _ptr(img, e + 16)
        if not ph or not 0 < nc <= 256 or not lo <= hi <= ch:
            return None
        hs = []
        for j in range(nc):
            h = ph + 16 * j
            ptype = img.u32(h + 4)
            if ptype and h + 4 not in img.rset:
                return None
            addr = _code(img, h + 12)
            if not addr:
                return None
            hs.append((img.u32(h), ptype, img.u32(h + 8), addr))
            ca.append(h + 12)
        tries.append((lo, hi, ch, hs))
    return dict(magic=magic, max_state=ms, unwind=unwind, tries=tries, ip2state=nip), ua, ca


def parse(img, facts):
    va, vs = img.secs['.rdata'][:2]
    funcinfo, ua, ca = {}, set(), set()
    for a in range(va, va + vs - 28, 4):
        if img.u32(a) in MAGICS:
            r = _funcinfo(img, a)
            if r:
                funcinfo[a] = r[0]
                ua.update(r[1])
                ca.update(r[2])
    fn = facts['fn']
    owner_of = {}
    for va_, f in fn.items():
        for _, t in f.get('codeptrs', ()):
            owner_of.setdefault(t, va_)
    stubs, owner = {}, {a: None for a in funcinfo}
    for sv, f in fn.items():
        if f.get('size', 1 << 30) > 0x40:
            continue
        for _, d in f.get('data', ()):
            if d in funcinfo:
                stubs[sv] = d
                if sv in owner_of and owner[d] is None:
                    owner[d] = owner_of[sv]
                break
    return dict(funcinfo=funcinfo, stubs=stubs, owner=owner, unwind_at=ua, catch_at=ca)


def classify(facts, eh):
    slots = set()
    for v, (_, _, sl, _) in facts['vft'].items():
        slots.update(v + 4 * i for i in range(len(sl)))
    xc0 = facts['meta'].get('xc_va', 0)
    xcs = set(xc0 + 4 * i for i in range(len(facts['xc'])))
    kind = {}
    for a, _ in facts['ptrs']:
        kind[a] = ('slot' if a in slots else 'xc' if a in xcs else 'eh_unwind' if a in eh['unwind_at']
                   else 'eh_catch' if a in eh['catch_at'] else 'other')
    return kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--facts', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    t0 = time.time()
    img = exefacts.Image(a.exe)
    facts = pickle.load(open(a.facts, 'rb'))
    eh = parse(img, facts)
    kind = classify(facts, eh)
    fis = eh['funcinfo']
    bymagic = collections.Counter(hex(f['magic']) for f in fis.values())
    acts = [x for f in fis.values() for _, x in f['unwind'] if x]
    hs = [h[3] for f in fis.values() for t in f['tries'] for h in t[3]]
    print('funcinfo validated', len(fis), 'by magic', dict(bymagic))
    print('unwind entries non-null actions', len(acts), 'distinct', len(set(acts)))
    print('try blocks', sum(len(f['tries']) for f in fis.values()), 'catch handlers', len(hs), 'distinct', len(set(hs)))
    print('stubs', len(eh['stubs']), 'funcinfo with owner', sum(1 for v in eh['owner'].values() if v),
          'without', sum(1 for v in eh['owner'].values() if not v))
    print('classification', dict(collections.Counter(kind.values())))
    fn = facts['fn']
    oth = collections.Counter((img.sec(d), t in fn and bool(fn[t].get('decomp')))
                              for d, t in facts['ptrs'] if kind[d] == 'other')
    print('other by (section, target is decomp fn)', dict(oth))
    eh['kind'] = kind
    pickle.dump(eh, open(a.out, 'wb'))
    print('seconds %.1f' % (time.time() - t0))


if __name__ == '__main__':
    main()
