#!/usr/bin/env python3
"""T1: tools/farm/boot_determinism.py - two identical emulated boots must agree (S5's timer slices of
2d1afaeb..6da559aa did not). Synthetic summaries and traces: identical boots that ended by themselves pass; a
different summary (a skip count, a milestone) or a different trace fails, timing fields are ignored; a boot cut
short by the clock passes when it is a prefix of the other and fails when the traces diverge or it reached a
milestone the other did not; a missing summary exits 2."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', '..', 'tools', 'farm', 'boot_determinism.py')
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


def boot(d, reached=3, skips=10, stop='game exited', seconds=40.0, trace=('A', 'B', 'C')):
    os.makedirs(d, exist_ok=True)
    names = ['winmain', 'ini-read', 'renderer-init', 'archive-list', 'bsa-opened']
    json.dump({'milestones': [{'name': n, 'reached': i < reached, 'at_s': i * seconds / 9} for i, n in enumerate(names)],
               'skips': skips, 'stop': {'reason': stop}, 'seconds': seconds}, open(os.path.join(d, 'boot_summary_stub.json'), 'w'))
    open(os.path.join(d, 'trace.tsv'), 'w').write(''.join('call\t%d\t1\t0\t%s\n' % (i, n) for i, n in enumerate(trace)))


def run(a, b, traces=True):
    argv = [sys.executable, '-I', TOOL, a, b]
    if traces:
        argv += ['--trace-a', os.path.join(a, 'trace.tsv'), '--trace-b', os.path.join(b, 'trace.tsv')]
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


t = tempfile.mkdtemp()
try:
    A, B = os.path.join(t, 'a'), os.path.join(t, 'b')
    boot(A); boot(B, seconds=55.5)
    rc, out = run(A, B)
    ok('identical boots (only the timings differ): PASS', rc == 0 and out.startswith('PASS: identical'))
    boot(B, skips=4624)
    rc, out = run(A, B)
    ok('a different skip count: FAIL, the field named', rc == 1 and '/skips' in out)
    boot(B, trace=('A', 'X', 'C'))
    rc, out = run(A, B)
    ok('a different function trace: FAIL at line 2', rc == 1 and 'line 2' in out)
    boot(A, stop='STOPPED: run stopped by SIGTERM (wall-clock limit)', reached=2, trace=('A', 'B'))
    boot(B, reached=4, trace=('A', 'B', 'C', 'D'))
    rc, out = run(A, B)
    ok('one cut short by the clock, a prefix of the other: PASS', rc == 0 and out.startswith('PASS: consistent'))
    boot(A, stop='STOPPED: wall-clock limit', reached=2, trace=('A', 'Y'))
    rc, out = run(A, B)
    ok('cut short, the traces diverge: FAIL', rc == 1 and 'differ at line 2' in out)
    boot(A, stop='STOPPED: wall-clock limit', reached=2, trace=('A', 'B'))
    json.dump({'milestones': [{'name': 'bsa-opened', 'reached': True}], 'stop': {'reason': 'wall-clock'}},
              open(os.path.join(A, 'boot_summary_stub.json'), 'w'))
    rc, out = run(A, B, traces=False)
    ok('cut short, a milestone the longer did not reach: FAIL', rc == 1 and 'bsa-opened' in out)
    shutil.rmtree(B)
    rc, out = run(A, B)
    ok('a missing summary: exit 2', rc == 2)
finally:
    shutil.rmtree(t, ignore_errors=True)
print('test_boot_determinism: all ok' if not fails else 'test_boot_determinism: %d failed' % fails)
sys.exit(1 if fails else 0)
