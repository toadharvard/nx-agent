#!/usr/bin/env python3
"""W49: plain (RTTI-less) image vftables are extended like RTTI ones, bounded by the next deleting destructor, and the
other classes the units name for one primary image vftable join its V row as displaced primaries.

    python3 tools/link/test_w49.py     # the plain_vtfill half needs the player's exe (FNVNX_EXE or ~/gd)

* alias_alt: ShaderMap and NiTStringPointerMap<ShaderBufferRecord*> both name 0x010af6cc; the V row holds the second:
  the first joins alt as a primary at +8 (once); a class without a native vtable, or one whose vtable is the V row's,
  adds nothing; FNVNX_VTPLAIN=0: nothing;
* plain_vtfill (exe): 0x010af6cc keeps its 7 slots, 0x010ade18's 35-slot scan stops before the next deleting
  destructor (7); an RTTI vftable and a vftable no primary V row names are left as they are.
"""
import collections
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft  # noqa: E402
import fnvlink  # noqa: E402
import vtables  # noqa: E402

FAILS = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


class Rtti:
    def __init__(self, groups):
        self.groups = groups
        self.where = {z: [types.SimpleNamespace(path='/o/%s.o' % z)] for z in groups}

    def ents(self, z):
        return self.groups.get(z)


def group(n):
    E = vtables.Ent
    return [E(0, None, 0), E(0, '_ZTIx', 0)] + [E(0, 'f%d' % i, 0) for i in range(n)]


def test_alias():
    a = 0x010af6cc
    vnames = {('?a', a): ('ShaderMap', None, None), ('?b', a): ('NiTStringPointerMap<ShaderBufferRecord*>', None, None),
              ('?c', a): ('NoTable', None, None), ('?d', 0x01000000): ('Lone', None, None)}
    by_cls = {'ShaderMap': ['_ZTV9ShaderMap'],
              'NiTStringPointerMap<ShaderBufferRecord*>': ['_ZTV19NiTStringPointerMapIP18ShaderBufferRecordE'],
              'Lone': ['_ZTV4Lone']}
    rtti = Rtti({'_ZTV9ShaderMap': group(4), '_ZTV19NiTStringPointerMapIP18ShaderBufferRecordE': group(4),
                 '_ZTV4Lone': group(2)})
    vmap = {a: ('_ZTV19NiTStringPointerMapIP18ShaderBufferRecordE', 8, '/o/x.o', 'primary'),
            0x01000000: ('_ZTV4Lone', 8, '/o/l.o', 'primary')}
    alt = collections.defaultdict(list)
    n = fnvlink.alias_alt(vnames, vmap, by_cls, rtti, alt)
    check(n == 1 and alt[a] == [('_ZTV9ShaderMap', 8, '/o/_ZTV9ShaderMap.o', 'primary')],
          'alias_alt: ShaderMap joins 010af6cc as a displaced primary: %s' % dict(alt))
    n2 = fnvlink.alias_alt(vnames, vmap, by_cls, rtti, alt)
    check(n2 == 0 and len(alt[a]) == 1, 'alias_alt: added once')
    os.environ['FNVNX_VTPLAIN'] = '0'
    try:
        check(fnvlink.alias_alt(vnames, vmap, by_cls, rtti, collections.defaultdict(list)) == 0, 'FNVNX_VTPLAIN=0')
    finally:
        del os.environ['FNVNX_VTPLAIN']


def test_plain(exe):
    ev = exevft.scan(exe)
    pv = exevft.plain_vftables(exe, [0x010af6cc, 0x010ade18], set(ev) | {0x010af6cc, 0x010ade18})
    rt = next(v for v, e in ev.items() if e.td and e.off == 0)
    exev = dict(ev)
    exev.update(pv)
    vmap = {0x010af6cc: ('_ZTV9ShaderMapX', 8, '/o/x.o', 'primary'),
            rt: ('_ZTVrt', 8, '/o/r.o', 'primary')}
    alt = {0x010ade18: [('_ZTV17StringHashMapBase', 8, '/o/s.o', 'primary')]}
    got, n = fnvlink.plain_vtfill(exev, vmap, alt, exe, True)
    check(n == 2, 'plain_vtfill: both plain vftables: %d' % n)
    check(len(got[0x010af6cc].slots) == 7 and got[0x010af6cc].td, '010af6cc: 7 slots, a stand-in td')
    check(len(pv[0x010ade18].slots) > 7 and len(got[0x010ade18].slots) == 7,
          '010ade18: %d scanned -> 7 (next deleting destructor)' % len(pv[0x010ade18].slots))
    check(got[rt] is ev[rt], 'an RTTI vftable is untouched')
    got0, n0 = fnvlink.plain_vtfill(exev, vmap, alt, exe, False)
    check(n0 == 0 and got0 is exev, 'VTFILL off: unchanged')


def main():
    test_alias()
    exe = os.environ.get('FNVNX_EXE') or os.path.expanduser('~/gd/FalloutNV.exe')
    if os.path.isfile(exe):
        test_plain(exe)
    else:
        print('skip plain_vtfill: no exe')
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
