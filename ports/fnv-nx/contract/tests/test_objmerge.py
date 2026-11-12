#!/usr/bin/env python3
"""Synthetic check of objfacts_merge (invented entries, no game data): sorted union, byte equality, duplicate key rejected."""
import os, subprocess, sys, shutil, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import store

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def fn(tag):
    return ([(4, '_ZTVa', 8, 'obj', 0)], {0x1000 + tag}, {0x400000 + tag}, {'sym%d' % tag}, [tag])


a = {('a.o', 0x10): fn(1), ('a.o', 'ztv'): {'_ZTVa': [(8, 0, ['f', None])]}}
b = {('b.o', 0x20): fn(2), ('b.o', 'ztv'): {}}
d = tempfile.mkdtemp()
os.makedirs(d + '/in')
store.save(d + '/in/b.o.obj.json', 'objone', b)
store.save(d + '/in/a.o.obj.json', 'objone', a)


def run(inp, out):
    return subprocess.run([sys.executable, '-s', '-B', os.path.join(C, 'objfacts_merge.py'), '--inputs', inp, '--out', out],
                          capture_output=True, text=True)


r = run(d + '/in', d + '/m.json')
check('merge exit', r.returncode, 0)
store.save(d + '/direct.json', 'obj', {**a, **b})
check('bytes equal to a direct save of the union', open(d + '/m.json', 'rb').read(), open(d + '/direct.json', 'rb').read())
check('round trip', store.load(d + '/m.json', 'obj'), {**a, **b})
# a second input holding a key of the first
store.save(d + '/in/c.o.obj.json', 'objone', {('a.o', 0x10): fn(3)})
r = run(d + '/in', d + '/m2.json')
check('duplicate exit', r.returncode, 1)
check('duplicate names the key', 'a.o\t16' in r.stderr and 'c.o.obj.json' in r.stderr, True)
check('duplicate writes nothing', os.path.exists(d + '/m2.json'), False)
if fails:
    print('\n'.join(fails))
    shutil.rmtree(d, ignore_errors=True)
    sys.exit(1)
print('ok')
