#!/usr/bin/env python3
"""Bit-exact tests of the S15-simd NEON overrides (tools/link/overrides, docs/PERF.md 11.5).

    python3 -I tests/s15/simd/run.py        # needs aarch64-linux-gnu-gcc and qemu-aarch64; skips without

Per kernel: the override's source is compiled as build.sh compiles it for the game (AArch64 ILP32 object, the
same flags), which must succeed, and its static instruction count is printed. Then the override and
<kernel>_test.c (a scalar reference in the decomp's order of operations, random and edge inputs) are compiled
for AArch64 LP64 Linux with the same floating-point flags and run under qemu-aarch64: every case must give the
reference's bits. Our own code only: no decomp text, no game data.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
OVR = os.path.join(PORT, 'tools', 'link', 'overrides')
CC = os.environ.get('CC', 'aarch64-linux-gnu-gcc')
QEMU = os.environ.get('QEMU', 'qemu-aarch64')
OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')

# kernel -> (override source, native symbol)
KERNELS = {
    'slerp': ('slerp_00a6e330.c', 'fnvnx_slerp_00a6e330'),
    'nibound': ('nibound_update_00a7fdd0.c', 'fnvnx_nibound_update_00a7fdd0'),
    'mat3': ('mat3_tmul_00a588a0.c', 'fnvnx_mat3_tmul_00a588a0'),
    'quat_from_matrix': ('quat_from_matrix_00a6df40.c', 'fnvnx_quat_from_matrix_00a6df40'),
    'sphere_plane': ('sphere_plane_004b6100.c', 'fnvnx_sphere_plane_004b6100'),
    'cull_worker': ('cull_worker_00a694e0.c', 'fnvnx_cull_worker_00a694e0'),
}


def build_flags():
    """The C flags tools/link/overrides/build.sh passes (one source of truth: parsed from the script)."""
    txt = open(os.path.join(OVR, 'build.sh')).read()
    m = re.search(r'\$CC (.*?) -c ', txt)
    return [f for f in m.group(1).split() if f != '-mabi=ilp32']


def static_count(obj, sym):
    out = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', obj], capture_output=True, text=True).stdout
    n, on = 0, False
    for ln in out.splitlines():
        if ln.endswith('<%s>:' % sym):
            on = True
            continue
        if on:
            if not ln.strip():
                break
            if re.match(r'\s+[0-9a-f]+:\s+\S', ln) and not ln.split(':', 1)[1].strip().startswith('nop'):
                n += 1
    return n


def main():
    if not shutil.which(CC) or not shutil.which(QEMU):
        print('skip: %s or %s missing' % (CC, QEMU))
        return 0
    flags = build_flags()
    fail = []
    sel_given = sys.argv[1:]
    sel = [k for k in sel_given if k in KERNELS] if sel_given else sorted(KERNELS)
    with tempfile.TemporaryDirectory() as tmp:
        for k in sel:
            src, sym = KERNELS[k]
            o32 = os.path.join(tmp, k + '.ilp32.o')
            r = subprocess.run([CC, '-mabi=ilp32', *flags, '-c', os.path.join(OVR, src), '-o', o32],
                               capture_output=True, text=True)
            if r.returncode:
                print('FAIL %s: ILP32 build\n%s' % (k, r.stderr))
                fail.append(k)
                continue
            exe = os.path.join(tmp, k)
            r = subprocess.run([CC, '-static', *flags, os.path.join(OVR, src), os.path.join(HERE, k + '_test.c'),
                                '-o', exe, '-lm'], capture_output=True, text=True)
            if r.returncode:
                print('FAIL %s: test build\n%s' % (k, r.stderr))
                fail.append(k)
                continue
            r = subprocess.run([QEMU, exe], capture_output=True, text=True, timeout=600)
            print(r.stdout.rstrip())
            print('%s %s: %d static A64 instructions (ILP32 object)' % ('ok  ' if r.returncode == 0 else 'FAIL', k,
                                                                         static_count(o32, sym)))
            if r.returncode:
                fail.append(k)
        if not sel_given or 'shuffle' in sel_given:
            exe = os.path.join(tmp, 'shuffle')
            cxx = CC.replace('gcc', 'g++')
            r = subprocess.run([cxx, '-static', '-O2', '-std=gnu++17', '-ffp-contract=off',
                                '-I' + os.path.join(PORT, 'shim', 'include'),
                                '-I' + os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon')),
                                os.path.join(HERE, 'shuffle_test.cpp'), '-o', exe], capture_output=True, text=True)
            if r.returncode:
                print('FAIL shuffle: test build\n%s' % r.stderr[-3000:])
                fail.append('shuffle')
            else:
                r = subprocess.run([QEMU, exe], capture_output=True, text=True, timeout=600)
                print(r.stdout.rstrip())
                print('%s shuffle (shim/include/fnvnx_sse_fast.h)' % ('ok  ' if r.returncode == 0 else 'FAIL'))
                if r.returncode:
                    fail.append('shuffle')
    print('%d failed' % len(fail))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
