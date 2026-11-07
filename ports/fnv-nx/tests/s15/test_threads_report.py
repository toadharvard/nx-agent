#!/usr/bin/env python3
"""tools/threads/report.py on synthetic logs (D25 #7): cumulative periodic dumps, function pairs, cycles, long waits
and the suppression file (kinds, either order, build column, stale entries, required reason). No guest, no game data.

    python3 -I tests/s15/test_threads_report.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, '..', '..', 'tools', 'threads', 'report.py')
LOG = """\
hb-race write-read prev 0x00001000 cur 0x00002000 addr 0x30000000 n 3 threads 1 2
hb: summary races 3 pairs 1 checks 10 threads 3 untracked 0 evictions 0 read-evictions 0 pairs-full 0
[fnvnx] hb-race write-read prev 0x00001000 cur 0x00002000 addr 0x30000000 n 5 threads 1 2
hb-race write-write prev 0x00003000 cur 0x00004000 addr 0x30000010 n 2 threads 2 3
hb-race read-write prev 0x00002000 cur 0x00001000 addr 0x30000020 n 1 threads 2 1
hb: summary races 8 pairs 3 checks 20 threads 3 untracked 0 evictions 0 read-evictions 0 pairs-full 0
wg: deadlock cycle 2 threads: tid 0x1004 mutex 0x20000000 pc 0x00005000 owner 0x1008 -> tid 0x1008 cs 0x20000100 pc 0x00006000 owner 0x1004
wg: long-wait tid 0x100c event 0x20000200 owner - pc 0x00007000 waited 12000 ms timeout INFINITE
wg: long-wait tid 0x1010 semaphore 0x20000300 owner - pc 0x00008000 waited 15000 ms timeout INFINITE
wg: summary cycles 1 long-waits 2 scans 30 threads 5 limit 10000 ms
"""


def run(d, supp=None, elf=None):
    log, out = os.path.join(d, 'run.log'), os.path.join(d, 'r.md')
    open(log, 'w').write(LOG)
    argv = [sys.executable, '-I', REPORT, log, '--out', out]
    if elf:
        argv += ['--elf', elf]
    if supp is not None:
        p = os.path.join(d, 'supp.tsv')
        open(p, 'w').write(supp)
        argv += ['--suppress', p]
    r = subprocess.run(argv, capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if r.returncode == 0 else None
    return r, j, open(out).read() if r.returncode == 0 else ''


def main():
    fails = []
    def ok(name, cond):
        print(('ok   ' if cond else 'FAIL ') + name)
        if not cond:
            fails.append(name)
    with tempfile.TemporaryDirectory() as d:
        r, j, md = run(d)
        # the periodic dump: the last n of an access pair counts (5, not 3 + 5); the two orders of 0x1000/0x2000 are
        # one function pair
        ok('cumulative dumps, function pairs', j and j['races'] == 8 and j['function_pairs'] == 2 and j['race_pairs'] == 3)
        ok('cycles and long waits', j and j['cycles'] == 1 and j['long_waits'] == 2)
        ok('summary is the last one', 'checks 20' in md)
        # suppressions: either order, kind subset, long-wait by caller and object kind, stale entry
        supp = ('# known benign\n'
                'race\t0x00002000\t0x00001000\t*\tstats counter, torn read accepted (test)\n'
                'write-write\t0x00003000\t0x00004000\t*\tnot this kind only? it is: suppressed (test)\n'
                'long-wait\t0x00007000\tevent\t*\tidle worker parks on its event (test)\n'
                'race\t0x00009000\t0x0000a000\t*\tstale (test)\n')
        r, j, md = run(d, supp)
        ok('suppressed races leave the counts', j and j['races'] == 0 and j['suppressed_races'] == 8)
        ok('suppressed long wait', j and j['long_waits'] == 1 and j['suppressed_long_waits'] == 1)
        ok('stale entry listed', j and j['stale_suppressions'] == ['supp.tsv:5'] and 'stale (matched nothing)' in md)
        ok('reasons in the table', 'idle worker parks on its event' in md)
        # a kind the entry does not name keeps the pair reported
        r, j, md = run(d, 'read-write\t0x00001000\t0x00002000\t*\tonly read-write (test)\n')
        ok('kind mismatch keeps the pair', j and j['races'] == 8 and j['suppressed_races'] == 0)
        # the build column: another build's entry does not apply
        elf = os.path.join(d, 'img.bin')
        open(elf, 'wb').write(b'not an elf, only its hash matters here')
        bid = hashlib.sha256(open(elf, 'rb').read()).hexdigest()[:12]
        r, j, md = run(d, 'race\t0x1000\t0x2000\t000000000000\tother build (test)\n', elf)
        ok('other build: not applied', j and j['suppressed_races'] == 0 and j['build'] == bid)
        r, j, md = run(d, 'race\t0x1000\t0x2000\t%s\tthis build (test)\n' % bid, elf)
        ok('this build: applied', j and j['suppressed_races'] == 6)
        # a reason is required
        r, j, md = run(d, 'race\t0x1000\t0x2000\t*\t\n')
        ok('no reason: refused', r.returncode != 0 and 'reason' in r.stderr)
    print('test_threads_report: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
