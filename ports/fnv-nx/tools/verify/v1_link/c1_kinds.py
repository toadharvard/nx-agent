#!/usr/bin/env python3
"""c1_kinds.py - V1-link claim C1, address-map part: the kinds F/C/X/G/V/E of addrmap.tsv are right.

    c1_kinds.py <addrmap.tsv> <units.json> <trial.elf> <work dir> [sample per kind (default 60)]

All rows: the kind agrees with the MSVC definitions units.json gives for the address (C <-> ??0, X <-> ??1,
G <-> ??_G / ??_E, F <-> any other function or a provider row, V <-> a ??_7 name some unit binds there, E <-> no unit
defines the address and it lies in the image's .text); addresses sorted and unique.
Sample (seeded, per kind): the native symbol's Itanium form agrees with the kind (C1/C2, D1/D2, D0, _ZTV) and its
demangled qualified name with the MSVC one (V1's v1names, last component for shells); in the trial image the link
symbol is defined and equals the native symbol (+offset for V), and for G the D1 symbol is the same class's.
"""
import collections
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elf32    # noqa: E402
import v1names  # noqa: E402

TEXT_LO, TEXT_HI = 0x00401000, 0x00fdf000


def msvc_family(n):
    if n.startswith('??0'):
        return 'C'
    if n.startswith('??1'):
        return 'X'
    if n.startswith(('??_G', '??_E')):
        return 'G'
    return 'F'


def main():
    amap, units_json, elf, work = sys.argv[1:5]
    per = int(sys.argv[5]) if len(sys.argv) > 5 else 60
    os.makedirs(work, exist_ok=True)
    defs = collections.defaultdict(set)
    vft = collections.defaultdict(set)
    for u in json.load(open(units_json))['units']:
        for fn in u['functions']:
            defs[int(fn['address'], 16)].add(fn['symbol'])
        for n, a in u['symbols'].items():
            if n.startswith('??_7'):
                vft[int(a, 16)].add(n)
    rows = []
    for line in open(amap):
        if line.startswith('#'):
            continue
        c = line.rstrip('\n').split('\t')
        c += [''] * (9 - len(c))
        rows.append(c)
    cnt = collections.Counter()
    bad = []
    addrs = [int(c[0], 16) for c in rows]
    if addrs != sorted(addrs) or len(set(addrs)) != len(addrs):
        bad.append(('-', '-', 'addresses not sorted / not unique'))
    for c in rows:
        a, k = int(c[0], 16), c[1]
        cnt['rows ' + k] += 1
        fam = {msvc_family(n) for n in defs.get(a, ())}
        if k in 'CXG':
            if k not in fam:
                bad.append((c[0], k, 'units.json definitions here: %s' % sorted(defs.get(a, ()))[:3]))
        elif k == 'F':
            if not defs.get(a) and not c[5].startswith('provider'):
                bad.append((c[0], k, 'F without a definition or provider'))
            elif defs.get(a) and fam & {'C', 'X', 'G'} and 'F' not in fam:
                bad.append((c[0], k, 'F but every definition is a ctor/dtor: %s' % sorted(defs[a])[:2]))
        elif k == 'V':
            if not vft.get(a) and 'hand' not in c[5] and 'stored' not in c[5]:
                bad.append((c[0], k, 'V without a ??_7 name at the address (%s)' % c[5][:60]))
        elif k == 'E':
            if defs.get(a):
                bad.append((c[0], k, 'E but defined: %s' % sorted(defs[a])[:2]))
            elif not (TEXT_LO <= a < TEXT_HI):
                bad.append((c[0], k, 'E outside the image .text'))
        else:
            bad.append((c[0], k, 'unknown kind'))
    for b in bad:
        cnt['all-rows problem ' + b[1]] += 1
    # sample
    img = elf32.Image(elf)
    rng = random.Random(4)
    by_kind = collections.defaultdict(list)
    for c in rows:
        by_kind[c[1]].append(c)
    sample = []
    for k in sorted(by_kind):
        sample += rng.sample(by_kind[k], min(per, len(by_kind[k])))
    mnames = sorted({n for c in sample for n in defs.get(int(c[0], 16), ())} |
                    {n for c in sample for n in vft.get(int(c[0], 16), ())})
    mdem = v1names.undname(mnames)
    idem = v1names.cxxfilt(sorted({c[3] for c in sample if c[3]}))
    sbad = []
    for c in sample:
        a, k, link, native, off = int(c[0], 16), c[1], c[2], c[3], c[6]
        cnt['sampled ' + k] += 1
        probs = []
        if k == 'E':
            if link or native:
                probs.append('E with symbols')
            if probs:
                sbad.append((c[0], k, '; '.join(probs)))
            continue
        lv = img.by_name.get(link)
        nv = img.by_name.get(native) or (None if native not in img.locals_by_name else
                                         (img.locals_by_name[native][0],))
        if lv is None:
            probs.append('link symbol %s undefined in the image' % link)
        elif nv is None and k != 'V':
            probs.append('native %s not in the image' % native)
        elif k != 'V' and lv[0] != nv[0]:
            if lv[0] in img.locals_by_name.get(native, []):
                cnt['native name duplicated, the map object keeps a local copy'] += 1
            else:
                probs.append('%s (%#x) != %s (%#x)' % (link, lv[0], native, nv[0]))
        it = idem.get(native, native)
        if k == 'C' and not re.search(r'C[12]E', native):
            probs.append('C native is not a C1/C2 constructor: ' + native)
        if k == 'X' and not re.search(r'D[12]Ev$', native):
            probs.append('X native is not a D1/D2 destructor: ' + native)
        if k == 'G' and not native.endswith('D0Ev'):
            probs.append('G native is not D0: ' + native)
        if k == 'G' and (not re.search(r'D[12]Ev$', c[8]) or c[8][:-4] != native[:-4]):
            probs.append('G d1 %s is not the D1/D2 of %s' % (c[8], native))
        if k == 'V':
            if not native.startswith('_ZTV'):
                probs.append('V native is not a vtable: ' + native)
            # "const Foo::`vftable'{for `B'}" -> Foo
            mcls = set()
            for n in vft.get(a, ()):
                t = mdem.get(n, '')
                if "::`vftable'" in t:       # the class may be a template with blanks in its arguments
                    mcls.add(v1names.tok_norm(re.sub(r'^const ', '', t.split("::`vftable'")[0])))
            icls = v1names.tok_norm(re.sub(r'^vtable for ', '', it))
            if mcls and icls not in mcls and not any(m.split('::')[-1] == icls.split('::')[-1] for m in mcls):
                probs.append('V class %s, MSVC names %s' % (icls, sorted(mcls)[:3]))
        elif not c[5].startswith('provider'):
            ik = v1names.key_itanium(it)
            mk = [v1names.key_msvc(mdem[n]) for n in defs.get(a, ()) if mdem.get(n)]

            def same(iq, mq):
                if k in 'GX':      # compare the class: '`scalar deleting dtor'' / '~X' name the class's destructor
                    iq, mq = iq.rsplit('::', 1)[0], mq.rsplit('::', 1)[0]
                return iq == mq or iq.split('::')[-1] == mq.split('::')[-1]
            if mk and not any(same(ik.qname, m.qname) for m in mk):
                probs.append('native %s is not named like the definitions %s' % (ik.qname, [m.qname for m in mk][:2]))
        if probs:
            sbad.append((c[0], k, '; '.join(probs)))
    for b in sbad:
        cnt['sample problem ' + b[1]] += 1
    with open(os.path.join(work, 'c1_kinds.tsv'), 'w') as f:
        for b in bad:
            f.write('all\t%s\t%s\t%s\n' % b)
        for b in sbad:
            f.write('sample\t%s\t%s\t%s\n' % b)
    for k in sorted(cnt):
        print('%-28s %d' % (k, cnt[k]))
    for b in (bad[:15] + sbad[:25]):
        print('  ', b)
    return 1 if bad or sbad else 0


if __name__ == '__main__':
    sys.exit(main())
