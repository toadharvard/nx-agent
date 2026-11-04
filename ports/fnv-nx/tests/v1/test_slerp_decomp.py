#!/usr/bin/env python3
"""test_slerp_decomp.py - V1 (D14) against S15-simd f0861e2: the NEON override of NiQuaternion::Slerp 0x00a6e330
(tools/link/overrides/slerp_00a6e330.c) against the decomp's own bodies, not S15's transcription (slerp_test.c).

    python3 -I tests/v1/test_slerp_decomp.py [--work <dir>]     (FNV_DECOMP=<decomp checkout>, default ext/fnv_decomp)

The four functions (FUN_00a6dc30, NiQuaternion::Slerp, isqrt_approx_in_neighborhood, NormalizeQuaternion) are
copied from ext/fnv_decomp/src/gamebryo2.2/corelibs/nimain/niquaternion.cpp into a temporary file at run time (no
decomp text in git) and compiled with slerp/harness.cpp:

1. AArch64 (compile.sh's float flags, qemu-aarch64): the override must give the decomp body's bits on 144,000
   cases (unit, antipodal dot < 0, cos -> 1, t around 0.5, NaN / inf / denormal, raw bits; Blow's published
   constants and random ones). NaN-payload-only differences are counted apart (S15's test accepts any NaN).
2. x86-64 SSE (-mfpmath=sse) of the same decomp bodies: IEEE single/double as MSVC's SSE code, must equal (1).
3. x87 (-mfpmath=387, excess precision): the PC exe's likely FPU; informational, max ULP vs (1) on finite outputs,
   for all inputs and for the engine's unit-quaternion inputs alone.
4. Dynamic A64 instructions per call (unicorn), checking f0861e2's "134 -> 88 [M]" without S13's harness link: the
   decomp bodies as one ILP32 unit (slerp/dyn_unit.cpp, compile.sh's code-generation flags without its plugins, which
   change no float code) against the override (build.sh's flags), on unit quaternions; informational.
SL1: (1)'s NaN-payload differences refute the override header's "to the bit ... NaN included" (PERF.md 11.6 only
claims any-NaN-matches-any-NaN). Exit 1 when (1), (2) or SL1 differs.
"""
import argparse
import os
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
DECOMP = os.path.join(os.environ.get('FNV_DECOMP') or os.path.join(PORT, 'ext', 'fnv_decomp'), 'src', 'gamebryo2.2', 'corelibs', 'nimain', 'niquaternion.cpp')
OVR = os.path.join(PORT, 'tools', 'link', 'overrides', 'slerp_00a6e330.c')
FP = ['-O2', '-frounding-math', '-ffp-contract=off', '-fwrapv', '-fno-strict-aliasing', '-fsigned-char']
HEADS = [r'float FUN_00a6dc30\(', r'void NiQuaternion::Slerp\(', r'static inline float isqrt_approx_in_neighborhood\(',
         r'void FUN_00a4ff00_shell::NormalizeQuaternion\(']


def extract(src):
    out = []
    for h in HEADS:
        m = re.search(h, src)
        if not m:
            raise SystemExit('decomp: %s not found' % h)
        i = src.index('{', m.end())
        d = 0
        for j in range(i, len(src)):
            d += {'{': 1, '}': -1}.get(src[j], 0)
            if d == 0:
                break
        out.append(src[m.start():j + 1])
    return '\n\n'.join(out) + '\n'


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def ulp(a, b):
    def key(u):
        return u if u < 0x80000000 else 0x80000000 - u
    return abs(key(a) - key(b))


A32 = ['-mabi=ilp32', '-O2', '-fPIC', '-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv',
       '-fno-delete-null-pointer-checks', '-ffp-contract=off', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
       '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics']


def dyn(work):
    """Dynamic instruction counts of the decomp's Slerp (with its two callees) and of the override, ILP32."""
    import random
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_S0, \
        UC_ARM64_REG_SP, UC_ARM64_REG_LR
    unit, ovr, elf = (os.path.join(work, n) for n in ('dyn_unit.o', 'ovr32.o', 'dyn.elf'))
    for argv in (['aarch64-linux-gnu-g++', *A32, '-std=gnu++17', '-fms-extensions', '-I' + work, '-c',
                  os.path.join(HERE, 'slerp', 'dyn_unit.cpp'), '-o', unit],
                 ['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-U_FORTIFY_SOURCE', '-std=gnu11',
                  '-ffp-contract=off', '-frounding-math', '-c', OVR, '-o', ovr],
                 ['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-e', '0', '-Ttext=0x10000', unit, ovr, '-o', elf]):
        r = sh(argv)
        if r.returncode:
            raise RuntimeError(r.stderr[-300:])
    f = ELFFile(open(elf, 'rb'))
    sym = {s.name: s['st_value'] for s in f.get_section_by_name('.symtab').iter_symbols()}
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(0, 0x400000)
    for seg in f.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            mu.mem_write(seg['p_vaddr'], seg.data())
    mu.mem_write(sym['__fnv_0109c9d8'], struct.pack('<d', 0.82279687))
    mu.mem_write(sym['__fnv_0109c9d0'], struct.pack('<d', 0.58549219))
    mu.mem_write(sym['__fnv_011f3ff0'], struct.pack('<f', 1.000311 / 0.959066 ** 0.5))
    mu.mem_write(sym['__fnv_011f3fec'], struct.pack('<f', 1.000311 * (-0.5 / (0.959066 * 0.959066 ** 0.5))))
    n = [0]
    mu.hook_add(UC_HOOK_CODE, lambda uc, addr, size, ud: n.__setitem__(0, n[0] + 1))
    rnd = random.Random(7)

    def q():
        v = [rnd.uniform(-1, 1) for _ in range(4)]
        m = sum(x * x for x in v) ** 0.5
        return [x / m for x in v]
    A, B, O, RET = 0x300000, 0x300010, 0x300020, 0x3ff000
    res = {}
    for name, s_ in (('decomp', '_ZN12NiQuaternion5SlerpEfRKS_S1_'), ('override', 'fnvnx_slerp_00a6e330')):
        rnd.seed(7)
        counts = []
        for _ in range(200):
            mu.mem_write(A, struct.pack('<4f', *q()))
            mu.mem_write(B, struct.pack('<4f', *q()))
            mu.reg_write(UC_ARM64_REG_X0, O)
            mu.reg_write(UC_ARM64_REG_X1, A)
            mu.reg_write(UC_ARM64_REG_X2, B)
            mu.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', rnd.random()))[0])
            mu.reg_write(UC_ARM64_REG_SP, 0x200000)
            mu.reg_write(UC_ARM64_REG_LR, RET)
            n[0] = 0
            mu.emu_start(sym[s_], RET)
            counts.append(n[0])
        res[name] = (min(counts), sum(counts) / len(counts), max(counts))
    print('info dyncount (ILP32, unit quaternions, 200 calls): decomp min/mean/max %d/%.1f/%d, override %d/%.1f/%d '
          '(f0861e2 claims 134 -> 88 on S13 harness cases)' % (*res['decomp'], *res['override']))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    for t in ('aarch64-linux-gnu-g++', 'aarch64-linux-gnu-gcc', 'qemu-aarch64', 'g++'):
        if not shutil.which(t):
            print('skip: %s missing' % t)
            return 0
    if not os.path.isfile(DECOMP):
        print('skip: no decomp at ' + DECOMP)
        return 0
    work = a.work or tempfile.mkdtemp(prefix='v1-slerp-')
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, 'decomp_slerp.inc'), 'w') as f:
        f.write(extract(open(DECOMP).read()))
    inc = ['-I' + work, '-std=gnu++17']
    harness = os.path.join(HERE, 'slerp', 'harness.cpp')
    ovr_o = os.path.join(work, 'ovr.o')
    builds = {}
    r = sh(['aarch64-linux-gnu-gcc', '-std=gnu11', *FP, '-c', OVR, '-o', ovr_o])
    if r.returncode:
        print('override build: ' + r.stderr[-400:])
        return 2
    exe = os.path.join(work, 'a64')
    r = sh(['aarch64-linux-gnu-g++', *FP, *inc, '-static', '-DWITH_OVERRIDE', harness, ovr_o, '-o', exe, '-lm'])
    if r.returncode:
        print('a64 build: ' + r.stderr[-400:])
        return 2
    builds['a64'] = ['qemu-aarch64', exe]
    for tag, extra in (('sse', ['-mfpmath=sse']), ('x87', ['-mfpmath=387', '-fexcess-precision=standard'])):
        exe = os.path.join(work, tag)
        r = sh(['g++', *FP, *inc, *extra, harness, '-o', exe, '-lm'])
        if r.returncode:
            print('%s build: %s' % (tag, r.stderr[-400:]))
            return 2
        builds[tag] = [exe]
    res = {k: sh(v, timeout=300) for k, v in builds.items()}
    bad = 0
    print('ok  ' if res['a64'].returncode == 0 else 'FAIL', 'override vs decomp body (AArch64): ' +
          res['a64'].stderr.strip().replace('\n', '; '))
    bad += res['a64'].returncode != 0
    m = re.search(r'(\d+) differ only in a NaN payload', res['a64'].stderr)
    nanpl = int(m.group(1)) if m else -1
    print('ok  ' if nanpl == 0 else 'FAIL', 'SL1 slerp_00a6e330.c\'s header: "the decomp\'s [result] to the bit for every '
          'input, NaN and infinity included": %d cases with NaN inputs give a NaN of another sign or payload than the decomp'
          % nanpl)
    bad += nanpl != 0
    la = res['a64'].stdout.split('\n')
    for tag, modes in (('sse', '012345'), ('x87', '012345'), ('x87', '012')):
        lb = res[tag].stdout.split('\n')
        diff = maxu = nonfin = nanpay = 0
        worst = ''
        for x, y in zip(la, lb):
            if x == y or x.split()[-1] not in modes:
                continue
            real = False
            for u, v in zip(x.split()[:4], y.split()[:4]):
                u, v = int(u, 16), int(v, 16)
                if u == v:
                    continue
                fu, fv = (struct.unpack('<f', struct.pack('<I', w))[0] for w in (u, v))
                if fu != fu and fv != fv:
                    nanpay += 1          # both NaN: x86's default NaN is 0xffc00000, AArch64's 0x7fc00000
                elif not (math.isfinite(fu) and math.isfinite(fv)):
                    nonfin += 1
                    real = True
                else:
                    real = True
                    if ulp(u, v) > maxu:
                        maxu, worst = ulp(u, v), '%r vs %r' % (fu, fv)
            diff += real
        total = sum(1 for x in la if x and x.split()[-1] in modes)
        okk = len(la) == len(lb) and diff == 0
        line = ('%d of %d cases differ beyond a NaN payload (%d NaN-payload-only components), max %d ULP on finite '
                'outputs%s, %d finite/non-finite' % (diff, total, nanpay, maxu,
                                                     ' (%s)' % worst if worst else '', nonfin))
        if tag == 'sse':
            bad += not okk
            print('ok  ' if okk else 'FAIL', 'decomp AArch64 vs x86-64 SSE: ' + line)
        else:
            print('info decomp AArch64 vs x87 (%s): ' % ('all inputs' if modes == '012345' else
                                                       'unit, antipodal, cos -> 1 inputs') + line)
    try:
        dyn(work)
    except Exception as e:   # informational only
        print('info dyncount: not run (%s)' % e)
    print('%d failed' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
