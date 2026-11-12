#!/usr/bin/env python3
"""Synthetic check of globext_run.certify_all (invented stems, n1 pins and address ranges; no exe data): certification, universe, probes."""
import os, sys

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import globext_run as r

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def stem(name, units, objects, sympair, **kw):
    d = dict(stem=name, units=units, objects=objects, sympair=sympair, name_ok={s[0]: ('namegate',) for s in sympair if s[2] == 'data'},
             record_ok={'US@@': ('layout', 'abi')}, scalars_ok=True, cl_data=[s[1] for s in sympair if s[2] == 'data'], cl_static=[])
    d.update(kw)
    return d


a = stem('a.cpp', [0, 1], [('gs', 8, 4, 'US@@', '1S', ()), ('v', 4, 4, '-', 'Vi', ()), ('x', 4, 4, '-', 'i', ())],
         [('gs', '?gs@@3US@@A', 'data', 'def'), ('v', '?v@@3HC', 'data', 'def'), ('x', '?x@@3HA', 'data', 'def')], cl_static=['?st'])
b = stem('b.cpp', [2], [('w', 4, 4, '-', 'i', ())], [('w', '?w@@3HA', 'data', 'def')], scalars_ok=False)
n1 = {(0, '?gs@@3US@@A'): 0x1000, (1, '?gs@@3US@@A'): 0x1000, (0, '?v@@3HC'): 0x1010, (0, '?x@@3HA'): 0x1018, (1, '?x@@3HA'): 0x1020,
      (2, '?w@@3HA'): 0x1030, (0, '?st'): 0x1040}
res = r.certify_all([a, b], n1, [(0x1000, 0x2000)], [0x1004, 0x1018, 0x1030, 0x1040, 0x1100, 0x9000], (0x1000, 0x1020))
check('objects', [(o[1], o[2], o[5]) for o in res['objects']], [(0x1000, 8, 'gs'), (0x1010, 4, 'v')])
check('x rejected', [s for s, w in res['rejected']['a.cpp']], ['x'])
check('x reason', res['rejected']['a.cpp'][0][1], 'name has two addresses over the units')
check('w reason', res['rejected']['b.cpp'], [('w', 'scalar type i not certified')])
check('universe', sorted(res['universe']), [0x1000, 0x1010, 0x1018, 0x1020, 0x1030])
check('counts', (res['counts']['certified'], res['counts']['universe'], res['counts']['universe_certified']), (2, 5, 2))
check('range', res['counts']['range'], dict(lo=0x1000, hi=0x1020, certified=2, universe=3))
check('probe in', res['probes'][0x1004], 'certified gs [0x1000,0x1008) abi+layout+n1+namegate')
check('probe universe', res['probes'][0x1030], 'none certified; universe: ?w@@3HA@b.cpp')
check('probe internal', res['probes'][0x1040], 'none certified; internal (static) symbol of a cl object: ?st@a.cpp')
check('probe none', res['probes'][0x1100], 'none certified; no pinned data symbol')
check('probe outside', res['probes'][0x9000], 'none certified; no pinned data symbol')
check('absolute pin is no address', r.certify_all([b], {(2, '?w@@3HA'): 5 | r.ABSOLUTE}, [(0, 10)])['universe'], {})
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('globext_run: ok')
