#!/usr/bin/env python3
"""test_s15simd_shuffle.py - V1 (D14) against S15-simd e1e16cb: fnvnx_sse_fast.h's two-source _mm_shuffle_ps
sequences against x86 _mm_shuffle_ps itself (the host compiler's <xmmintrin.h>, run natively), not against the
lane rule S15's shuffle_test.cpp encodes.

    python3 -I tests/v1/test_s15simd_shuffle.py      (SSE2NEON=<sse2neon checkout>, default ext/sse2neon)

1. s15simd/shuffle.cpp, every immediate 0..255 x 24 vector pairs (signalling / quiet NaNs of both signs with payloads,
   +-0, denormals, inf, raw bits) x 5 forms ((a, b), (b, a), (a, a), two names of one value, array elements): the
   AArch64 build (sse2neon + fnvnx_sse_fast.h, compile.sh's float flags, qemu-aarch64) must print the x86 bits.
2. Static instruction count (ret excluded) of noinline two-source functions for the replaced immediates 0x00 / 0x55 /
   0xaa / 0xff / 0xc4 / 0xd4 (claim: 2 each) and, with -DFNVNX_SSE_FAST_OFF, sse2neon's own expansion.
   The same with the result consumed by an add (the result register is free), counted minus the add.
SH1: 0xc4 returned from a noinline function is 3 instructions (the asm's early-clobber output forces a copy of a).
Exit 1 when (1) differs or a replaced immediate is not 2 instructions.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
SSE2NEON = os.environ.get('SSE2NEON') or os.path.join(PORT, 'ext', 'sse2neon')
SRC = os.path.join(HERE, 's15simd', 'shuffle.cpp')
FP = ['-O2', '-std=gnu++17', '-frounding-math', '-ffp-contract=off', '-fwrapv', '-fno-strict-aliasing', '-fsigned-char']
A64 = ['-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + SSE2NEON]


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def counts(obj):
    out = sh(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', obj]).stdout
    res, cur = {}, None
    for ln in out.splitlines():
        m = re.match(r'[0-9a-f]+ <(sh[2u]_0x[0-9a-f]+)>:', ln)
        if m:
            cur = m.group(1)
            res[cur] = []
            continue
        if cur and not ln.strip():
            cur = None
        elif cur:
            ins = ln.split(':', 1)[1].strip()
            if ins and not ins.startswith('nop') and not ins.startswith('ret'):
                res[cur].append(ins)
    return res


def main():
    for t in ('aarch64-linux-gnu-g++', 'qemu-aarch64', 'g++'):
        if not shutil.which(t):
            print('skip: %s missing' % t)
            return 0
    if not os.path.isfile(os.path.join(SSE2NEON, 'sse2neon.h')):
        print('skip: no sse2neon at ' + SSE2NEON)
        return 0
    work = tempfile.mkdtemp(prefix='v1-shuf-')
    x86, a64 = os.path.join(work, 'x86'), os.path.join(work, 'a64')
    for argv in (['g++', *FP, '-msse2', SRC, '-o', x86],
                 ['aarch64-linux-gnu-g++', *FP, *A64, '-static', SRC, '-o', a64]):
        r = sh(argv)
        if r.returncode:
            print('build: ' + r.stderr[-2000:])
            return 2
    ox, oa = sh([x86], timeout=300).stdout.splitlines(), sh(['qemu-aarch64', a64], timeout=300).stdout.splitlines()
    forms = ['(a, b)', '(b, a)', '(a, a)', '(a, copy of a)', '(v[1], v[0])']
    bad = {}
    for x, y in zip(ox, oa):
        if x != y:
            xs, ys = x.split(), y.split()
            for f in range(5):
                if xs[1 + 4 * f:5 + 4 * f] != ys[1 + 4 * f:5 + 4 * f]:
                    bad.setdefault((xs[0], f), (xs[1 + 4 * f:5 + 4 * f], ys[1 + 4 * f:5 + 4 * f]))
    fail = 0
    ok1 = len(ox) == len(oa) == 24 * 256 and not bad
    print('%s %d cases x 5 forms (x86 _mm_shuffle_ps vs AArch64 sse2neon + fnvnx_sse_fast.h): %d (imm, form) differ' % (
        'ok  ' if ok1 else 'FAIL', len(ox), len(bad)))
    for (imm, f), (w, g) in sorted(bad.items())[:8]:
        print('     imm 0x%s %s: x86 %s, AArch64 %s' % (imm, forms[f], ' '.join(w), ' '.join(g)))
    fail += not ok1
    res = {}
    for tag, extra in (('fast', []), ('sse2neon', ['-DFNVNX_SSE_FAST_OFF'])):
        o = os.path.join(work, tag + '.o')
        r = sh(['aarch64-linux-gnu-g++', *FP, *A64, '-DCOUNT', *extra, '-c', SRC, '-o', o])
        if r.returncode:
            print('build: ' + r.stderr[-2000:])
            return 2
        res[tag] = counts(o)
    for fn in sorted(res['fast']):
        f, s = res['fast'][fn], res['sse2neon'][fn]
        imm, used = fn[4:], fn.startswith('shu_')
        n, ns = len(f) - used, len(s) - used   # shu_: minus the add
        replaced = imm in ('0x00', '0x55', '0xaa', '0xff', '0xc4', '0xd4')
        ok = n == 2 if replaced else [i.split()[0] for i in f] == [i.split()[0] for i in s]
        fail += not ok
        print('%s imm %s two-source%s: %d instructions (%s) <- sse2neon %d%s' % (
            'ok  ' if ok else 'FAIL', imm, ', result used by an add' if used else ', returned', n, '; '.join(f), ns,
            '' if replaced else ' (not replaced)'))
    print('%d failed' % fail)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
