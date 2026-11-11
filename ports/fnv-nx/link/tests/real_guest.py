#!/usr/bin/env python3
"""real_guest - the link stage on a guest linked from the REAL toolchain (PLAN_FIRST_ELF A7 follow-up).  TEST RUNNER, not a gate.

    real_guest.py --universe U --out DIR --dkp DEVKITPRO [--shards 64] [--scale 9] [--jobs 2] [--l3a DIR] [--fixture]

Builds the guest runtime (toolchain/guest/build.sh), the synthetic lifted set of synth_lift.py WITHOUT its test start object and host
fixture, the fixture `main` (real_main.c), and runs ld.py with the toolchain's start files, library directories and `-lgcc -lc -lsysbase` as
the gcc driver derives them (`-print-file-name`), then the gates `disp`, `pcrel`, `abs`.  The undefined list of ld.py is the result when the
link is red.  With --fixture the names the platform runtime and the guest start provide at run time (host_fixture.c here) are linked too.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import synth_lift

TOOLCHAIN = os.path.join(HERE, '..', '..', 'toolchain')
GCC_FLAGS = ['-mabi=ilp32', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fPIE', '-ftls-model=local-exec', '-O2']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dkp', required=True)
    ap.add_argument('--shards', type=int, default=64)
    ap.add_argument('--scale', type=int, default=9)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--l3a', default='/home/user/l1work/l3a2')
    ap.add_argument('--reuse', action='store_true', help='keep the synthetic lifted set already built under OUT/synth')
    ap.add_argument('--fixture', help='C file with the names the platform provides (linked as an input)')
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    env = dict(os.environ, DEVKITPRO=a.dkp, PATH=os.path.join(a.dkp, 'devkitA64', 'bin') + os.pathsep + os.environ['PATH'])
    gcc = 'aarch64-none-elf-gcc'
    os.makedirs(out, exist_ok=True)
    rt = os.path.join(out, 'rt')
    subprocess.run([os.path.join(TOOLCHAIN, 'guest', 'build.sh'), rt], env=env, check=True, capture_output=True)
    where = lambda f: subprocess.run([gcc, '-mabi=ilp32', '-fPIC', '-print-file-name=' + f], env=env, capture_output=True, text=True, check=True).stdout.strip()
    start = [os.path.normpath(where(f)) for f in ('crti.o', 'crtbegin.o')]
    end = [os.path.normpath(where(f)) for f in ('crtend.o', 'crtn.o')]
    libs = ['gcc', 'c', 'sysbase']
    libdirs = []
    for n in libs:
        d = os.path.dirname(os.path.normpath(where('lib%s.a' % n)))
        if d not in libdirs:
            libdirs.append(d)
    synth_lift.l3a_copy(a.l3a)
    ents = synth_lift.load_universe(a.universe)
    import time
    t = time.monotonic()
    work = os.path.join(out, 'synth')
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, 'universe.tsv'), 'w', newline='\n') as fh:
        fh.write(''.join('%08x\tdecomp\t%d\t-\n' % e for e in ents))
    if a.reuse and os.path.isdir(os.path.join(work, 'in')):
        order = [ents[i][0] for k in range(a.shards) for i in range(k, len(ents), a.shards)]
        far = (order[0], order[-1])
    else:
        far = synth_lift.build(ents, work, a.scale, a.shards, 8, a.l3a, jobs=a.jobs, test_objects=False)
    built = round(time.monotonic() - t, 1)
    # the runtime and the fixture main, as the in/ directory next to the lifted set
    rin = os.path.join(out, 'rtin')
    shutil.rmtree(rin, ignore_errors=True)
    os.makedirs(rin)
    for f in ('crt0.o', 'libfnvnx-guest.a'):
        shutil.copy(os.path.join(rt, f), os.path.join(rin, f))
    first, imp = ents[0][0], 0xf0000000
    defs = ['-DFIRST_ADDR=0x%08xu' % first, '-DFIRST_BODY=L_%08x' % first, '-DIMPORT_KEY=0x%08xu' % imp, '-DIMPORT_BODY=fnvl_import_0']
    subprocess.run([gcc] + GCC_FLAGS + ['-I', os.path.join(TOOLCHAIN, 'include')] + defs + ['-c', os.path.join(HERE, 'real_main.c'), '-o', os.path.join(rin, 'main.o')], env=env, check=True)
    if a.fixture:
        subprocess.run([gcc] + GCC_FLAGS + ['-I', os.path.join(TOOLCHAIN, 'include'), '-c', a.fixture, '-o', os.path.join(rin, 'host_fixture.o')], env=env, check=True)
    extra = ['--in', rin] + ['--lib-dir=' + d for d in libdirs] + ['--lib=' + n for n in libs] + ['--startfile=' + f for f in start] + ['--endfile=' + f for f in end]
    ld = os.path.join(a.dkp, 'devkitA64', 'bin', 'aarch64-none-elf-ld')
    synth_lift.LD = ld
    res, meas = synth_lift.link_and_gate(ents, work, far, ld_extra=extra, ld_args=['-maarch64elf32', '-EL', '-X'])
    synth_lift.report('real toolchain: %d entries' % len(ents), res, meas)
    print('build s: %s' % built)


if __name__ == '__main__':
    main()
