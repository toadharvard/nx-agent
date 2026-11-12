#!/usr/bin/env python3
"""Check of link/odr.py on tiny C++ objects compiled for the target (ILP32): identical copies are green; a differing byte, a differing
relocation target, a differing string and a differing member each make the signature red and named; malformed input is an error."""
import os
import subprocess
import sys
import tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, '..'), os.path.join(HERE, '..', '..', 'contract')]
import abi_gaterun
import odr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

CXX = ['aarch64-linux-gnu-g++-13', '-mabi=ilp32', '-c', '-O0', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-fno-asynchronous-unwind-tables']
fails = testcase.FAILED
d = tempfile.mkdtemp()




def cc(name, src, opt='-O0'):
    s = os.path.join(d, name + '.cc')
    o = os.path.join(d, name + '.o')
    open(s, 'w').write(src)
    r = subprocess.run(CXX + [opt, s, '-o', o], capture_output=True, text=True)
    if r.returncode:
        fails.append('compile %s: %s' % (name, r.stderr))
    return o


def gate(*objs):
    return abi_gaterun.run(odr, list(objs), 'odr')


BODY = 'extern int ext(int); extern int other(const char*);\ninline int inl(int x) { return x * %d + ext(x); }\nint use%s(int x) { return inl(x); }\n'
a = cc('a', BODY % (3, 'A'))
b = cc('b', BODY % (3, 'B'))
c = cc('c', BODY % (5, 'C'))
rc, r = gate(a, b)
case('identical inline copies', got=(rc, r['status'], r['universe']), want=(0, 'green', 1))
rc, r = gate(a, b, c)
case('differing constant is red and named', got=(rc, r['universe']), want=(1, 1))
case('names the signature and the objects', got=('_Z3inli' in r['uncertified'][0], 'c.o' in r['uncertified'][0], 'bytes differ' in r['uncertified'][0]), want=(True, True, True))

REL = 'extern int e1(int); extern int e2(int);\ninline int inl(int x) { return %s(x); }\nint use%s(int x) { return inl(x); }\n'
rc, r = gate(cc('r1', REL % ('e1', 'A')), cc('r2', REL % ('e1', 'B')))
case('same relocation target', got=rc, want=0)
rc, r = gate(cc('r3', REL % ('e1', 'A')), cc('r4', REL % ('e2', 'B')))
case('different relocation target', got=(rc, 'relocations differ' in r['uncertified'][0]), want=(1, True))

STR = 'extern int p(const char*);\ninline __attribute__((noinline)) int inl() { return p("%s"); }\nint use%s() { return inl(); }\n'
rc, r = gate(cc('s1', STR % ('hello', 'A'), '-Os'), cc('s2', STR % ('hello', 'B'), '-Os'))
case('same string in different objects', got=rc, want=0)
rc, r = gate(cc('s3', STR % ('hello', 'A'), '-Os'), cc('s4', STR % ('world', 'B'), '-Os'))
case('different string is red', got=(rc, 'relocations differ' in r['uncertified'][0]), want=(1, True))
rc, r = gate(cc('o1', STR % ('hello', 'A')), cc('o2', STR % ('hello', 'B')))
case('string in a plain section is opaque, uncertified even when equal', got=(rc, 'not delimited' in r['uncertified'][0]), want=(1, True))

INL = 'struct W { virtual int f() { return %d; } };\nint mk%s() { W w; return w.f(); }\n'
w1 = cc('w1', INL % (1, 'A'))
w2 = cc('w2', INL % (1, 'B'))
w3 = cc('w3', INL % (2, 'C'))
rc, r = gate(w1, w2)
case('identical vtable groups', got=(rc, r['status']), want=(0, 'green'))
rc, r = gate(w1, w3)
case('vtable group with a differing inline virtual', got=rc, want=1)

lone = cc('lone', 'inline int only(int x) { return x + 1; }\nint useL(int x) { return only(x); }\n')
rc, r = gate(lone, a)
case('a signature in one object is not in the universe', got=(rc, r['universe']), want=(0, 0))
rc, r = gate(a)
case('one object', got=(rc, r['universe']), want=(0, 0))
rc, r = gate(a, a)
case('the same path twice is one copy', got=(rc, r['universe']), want=(0, 0))

bad = os.path.join(d, 'bad.o')
open(bad, 'wb').write(b'not an elf at all')
case('malformed object', got=odr.main([a, bad]), want=2)
case('missing file', got=odr.main([os.path.join(d, 'nope.o')]), want=2)
ex = os.path.join(d, 'x.out')
open(ex, 'wb').write(open(a, 'rb').read()[:20])
case('truncated object', got=odr.main([a, ex]), want=2)

for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
