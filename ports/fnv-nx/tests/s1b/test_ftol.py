#!/usr/bin/env python3
"""test_ftol.py - FTOL1's fnvnx_ftol2_sse is exactly the CRT's _ftol2_sse on an SSE2 PC (cvttsd2si), inline.

    python3 tests/s1b/test_ftol.py     # needs a host C compiler (x86-64), aarch64-linux-gnu-g++ and qemu-aarch64

The helper text is taken from patches/ftol1_*.s1b (every copy must be the same). Reference: the host's own
`cvttsd2si` (_mm_cvttsd_si32) on the same doubles - libcmt's _ftol2_sse takes that path whenever __sse2_available is
set; the x87 _ftol2 fallback (low half of the int64 `fistp`) is not what an SSE2 machine runs. Values: NaN payloads,
infinities, zeros, int32 bounds and their neighbours, values beyond int32 / int64 / float range, denormals, and
float operands (promoted to double as at the patched call sites). Also: the -O2 AArch64 code of a function using the
helper contains no call (the helper must stay inline), and it compiles for ILP32.
"""
import glob
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
HOSTCC = os.environ.get('HOSTCC', 'cc')
QEMU = os.environ.get('QEMU', 'qemu-aarch64')

DOUBLES = [float('nan'), -float('nan'), float('inf'), -float('inf'), 0.0, -0.0, 0.5, -0.5, 0.999999, -0.999999,
           1.0, -1.0, 2147483647.0, 2147483647.75, 2147483648.0, 2147483648.5, -2147483648.0, -2147483648.75,
           -2147483649.0, -2147483649.5, 3e9, -3e9, 4294967296.0, 1e10, -1e10, 9.2e18, 1e19, -1e19, 1e300, -1e300,
           5e-324, -5e-324, 123456.789, -98765.4321, 16777217.0]
FLOATS = [3.4028234663852886e38, -3.4028234663852886e38, 2147483648.0, -2147483648.0, 3e9, -3e9, 2147483520.0,
          -2147483520.0, 1e-45]
NAN_BITS = [0x7ff8000000000000, 0xfff8000000000000, 0x7ff0000000000001, 0x7fffffffffffffff]


def helper_text():
    defs = set()
    for p in sorted(glob.glob(os.path.join(PORT, 'patches', 'ftol1_*.s1b'))):
        for line in open(p, encoding='utf-8'):
            m = re.search(r'static inline int \(fnvnx_ftol2_sse\)\(double x\) \{.*?\}(?= /\*| *$)', line)
            if m and not line.startswith('#'):
                defs.add(m.group(0))
    return defs


def bits_list():
    out = [struct.unpack('<Q', struct.pack('<d', d))[0] for d in DOUBLES] + NAN_BITS
    out += [struct.unpack('<Q', struct.pack('<d', struct.unpack('<f', struct.pack('<f', f))[0]))[0] for f in FLOATS]
    return out


def main():
    fails = []
    defs = helper_text()
    if len(defs) != 1:
        print('FAIL helper definitions differ or are missing: %d variants' % len(defs))
        return 1
    helper = defs.pop()
    vals = bits_list()
    arr = ', '.join('0x%016xULL' % v for v in vals)
    with tempfile.TemporaryDirectory() as t:
        ref_c = os.path.join(t, 'ref.c')
        open(ref_c, 'w').write(
            '#include <emmintrin.h>\n#include <stdio.h>\n#include <string.h>\n'
            'static const unsigned long long v[] = {%s};\n'
            'int main(void) { for (unsigned i = 0; i < sizeof v / sizeof v[0]; i++) { double d; '
            'memcpy(&d, &v[i], 8); printf("%%08x\\n", (unsigned)_mm_cvttsd_si32(_mm_set_sd(d))); } return 0; }\n' % arr)
        subprocess.run([HOSTCC, '-O1', '-msse2', ref_c, '-o', os.path.join(t, 'ref')], check=True)
        ref = subprocess.run([os.path.join(t, 'ref')], capture_output=True, text=True, check=True).stdout.split()
        tgt_cpp = os.path.join(t, 'tgt.cpp')
        open(tgt_cpp, 'w').write(
            '#include <stdio.h>\n#include <string.h>\n%s\n'
            'static const unsigned long long v[] = {%s};\n'
            '__attribute__((noinline)) int conv(double d) { return fnvnx_ftol2_sse(d); }\n'
            '__attribute__((noinline)) int convf(float f) { return fnvnx_ftol2_sse(f); }\n'
            'int main() { for (unsigned i = 0; i < sizeof v / sizeof v[0]; i++) { double d; '
            'memcpy(&d, &v[i], 8); printf("%%08x\\n", (unsigned)conv(d)); } '
            'float f = 3e9f; if (convf(f) != conv((double)f)) return 2; return 0; }\n' % (helper, arr))
        exe = os.path.join(t, 'tgt')
        subprocess.run([CXX, '-O2', '-static', tgt_cpp, '-o', exe], check=True)
        r = subprocess.run([QEMU, exe], capture_output=True, text=True)
        got = r.stdout.split()
        if r.returncode:
            fails.append('target run exit %d (float operand differs from its double)' % r.returncode)
        for v, a, b in zip(vals, ref, got):
            if a != b:
                fails.append('0x%016x (%r): cvttsd2si %s, helper %s' % (v, struct.unpack('<d', struct.pack('<Q', v))[0],
                                                                       a, b))
        if len(ref) != len(vals) or len(got) != len(vals):
            fails.append('value count: ref %d, target %d, want %d' % (len(ref), len(got), len(vals)))
        # inline: no call in the -O2 code of a user of the helper; ILP32 compiles
        user = os.path.join(t, 'user.cpp')
        open(user, 'w').write('%s\nint use(float a, float b) { return fnvnx_ftol2_sse(a - b) + 1; }\n' % helper)
        obj = os.path.join(t, 'user.o')
        subprocess.run([CXX, '-mabi=ilp32', '-O2', '-c', user, '-o', obj], check=True)
        dis = subprocess.run([CXX.replace('g++', 'objdump'), '-d', '--no-show-raw-insn', obj], capture_output=True,
                             text=True, check=True).stdout
        insns = [l for l in dis.splitlines() if re.match(r'\s+[0-9a-f]+:\t', l)]
        if any(re.search(r'\t(bl|blr|b)\t[0-9a-f]+ <(?!_Z3useff)', l) for l in insns):
            fails.append('helper not inline: %s' % [l.strip() for l in insns])
        print('values %d, ILP32 user of the helper: %d instructions' % (len(vals), len(insns)))
    for f in fails:
        print('FAIL', f)
    print('OK' if not fails else '%d failures' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
