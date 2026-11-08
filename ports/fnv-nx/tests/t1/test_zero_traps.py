#!/usr/bin/env python3
"""T1: zero_targets.py's D24 trap stubs: a full report whose guest did not link measured nothing (the next older
head's count is used, never 0); a guest that linked without an undefined-symbols table counts 0."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZT = os.path.join(HERE, '..', '..', 'tools', 'report', 'zero_targets.py')
fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


def full(guest, table):
    md = '# full build x\n\n| stage | status | run | seconds | summary |\n|---|---|---|---:|---|\n'
    md += '| link | %s | ran | 1 s | x |\n| guest | %s | ran | 1 s | x |\n' % ('ok' if guest != 'skipped' else 'fail', guest)
    if table:
        md += '\n| undefined symbols | category | owner |\n|---|---|---|\n| 5 | a | b |\n| 7 | c | d |\n'
    return md


def row(t, heads, head):
    out = os.path.join(t, 'z.md')
    open(os.path.join(t, 'heads.txt'), 'w').write(''.join(h + '\n' for h in heads))
    subprocess.run([sys.executable, '-I', ZT, os.path.join(t, 'r'), '--heads', os.path.join(t, 'heads.txt'), '--head',
                    head, '--out', out], capture_output=True, text=True, check=True)
    return next((l for l in open(out) if '| trap stubs |' in l), '')


def main():
    with tempfile.TemporaryDirectory() as t:
        a, b, c = 'aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc'   # c newest
        os.makedirs(os.path.join(t, 'r', 'full1'))
        w = lambda h, s: open(os.path.join(t, 'r', 'full1', '%s-full.md' % h), 'w').write(s)
        w(a, full('ok', True))
        w(b, full('skipped', False))
        r = row(t, [c, b, a], c)
        ok('guest did not link at b: the count of a (12), not 0', '| 12 |' in r and a in r and '| 0 |' not in r)
        w(c, full('warn', False))
        r = row(t, [c, b, a], c)
        ok('guest linked at c without a table: 0, against a (12)', '| 0 |' in r and c in r and '-12' in r)
    print('test_zero_traps: all ok' if not fails else 'test_zero_traps: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
