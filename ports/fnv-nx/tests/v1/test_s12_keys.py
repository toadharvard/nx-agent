#!/usr/bin/env python3
"""test_s12_keys.py - V1 (D14) against S12-ordering 3426547 (ORDER-OBJECT / ORDER-OPERATOR switches object= / operator=).

Claim (docs/ORDERING.md): with the default object= / operator= the objects are byte-identical, and object=last
operator=rtl give MSVC's push order (arguments right to left, then the object) while direct calls stay direct.
Every case is our own synthetic code. (The whole-program key cases K1-K10 went with the keys: R9 orders every access.)
  O1  object= / operator= defaults: objects byte-identical with and without the arguments (virtual calls, << chains)
  O2  object=last operator=rtl under qemu: nested member calls and [] / << chains print MSVC's push order; direct
      calls stay `bl`
A case is a DEFECT when a switch does not do what the docs say.
  python3 -I tests/v1/test_s12_keys.py     -> one line per case, exit 1 when any shows a defect (skip: no toolchain)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
WORK = tempfile.mkdtemp(prefix='v1-s12keys-')   # raises when it cannot: never an empty or relative path
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-fpermissive',
         '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-mno-outline-atomics']
fails = 0


def out(case, defect, text):
    global fails
    if defect:
        fails += 1
    print('%-7s %-4s %s' % ('DEFECT' if defect else 'ok', case, text))


def note(case, text):
    print('%-7s %-4s %s' % ('note', case, text))


def skip(why):
    print('skip: ' + why)
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0)


if not shutil.which(CXX) or not shutil.which('g++'):
    skip('needs %s and g++' % CXX)
r = subprocess.run(['bash', os.path.join(PORT, 'toolchain', 'order', 'build.sh'), os.path.join(WORK, 'plug')],
                   capture_output=True, text=True, env=dict(os.environ, CXX=CXX))
if r.returncode != 0:
    skip('fnvnx_order plugin does not build: ' + r.stderr.strip()[-200:])
SO = r.stdout.strip()
PLUG = ['-fplugin=' + SO]

def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def gxx(args, cwd=WORK):
    return subprocess.run([CXX] + FLAGS + PLUG + args, capture_output=True, text=True, cwd=cwd)


# ---- O1: object= / operator= defaults are byte-identical
O_SRC = r'''
extern "C" int printf(const char *, ...);
static char buf[64]; static int nb;
int t(char c, int v) { buf[nb++] = c; return v; }
struct S { int v; virtual int vf(int a, int b) { return v + a + b; } __attribute__((noinline)) int f(int a, int b) { return v + a * b; }
           S &operator<<(int x) { v += x; return *this; } int operator[](int i) { return v + i; } };
S s0, s1;
S *obj(char c) { t(c, 0); return c == 'o' ? &s0 : &s1; }
struct T { int v; };
T &operator<<(T &a, int b) { a.v = a.v * 10 + b; return a; }
T tt; T &tobj() { t('T', 0); return tt; }
int run(const char *name, int (*fn)()) { nb = 0; int r = fn(); buf[nb] = 0; printf("%s %s %d\n", name, buf, r); return r; }
// direct member call: object o, arguments a b; then nested: object p, argument q() whose own object is r
int c1() { return obj('o')->f(t('a', 1), t('b', 2)); }
int c2() { return obj('o')->vf(t('a', 1), t('b', 2)); }
int c3() { return obj('p')->f(obj('r')->f(t('x', 1), t('y', 2)), t('q', 3)); }
int c4() { tobj() << t('a', 1) << t('b', 2); return tt.v; }
int c5() { return (*obj('o'))[t('i', 1)]; }
int main() { run("c1", c1); run("c2", c2); run("c3", c3); run("c4", c4); run("c5", c5); return 0; }
'''
# MSVC x86 push order (args right to left, then the object for thiscall, each call's own operands before the call)
O_MSVC = {'c1': 'bao', 'c2': 'bao', 'c3': 'qyxrp', 'c4': 'baT', 'c5': 'io'}
write(os.path.join(WORK, 'o.cpp'), O_SRC)
objs = {}
for tag, extra in (('none', []), ('first', ['-fplugin-arg-fnvnx_order-object=first']),
                   ('cpp17', ['-fplugin-arg-fnvnx_order-operator=cpp17']),
                   ('both', ['-fplugin-arg-fnvnx_order-object=first', '-fplugin-arg-fnvnx_order-operator=cpp17'])):
    r = gxx(extra + ['-c', '-o', 'o_%s.o' % tag, 'o.cpp'])
    objs[tag] = open(os.path.join(WORK, 'o_%s.o' % tag), 'rb').read() if r.returncode == 0 else None
same = objs['none'] is not None and all(objs[k] == objs['none'] for k in objs)
out('O1', not same, 'object=first / operator=cpp17 given explicitly vs omitted: objects %s'
    % ('byte-identical' if same else 'DIFFER (%s)' % {k: (len(v) if v else None) for k, v in objs.items()}))


# ---- O2: object=last operator=rtl, run under qemu (LP64 user mode; the order does not depend on the ABI)
def o2():
    if not shutil.which('qemu-aarch64'):
        note('O2', 'skipped: needs qemu-aarch64')
        return
    sysroot = '/usr/aarch64-linux-gnu'
    lp64 = [f for f in FLAGS if f != '-mabi=ilp32']
    res = {}
    for tag, extra in (('def', []), ('msvc', ['-fplugin-arg-fnvnx_order-object=last',
                                               '-fplugin-arg-fnvnx_order-operator=rtl'])):
        exe = os.path.join(WORK, 'o_' + tag)
        r = subprocess.run([CXX] + lp64 + PLUG + extra + ['-static', '-o', exe, 'o.cpp'], capture_output=True,
                           text=True, cwd=WORK)
        if r.returncode != 0:
            note('O2', 'skipped: cannot link a static aarch64 program: ' + r.stderr[-200:])
            return
        p = subprocess.run(['qemu-aarch64', '-L', sysroot, exe], capture_output=True, text=True, timeout=60)
        res[tag] = dict((l.split()[0], l.split()[1]) for l in p.stdout.splitlines() if len(l.split()) == 3)
        if tag == 'msvc':
            a = subprocess.run([CXX] + FLAGS + PLUG + extra + ['-S', '-o', '-', 'o.cpp'], capture_output=True,
                               text=True, cwd=WORK).stdout
            direct = functions(a)
    for c, want in sorted(O_MSVC.items()):
        got = res['msvc'].get(c)
        out('O2', got != want, '%s object=last operator=rtl prints %s, MSVC push order %s (defaults: %s)'
            % (c, got, want, res['def'].get(c)))
    b1 = body(direct, 'c1')
    bl = [l for l in b1 if l.startswith('bl') or l.startswith('b\t')]
    out('O2', not any('_ZN1S1fEii' in l for l in bl) or any(l.startswith('blr') for l in b1),
        'c1 under object=last calls S::f directly: %s' % ' / '.join(bl))


o2()
shutil.rmtree(WORK, ignore_errors=True)
print('%d defect(s)' % fails)
sys.exit(1 if fails else 0)
