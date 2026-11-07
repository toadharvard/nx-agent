#!/usr/bin/env python3
"""tools/perf/mem.py on synthetic logs (D25 #8, tools/perf/MEM.md): the peak against the budget (default 3.2 GiB,
GPU inside), cumulative flushes (the last flush's sites hold), failures, several runs (the worst decides), a log
without memory lines (exit 3). No guest, no game data.

    python3 -I tests/s15/test_mem_report.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, '..', '..', 'tools', 'perf', 'mem.py')
G = 1 << 30
M = 1 << 20
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail) if detail and not cond else ''))
    if not cond:
        fails.append(name)


def run(d, logs, *args):
    paths = []
    for i, text in enumerate(logs):
        p = os.path.join(d, 'run%d.log' % i)
        open(p, 'w').write(text)
        paths.append(p)
    out = os.path.join(d, 'm.md')
    r = subprocess.run([sys.executable, '-I', MEM] + paths + ['--out', out] + list(args), capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else None
    return r, j, open(out).read() if os.path.isfile(out) else ''


def log(peak_heap, peak_virtual, peak_gpu, fail=False, flushes=2):
    total = peak_heap + peak_virtual + peak_gpu
    lines = ['[fnvnx] mem: t 1000 heap %d virtual %d gpu %d' % (peak_heap // 2, peak_virtual, 0),
             'mem: t 2000 heap %d virtual %d gpu %d' % (peak_heap, peak_virtual, peak_gpu)]
    if fail:
        lines.append('mem: fail gpu 4194304 caller 0x20001234 t 2100')
    for k in range(flushes):
        lines += ['mem: peak %d heap %d virtual %d gpu %d t 2000' % (total, peak_heap, peak_virtual, peak_gpu),
                  'mem: site heap 0x20000100 live %d peak %d allocs %d bytes %d fails 0' % (M, peak_heap, 10 + k, peak_heap),
                  'mem: site gpu 0x20000200 live 0 peak %d allocs 3 bytes %d fails %d' % (peak_gpu, peak_gpu, int(fail)),
                  'mem: summary heap %d virtual %d gpu 0 peak %d fails %d untracked 0 sites 2 period 1000' %
                  (M, peak_virtual, total, int(fail))]
    return '\n'.join(lines) + '\n'


def main():
    with tempfile.TemporaryDirectory() as d:
        r, j, md = run(d, [log(1 * G, 1 * G, G // 2)])
        ok('under budget: PASS', j and j['verdict'] == 'PASS' and j['peak_total'] == int(2.5 * G), j and j['verdict'])
        ok('default budget 3.2 GiB', j and j['budget_bytes'] == int(3.2 * G))
        ok('gpu inside the total', j and j['runs'][0]['peak']['gpu'] == G // 2)
        ok('last flush holds (allocs 11, not 21)', j and j['top_sites'][0]['allocs'] == 11, j and j['top_sites'])
        r, j, md = run(d, [log(2 * G, 1 * G, G // 2)])
        ok('over budget: FAIL', j and j['verdict'] == 'FAIL' and j['over_budget'])
        r, j, md = run(d, [log(2 * G, 1 * G, G // 2)], '--budget-gib', '4')
        ok('--budget-gib', j and j['verdict'] == 'PASS' and j['budget_bytes'] == 4 * G)
        r, j, md = run(d, [log(G, G // 2, 0, fail=True)])
        ok('a failure: FAIL', j and j['verdict'] == 'FAIL' and j['fails'] == 1 and '| gpu | 4194304 |' in md, md[-400:])
        r, j, md = run(d, [log(G, G, 0), log(2 * G, G, G)])
        ok('two runs: the worst decides', j and j['peak_total'] == 4 * G and len(j['runs']) == 2 and j['verdict'] == 'FAIL')
        r, j, md = run(d, ['no memory lines here\n'])
        ok('no data: exit 3', r.returncode == 3 and j and j['verdict'] == 'no data')
    print('test_mem_report: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
