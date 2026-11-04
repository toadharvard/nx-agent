#!/usr/bin/env python3
"""The multicore check under QEMU (tests/s15/mtqemu, docs/PERF.md 6.6): the real platform/win32 sync layer and
toolchain/guest/threads.c on real host threads, with the hb.c happens-before checker.

    python3 -I tests/s15/test_mtqemu.py [--keep DIR]

Needs aarch64-linux-gnu-gcc/g++/ld and qemu-aarch64 (skips without). Builds in seconds; one run takes 2-6 s.
Every case must pass on every run, except `parallel`: its speedup depends on what else the host runs on the
CPUs the placement uses (Switch cores 0-3 -> host CPUs 0-3), so it must pass in at least one of up to 3 runs.
A run that does not finish in 120 s is a failure (a lost wake or a self-join hangs, it does not crash).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = ('parallel', 'cs', 'stages', 'spin-decomp', 'spin-override', 'unsync', 'detach')
RUNS = 3
TIMEOUT = 120


def run_once(out):
    try:
        p = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(out, 'linhost'),
                            os.path.join(out, 'guest.elf')], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or '')
        return None, text
    return p.returncode, p.stdout + p.stderr


def parse(text):
    res = {}
    for m in re.finditer(r'^mtq: (\S+) (ok|FAIL) (.*)$', text, re.M):
        res[m.group(1)] = (m.group(2) == 'ok', m.group(3))
    return res


def main():
    for tool in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-ld', 'qemu-aarch64'):
        if not shutil.which(tool):
            print(f'skip: {tool} not found')
            return 0
    keep = sys.argv[sys.argv.index('--keep') + 1] if '--keep' in sys.argv else None
    out = keep or tempfile.mkdtemp(prefix='mtqemu-')
    fails = []
    try:
        b = subprocess.run(['bash', os.path.join(HERE, 'mtqemu', 'build.sh'), out], capture_output=True, text=True)
        if b.returncode:
            print(b.stdout + b.stderr)
            print('FAIL build')
            return 1
        parallel_ok = False
        for i in range(RUNS):
            rc, text = run_once(out)
            res = parse(text)
            if rc is None:
                last = [l for l in text.splitlines() if l.startswith('mtq: ')][-1:] or ['(no output)']
                fails.append(f'run {i + 1}: no end in {TIMEOUT} s after "{last[0]}"')
                break
            for c in CASES:
                if c not in res:
                    fails.append(f'run {i + 1}: case {c} missing')
                elif c != 'parallel' and not res[c][0]:
                    fails.append(f'run {i + 1}: {c} FAIL {res[c][1]}')
            if 'parallel' in res:
                print(f'run {i + 1}: parallel {"ok" if res["parallel"][0] else "FAIL"} {res["parallel"][1]}')
                parallel_ok |= res['parallel'][0]
            if i == 0:
                for c in CASES[1:]:
                    if c in res:
                        print(f'run 1: {c} {"ok" if res[c][0] else "FAIL"} {res[c][1]}')
            if parallel_ok or fails:
                break
        if not parallel_ok and not fails:
            fails.append(f'parallel: no run of {RUNS} reached speedup 1.5')
    finally:
        if not keep:
            shutil.rmtree(out, ignore_errors=True)
    for f in fails:
        print('FAIL ' + f)
    print(f'test_mtqemu: {len(fails)} failed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
