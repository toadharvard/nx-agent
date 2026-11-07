#!/usr/bin/env python3
"""The frame profiler (tools/perf/frames.py, linhost --profile; tools/perf/FRAMES.md).

    python3 -I tests/s15/test_frames.py [--no-live]

1. synthetic profiles: frames from the frame thread's marks (warm-up skipped), per-thread CPU and busy %, the
   critical-path thread, the --scale device estimate and the budget count, a line cut by a kill, no samples (exit 3);
2. live (skips without aarch64-linux-gnu-* and qemu-aarch64): the mtqemu guest under linhost --profile --frame
   mtq_frame_mark. Its frame loop has the worker do twice main's work, so the worker must be the critical path in most
   frames, ~100 % busy against main's ~50 %, with mtq_spin its top function.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, '..', '..', 'tools', 'perf', 'frames.py')
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + detail if detail and not cond else ''))
    if not cond:
        fails.append(name)


def frames(d, prof_text, *args):
    p = os.path.join(d, 'prof.txt')
    open(p, 'w').write(prof_text)
    out = os.path.join(d, 'f.md')
    r = subprocess.run([sys.executable, '-I', FRAMES, p, '--out', out] + list(args), capture_output=True, text=True)
    j = json.load(open(out[:-3] + '.json')) if os.path.isfile(out[:-3] + '.json') else None
    return r, j


def synthetic(d):
    # 1 kHz; main (tid 10) marks a frame every 20 ms; per frame the worker (tid 11) has 15 samples, main 5
    lines = ['p 1000 0', 't 10 %d 1' % ((1 << 64) - 1), 't 11 0 6']
    for k in range(6):
        t0 = k * 20000000
        lines.append('f 10 %d %d' % (t0, k + 1))
        if k == 5:
            break
        for i in range(15):
            lines.append('s 11 %d 0x2000a000' % (t0 + 1000000 * i + 500))
        for i in range(5):
            lines.append('s 10 %d 0x20009000' % (t0 + 1000000 * i + 700))
    text = '\n'.join(lines) + '\ns 11 99'   # the last line cut by a kill
    r, j = frames(d, text)
    ok('synthetic: runs', r.returncode == 0 and j is not None, r.stderr[-300:])
    if not j:
        return
    ok('synthetic: 5 frames, first skipped', j['frames'] == 4, str(j['frames']))
    ok('synthetic: wall 20 ms', abs(j['wall_ms']['median'] - 20.0) < 1e-6)
    ok('synthetic: worker is critical', j['critical_tid'] == 11 and j['critical_thread'].startswith('slot 0'))
    w, m = j['threads']['11'], j['threads']['10']
    ok('synthetic: busy 75 / 25 %', abs(w['busy_mean'] - 75) < 1e-6 and abs(m['busy_mean'] - 25) < 1e-6,
       '%s %s' % (w['busy_mean'], m['busy_mean']))
    ok('synthetic: main named', m['name'] == 'main')
    ok('synthetic: top function', j['top'][0]['func'] == '0x2000a000')
    r, j = frames(d, text, '--scale', '0.5')
    ok('synthetic: device estimate', j and abs(j['device_ms']['median'] - 7.5) < 1e-6 and j['device_ms']['over_budget'] == 0)
    r, j = frames(d, text, '--scale', '0.5', '--budget-ms', '5')
    ok('synthetic: budget count', j and j['device_ms']['over_budget'] == 4)
    r, j = frames(d, 'p 1000 0\nt 10 0 1\n')
    ok('no samples: exit 3', r.returncode == 3)


def live(d):
    for t in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-ld', 'qemu-aarch64'):
        if not shutil.which(t):
            print('skip live: %s not found' % t)
            return
    b = subprocess.run(['bash', os.path.join(HERE, 'mtqemu', 'build.sh'), d], capture_output=True, text=True)
    if b.returncode:
        ok('live: build', False, (b.stdout + b.stderr)[-400:])
        return
    prof, log = os.path.join(d, 'profile.txt'), os.path.join(d, 'run.log')
    try:
        r = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(d, 'linhost'), '--profile', prof,
                            '--frame', 'mtq_frame_mark', os.path.join(d, 'guest.elf')],
                           capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        ok('live: run ends', False, 'timeout')
        return
    open(log, 'w').write(r.stdout + r.stderr)
    ok('live: frame function patched', '--frame: mtq_frame_mark at' in r.stderr)
    # (the detach case's 120 threads exceed qemu-user's 32 timers: those report timer_create failures, by design)
    out = os.path.join(d, 'f.md')
    f = subprocess.run([sys.executable, '-I', FRAMES, prof, '--elf', os.path.join(d, 'guest.elf'), '--log', log,
                        '--out', out], capture_output=True, text=True)
    if f.returncode:
        ok('live: frames.py', False, f.stderr[-300:])
        return
    j = json.load(open(out[:-3] + '.json'))
    nm = subprocess.run(['aarch64-linux-gnu-nm', os.path.join(d, 'guest.elf')], capture_output=True, text=True).stdout
    spin = next(('0x%08x' % int(l.split()[0], 16) for l in nm.splitlines() if l.endswith(' mtq_spin')), None)
    ok('live: 30 frames', j['frames'] == 30 - 1, str(j['frames']))   # 31 marks -> 30 frames, warm-up skipped
    crit = j['threads'].get(str(j['critical_tid']), {})
    main = next((v for v in j['threads'].values() if v['name'] == 'main'), {})
    ok('live: the worker is the critical path', j['critical_thread'] != 'main' and
       crit.get('critical_frames', 0) >= 0.7 * j['frames'], '%s %s' % (j['critical_thread'], crit))
    # the loop: main 1 unit, the worker 2 units a frame -> worker ~100 % busy, main ~50 % (overruns weighted)
    ok('live: worker ~100 % busy, main ~50 %', crit.get('busy_mean', 0) > 75 and 30 < main.get('busy_mean', 0) < 70,
       '%s vs %s' % (crit.get('busy_mean'), main.get('busy_mean')))
    md = open(out).read()
    sect = md.split('## top functions of the critical thread', 1)[-1]
    ok('live: mtq_spin tops the critical thread', spin is not None and spin in sect.split('\n')[4], spin or 'no symbol')
    print('live: %s frames, wall median %.1f ms, critical %s busy %.0f%%, main busy %.0f%%, %d samples' %
          (j['frames'], j['wall_ms']['median'] or 0, j['critical_thread'], crit.get('busy_mean', 0),
           main.get('busy_mean', 0), j['samples']))


def main():
    with tempfile.TemporaryDirectory() as d:
        synthetic(d)
    if '--no-live' not in sys.argv:
        with tempfile.TemporaryDirectory() as d:
            live(d)
    print('test_frames: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
