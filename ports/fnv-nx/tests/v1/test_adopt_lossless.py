#!/usr/bin/env python3
"""test_adopt_lossless.py - V1 (D14) against the orchestrator's stream "adopt" merges (`fnv-nx fnv-<stream>: adopt
the stream work at <commit> (3-way: ...)`): no line of the stream's work is lost.

    python3 -I tests/v1/test_adopt_lossless.py [<adopt commit>:<stream commit> ...]
    (default: 352c315:7bfec755 (fnv-s5), 6a5c605:ce6d1272 (fnv-s16); fetch the stream branches first)

For every file the stream commit changed relative to its merge base with the adopt commit's parent, the adopt
commit's version must equal the stream's, or (a file merged line-wise) contain every line the stream added. One
line per adopt; exit 1 when a stream line is missing, 2 when a commit is not available locally.
"""
import subprocess
import sys

DEFAULT = ['352c315:7bfec755', '6a5c605:ce6d1272']


def git(*a, check=True):
    p = subprocess.run(['git'] + list(a), capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def show(rev, path):
    p = subprocess.run(['git', 'show', '%s:%s' % (rev, path)], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def main(argv):
    bad = 0
    for pair in argv or DEFAULT:
        adopt, stream = pair.split(':')
        try:
            base = git('merge-base', adopt + '^', stream).strip()
        except RuntimeError as e:
            print('%s: missing commit (%s); fetch the stream branch' % (pair, e))
            return 2
        files = [f for f in git('diff', '--name-only', base, stream).splitlines() if f]
        lost = []
        for f in files:
            want, got = show(stream, f), show(adopt, f)
            if want == got:
                continue
            if want is None:                     # the stream deleted it
                if got is not None:
                    lost.append('%s (deleted by the stream, present after the adopt)' % f)
                continue
            if got is None:
                lost.append('%s (missing after the adopt)' % f)
                continue
            added = [l for l in git('diff', '-U0', base, stream, '--', f).splitlines()
                     if l.startswith('+') and not l.startswith('+++')]
            have = set(got.splitlines())
            miss = [l[1:] for l in added if l[1:] not in have]
            if miss:
                lost.append('%s (%d stream lines missing, e.g. %r)' % (f, len(miss), miss[0][:60]))
        ok = not lost
        bad |= not ok
        print('%s: %s - %d stream files%s' % (pair, 'ok' if ok else 'DEFECT', len(files),
                                              '' if ok else '; ' + '; '.join(lost[:5])))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
