#!/usr/bin/env python3
"""real_reloc - the self-relocation of the guest runtime at link base 0, in the emulator.  TEST RUNNER, not a gate.

    real_reloc.py --dkp DEVKITPRO --out DIR [--runtime-tree DIR]

Links reloc_edge_main.c with the guest runtime and host_fixture.c through ld.py (base 0, strict), converts the ELF with elf2nso.py and runs
tests/s5/emu_guest.py (the emulator places a relocatable guest at 0x05000000).  With --runtime-tree the runtime comes from that tree
(a copy of toolchain/ with a changed guest/runtime.c) instead of the repository's: the proposed patch is checked by running both.
Prints the emulator's verdict line of each run.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
FLAGS = ['-mabi=ilp32', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fPIE', '-ftls-model=local-exec', '-O2']


def run(dkp, out, tree):
    env = dict(os.environ, DEVKITPRO=dkp, PATH=os.path.join(dkp, 'devkitA64', 'bin') + os.pathsep + os.environ['PATH'])
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, 'in'))
    subprocess.run([os.path.join(tree, 'guest', 'build.sh'), os.path.join(out, 'rt')], env=env, check=True, capture_output=True)
    for f in ('crt0.o', 'libfnvnx-guest.a'):
        shutil.copy(os.path.join(out, 'rt', f), os.path.join(out, 'in', f))
    inc = ['-I', os.path.join(ROOT, 'toolchain', 'include')]
    for src, o in (('reloc_edge_main.c', 'main.o'), ('host_fixture.c', 'host_fixture.o')):
        subprocess.run(['aarch64-none-elf-gcc'] + FLAGS + inc + ['-c', os.path.join(HERE, src), '-o', os.path.join(out, 'in', o)], env=env, check=True)
    where = lambda f: os.path.normpath(subprocess.run(['aarch64-none-elf-gcc', '-mabi=ilp32', '-fPIC', '-print-file-name=' + f], env=env, capture_output=True, text=True, check=True).stdout.strip())
    libdirs = []
    for n in ('gcc', 'c', 'sysbase'):
        d = os.path.dirname(where('lib%s.a' % n))
        if d not in libdirs:
            libdirs.append(d)
    cmd = [sys.executable, '-I', os.path.join(HERE, '..', 'ld.py'), '--ld', os.path.join(dkp, 'devkitA64', 'bin', 'aarch64-none-elf-ld'),
           '--ld-arg=-maarch64elf32', '--ld-arg=-EL', '--ld-arg=-X', '--script', os.path.join(HERE, '..', 'guest.ld'), '--in', os.path.join(out, 'in'),
           '--out', os.path.join(out, 'ld')] + ['--lib-dir=' + d for d in libdirs] + ['--lib=' + n for n in ('gcc', 'c', 'sysbase')] + \
          ['--startfile=' + where(f) for f in ('crti.o', 'crtbegin.o')] + ['--endfile=' + where(f) for f in ('crtend.o', 'crtn.o')]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode:
        return 'ld.py failed: ' + (r.stdout + r.stderr).strip()[-600:]
    nso = os.path.join(out, 'guest.nso')
    subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'toolchain', 'tools', 'elf2nso.py'), '--no-compress', os.path.join(out, 'ld', 'guest.elf'), nso], check=True, capture_output=True)
    r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'tests', 's5', 'emu_guest.py'), nso, '--expect', 'reloc edge: past_end ok'], capture_output=True, text=True)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith('reloc edge') or l.strip() in ('PASS', 'FAIL') or l.startswith('==')]
    return ' | '.join(lines) or r.stderr[-400:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dkp', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--runtime-tree', help='a toolchain/ tree whose guest/runtime.c replaces the repository\'s')
    a = ap.parse_args()
    print('repository runtime: %s' % run(a.dkp, os.path.join(a.out, 'repo'), os.path.join(ROOT, 'toolchain')))
    if a.runtime_tree:
        print('patched runtime:    %s' % run(a.dkp, os.path.join(a.out, 'patched'), a.runtime_tree))


if __name__ == '__main__':
    main()
