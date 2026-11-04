#!/usr/bin/env python3
"""test_s15_pass_bucket.py - V1 (D14) against S15-simd e8891c1: BSBatchRenderer::FindPassBucket re-measured
70 + 12 -> 42 + 12 A64 instructions per call "with no code change". What changed between the two links?

    python3 -I tests/v1/test_s15_pass_bucket.py     -> one line per case, exit 1 when a case shows a defect

The decomp's bsbatchrenderer.cpp is read from ext/fnv_decomp at run time (nothing of it is in git): the part up to
FindBucketIndex's end is compiled with compile.sh's flags twice, once with the distro hardening compile.sh dropped
in 2be012c (-fstack-protector-strong -fstack-clash-protection, as in PERF.md section 7's measurement, whose rank 1
lever this is), once without (compile.sh now). x86 counts: 70 / 1.84 = 38 and 42 / 1.11 = 38, the same path.
  PB1  FindPassBucket takes &groupIndex (GetAt's out parameter), so the stack protector instruments it: the
       hardened build has __stack_chk_guard and is at least 11 instructions longer on its straight path
       (prologue canary 4, epilogue check 6+), FindBucketIndex (no address-taken local) is the same size in both:
       the 82 -> 54 drop is a link built with other flags, plausible, not a measurement error of the old number
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
NM = 'aarch64-linux-gnu-nm'


def main():
    src = os.path.join(PORT, 'ext', 'fnv_decomp', 'src', 'bsshader', 'bsbatchrenderer.cpp')
    if not (os.path.isfile(src) and shutil.which(CXX) and shutil.which(NM)):
        print('skip: needs ext/fnv_decomp and the aarch64 cross g++')
        return 0
    text = open(src, errors='replace').read()
    end = text.find('// @fnv 0x00b9aec0')
    if end < 0 or 'FindPassBucket' not in text[:end]:
        print('skip: the decomp layout changed (no FindBucketIndex before 0x00b9aec0)')
        return 0
    t = tempfile.mkdtemp(prefix='v1-fpb-')
    try:
        unit = os.path.join(t, 'unit.cpp')
        open(unit, 'w').write(text[:end])
        base = ['-mabi=ilp32', '-std=gnu++17', '-c', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math',
                '-fsigned-char', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks',
                '-ffp-contract=off', '-fPIC', '-fpermissive', '-Wno-narrowing', '-w', '-march=armv8-a+crc+crypto',
                '-mtune=cortex-a57', '-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
                '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + os.path.join(PORT, 'ext', 'sse2neon'),
                '-I' + os.path.join(PORT, 'ext', 'fnv_decomp', 'include'),
                '-I' + os.path.join(PORT, 'ext', 'fnv_decomp', 'src'), '-I' + os.path.dirname(src)]
        sizes = {}
        for v, extra in (('new', ['-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics',
                                  '-U_FORTIFY_SOURCE']),
                         ('old', ['-fstack-protector-strong', '-fstack-clash-protection'])):
            o = os.path.join(t, v + '.o')
            r = subprocess.run([CXX] + base + extra + [unit, '-o', o], capture_output=True, text=True)
            if r.returncode:
                print('skip: the unit does not compile outside the prepared tree: %s' % r.stderr[-300:])
                return 0
            nm = subprocess.run([NM, '-S', '-C', o], capture_output=True, text=True).stdout
            for f in ('FindPassBucket', 'FindBucketIndex'):
                m = re.search(r'^\w+ (\w+) T BSBatchRenderer::%s\(' % f, nm, re.M)
                sizes[v, f] = int(m.group(1), 16) // 4 if m else None
            sizes[v, 'chk'] = '__stack_chk_guard' in nm
        d = (sizes['old', 'FindPassBucket'] or 0) - (sizes['new', 'FindPassBucket'] or 0)
        ok = (sizes['old', 'chk'] and not sizes['new', 'chk'] and d >= 11
              and sizes['old', 'FindBucketIndex'] == sizes['new', 'FindBucketIndex'])
        print('PB1 %s' % (('ok (FindPassBucket %d -> %d static A64 instructions without the hardening, '
                           'FindBucketIndex %d both)' % (sizes['old', 'FindPassBucket'], sizes['new', 'FindPassBucket'],
                                                          sizes['new', 'FindBucketIndex'])) if ok else
                          'DEFECT: hardening does not explain the drop: %r' % sizes))
        return 0 if ok else 1
    finally:
        shutil.rmtree(t, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
