#!/usr/bin/env python3
"""The hang detectors (D25 #1 / #7 on long runs): linhost --progress-timeout (tests/s15/mtqemu/linhost.c) and
tools/perf/emu_progress.py for the emulator.

    python3 -I tests/s15/test_progress.py [--no-live]

1. emu_progress on a stand-in emulator with a fake clock: new functions, milestones and console output are progress;
   S seconds without any end the run with "== hang" and one stack line per thread (saved contexts restored to read
   them), as SystemExit for emu_guest's STOPPED; the threads' contexts are put back.
2. live (skips without aarch64-linux-gnu-* and qemu-aarch64): a tiny guest that spins in one loop under linhost
   --progress-timeout 3 exits 4 with the hang line and its thread's pc inside the loop function; the mtqemu guest's
   full run under --progress-timeout 60 never triggers it; tools/report/faults.py reads the hang as kind hang.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(P, 'tools', 'perf'))
import emu_progress  # noqa: E402
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail)[-600:] if detail and not cond else ''))
    if not cond:
        fails.append(name)


class FakeUC:
    def __init__(self):
        self.regs = {'pc': 0x1000, 'x29': 0}
        self.restored = []

    def reg_read(self, r):
        return self.regs[r]

    def context_save(self):
        return dict(self.regs)

    def context_restore(self, c):
        self.restored.append(c)
        self.regs = dict(c)

    def mem_read(self, a, n):
        raise RuntimeError('no memory')


class FakeEmu:
    def __init__(self):
        self.uc, self.miles, self.console = FakeUC(), {}, b''
        self.threads = {1: types.SimpleNamespace(state='ready', ctx={'pc': 0x2000, 'x29': 0}),
                        2: types.SimpleNamespace(state='blocked', ctx={'pc': 0x3000, 'x29': 0})}
        self.cur = types.SimpleNamespace(tid=1)

    def sym(self, a):
        return '%#x f%x+0x4' % (a, a)

    def pick(self):
        return 1


def emu_part():
    t = [0.0]
    eg = types.SimpleNamespace(Emu=FakeEmu)
    w = emu_progress.install(eg, 10, clock=lambda: t[0])
    e = FakeEmu()
    e.pick()
    for step in range(5):   # a new function every 5 s: no hang
        t[0] += 5
        e.uc.regs['pc'] = 0x1000 + 0x100 * (step + 1)
        e.pick()
    ok('emu: new functions keep it alive', not w.fired and w.what == 'new function')
    t[0] += 6
    e.miles['main-menu'] = 1
    e.pick()
    t[0] += 6
    e.console = b'hello'
    e.pick()
    ok('emu: milestone and console are progress', not w.fired and w.what == 'console')
    t[0] += 10.5
    out = []
    import builtins
    real = builtins.print
    builtins.print = lambda *a, **k: out.append(' '.join(str(x) for x in a))
    exc = None
    try:
        e.pick()
    except SystemExit as x:
        exc = x
    finally:
        builtins.print = real
    ok('emu: hang raises SystemExit', exc is not None and 'hang: no progress for 10 s' in str(exc), exc)
    ok('emu: hang header', out and out[0].startswith('== hang: no progress for 10 s'), out)
    ok('emu: one line per thread, saved contexts read', len([l for l in out if l.startswith('   thread ')]) == 2 and
       any('pc 0x3000' in l for l in out), out)
    ok('emu: the current context is put back', e.uc.regs['pc'] == 0x1500, e.uc.regs)


def live_part():
    for tl in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-ld', 'qemu-aarch64'):
        if not shutil.which(tl):
            print('skip live: %s not found' % tl)
            return
    with tempfile.TemporaryDirectory() as d:
        b = subprocess.run(['bash', os.path.join(HERE, 'mtqemu', 'build.sh'), d], capture_output=True, text=True)
        if b.returncode:
            ok('live: build', False, b.stdout + b.stderr)
            return
        mq = os.path.join(HERE, 'mtqemu')
        src = os.path.join(d, 'hang.c')
        open(src, 'w').write('/* a guest that spins in one loop: no new code after its start */\n'
                             'volatile unsigned long g_x;\n'
                             '__attribute__((noinline)) void hang_loop(void) { for (;;) g_x++; }\n'
                             'int mtq_main(void) { hang_loop(); return 0; }\n')
        cc = ['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fno-pic', '-fno-pie', '-fno-stack-protector',
              '-ffreestanding', '-ftls-model=local-exec', '-mno-outline-atomics', '-I' + os.path.join(mq, 'include'),
              '-I' + os.path.join(P, 'toolchain', 'include'), '-c']
        objs = []
        for s in (os.path.join(mq, 'gstart.c'), os.path.join(mq, 'mlibc.c'), src):
            o = os.path.join(d, os.path.basename(s) + '.o')
            extra = ['-fno-builtin', '-fno-tree-loop-distribute-patterns', '-Wno-builtin-declaration-mismatch'] \
                if s.endswith('mlibc.c') else []
            r = subprocess.run(cc + extra + [s, '-o', o], capture_output=True, text=True)
            if r.returncode:
                ok('live: hang guest compiles', False, r.stderr)
                return
            objs.append(o)
        elf = os.path.join(d, 'hang.elf')
        r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-T', os.path.join(mq, 'mt.ld')] + objs +
                           ['-o', elf], capture_output=True, text=True)
        if r.returncode:
            ok('live: hang guest links', False, r.stderr)
            return
        try:
            r = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(d, 'linhost'), '--progress-timeout', '3',
                                elf], capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            ok('live: the hang ends the run', False, 'timeout')
            return
        out = r.stdout + r.stderr
        ok('live: exit 4 on a hang', r.returncode == 4, (r.returncode, out[-600:]))
        ok('live: hang line', 'linhost: hang: no progress for 3 s' in out, out[-600:])
        nm = subprocess.run(['aarch64-linux-gnu-nm', '-S', elf], capture_output=True, text=True).stdout
        loop = next((l.split() for l in nm.splitlines() if l.endswith(' hang_loop')), None)
        import re
        m = re.search(r'linhost: hang thread \d+ pc 0x([0-9a-f]+)', out)
        inside = bool(loop and m and int(loop[0], 16) <= int(m.group(1), 16) < int(loop[0], 16) + int(loop[1], 16))
        ok('live: the thread line shows the loop', inside, (loop, m and m.group(0)))
        log = os.path.join(d, 'hang.log')
        open(log, 'w').write(out)
        fr = subprocess.run([sys.executable, '-I', os.path.join(P, 'tools', 'report', 'faults.py'), log, '--elf', elf,
                             '--out', os.path.join(d, 'f.md')], capture_output=True, text=True)
        ok('live: faults.py reads it as hang in hang_loop', fr.returncode == 0 and
           '| hang | hang_loop |' in open(os.path.join(d, 'f.md')).read(), fr.stdout + fr.stderr)
        try:
            r = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(d, 'linhost'), '--progress-timeout', '60',
                                os.path.join(d, 'guest.elf')], capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            ok('live: a busy run does not trigger it', False, 'timeout')
            return
        out = r.stdout + r.stderr
        ok('live: a busy run does not trigger it', 'linhost: hang' not in out and 'mtq: done, 0 failed' in out, out[-400:])


def main():
    emu_part()
    if '--no-live' not in sys.argv:
        live_part()
    print('test_progress: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
