#!/usr/bin/env python3
"""nobinding.py: objects of excluded roots that bind no symbol give the proof; one bound symbol is red and lists object, symbol, address."""
import os, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import nobinding, store

fails = []
obj = {('r/a.o', 'sympair'): [('_Zf', '?f@@YAXXZ', 'func', 'def')], ('r/a.o', 'ztv'): {}, ('r/b.o', 'sympair'): []}
bind = lambda sym: dict(sym=sym, vt={}, n3={}, unbound_sym=[], unbound_vt=[])
if nobinding.judge(obj, bind({})) != (2, []):
    fails.append('judge clean')
if nobinding.judge(obj, bind({('r/a.o', '_Zf'): 0x401000})) != (2, [('r/a.o', '_Zf', 0x401000)]):
    fails.append('judge bound')
with tempfile.TemporaryDirectory() as d:
    store.save(d + '/obj.json', 'obj', obj)
    run = lambda sym: subprocess.run([sys.executable, '-I', C + '/nobinding.py', '--obj', d + '/obj.json', '--bind', d + '/b.json', '--out', d + '/p.txt'], capture_output=True, text=True)
    store.save(d + '/b.json', 'bind', bind({}))
    r = run({})
    if r.returncode or open(d + '/p.txt').read() != '2 objects, 0 symbols bound\n':
        fails.append('clean cli %r' % r.stderr)
    os.remove(d + '/p.txt')
    store.save(d + '/b.json', 'bind', bind({('r/a.o', '_Zf'): 0x401000}))
    r = run({})
    if r.returncode != 1 or os.path.exists(d + '/p.txt') or 'r/a.o _Zf 00401000' not in r.stderr:
        fails.append('bound cli %r' % r.stderr)
if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('nobinding: ok')
