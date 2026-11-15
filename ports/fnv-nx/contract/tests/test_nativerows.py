#!/usr/bin/env python3
"""Synthetic check of nativerows.py: one defined symbol per native address, the three error classes, the CLI and its determinism."""
import os, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import nativerows, store

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


obj = {('a.o', 'sympair'): [('_Zf', '?f@@YAXXZ', 'func', 'def'), ('_Zg', '?g@@YAXXZ', 'func', 'ref'), ('_Zd', '?d@@3HA', 'data', 'def'),
                            ('_ZC1', '?0@', 'ctor.c1', 'def'), ('_ZC2', '?0@', 'ctor.c2', 'def'), ('_Zin', '?in@@YAXXZ', 'func', 'def')],
       ('b.o', 'sympair'): [('_Zin', '?in@@YAXXZ', 'func', 'def'), ('_Zg', '?g@@YAXXZ', 'func', 'def')], ('a.o', 'vtpair'): []}
bind = {('a.o', '_Zf'): 0x401000, ('a.o', '_Zg'): 0x402000, ('a.o', '_Zd'): 0x600000, ('a.o', '_ZC1'): 0x403000, ('a.o', '_ZC2'): 0x403000,
        ('a.o', '_Zin'): 0x404000, ('b.o', '_Zin'): 0x404000, ('b.o', '_Zg'): 0x402000}
V = lambda *a: {x: 'native' for x in a}
check('one symbol per address, inline counted once, a ref does not count (b.o defines _Zg)', nativerows.native_rows(obj, bind, {**V(0x401000, 0x402000, 0x404000), 0x405000: 'lifted'}),
      [(0x401000, '_Zf'), (0x402000, '_Zg'), (0x404000, '_Zin')])
check('no native address: no rows', nativerows.native_rows(obj, bind, {0x401000: 'lifted'}), [])
for what, v in (('ambiguous (ctor.c1, ctor.c2)', V(0x403000)), ('no defined symbol', V(0x409000)), ('data is not a body', V(0x600000))):
    try:
        nativerows.native_rows(obj, bind, v)
        fails.append(what + ': no error')
    except ValueError as e:
        check(what + ' names the address', '%08x' % next(iter(v)) in str(e), True)
with tempfile.TemporaryDirectory() as d:
    store.save(d + '/obj.json', 'obj', obj); store.save(d + '/bind.json', 'bind', dict(sym=bind, vt={}, n3={}, unbound_sym=[], unbound_vt=[]))
    open(d + '/b.tsv', 'w').write('00401000\tnative\tN1 N2 N3 N4\n00405000\tlifted\tN1: x\n00402000\tnative\tN1 N2 N3 N4\n')
    r = subprocess.run([sys.executable, '-I', C + '/nativerows.py', '--obj', d + '/obj.json', '--bind', d + '/bind.json', '--bodysel', d + '/b.tsv', '--out', d + '/n.tsv'], capture_output=True, text=True)
    check('cli', (r.returncode, open(d + '/n.tsv').read()), (0, '00401000\t_Zf\n00402000\t_Zg\n'))
if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('nativerows: ok')
