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
CASES = ('parallel', 'cs', 'stages', 'spin-decomp', 'spin-override', 'unsync', 'detach', 'placement', 'ilk-count', 'ilk-refs', 'ilk-cost',
         'litmus-order', 'litmus-plain', 'race-pairs', 'wg-clean', 'wg-deadlock', 'wg-hang', 'frames', 'feed')
OPTIONAL = ('litmus-order', 'litmus-plain')   # skipped where the fnvnx_order plugin does not build
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
    for m in re.finditer(r'^mtq: (\S+) (ok|FAIL|skip) (.*)$', text, re.M):
        res[m.group(1)] = (m.group(2) != 'FAIL', m.group(3)) if m.group(2) != 'skip' or m.group(1) in OPTIONAL \
            else (False, 'skipped')
    return res


def check_reports(out, text, fails):
    """D25 #7: tools/threads/report.py maps race-pairs' race to the writer/reader pair and wg-deadlock's cycle to the
    two thread functions (the watchdog records the call site through hb.c's wraps), by function start address."""
    log, rep = os.path.join(out, 'run.log'), os.path.join(out, 'threads.md')
    open(log, 'w').write(text)
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', '..', 'tools', 'threads', 'report.py'), log,
                        '--elf', os.path.join(out, 'guest.elf'), '--out', rep], capture_output=True, text=True)
    if r.returncode:
        fails.append('report.py: rc %d %s' % (r.returncode, r.stderr.strip()[-200:]))
        return
    import json
    j = json.load(open(rep[:-3] + '.json'))
    nm = subprocess.run(['aarch64-linux-gnu-nm', os.path.join(out, 'guest.elf')], capture_output=True, text=True).stdout
    addr = {l.split()[2]: '0x%08x' % int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
    want = sorted([addr.get('mtq_pair_writer'), addr.get('mtq_pair_reader')])
    pairs = [sorted([p['a'], p['b']]) for p in j['function_pair_list']]
    print(f'threads report: function pairs {pairs}, cycles {j["cycles"]}, long waits {j["long_waits"]}')
    if want not in pairs:
        fails.append(f'report.py: race-pairs pair {want} not among {pairs}')
    wg = sorted([addr.get('_ZL4wg_aPv'), addr.get('_ZL4wg_bPv')])
    cyc = [sorted(e['func'] for e in c) for c in j['cycle_list']]
    if wg not in cyc:
        fails.append(f'report.py: the deadlock cycle is not in wg_a/wg_b {wg}: {cyc}')


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
        # the inlined Interlocked*: ldaxr/stlxr + dmb ish in ilk_mt's code for the 4 operations on the aligned counter;
        # the misaligned counter's address is known to be misaligned, so its 4 go straight to sync.cpp's split lock
        dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', os.path.join(out, 'ilk_mt_codegen.o')],
                             capture_output=True, text=True).stdout
        calls = re.findall(r'\tbl?\s+[0-9a-f]+ <(Interlocked\w+|fnvnx_interlocked_slow)>', dis)
        body = re.search(r'<_ZL12count_threadPv>:\n(.*?)\n\n', dis, re.S)
        body = body.group(1) if body else ''
        n_ldaxr, n_dmb = len(re.findall(r'\tldaxr\t', body)), len(re.findall(r'\tdmb\tish\n', body + '\n'))
        ok = n_ldaxr >= 4 and n_dmb >= 4 and len(calls) >= 4
        print(f'inline Interlocked: count_thread has {n_ldaxr} ldaxr, {n_dmb} dmb ish; '
              f'{len(calls)} branches to an Interlocked function (misaligned paths)')
        if not ok:
            fails.append('ilk_mt.o: the Interlocked calls are not inlined (ldaxr %d, dmb %d)' % (n_ldaxr, n_dmb))
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
                check_reports(out, text, fails)
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
