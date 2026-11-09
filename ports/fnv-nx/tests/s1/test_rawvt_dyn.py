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
#ifdef WITH_M1
void* k2[] = {(void*)qi, (void*)ar, (void*)d1, (void*)d0, (void*)s1};   /* pair at exe slot 2 */
extern "C" { const struct fnvnx_m1k_range __fnvnx_m1k_ranges[] = {{(unsigned long)k2, (unsigned long)(k2 + 5), 2}};
             const unsigned long __fnvnx_m1k_count = 1; }
#else
void* k2[] = {(void*)qi, (void*)ar, (void*)d0, (void*)s1};
#endif
#ifdef WITH_M1
extern "C" { __attribute__((section(".data.rel.ro.fnvnx_m1k4"), used)) void* k4[] = {(void*)qi, (void*)ar, (void*)qi, (void*)qi, (void*)d1, (void*)d0, (void*)s1}; }
#else
void* k4[] = {(void*)qi, (void*)ar, (void*)qi, (void*)qi, (void*)d0, (void*)s1};
#endif
int main() { void** a = m1; void** b = com; void** c = k4;
  printf("%d %d %d %d %d\n", ((Fn)FNVNX_RAWVT_DYN(a, 1))(0), ((Fn)FNVNX_RAWVT_DYN(b, 1))(0), ((Fn)FNVNX_RAWVT_DYN(c, 1))(0), ((Fn)FNVNX_RAWVT_DYN(c, 5))(0),
         (int)FNVNX_RAWVT_DYNOFF(b, 4) * 10 + (int)FNVNX_RAWVT_DYNOFF(a, 4));
  void** d = k2; printf("%d %d\n", ((Fn)FNVNX_RAWVT_DYN(d, 1))(0), ((Fn)FNVNX_RAWVT_DYN(d, 3))(0)); }   /* byte offsets: 4-byte slots (ILP32) */
'''
open(os.path.join(t, 'd.cpp'), 'w').write(SRC)
open(os.path.join(t, 'd.ld'), 'w').write('SECTIONS { .data.rel.ro.fnvnx_m1 : { __fnvnx_m1_start = .; '
                                        '*(.data.rel.ro.fnvnx_m1) __fnvnx_m1_end = .; }\n'
                                        '.data.rel.ro.fnvnx_m1k4 : { __fnvnx_m1k4_start = .; *(.data.rel.ro.fnvnx_m1k4) '
                                        '__fnvnx_m1k4_end = .; } }\nINSERT AFTER .data;\n')
fails = 0
for what, extra, want in (('minus-one shifts (exe 1 -> native 2), COM not, a K=4 table only after 4', ['-DWITH_M1', '-Wl,-T,' + os.path.join(t, 'd.ld')], '1 11 11 1 48\n11 1'),
                          ('no bounds in the link (an exe-order table): nothing shifts', [], '1 11 11 1 44\n11 1')):
    exe = os.path.join(t, 'd')
    r = subprocess.run(['g++', '-O2', '-I', INC, os.path.join(t, 'd.cpp'), *extra, '-o', exe], capture_output=True, text=True)
    got = subprocess.run([exe], capture_output=True, text=True).stdout.strip() if r.returncode == 0 else r.stderr[-300:]
    ok = got == want
    print('%-75s %s' % (what, 'ok' if ok else 'FAIL (%s)' % got)); fails += not ok
# RAWVT1-DEL0: the exe's slot-0 call ??_G(flags): flag 1 -> D0 (slot 1) on a minus-one table, flag 0 -> D1, an exe table
# -> its own slot 0 with the flag
D0SRC = r'''#include "fnvnx_rawvt.h"
#include <cstdio>
static int log_ = 0;
static void d1(void*) { log_ = 1; } static void d0(void*) { log_ = 2; } static void* g(void*, unsigned f) { log_ = 10 + f; return 0; }
extern "C" { __attribute__((section(".data.rel.ro.fnvnx_m1"), used)) void* m1[] = {(void*)d1, (void*)d0}; }
void* ex[] = {(void*)g};
int main() { void* a = m1; void* b = ex; void* o1 = &a; void* o2 = &b;
  FNVNX_RAWVT_DEL0(o1, 1); int x = log_; FNVNX_RAWVT_DEL0(o1, 0); int y = log_; FNVNX_RAWVT_DEL0(o2, 1); int z = log_;
  printf("%d %d %d\n", x, y, z); }
'''
open(os.path.join(t, 'd0.cpp'), 'w').write(D0SRC)
exe = os.path.join(t, 'd0')
r = subprocess.run(['g++', '-O1', '-D__thiscall=', '-I', INC, os.path.join(t, 'd0.cpp'), '-Wl,-T,' + os.path.join(t, 'd.ld'), '-o', exe],
                   capture_output=True, text=True)
got = subprocess.run([exe], capture_output=True, text=True).stdout.strip() if r.returncode == 0 else r.stderr[-300:]
ok = got == '2 1 11'
print('%-75s %s' % ('DEL0: flag 1 reaches D0, flag 0 D1, an exe table its own ??_G', 'ok' if ok else 'FAIL (%s)' % got)); fails += not ok
print('%d failed' % fails)
sys.exit(1 if fails else 0)
