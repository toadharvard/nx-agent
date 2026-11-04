#!/usr/bin/env python3
"""V2 adversarial cases for S12 0a729f1 (whole-program keys, docs/ORDERING.md): stores x86 makes as releases to a
field or variable another unit hands to Interlocked* must be ordered (stlr, or dmb ish before the store), shapes
outside the 14 the commit fixed and outside its documented open list.

    SSE2NEON=.. python3 tests/v2/test_s12_keys_more.py    # aarch64-linux-gnu-g++, g++, the plugin's headers

Units: A discovers the keys (Interlocked operands), C is compiled with the merged list (keys_merge.py) and its asm
read, as V1's test_s12_keys.py does.
  N1  read-modify-write writer of a keyed field (`o->m_state &= ~1`, x86 `and [m], imm` is a release store)
  N2  a keyed global array: A CASes `&g_locks[i]`, C stores `g_locks[j] = 0`
  N3  a keyed static class member: A increments `&Pool::s_count`, C stores `Pool::s_count = 0`
  n4  (note, not counted: matching is by name by design) the same word under a decomp shell's name in C
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-fpermissive',
         '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-mno-outline-atomics']
HDR = r'''
extern "C" long InterlockedIncrement(volatile long *);
extern "C" long InterlockedCompareExchange(volatile long *, long, long);
struct Obj { long m_count; long m_state; };
struct Pool { static long s_count; };
struct Shell { long pad; long cnt; };
extern long g_locks[8];
'''
A = r'''#include "h.h"
void a_state(Obj *o) { while (InterlockedCompareExchange(&o->m_state, 3, 2)) {} }
void a_arr(int i) { while (InterlockedCompareExchange(&g_locks[i], 1, 0)) {} }
void a_static() { InterlockedIncrement(&Pool::s_count); }
'''
C = r'''#include "h.h"
long Pool::s_count;
long g_locks[8];
void c_rmw(Obj *o) { o->m_state &= ~1; }
void c_arr(int j) { g_locks[j] = 0; }
void c_static() { Pool::s_count = 0; }
void c_shell(Shell *s) { s->cnt = 0; }
'''


def main():
    if not shutil.which(CXX) or not shutil.which('g++'):
        print('skip: needs %s and g++' % CXX)
        return 0
    work = tempfile.mkdtemp(prefix='v2-s12keys-')   # raises when it cannot create the directory
    try:
        r = subprocess.run(['bash', os.path.join(PORT, 'toolchain', 'order', 'build.sh'), os.path.join(work, 'plug')],
                           capture_output=True, text=True, env=dict(os.environ, CXX=CXX))
        if r.returncode:
            print('skip: the fnvnx_order plugin does not build')
            return 0
        gxx = lambda args: subprocess.run([CXX] + FLAGS + ['-fplugin=' + r.stdout.strip()] + args, cwd=work,
                                          capture_output=True, text=True)
        for n, t in (('h.h', HDR), ('a.cpp', A), ('c.cpp', C)):
            open(os.path.join(work, n), 'w').write(t)
        if gxx(['-fsyntax-only', '-fplugin-arg-fnvnx_order-discover=a.keys', 'a.cpp']).returncode:
            print('FAIL unit a does not compile')
            return 1
        m = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'toolchain', 'order', 'keys_merge.py'),
                            os.path.join(work, 'a.keys')], capture_output=True, text=True)
        open(os.path.join(work, 'all.keys'), 'w').write(m.stdout)
        keys = set(m.stdout.splitlines())
        if gxx(['-fplugin-arg-fnvnx_order-keys=all.keys', '-S', '-o', 'c.s', 'c.cpp']).returncode:
            print('FAIL unit c does not compile')
            return 1
        funcs, cur = {}, None
        for line in open(os.path.join(work, 'c.s')):
            mm = re.match(r'^([A-Za-z_][\w.$]*):', line)
            if mm and not mm.group(1).startswith('.L'):
                cur = mm.group(1)
                funcs[cur] = []
            elif cur and line.startswith('\t') and not line.startswith('\t.'):
                funcs[cur].append(line.strip())
        fails = 0
        for case, fn, what, counted in (('N1', 'c_rmw', 'o->m_state &= ~1', True),
                                        ('N2', 'c_arr', 'g_locks[j] = 0', True),
                                        ('N3', 'c_static', 'Pool::s_count = 0', True),
                                        ('n4', 'c_shell', 'Shell::cnt = 0 (same word, a shell name)', False)):
            body = next((v for k, v in funcs.items() if k.startswith('_Z%d%s' % (len(fn), fn))), [])
            good = any(re.match(r'(stlr|dmb)', l) for l in body)
            tag = ('ok' if good else 'FAIL') if counted else 'note'
            print('%s %s %s: %s (%s)' % (tag, case, what, 'ordered' if good else 'plain store',
                                         ' / '.join(l for l in body if not l.startswith('ret'))))
            fails += counted and not good
        print('keys: %s' % sorted(keys))
        print('ok' if not fails else 'FAILED')
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
