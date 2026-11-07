#!/usr/bin/env python3
"""The script-error lane (D25 #10): tools/perf/callwatch.py (linhost --calls and the emulator hook) and
tools/report/scripterr.py. No game data: the watched function is the mtqemu guest's mtq_frame_mark(k), the
"form" its argument.

    python3 -I tests/s15/test_scripterr.py [--no-live]

1. scripterr.py on synthetic logs: call-watcher lines of both sides, the private patterns (no text written), the
   original's baseline subtracted, calls past the logged ones, no data (exit 3);
2. callwatch.install on a stand-in emulator: targets by exe address (__fnv_<orig>), regex and name; register and
   dereferenced values; the totals line;
3. live (skips without the cross tools / qemu-aarch64): callwatch.py resolve + linhost --calls on the mtqemu guest:
   31 calls of mtq_frame_mark with forms 0..30, one caller function; scripterr.py groups them.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.normpath(os.path.join(HERE, '..', '..'))
SE = os.path.join(P, 'tools', 'report', 'scripterr.py')
CW = os.path.join(P, 'tools', 'perf', 'callwatch.py')
sys.path.insert(0, os.path.join(P, 'tools', 'perf'))
import callwatch  # noqa: E402
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail)[-600:] if detail and not cond else ''))
    if not cond:
        fails.append(name)


def se(d, logs, *args):
    ps = []
    for i, t in enumerate(logs):
        p = os.path.join(d, 's%d.log' % i)
        open(p, 'w').write(t)
        ps.append(p)
    out = os.path.join(d, 'se%d.md' % len([f for f in os.listdir(d) if f.startswith('se')]))
    r = subprocess.run([sys.executable, '-I', SE] + ps + ['--out', out] + list(args), capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else None
    return r, j, open(out).read() if os.path.isfile(out) else ''


def synthetic(d):
    LOG = ('linhost: call script-error caller 0x20001000 form 0x0001a2b3 code 0x2\n'
           'linhost: call script-error caller 0x20001000 form 0x0001a2b3 code 0x2\n'
           'linhost: call script-error caller 0x20002000 form 0x00000abc code 0x5\n'
           'emu: call console-error caller orig:00401230 form 0x00012345\n'
           '[fnvnx] SCRIPT: Error in script (0002F00D) at line 12: secret game text here\n'
           'linhost: calls script-error n 9\n')
    pats = os.path.join(d, 'pats.tsv')
    open(pats, 'w').write('# kind<TAB>regex\nscript-runtime\tError in script \\((?P<form>[0-9A-Fa-f]{8})\\)\n')
    r, j, md = se(d, [LOG], '--patterns', pats)
    ok('synthetic: runs', r.returncode == 0 and j, r.stderr)
    if not j:
        return
    g = {(x['form'], x['kind'], x['caller']): x for x in j['groups']}
    ok('synthetic: grouped by form, kind, caller', len(j['groups']) == 4 and g[('0001a2b3', 'script-error', '0x20001000')]['count'] == 2,
       j['groups'])
    ok('synthetic: emulator caller key kept', ('00012345', 'console-error', 'orig:00401230') in g)
    ok('synthetic: pattern form, no text', ('0002f00d', 'script-runtime', '-') in g and 'secret' not in md and
       'secret' not in json.dumps(j))
    ok('synthetic: calls past the logged ones', j['calls_not_logged'] == {'script-error': 6})
    ok('synthetic: metric = groups without a baseline', j['metric'] == 4)
    base = os.path.join(d, 'base.json')
    json.dump({'schema': 'fnvnx-scripterr/1', 'groups': [{'form': '0002f00d', 'kind': 'script-runtime'},
                                                          {'form': '00000abc', 'kind': 'script-error'}]}, open(base, 'w'))
    r, j, md = se(d, [LOG], '--patterns', pats, '--baseline', base)
    ok('synthetic: the original baseline subtracted', j and j['metric'] == 2 and j['in_original'] == 2, j and j['metric'])
    r, j, md = se(d, ['nothing\n'])
    ok('synthetic: no data, exit 3', r.returncode == 3)


class UC:
    def __init__(self, mem):
        self.mem, self.hooks, self.regs = mem, [], {}

    def hook_add(self, kind, cb, user_data=None, begin=0, end=0):
        self.hooks.append((begin, cb, user_data))

    def reg_read(self, r):
        return self.regs.get(r, 0)

    def mem_read(self, a, n):
        if a not in self.mem:
            raise RuntimeError('unmapped')
        return self.mem[a].to_bytes(4, 'little')


def emu_part(d):
    cfg = os.path.join(d, 'watch.cfg')
    open(cfg, 'w').write('# private in real use\n0x00401230 script-error form=*x1+0xc code=x2\n'
                         're:^ConsoleErr console-error form=x0\nmissing_fn other form=x0\n')

    class FakeEmu:
        def __init__(self):
            self.uc = UC({0x5000 + 0xC: 0x0001A2B3})
            self.code_syms = {'__fnv_00401230': 0x10000, 'ConsoleErrA': 0x20000, 'ConsoleErrB': 0x20100}

        def code_addr(self, n):
            return self.code_syms.get(n)

        def sym(self, a):
            return '%#x Caller::f [orig 00800000]+0x10' % a

        def run(self):
            for begin, cb, data in self.uc.hooks:
                self.uc.regs = {'x0': 0x77, 'x1': 0x5000, 'x2': 3, 'x30': 0x30000}
                cb(self.uc, begin, 4, data)
            return 0
    eg = types.SimpleNamespace(Emu=FakeEmu)
    callwatch.install(eg, cfg)
    out = []
    import builtins
    real = builtins.print
    builtins.print = lambda *a, **k: out.append(' '.join(str(x) for x in a))
    try:
        FakeEmu().run()
    finally:
        builtins.print = real
    ok('emu: 3 functions watched (exe address, 2 by regex)', any('callwatch: 3 functions watched' in l for l in out), out)
    ok('emu: dereferenced form and register code', 'emu: call script-error caller orig:00800000 form 0x1a2b3 code 0x3' in out, out)
    ok('emu: totals per kind', 'emu: calls console-error n 2' in out and 'emu: calls script-error n 1' in out, out)


def live(d):
    for t in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-ld', 'qemu-aarch64'):
        if not shutil.which(t):
            print('skip live: %s not found' % t)
            return
    b = subprocess.run(['bash', os.path.join(HERE, 'mtqemu', 'build.sh'), d], capture_output=True, text=True)
    if b.returncode:
        ok('live: build', False, b.stdout + b.stderr)
        return
    elf = os.path.join(d, 'guest.elf')
    cfg, watch = os.path.join(d, 'w.cfg'), os.path.join(d, 'watch.txt')
    open(cfg, 'w').write('mtq_frame_mark frame-mark form=x0 bad=*x0+0\n')
    r = subprocess.run([sys.executable, '-I', CW, 'resolve', cfg, '--elf', elf, '--out', watch], capture_output=True, text=True)
    ok('live: resolve', r.returncode == 0 and 'call 0x' in open(watch).read(), r.stderr)
    try:
        r = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(d, 'linhost'), '--calls', watch, elf],
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        ok('live: run', False, 'timeout')
        return
    out = r.stdout + r.stderr
    calls = [l for l in out.splitlines() if l.startswith('linhost: call frame-mark')]
    ok('live: 31 calls with form = the argument', len(calls) == 31 and calls[0].endswith('form 0x0 bad unreadable') and
       ' form 0x1e ' in calls[-1] + ' ', calls[:2] + calls[-1:])
    ok('live: totals line', 'linhost: calls frame-mark n 31' in out)
    ok('live: the guest still passes', 'mtq: done, 0 failed' in out, out[-300:])
    log = os.path.join(d, 'run.log')
    open(log, 'w').write(out)
    rr = subprocess.run([sys.executable, '-I', SE, log, '--elf', elf, '--out', os.path.join(d, 'se.md')],
                        capture_output=True, text=True)
    j = json.load(open(os.path.join(d, 'se.json')))
    callers = {g['caller'] for g in j['groups']}
    # one caller: the frame loop (t_frames, inlined into mtq_main at -O2)
    ok('live: scripterr groups 31 forms at one caller function', j['metric'] == 31 and len(callers) == 1 and
       list(callers)[0] in ('_ZL8t_framesv', 'mtq_main'), (j['metric'], callers, rr.stderr))


def main():
    with tempfile.TemporaryDirectory() as d:
        synthetic(d)
        emu_part(d)
    if '--no-live' not in sys.argv:
        with tempfile.TemporaryDirectory() as d:
            live(d)
    print('test_scripterr: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
