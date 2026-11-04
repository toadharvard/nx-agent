#!/usr/bin/env python3
"""tests/s1/test_fortify.py - the game build compiles without _FORTIFY_SOURCE (S1-fuzzB).

Ubuntu's aarch64 g++ predefines _FORTIFY_SOURCE=3: memcpy into a sized object, memset and sprintf become the glibc
checking calls __memcpy_chk / __memset_chk / __sprintf_chk, which abort where the exe's CRT did not. Checks that
compile.sh, census.sh and tools/static/flags.sh undefine it (bodies.py builds with compile.sh --print-flags), and that a hand-written unit
compiled with BUILD_FLAGS calls no *_chk function. Our own snippet, nothing of the decomp.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
FAILS = []


def check(cond, what):
    print('%-60s %s' % (what, 'ok' if cond else 'FAIL'))
    if not cond:
        FAILS.append(what)


for rel in ('tools/link/compile.sh', 'tools/census.sh', 'tools/static/flags.sh'):
    check('-U_FORTIFY_SOURCE' in open(os.path.join(PORT, rel)).read(), '%s undefines _FORTIFY_SOURCE' % rel)

flags = subprocess.run(['bash', '-c', '. "%s/tools/static/flags.sh"; printf "%%s\\n" "${BUILD_FLAGS[@]}"' % PORT],
                       capture_output=True, text=True).stdout.split()
src = r'''
#include <stdio.h>
#include <string.h>
struct S { char name[8]; int n; };
void f(struct S* s, const char* p, unsigned n, int k) {
    memcpy(s->name, p, n);
    memset(s->name, 0, n);
    sprintf(s->name, "%d", k);
    strcpy(s->name, p);
}
'''
d = tempfile.mkdtemp(prefix='fortify')
c, o = os.path.join(d, 'u.cpp'), os.path.join(d, 'u.o')
open(c, 'w').write(src)
cxx = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
r = subprocess.run([cxx] + flags + ['-O2', '-c', c, '-o', o], capture_output=True, text=True)
check(r.returncode == 0, 'the snippet compiles with BUILD_FLAGS -O2 (%s)' % r.stderr.strip()[:120])
if r.returncode == 0:
    nm = subprocess.run(['aarch64-linux-gnu-nm', '-u', o], capture_output=True, text=True).stdout
    chk = [l.split()[-1] for l in nm.splitlines() if l.split() and l.split()[-1].endswith('_chk')]
    check(not chk, 'no __*_chk calls under BUILD_FLAGS (%s)' % chk)
    r2 = subprocess.run([cxx] + [f for f in flags if f != '-U_FORTIFY_SOURCE'] + ['-O2', '-c', c, '-o', o],
                        capture_output=True, text=True)
    nm2 = subprocess.run(['aarch64-linux-gnu-nm', '-u', o], capture_output=True, text=True).stdout
    check('_chk' in nm2, 'control: without the flag the distro default emits __*_chk')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
