#!/usr/bin/env python3
"""Synthetic check of bind.py (invented rows and N1 maps): every branch, the CLI, and the store round trip of bind / obj."""
import json, os, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
sys.path.insert(0, os.path.join(C, '..', 'verify'))
import scratch
scratch.install()
import bind, store
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
def raises(what, fn, *a):
    try:
        fn(*a)
    except ValueError:
        return
    fails.append(what + ': no error')


ABS = 1 << 32
n1 = dict(fn={}, exe_sha256='0', sym={(3, '?f@@YAXXZ'): 0x401000, (3, '?g@@YAXXZ'): 0x402000, (3, '?abs@@3HA'): ABS | 5,
                                      (4, '?f@@YAXXZ'): 0x409000, (3, 7, '?st'): 1}, vt={(3, '??_7A@@6B@'): 0x500000})
rows = [('_Z1fv', '?f@@YAXXZ', 'func', 'def'), ('_Z1gv', '?g@@YAXXZ', 'func', 'ref'), ('_Zabs', '?abs@@3HA', 'data', 'def'),
        ('_Zth', '', 'thunk', 'def'), ('_ZTV1A', '', 'vtable', 'def'), ('_ZTI1A', '', 'gcc', 'def'), ('_Zin', '', 'internal', 'def'),
        ('_Zun', '', 'unnamed:local scope number', 'def'), ('_Zmiss', '?h@@YAXXZ', 'func', 'def'), ('_Zempty', '', 'func', 'def')]
sa, ub = bind.symbol_addresses(3, rows[:-1] + [('_Zempty', '', 'unnamed:x', 'def')], n1)
case('addresses', got=sa, want={'_Z1fv': 0x401000, '_Z1gv': 0x402000, '_Zabs': ABS | 5})
case('unbound', got=ub, want=[('_Zth', 'thunk', 'no exe twin'), ('_ZTV1A', 'vtable', bind.NO_TWIN['vtable']), ('_ZTI1A', 'gcc', 'no exe twin'),
                      ('_Zin', 'internal', 'no exe twin'), ('_Zun', 'unnamed:local scope number', 'unnamed:local scope number'),
                      ('_Zmiss', 'func', 'not in N1'), ('_Zempty', 'unnamed:x', 'unnamed:x')])
case('no msvc name', got=bind.symbol_addresses(3, [('s', '', 'func', 'def')], n1), want=({}, [('s', 'func', 'no MSVC name')]))
case('unit scope', got=bind.symbol_addresses(4, rows[:1], n1)[0], want={'_Z1fv': 0x409000})
raises('two names', bind.symbol_addresses, 3, [('s', '?f@@YAXXZ', 'func', 'def'), ('s', '?g@@YAXXZ', 'func', 'def')], n1)

va, uv = bind.vtable_addresses(3, [('_ZTV1A', 8, '??_7A@@6B@', 0), ('_ZTV1A', 24, '??_7B@@6B@', 8), ('_ZTV1A', 32, '', 12),
                                   ('_ZTV1A', 40, 'unnamed:r', 16)], n1)
case('vt', got=va, want={('_ZTV1A', 8): 0x500000})
case('vt unbound', got=uv, want=[('_ZTV1A', 24, 'not in N1'), ('_ZTV1A', 32, 'no MSVC name'), ('_ZTV1A', 40, 'unnamed:r')])
n1b = dict(n1, vt=dict(n1['vt']))
case('vt repeated row', got=bind.vtable_addresses(3, [('z', 8, '??_7A@@6B@', 0)] * 2, n1)[0], want={('z', 8): 0x500000})

A = {'_Za': 0x10, '_Zb': 0x20, '_Zc': 0x30, '_Zd': 0x40, '_Ze': 0x50}
R = [('_Za', 'ok', ('_Zb', '_Zinl'), '', ()),            # inlined symbol without address but an ok inline row: ok, lists only _Zb
     ('_Zinl', 'ok', (), 'inline', ()),
     ('_Zb', 'ok', ('_Zbad',), '', ()),                  # inlined symbol with no row at all
     ('_Zc', 'ok', ('_Zdiv',), '', ()), ('_Zdiv', 'div', (), 'inline', ()),   # inline row with a bad verdict
     ('_Zc', 'ok', (), 'constprop.0', ()),               # clone: combines with the line above, worst wins
     ('_Zd', 'ok+E', (), 'sdd', ()), ('_Zd', 'ok+E', ('_Ze',), 'alias', ()),  # all ok+E
     ('_Ze', 'ok', (), '', ()), ('_Ze', 'ok+E', (), 'alias', ()),             # mixed ok / ok+E
     ('_Znone', 'shift', (), '', ()),                    # no exe address: ignored
     ('_Zbad2', 'ok', ('_Za',), 'thunk', ())]
nv = bind.n3_verdicts(3, R, A)
case('n3', got=nv, want={0x10: ('ok', [0x20]), 0x20: (bind.NO_VERDICT, []), 0x30: (bind.NO_VERDICT, []), 0x40: ('ok+E', [0x50]), 0x50: ('ok', [])})
nv2 = bind.n3_verdicts(3, [('_Zx', 'asm', ('_Zy',), '', ()), ('_Zx', 'div', (), 'alias', ()), ('_Zy', 'ok', (), '', ())], dict(_Zx=1, _Zy=2))
case('n3 worst sorted, inlined of a bad row kept', got=nv2, want={1: ('asm', [2]), 2: ('ok', [])})
case('n3 inline row not ok twice', got=bind.n3_verdicts(3, [('_Zx', 'ok', ('_Zi',), '', ()), ('_Zi', 'ok', (), 'inline', ()), ('_Zi', 'uninit', (), 'inline', ())],
                                                    dict(_Zx=1)), want={1: (bind.NO_VERDICT, [])})
case('n3 empty', got=bind.n3_verdicts(3, [], {}), want={})
# bodysel.decide takes the shape
import bodysel
case('bodysel shape', got=bodysel.decide({0x50: 'f'}, dict(fn={0x50: dict(n1=True, sym='f', why='')}), {}, nv)[0x50], want=['NATIVE', 'N1 N2 N3 N4'])

# units and the CLI
units = dict(units=[dict(source='a/b.c'), dict(source='x.c'), dict(source='y.c'), dict(source='dir/u.cpp')])
case('unit index', got=bind.unit_indices(units, ['a_b.c.o', 'dir_u.cpp.o']), want={'a_b.c.o': 0, 'dir_u.cpp.o': 3})
raises('missing unit', bind.unit_indices, units, ['nope.o'])
# one address, rows in two objects: the object of the unit the N1 certificate records decides; no row there = no verdict
R3 = dict(n3={('a_b.c.o', 0x10): ('ok', [0x20]), ('dir_u.cpp.o', 0x10): ('asm', []), ('a_b.c.o', 0x20): ('ok', []),
              ('dir_u.cpp.o', 0x30): ('ok', []), ('a_b.c.o', 0x99): ('ok', [])})
N3 = dict(fn={0x10: dict(unit=3), 0x20: dict(unit=0), 0x30: dict(unit=0)})
case('n3 by address', got=bind.n3_by_address(R3, N3, units), want={0x10: ('asm', []), 0x20: ('ok', [])})
d0 = tempfile.mkdtemp()
bind.write_n3(os.path.join(d0, 'n3.tsv'), {0x20: ('ok', []), 0x10: ('ok+E', [0x30, 0x20])})
case('n3.tsv', got=open(os.path.join(d0, 'n3.tsv')).read(), want='00000010\tok+E\t00000030\t00000020\n00000020\tok\n')
obj = {('dir_u.cpp.o', 'ztv'): {}, ('dir_u.cpp.o', 'sympair'): rows[:3], ('dir_u.cpp.o', 'vtpair'): [('_ZTV1A', 8, '??_7A@@6B@', 0)],
       ('dir_u.cpp.o', 'n3'): [('_Z1fv', 'ok', (), '', ('1R',))],
       ('a_b.c.o', 'ztv'): {}, ('a_b.c.o', 'sympair'): [], ('a_b.c.o', 'vtpair'): [], ('a_b.c.o', 'n3'): [],
       ('a_b.c.o', 0x401000): ([], set(), set(), set(), [])}
n1['sym'] = {(3, '?f@@YAXXZ'): 0x401000, (3, '?g@@YAXXZ'): 0x402000, (3, '?abs@@3HA'): ABS | 5}
d = tempfile.mkdtemp()
store.save(d + '/obj.json', 'obj', obj)
case('obj round trip with sections', got=store.load(d + '/obj.json', 'obj'), want=obj)
case('is_function_key', got=[store.is_function_key(k) for k in sorted(obj, key=str)][:3], want=[False, False, False])
store.save(d + '/n1.json', 'n1', n1)
json.dump(units, open(d + '/units.json', 'w'))
cmd = [sys.executable, '-s', '-B', os.path.join(C, 'bind.py'), '--obj', d + '/obj.json', '--n1', d + '/n1.json', '--units', d + '/units.json']
r = subprocess.run(cmd + ['--out', d + '/b.json'], capture_output=True, text=True)
case('cli status', got=r.returncode, want=0)
case('cli line', got=r.stdout, want='symbols bound 3, unbound 0 {}; address points bound 0, unbound 1; n3 verdicts 1 {"ok": 1}\n'.replace('bound 0, unbound 1', 'bound 1, unbound 0'))
b = store.load(d + '/b.json', 'bind')
case('bind content', got=(b['sym'], b['vt'], b['n3']), want=({('dir_u.cpp.o', '_Z1fv'): 0x401000, ('dir_u.cpp.o', '_Z1gv'): 0x402000, ('dir_u.cpp.o', '_Zabs'): ABS | 5},
                                                     {('dir_u.cpp.o', '_ZTV1A', 8): 0x500000}, {('dir_u.cpp.o', 0x401000): ('ok', [])}))
subprocess.run(cmd + ['--out', d + '/b2.json'], capture_output=True)
case('deterministic bytes', got=open(d + '/b.json', 'rb').read(), want=open(d + '/b2.json', 'rb').read())
obj[('ghost.o', 'ztv')] = {}
store.save(d + '/obj3.json', 'obj', obj)
r = subprocess.run(cmd[:5] + [d + '/obj3.json'] + cmd[6:] + ['--out', d + '/b4.json'], capture_output=True, text=True)
case('object without rows needs no unit', got=r.returncode, want=0)
obj[('ghost.o', 'n3')] = [('_Zq', 'ok', (), '', ())]
store.save(d + '/obj2.json', 'obj', obj)
r = subprocess.run(cmd[:5] + [d + '/obj2.json'] + cmd[6:] + ['--out', d + '/b3.json'], capture_output=True, text=True)
case('object with rows and no unit: no error', got=r.returncode, want=0)
b3 = store.load(d + '/b3.json', 'bind')
case('object with rows and no unit binds nothing', got=(b3['sym'], b3['vt'], b3['n3']), want=(b['sym'], b['vt'], b['n3']))

for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
