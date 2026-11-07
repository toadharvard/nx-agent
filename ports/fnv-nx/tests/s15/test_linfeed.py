#!/usr/bin/env python3
"""linhost --feed (tests/s15/mtqemu/linhost.c) with tools/perf/linfeed.py: an S18 plan's keys reach the guest's
input_poll as FNVNX_EV_KEY, the steps run (press, until file - case-insensitive at any depth under --root -, until
a hook milestone, until a milestone linhost cannot see, mark), and the run ends with "scenario complete" (exit 0)
and the profile written. Skips without aarch64-linux-gnu-* and qemu-aarch64.

    python3 -I tests/s15/test_linfeed.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.normpath(os.path.join(HERE, '..', '..'))
fails = []


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail)[-600:] if detail and not cond else ''))
    if not cond:
        fails.append(name)


def main():
    for t in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++', 'aarch64-linux-gnu-ld', 'qemu-aarch64'):
        if not shutil.which(t):
            print('skip: %s not found' % t)
            return 0
    with tempfile.TemporaryDirectory() as d:
        b = subprocess.run(['bash', os.path.join(HERE, 'mtqemu', 'build.sh'), d], capture_output=True, text=True)
        if b.returncode:
            ok('build', False, b.stdout + b.stderr)
            return 1
        elf = os.path.join(d, 'guest.elf')
        root = os.path.join(d, 'sd')
        os.makedirs(os.path.join(root, 'fnvnx', 'MY GAMES', 'saves'))
        open(os.path.join(root, 'fnvnx', 'MY GAMES', 'saves', 'S18_A.FOS'), 'w').write('x')
        miles = os.path.join(d, 'miles.txt')
        open(miles, 'w').write('frame-mark call mtq_frame_mark\nini-read data ini-read\n')
        plan = {'format': 'fnvnx-scenario-plan/1', 'steps': [
            {'op': 'press', 'key': 'a', 'gap': 0.2},
            {'op': 'until', 'file': 'Saves/s18_a.fos', 'timeout': 20},
            {'op': 'until', 'milestone': 'ini-read', 'timeout': 0.2},
            {'op': 'press', 'key': 'no-such-key', 'gap': 0.1},
            {'op': 'until', 'milestone': 'frame-mark', 'timeout': 200},
            {'op': 'mark', 'name': 'done'}]}
        pj, feed = os.path.join(d, 'plan.json'), os.path.join(d, 'feed.txt')
        json.dump(plan, open(pj, 'w'))
        f = subprocess.run([sys.executable, '-I', os.path.join(P, 'tools', 'perf', 'linfeed.py'), pj, '--elf', elf,
                            '--milestones', miles, '--out', feed], capture_output=True, text=True)
        ok('linfeed', f.returncode == 0, f.stderr)
        text = open(feed).read() if os.path.isfile(feed) else ''
        ok('feed: hook for the call milestone', '\nhook 0x' in text and ' frame-mark\n' in text, text)
        ok('feed: a=usage 4 down then up', 'press 200000000 4:1 4:0' in text, text)
        ok('feed: data milestone has no hook', 'until-none 200000000 ini-read' in text, text)
        ok('feed: unknown key skipped and listed', 'no-such-key' in f.stderr, f.stderr)
        prof = os.path.join(d, 'prof.txt')
        try:
            r = subprocess.run(['qemu-aarch64', '-R', '0xC0000000', os.path.join(d, 'linhost'), '--root', root,
                                '--feed', feed, '--profile', prof, '--frame', 'mtq_frame_mark', elf],
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            ok('run ends', False, 'timeout')
            return 1
        out = r.stdout + r.stderr
        ok('exit 0 at the end of the plan', r.returncode == 0, out[-800:])
        ok('the key reached input_poll', 'mtq: feed ok keys 2 downs 1 first usage 4' in out,
           [l for l in out.splitlines() if 'feed' in l])
        ok('until file (case-insensitive, any depth)', 'scenario until ok Saves/s18_a.fos' in out, out[-800:])
        ok('until unsupported milestone: its timeout', 'scenario until unsupported (timeout) ini-read' in out)
        ok('until hook milestone', 'scenario until ok frame-mark' in out)
        ok('mark and complete', 'scenario mark done' in out and 'scenario complete' in out)
        ok('profile written with frame marks', os.path.isfile(prof) and any(l.startswith('f ') for l in open(prof)))
    print('test_linfeed: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
