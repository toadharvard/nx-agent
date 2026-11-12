#!/usr/bin/env python3
"""Synthetic check of globext_rows.py (invented sections, addresses and gate outputs; no exe data): every certification branch."""
import os, sys

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import globext, globext_rows as g

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


sec = (b'gs\t8\t4\tUS@@\t1S\t\n_ZL3arr\t28\t4\t-\tA7_i\tinternal\nv\t4\t4\t-\tVi\t\nptrs\t16\t4\t-\tA4_P1S\t\nbad\t4\t4\t-\tf\t\n'
       b'nopair\t4\t4\t-\ti\t\nnoaddr\t4\t4\t-\ti\t\nabs\t4\t4\t-\ti\t\nrec2\t4\t4\tUT@@\t1T\t\nnoname\t4\t4\t-\ti\t\n')
objs = g.parse_objects(sec, 't.o')
check('parsed', len(objs), 10)
check('parsed row', objs[1], ('_ZL3arr', 28, 4, '-', 'A7_i', ('internal',)))
sp = [(s, 'm' + s, 'data', 'def') for s in ('gs', 'v', 'ptrs', 'bad', 'noaddr', 'abs', 'rec2', 'nopair')] + \
     [('_ZL3arr', '', 'internal', 'def'), ('noname', '', 'data', 'def')]
bind = {('t.o', 'gs'): 0x1000, ('t.o', 'v'): 0x1010, ('t.o', 'ptrs'): 0x1020, ('t.o', 'bad'): 0x1040, ('t.o', 'abs'): 5 | g.ABSOLUTE,
        ('t.o', 'rec2'): 0x1050, ('t.o', 'nopair'): 0x1060, ('t.o', 'noname'): 0x1070}
nok = {('t.o', s): ('namegate',) for s in ('gs', 'v', 'ptrs', 'bad', 'noaddr', 'abs', 'rec2', '_ZL3arr', 'noname')}
rok = {('t.o', 'US@@'): ('abi', 'layout')}
sok = {'i': ('datamodel',), 'P': ('datamodel',)}
rows, rej = g.certify('t.o', objs, sp, bind, nok, rok, sok, 'u1')
check('certified', sorted((r['symbol'], r['va'], r['size'], r['certified_by']) for r in rows),
      [('gs', 0x1000, 8, ['abi', 'layout', 'n1', 'namegate']), ('ptrs', 0x1020, 16, ['datamodel', 'n1', 'namegate']),
       ('v', 0x1010, 4, ['datamodel', 'n1', 'namegate'])])
check('rejected', dict(rej), {
    '_ZL3arr': 'no data def row with an MSVC name in sympair', 'bad': 'scalar type f not certified', 'nopair': 'name pair not certified',
    'noaddr': 'no exe address', 'abs': 'absolute-value pin, no address', 'rec2': 'record type UT@@ not certified',
    'noname': 'no data def row with an MSVC name in sympair'})
check('scalar_of', [g.scalar_of(x) for x in ('A3_A2_i', 'Vi', 'PFvvE', 'RK1S', 'A4_P1S')], ['i', 'i', 'P', 'P', 'P'])
check('feeds globext', len(globext.build(rows)), 3)
for bad in (b'a\t1\t1\t-\ti\n', b'a\tx\t1\t-\ti\t\n', b'a\t1\t1\t-\ti\tweird\n', b'\t1\t1\t-\ti\t\n'):
    try:
        g.parse_objects(bad, 'b.o')
        fails.append('no error for %r' % bad)
    except ValueError:
        pass
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('globext_rows: ok')
