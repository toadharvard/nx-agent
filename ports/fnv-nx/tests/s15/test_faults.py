#!/usr/bin/env python3
"""tools/report/faults.py on synthetic logs (D25 #1): the emulator's trace, stubbed faults, skip-summary and hang lines,
linhost's fault / frames / skipped-initializer / hang lines, grouping by (kind, function, 3 callers) with exe addresses
as the stable key, new / known / fixed against a previous head. No guest, no game data.

    python3 -I tests/s15/test_faults.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(HERE, '..', '..', 'tools', 'report', 'faults.py')
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail)[-600:] if detail and not cond else ''))
    if not cond:
        fails.append(name)


EMU = """\
   stubbed fault: Foo::init returns 0 (invalid memory read (UC_ERR_READ_UNMAPPED) at 0x20001234 Bar::get [orig 00401230]+0x14)
   stubbed fault: Foo::init returns 0 (invalid memory read (UC_ERR_READ_UNMAPPED) at 0x20001238 Bar::get [orig 00401230]+0x18)
skip-summary\t12\tinvalid memory write at 0x0\t0x20002000 Baz::ctor [orig 00500000]+0x8\t0x20003000 _GLOBAL__sub_I_x+0x0
== trace (invalid memory fetch at 0x0 (UC_ERR_FETCH_UNMAPPED)), thread 3
   pc  0x0
   call 0x20004000 Qux::run [orig 00600000]+0x20 | x8 from vtable
   lr  0x20004020 Qux::run [orig 00600000]+0x20
   x0 0x0 x1 0x1
   #0  0x20005000 Loop::tick [orig 00700000]+0x30
   #1  0x20006000 Main::frame [orig 00800000]+0x40
   #2  0x20007000 WinMain [orig 0086a850]+0x50
   #3  0x20008000 main+0x10
STOPPED: invalid memory fetch at 0x0
"""
LIN = """\
linhost: skipped initializer 0x20010000: signal 11 at pc 0x20011004
linhost: fault: signal 11 thread 1234 pc 0x20012008 lr 0x2001300c addr 0x0000000000000010 sp 0x1
linhost: frames 0x20013010 0x20014010 0x20015010
linhost: initializers skipped after a fault: 1
"""
HANG = """\
== hang: no progress for 600 s (last: new function 600.2 s ago)
   thread 1 ready pc 0x20020000 Spin::wait [orig 00900000]+0x4 frames 0x20021000 Job::run [orig 00910000]+0x8 | -
   thread 2 blocked pc 0x20022000 Other::x+0x4 frames -
STOPPED: hang: no progress for 600 s (last: new function)
"""


def run(d, logs, prev=None):
    paths = []
    for i, t in enumerate(logs):
        p = os.path.join(d, 'l%d.log' % i)
        open(p, 'w').write(t)
        paths.append(p)
    out = os.path.join(d, 'f%d.md' % len(os.listdir(d)))
    argv = [sys.executable, '-I', F] + paths + ['--out', out] + (['--prev', prev] if prev else [])
    r = subprocess.run(argv, capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else None
    return r, j, out


def main():
    with tempfile.TemporaryDirectory() as d:
        r, j, out = run(d, [EMU, LIN, HANG])
        ok('runs', r.returncode == 0 and j, r.stderr)
        if not j:
            return 1
        g = {(x['kind'], x['func']): x for x in j['groups']}
        st = g.get(('stubbed:invalid memory read', 'orig:00401230'))
        ok('two stubbed faults at one function: one group of 2', st and st['count'] == 2 and st['chain'] == ['Foo::init'], st)
        sk = g.get(('skipped-init:invalid memory write at #', 'orig:00500000'))
        ok('skip-summary: count 12, the initializer as caller', sk and sk['count'] == 12, j['groups'])
        fa = g.get(('fatal:invalid memory fetch at #', '?'))
        ok('emu fatal: 3 callers by exe address', fa and fa['chain'] == ['orig:00700000', 'orig:00800000', 'orig:0086a850'], fa)
        lf = [x for x in j['groups'] if x['kind'] == 'fatal:signal-11']
        ok('linhost fatal: signal kind, frames chain (unmapped: unstable)', lf and lf[0]['func'] == '0x20012008' and
           len(lf[0]['chain']) == 3 and lf[0]['unstable'], lf)
        ok('linhost skipped initializer', any(x['kind'] == 'skipped-init:signal-11' for x in j['groups']))
        hg = g.get(('hang', 'orig:00900000'))
        ok('hang: the first thread stack', hg and hg['chain'] == ['orig:00910000'], hg)
        ok('totals', j['total_groups'] == 6 and j['total_faults'] == 2 + 12 + 1 + 1 + 1 + 1 and j['fatal'] == 3, j)
        ok('unclassified without --prev', all(x['status'] == 'unclassified' for x in j['groups']))
        prev = out[:-3] + '.json'
        # next head: the stubbed fault is gone, a new one appeared, the rest is known
        emu2 = EMU.replace('Foo::init returns 0 (invalid memory read', 'Foo::init2 returns 0 (invalid memory read', 1) \
                  .split('\n', 2)
        emu2 = emu2[0] + '\n' + emu2[2]   # one stubbed line, now from Foo::init2
        r, j2, out2 = run(d, [emu2, LIN, HANG], prev)
        s = {x['status'] for x in j2['groups']}
        ok('new / known / fixed', j2['new'] == 1 and j2['fixed'] == 1 and j2['known'] == 5 and s == {'new', 'known', 'fixed'},
           [(x['status'], x['kind'], x['func'], x['chain']) for x in j2['groups']])
        md = open(out2).read()
        ok('md lists new first', md.index('| new |') < md.index('| known |') < md.index('| fixed |'), md)
        r, j3, _ = run(d, ['nothing\n'])
        ok('no fault lines, no run: exit 3', r.returncode == 3)
    print('test_faults: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
