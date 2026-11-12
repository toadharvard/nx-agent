#!/usr/bin/env python3
"""Check of link/odr.py on REAL objects: tiny C++ sources built by the one build (realobj.build: msvcmodel compiler, all plugins; no direct compiler
call), identical copies are green; a differing byte, a differing relocation target, a differing string and a differing member each make the
signature red and named; malformed input is an error (byte mutations of a real object, mutate.py)."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_gaterun
import mutate
import odr
import realobj

fails = []
d = tempfile.mkdtemp()


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


BODY = 'extern int ext(int); extern int other(const char*);\ninline int inl(int x) { return x * %d + ext(x); }\nint use%s(int x) { return inl(x); }\n'
REL = 'extern int e1(int); extern int e2(int);\ninline int inl(int x) { return %s(x); }\nint use%s(int x) { return inl(x); }\n'
STR = 'extern int p(const char*);\ninline __attribute__((noinline)) int inl() { return p("%s"); }\nint use%s() { return inl(); }\n'
INL = 'struct W { virtual int f() { return %d; } };\nint mk%s() { W w; return w.f(); }\n'
SRC = {'-O0': {'a': BODY % (3, 'A'), 'b': BODY % (3, 'B'), 'c': BODY % (5, 'C'),
               'r1': REL % ('e1', 'A'), 'r2': REL % ('e1', 'B'), 'r3': REL % ('e1', 'A'), 'r4': REL % ('e2', 'B'),
               'o1': STR % ('hello', 'A'), 'o2': STR % ('hello', 'B'),
               'w1': INL % (1, 'A'), 'w2': INL % (1, 'B'), 'w3': INL % (2, 'C'),
               'lone': 'inline int only(int x) { return x + 1; }\nint useL(int x) { return only(x); }\n'},
       '-Os': {'s1': STR % ('hello', 'A'), 's2': STR % ('hello', 'B'), 's3': STR % ('hello', 'A'), 's4': STR % ('world', 'B')}}
P = {}
for opt, srcs in SRC.items():
    res = realobj.build(srcs, flags=[opt])
    if res['status'] != 'green':
        print('FAIL real-object build at %s: %s' % (opt, res.get('failure')))
        sys.exit(1)
    for o in res['objects']:
        n = os.path.splitext(o['unit'])[0]
        P[n] = os.path.join(d, n + '.o')
        shutil.copyfile(o['object'], P[n])


def gate(*objs):
    return abi_gaterun.run(odr, list(objs), 'odr')


a, b, c = P['a'], P['b'], P['c']
rc, r = gate(a, b)
check('identical inline copies', (rc, r['status'], r['universe']), (0, 'green', 1))
rc, r = gate(a, b, c)
check('differing constant is red and named', (rc, r['universe']), (1, 1))
check('names the signature and the objects', ('_Z3inli' in r['uncertified'][0], 'c.o' in r['uncertified'][0], 'bytes differ' in r['uncertified'][0]), (True, True, True))

rc, r = gate(P['r1'], P['r2'])
check('same relocation target', rc, 0)
rc, r = gate(P['r3'], P['r4'])
check('different relocation target', (rc, 'relocations differ' in r['uncertified'][0]), (1, True))

rc, r = gate(P['s1'], P['s2'])
check('same string in different objects', rc, 0)
rc, r = gate(P['s3'], P['s4'])
check('different string is red', (rc, 'relocations differ' in r['uncertified'][0]), (1, True))
rc, r = gate(P['o1'], P['o2'])
check('string in a plain section is opaque, uncertified even when equal', (rc, 'not delimited' in r['uncertified'][0]), (1, True))

rc, r = gate(P['w1'], P['w2'])
check('identical vtable groups', (rc, r['status']), (0, 'green'))
rc, r = gate(P['w1'], P['w3'])
check('vtable group with a differing inline virtual', rc, 1)

rc, r = gate(P['lone'], a)
check('a signature in one object is not in the universe', (rc, r['universe']), (0, 0))
rc, r = gate(a)
check('one object', (rc, r['universe']), (0, 0))
rc, r = gate(a, a)
check('the same path twice is one copy', (rc, r['universe']), (0, 0))

bad = os.path.join(d, 'bad.o')
open(bad, 'wb').write(b'\x7fELX' + open(a, 'rb').read()[4:])       # the magic broken: not an ELF
check('malformed object', odr.main([a, bad]), 2)
check('missing file', odr.main([os.path.join(d, 'nope.o')]), 2)
ex = os.path.join(d, 'x.out')
open(ex, 'wb').write(mutate.truncate(open(a, 'rb').read(), 20))
check('truncated object', odr.main([a, ex]), 2)

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
