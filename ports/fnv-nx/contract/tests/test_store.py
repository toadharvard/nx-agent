#!/usr/bin/env python3
"""Synthetic check of store.py (round trip, byte stability) and gatefile.py (invented data, no game data)."""
import json, os, sys, shutil, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gatefile, store

d = tempfile.mkdtemp()
F = dict(a=16, size=4, ret=(0,), calls=[(16, 32)], tails=[], icalls=[(16, 'reg', 'eax', None)], itails=[], stores=[],
         vrefs=[], data=[(17, 64)], codeptrs=[], imports=[], jt=[], fs=0, x87=0, bad=0, n=1, bytes=4, end=20, decomp=True,
         sym='f', unit='u.c')
facts = dict(meta=dict(version=8), base=1, secs={'.text': (1, 2, 3, 4)}, relocs=[1, 2], imports={5: ('d', 'n')},
             vft={7: ('.?AVC', 0, [9], 3)}, ptrs=[(1, 2)], xc=[3], fn={16: F})
store.save(d + '/f.json', 'facts', facts)
assert store.load(d + '/f.json', 'facts') == facts
n1 = dict(fn={16: dict(unit=0, sym='f', size=4, n1=True, why='', relocs=[(0, 6, 's', 99)])},
          sym={(0, 's'): 99, (0, 3, 'st'): 100}, vt={(0, '??_7C@@6B@'): 7}, exe_sha256='00')
store.save(d + '/n.json', 'n1', n1)
assert store.load(d + '/n.json', 'n1') == n1
obj = {('a.o', 16): ([(1, 's', 0, 'obj', 4)], {64, 8}, {32}, {'x'}, [4, 0]), ('a.o', 'ztv'): {'_ZTV1': [(8, 0, ['a', None])]}}
store.save(d + '/o.json', 'obj', obj)
assert store.load(d + '/o.json', 'obj') == obj
# the bytes do not depend on set or dict insertion order
obj2 = dict(reversed(list(obj.items())))
obj2[('a.o', 16)] = ([(1, 's', 0, 'obj', 4)], {8, 64}, {32}, {'x'}, [4, 0])
store.save(d + '/o2.json', 'obj', obj2)
assert open(d + '/o.json', 'rb').read() == open(d + '/o2.json', 'rb').read()
vt = dict(bind={('u.c', '_ZTV1', 8): 7}, errors=[('u.c', '_ZTV1', 0, 'why')])
store.save(d + '/v.json', 'vtid', vt)
assert store.load(d + '/v.json', 'vtid') == vt
try:
    store.save(d + '/bad.json', 'vtid', dict(bind={('u\t', 'z', 1): 1}, errors=[]))
    raise SystemExit('TAB in a key was accepted')
except ValueError:
    pass
sec = {('a.o', 'sympair'): [('s', 'm', 'func', 'def')], ('a.o', 'vtpair'): [('_ZTV1', 8, '??_7C@@6B@', 0)],
       ('a.o', 'n3'): [('s', 'ok', ('i', 'j'), 'inline', ('1R',))], ('b.o', 'n3'): []}
for k in ('obj', 'objone'):
    store.save(d + '/s.json', k, sec)
    assert store.load(d + '/s.json', k) == sec
assert not any(store.is_function_key(k) for k in sec) and store.is_function_key(('a.o', 16))
# gate file: shape, order, cap, no output without GATE_OUT
os.environ.pop('GATE_OUT', None)
assert gatefile.write('t', 1, 1, [], '') is None
os.environ['GATE_OUT'] = d
gatefile.write('t', 100, 40, ['%d' % i for i in range(60, 0, -1)], 'x')
r = json.load(open(d + '/t.json'))
assert list(r) == sorted(r) and r['format'] == 'fnvnx-gate/1' and r['status'] == 'red' and len(r['uncertified']) == 50
assert r['uncertified'] == sorted(r['uncertified'])
gatefile.write('t', 3, 3, [], 'x')
assert json.load(open(d + '/t.json'))['status'] == 'green'
shutil.rmtree(d, ignore_errors=True)
print('ok')
print('ok')
