"""S1-rules: fnvnx_rawvt.h's FNVNX_RAWVT_DYN picks slot k + 1 for a vptr inside .data.rel.ro.fnvnx_m1 (a native
minus-one table), slot k for any other table (COM), and slot k when the link defines no bounds (weak 0 / 0).
Host compiler; the section and its bounds come from a linker script as S2's link makes them."""
import os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
INC = os.path.join(HERE, '..', '..', 'shim', 'include')
if not shutil.which('g++'):
    print('skipped: g++ missing'); sys.exit(0)
t = tempfile.mkdtemp()
SRC = r'''#include "fnvnx_rawvt.h"
#include <cstdio>
typedef int (*Fn)(void*);
static int d1(void*) { return 100; } static int d0(void*) { return 101; } static int s1(void*) { return 1; }
static int s2(void*) { return 2; } static int qi(void*) { return 10; } static int ar(void*) { return 11; }
#ifdef WITH_M1
extern "C" { __attribute__((section(".data.rel.ro.fnvnx_m1"), used)) void* m1[] = {(void*)d1, (void*)d0, (void*)s1, (void*)s2}; }
#else
void* m1[] = {(void*)d0, (void*)s1, (void*)s2};   /* the exe-order table: ??_G, s1, s2 */
#endif
void* com[] = {(void*)qi, (void*)ar};
int main() { void** a = m1; void** b = com; printf("%d %d\n", ((Fn)FNVNX_RAWVT_DYN(a, 1))(0), ((Fn)FNVNX_RAWVT_DYN(b, 1))(0)); }
'''
open(os.path.join(t, 'd.cpp'), 'w').write(SRC)
open(os.path.join(t, 'd.ld'), 'w').write('SECTIONS { .data.rel.ro.fnvnx_m1 : { __fnvnx_m1_start = .; '
                                        '*(.data.rel.ro.fnvnx_m1) __fnvnx_m1_end = .; } }\nINSERT AFTER .data;\n')
fails = 0
for what, extra, want in (('a minus-one table shifts (exe slot 1 -> native slot 2), COM does not', ['-DWITH_M1', '-Wl,-T,' + os.path.join(t, 'd.ld')], '1 11'),
                          ('no bounds in the link (an exe-order table): nothing shifts', [], '1 11')):
    exe = os.path.join(t, 'd')
    r = subprocess.run(['g++', '-O2', '-I', INC, os.path.join(t, 'd.cpp'), *extra, '-o', exe], capture_output=True, text=True)
    got = subprocess.run([exe], capture_output=True, text=True).stdout.strip() if r.returncode == 0 else r.stderr[-300:]
    ok = got == want
    print('%-75s %s' % (what, 'ok' if ok else 'FAIL (%s)' % got)); fails += not ok
print('%d failed' % fails)
sys.exit(1 if fails else 0)
