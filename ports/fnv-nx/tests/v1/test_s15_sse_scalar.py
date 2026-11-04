#!/usr/bin/env python3
"""test_s15_sse_scalar.py - V1 (D14) against S15-simd 8fc5af8: the scalar _mm_rsqrt_ss / _mm_add_ss / _mm_set_ss /
_mm_load_ss of shim/include/fnvnx_sse_fast.h.

    python3 -I tests/v1/test_s15_sse_scalar.py     -> one line per case, exit 1 when a case shows a defect

tests/v1/sse_scalar/sse_scalar_test.cpp (our code, synthetic bits) is built static for AArch64 LP64 with the
game's floating-point flags (-ffp-contract=off -frounding-math) and run under qemu-aarch64:
  SS1-O2, SS1-O3 (the game's -O2)  rsqrt_ss / set_ss / load_ss give sse2neon's bits, add_ss sse2neon's lane 0 (two
                          NaN operands: sse2neon's or x86's a, since GCC orders fadd's operands in both forms); lanes 1-3 of rsqrt_ss and add_ss are
                          a's bits exactly (x86 rsqrtss / addss); set_ss / load_ss zero lanes 1-3 and keep x's bits;
                          load_ss at unaligned, negative and large offsets
  SS2 (-O0)               the same at -O0. Refutation case: rsqrt_ss of a negative number gives ffc00000 where
                          sse2neon gives 7fc00000. frsqrts negates its first operand before NaN processing, and the
                          last fmul's NaN operand order is GCC's choice (`e * step` is emitted step-first at -O0), so
                          "the same bits" holds only where GCC happens to order the operands as sse2neon's vmulq
                          (x86 rsqrtss gives ffc00000 too: the difference is harmless for the oracle)
The claim is bit equality with sse2neon (not with x86's 12-bit rsqrtss table, which neither form reproduces).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
QEMU = shutil.which('qemu-aarch64-static') or shutil.which('qemu-aarch64')


def main():
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    if not (shutil.which(CXX) and QEMU and os.path.isfile(os.path.join(sse, 'sse2neon.h'))):
        print('skip: needs %s, qemu-aarch64 and ext/sse2neon' % CXX)
        return 0
    t = tempfile.mkdtemp(prefix='v1-sse-')
    bad = 0
    try:
        for opt in ('-O2', '-O3', '-O0'):
            cid = 'SS2' if opt == '-O0' else 'SS1' + opt
            exe = os.path.join(t, 'ss' + opt)
            b = subprocess.run([CXX, '-static', opt, '-std=gnu++17', '-ffp-contract=off', '-frounding-math',
                                '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse,
                                os.path.join(HERE, 'sse_scalar', 'sse_scalar_test.cpp'), '-o', exe],
                               capture_output=True, text=True)
            if b.returncode:
                print('%s DEFECT: build: %s' % (cid, b.stderr[-400:]))
                bad += 1
                continue
            r = subprocess.run([QEMU, exe], capture_output=True, text=True, timeout=300)
            last = (r.stdout.strip().splitlines() or ['?'])[-1]
            ok = r.returncode == 0
            print('%s %s' % (cid, ('ok (%s)' % last) if ok else 'DEFECT: ' + r.stdout.strip()[-600:]))
            bad += not ok
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('%d defects' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
