#!/usr/bin/env python3
"""boot_determinism.py - two runs of the same emulated boot must agree (T1, orchestrator: S5's timer slices of
2d1afaeb..6da559aa made boots nondeterministic; 620572bc preempts at host calls instead).

    boot_determinism.py <boot dir A> <boot dir B> [--tag stub] [--trace-a F --trace-b F] [--json out]

Each boot dir holds farm_boot.sh's boot_summary_<tag>.json. Two boots that each ended by themselves must have the
same summary (timing fields left out: any key naming seconds, time, wall, elapsed or ms) and, when given, the same
function trace. A boot stopped by the wall-clock limit or a signal ends at a point that depends on the machine's
speed, so then the shorter run must be a prefix of the longer: its function trace a prefix of the other's, every
milestone it reached reached by the other. Prints one line; exit 0 when they agree, 1 when they differ (the first
difference named), 2 on a missing summary."""
import argparse
import json
import os
import re
import sys

TIMING = re.compile(r'(second|time|wall|elapsed|_ms$|^ms$|_s$)', re.I)
DUR = re.compile(r'\b\d+(\.\d+)?\s*(ms|s|sec|secs|seconds|min)\b')


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if not TIMING.search(k)}
    if isinstance(o, list):
        return [strip(v) for v in o]
    if isinstance(o, str):   # durations inside messages ("running after 303s", "in 37.2 s")
        return DUR.sub('<t>', o)
    return o


def cut_short(summary):
    st = summary.get('stop') or {}
    text = ' '.join([str(st.get('reason') or '')] + [str(m) for m in st.get('messages') or []])
    return bool(re.search(r'wall-clock|SIGTERM|SIGINT|timeout|timed out', text, re.I))


def lines(p):
    """the trace's lines, timings out: a `ret` line's last column is --trace-fn's elapsed wall time in ns (S5:
    s5boot/9b1f9490e4e6-determinism differed only there, 164352 vs 150121 at line 4; the boots were the same)"""
    if not (p and os.path.isfile(p)):
        return None
    out = []
    for l in open(p, errors='replace'):
        if l.startswith('ret\t'):
            l = l.rstrip('\n').rsplit('\t', 1)[0] + '\n'
        out.append(l)
    return out


def first_diff(a, b):
    """index of the first differing element of two lists, or None when one is a prefix of the other"""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None


def diff_path(a, b, path=''):
    """the first path where two JSON values differ"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return path + '/' + k
            d = diff_path(a[k], b[k], path + '/' + k)
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff_path(x, y, '%s[%d]' % (path, i))
            if d:
                return d
        return None if len(a) == len(b) else '%s (length %d != %d)' % (path or '/', len(a), len(b))
    return None if a == b else (path or '/')


def check(da, db, tag='stub', ta=None, tb=None):
    """-> (agree: bool, message)"""
    sa = os.path.join(da, 'boot_summary_%s.json' % tag)
    sb = os.path.join(db, 'boot_summary_%s.json' % tag)
    if not (os.path.isfile(sa) and os.path.isfile(sb)):
        raise FileNotFoundError('no %s' % (sa if not os.path.isfile(sa) else sb))
    A, B = json.load(open(sa)), json.load(open(sb))
    la, lb = lines(ta), lines(tb)
    if not cut_short(A) and not cut_short(B):
        d = diff_path(strip(A), strip(B))
        if d:
            return False, 'the two boots ended by themselves with different summaries (first at %s)' % d
        if la is not None and lb is not None and la != lb:
            i = first_diff(la, lb)
            return False, ('the function traces differ at line %d' % (i + 1)) if i is not None else \
                'the function traces differ in length (%d, %d lines)' % (len(la), len(lb))
        return True, 'identical: both boots ended by themselves with the same summary%s' % (
            ' and the same %d-line function trace' % len(la) if la is not None and lb is not None else '')
    # at least one was cut short by the clock: the shorter must be a prefix of the longer
    if la is not None and lb is not None:
        i = first_diff(la, lb)
        if i is not None:
            return False, 'the function traces differ at line %d (a boot cut short must be a prefix of the other)' % (i + 1)
    ra = {m.get('name') for m in A.get('milestones') or [] if m.get('reached')}
    rb = {m.get('name') for m in B.get('milestones') or [] if m.get('reached')}
    short, long_ = (ra, rb) if len(ra) <= len(rb) else (rb, ra)
    if not short <= long_:
        return False, 'milestones reached by the shorter boot and not by the longer: %s' % ', '.join(sorted(short - long_))
    return True, 'consistent: a boot was cut short by the clock; the shorter is a prefix of the longer%s' % (
        ' (function traces %d / %d lines)' % (len(la), len(lb)) if la is not None and lb is not None else '')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('a'); ap.add_argument('b'); ap.add_argument('--tag', default='stub')
    ap.add_argument('--trace-a'); ap.add_argument('--trace-b'); ap.add_argument('--json')
    a = ap.parse_args(argv)
    try:
        ok, msg = check(a.a, a.b, a.tag, a.trace_a, a.trace_b)
    except FileNotFoundError as ex:
        print('boot_determinism: %s' % ex)
        return 2
    print(('PASS: ' if ok else 'FAIL: ') + msg)
    if a.json:
        json.dump({'schema': 'fnvnx-boot-determinism/1', 'agree': ok, 'message': msg}, open(a.json, 'w'), indent=1)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
