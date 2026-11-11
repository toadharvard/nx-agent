#!/usr/bin/env python3
"""test_ftoi_fuzz.py - V1 (D14) against S12's FTOI2 plugin (toolchain/ftoi/fnvnx_ftoi.cc, J-0135): float -> integer
conversions compiled with the plugin against x86's cvtt / x87 truncating stores (MSVC 2008's instructions), on
~9,000 doubles and ~6,000 floats per conversion: every width and signedness (8/16/32/64, signed and unsigned, char,
wchar_t, enum, bit-fields of 5, 20 and 40 bits), compound assignments, constants through inlining, and the sse2neon
intrinsics the decomp uses (_mm_cvtss_si32; _mm_cvttss_si32 as well), at -O0/-O1/-O2/-O3/-Os with compile.sh's flags.

    python3 -I tests/v1/test_ftoi_fuzz.py [--work <dir>]      (SSE2NEON=<sse2neon checkout> adds the intrinsics)

Needs: g++ with the aarch64 plugin headers (toolchain/ftoi/build.sh), aarch64-linux-gnu-g++, qemu-aarch64, a host
x86-64 gcc. One line per conversion class with mismatches, per build; exit 1 when the plugin build differs from x86
anywhere outside the classes listed as known (KNOWN, with the reason). Inputs: tests/v1/ftoi/gen.h (synthetic).
"""
import argparse
import collections
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(HERE, 'ftoi')
# compile.sh's FLAGS without -mabi=ilp32 / -fPIC / -c / the shim (qemu-user runs LP64 only; the fixed-width types
# and unsigned int stand for ILP32's long; `ul` is 64-bit here, masked to 32)
FLAGS = ['-std=gnu++17', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char', '-fno-strict-aliasing',
         '-fwrapv', '-fno-delete-null-pointer-checks', '-ffp-contract=off', '-fpermissive', '-Wno-narrowing',
         '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-mno-outline-atomics', '-static']
KNOWN = {}   # conversion class -> why a difference is by design (none yet)


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    work = a.work or tempfile.mkdtemp(prefix='v1-ftoi-')
    os.makedirs(work, exist_ok=True)
    r = sh(['bash', os.path.join(PORT, 'toolchain', 'ftoi', 'build.sh'), os.path.join(work, 'plugin')])
    if r.returncode:
        print('plugin does not build: ' + r.stderr.strip()[-300:])
        return 2
    so = r.stdout.strip().splitlines()[-1]
    # the direct float -> enum cast is not the exe's language (MSVC 2008: error C2440): an error, never a guess
    direct = os.path.join(work, 'enum_direct.cpp')
    with open(direct, 'w') as f:
        f.write('enum E { A, B };\nE g(float x) { return (E)x; }\nE h(float x) { return (E)(int)x; }\n')
    r = sh(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-O2', '-fplugin=' + so, '-fplugin-arg-fnvnx_ftoi-enum=error', '-S', '-o', os.devnull, direct])
    if r.returncode == 0 or r.stderr.count('FTOI:') != 1:
        print('FAIL: a direct float -> enum conversion must be exactly one FTOI error: ' + r.stderr[-300:])
        return 1
    r = sh(['gcc', '-O1', '-I' + SRC, '-o', os.path.join(work, 'ref'), os.path.join(SRC, 'ref.c')])
    if r.returncode:
        print('ref.c: ' + r.stderr[-300:])
        return 2
    ref = sh([os.path.join(work, 'ref')]).stdout.splitlines()
    s2n = os.environ.get('SSE2NEON')
    extra = ['-DWITH_SSE2NEON', '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + s2n] if s2n and os.path.isfile(os.path.join(s2n, 'sse2neon.h')) else []
    if not extra:
        ref = [l for l in ref if not l.startswith('mm_')]
        print('note: SSE2NEON not set, the intrinsics are not checked')
    bad = 0
    builds = [('O0', ['-O0'], True), ('O1', ['-O1'], True), ('O2', ['-O2'], True), ('O3', ['-O3'], True),
              ('Os', ['-Os'], True), ('O2-no-plugin', ['-O2'], False)]
    for tag, opt, plug in builds:
        exe = os.path.join(work, 't-' + tag)
        r = sh(['aarch64-linux-gnu-g++'] + FLAGS + opt + extra + (['-fplugin=' + so] if plug else []) +
               ['-I' + SRC, '-o', exe, os.path.join(SRC, 'target.cpp')])
        if r.returncode:
            print('%s: does not build: %s' % (tag, r.stderr.strip()[-300:]))
            bad = 1
            continue
        out = sh(['qemu-aarch64', exe], timeout=300).stdout.splitlines()
        os.remove(exe)
        if len(out) != len(ref):
            print('%s: %d lines, ref %d' % (tag, len(out), len(ref)))
            bad |= plug
            continue
        diff = collections.OrderedDict()
        for x, y in zip(ref, out):
            if x != y:
                k = x.split()[0]
                diff.setdefault(k, []).append((x, y.split()[-1]))
        if not plug:
            print('%s: %d of %d conversions differ (the test bites)' % (tag, sum(len(v) for v in diff.values()),
                                                                        len(ref)))
            bad |= not diff
            continue
        if not diff:
            print('%s: ok, %d conversions equal x86' % (tag, len(ref)))
        for k, v in diff.items():
            # the first difference, and the first on a NaN or infinity when there is one
            sp = [e for e in v if (int(e[0].split()[2], 16) >> (52 if e[0].split()[1] == 'd' else 23)) &
                  (0x7ff if e[0].split()[1] == 'd' else 0xff) in (0x7ff, 0xff)]
            x, got = v[0]
            c, s, b, want = x.split()
            known = KNOWN.get(k)
            print('%s: %s %s: %d differ, e.g. %s input %s: x86 %s, port %s' % (
                tag, 'known' if known else 'DIFF', k, len(v), s, b, want, got))
            if sp and sp[0] != v[0]:
                print('%s:     and %s input %s: x86 %s, port %s' % (tag, sp[0][0].split()[1], sp[0][0].split()[2],
                                                                  sp[0][0].split()[3], sp[0][1]))
            bad |= not known
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
