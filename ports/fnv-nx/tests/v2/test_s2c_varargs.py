#!/usr/bin/env python3
"""V2 adversarial cases for S2c-abi 0750425 / e2dbe73 (abi.varargs_check): a variadic pair it calls `same` (a plain
branch, no bridge) must run correctly under qemu-aarch64 for every call that worked on x86.

    python3 tests/v2/test_s2c_varargs.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, qemu-aarch64, llvm-undname

The flaw tested: x86 gives an 8-byte argument (double, __int64) two 4-byte stack words, AArch64 one register. The
check reasons per 4-byte word ("x86 worked, so the call site pushed a 4-byte word there"), so a pair whose two
4-byte parameters on one side are one 8-byte argument on the other passes as `same` and reads garbage natively.
  (VA1, `int f(...)` called with a double against f(int lo, int hi), is V1's S2C-VAUNPROTO-U2 and not repeated)
  VA2  fixed reference (int, int, int): a double the decomp split into two words; definition (int, ...) whose body
       reads va_arg(double), as a %f formatter does
  VA3  fixed reference (int, __int64); definition (int, ...) reading two va_arg(int) words (x86's layout)
  VA4  fixed reference (int, int, int); definition (int, ...) reading va_arg(__int64)
No pair at link-head 06857911 has these shapes (the one va_arg(double) body, 0x0087a8b0, receives a double), so
the cases are latent. A fix that reads the definition's va_arg types needs a hint the test can pass.
Each case prints `ok` when the native run gives the x86 result, or when abi.compare no longer says `same`.
"""
import os
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import abi           # noqa: E402
import fnvlink       # noqa: E402
import sigs          # noqa: E402
import test_bridges  # noqa: E402

TYPES = r'''
typedef __builtin_va_list va_list;
#define va_start(a, l) __builtin_va_start(a, l)
#define va_arg(a, t) __builtin_va_arg(a, t)
#define va_end(a) __builtin_va_end(a)
'''

# (name, ref MSVC, def MSVC, C++ definition def_<name>, C++ check calling br_<name>: 0 = the x86 result)
CASES = [
    ('VA2', '?l@@YAXHHH@Z', '?l@@YAXHZZ',
     'extern "C" double g_2; double g_2; extern "C" void def_VA2(int n, ...) { va_list a; va_start(a, n);'
     ' g_2 = va_arg(a, double); va_end(a); }',
     'extern "C" void br_VA2(int, unsigned, unsigned);\n'
     'static int t_VA2() { br_VA2(0, 0u, 0x3ff80000u); return g_2 == 1.5 ? 0 : 1; }'),   # x86 reads 1.5
    ('VA3', '?q@@YAXH_J@Z', '?q@@YAXHZZ',
     'extern "C" unsigned g_3; unsigned g_3; extern "C" void def_VA3(int n, ...) { va_list a; va_start(a, n);'
     ' unsigned lo = va_arg(a, unsigned); unsigned hi = va_arg(a, unsigned); va_end(a); g_3 = hi; }',
     'extern "C" void br_VA3(int, long long);\n'
     'static int t_VA3() { br_VA3(0, 0x1234567800000001LL); return g_3 == 0x12345678u ? 0 : 1; }'),
    ('VA4', '?r@@YAXHHH@Z', '?r@@YAXHZZ',
     'extern "C" long long g_4; long long g_4; extern "C" void def_VA4(int n, ...) { va_list a; va_start(a, n);'
     ' g_4 = va_arg(a, long long); va_end(a); }',
     'extern "C" void br_VA4(int, unsigned, unsigned);\n'
     'static int t_VA4() { br_VA4(0, 1u, 2u); return g_4 == 0x200000001LL ? 0 : 1; }'),
]


def main():
    names = [c[1] for c in CASES] + [c[2] for c in CASES]
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    fails = 0
    for name, rm, dm, cdef, ccheck in CASES:
        v = abi.compare(sigs.parse_msvc(rm, dem[rm]), sigs.parse_msvc(dm, dem[dm]))
        if v.kind != 'same':
            print('ok %s: verdict %s (not a plain branch)' % (name, v.kind))
            continue
        asm = ['    .text', '    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_%s\n' % (name, name, name)]
        tree = tempfile.mkdtemp(prefix='v2va-')   # raises when it cannot create the directory
        try:
            bad = test_bridges.execute(tree, TYPES, [cdef], [ccheck], ['t_%s()' % name], asm)
        finally:
            import shutil
            shutil.rmtree(tree, ignore_errors=True)
        print('%s %s: verdict same, native run %s' % ('FAIL' if bad else 'ok', name,
                                                      'differs from x86' if bad else 'matches x86'))
        fails += bad
    print('ok' if not fails else 'FAILED (%d of %d)' % (fails, len(CASES)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
