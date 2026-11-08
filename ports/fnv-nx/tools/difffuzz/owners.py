#!/usr/bin/env python3
"""owners.py - the D24 per-owner table of a fuzz pass: hard divergences of every shard summary, by cause and owner.

    python3 owners.py <head12> [--artifacts DIR] [--shards 8] [--out FILE.md]

Reads `<head12>-fuzz.json` (fnvnx-fuzz-summary/1) from the branches reports/fz<k> of the artifacts clone
(default /home/user/fnv-nx-build, fetched first), sums `hard.by_cause` over the shards and groups the causes by the
owners of docs/ARCHITECTURE.md D24: S1-rules (harness / oracle causes, bool and sign probes, unclassified), S2-bodies
(names the link binds to nothing, stub data), F1 (call flow, allocator family, extra / early-exit / inlined), F2
(vptr, virtual calls, ctor / dtor order, vtables, dyninit), F3 (memory: uninit, stores, ILP32, null reads, callee-saved),
F4 (floating point). A cause missing from OWNER is listed under `?` so the table never hides one. Prints a Markdown
table (owner, hard, causes) and, with --out, writes it there (an inbox note's section).
"""
import argparse
import collections
import json
import os
import subprocess
import sys

OWNER = {}
for o, cs in {
    'S1-rules': ('oracle-vcall', 'oracle-fault', 'oracle-callee-unloaded', 'unclassified', 'bool-repr', 'signedness',
                 'signedness?', 'shift-count', 'x86-custom-cc', 'msvc-inlined', 'stub-normalized', 'libc-variant',
                 'literal-arg', 'runtime-equiv', 'stack-arg', 'oracle-frame-smash', 'partial-return', 'cpuid-env'),
    'S2-bodies': ('native-undefined', 'after-native-undefined', 'native-stub-data', 'native-zero-stub', 'native-trap'),
    'F1': ('call-flow', 'native-extra-calls', 'native-early-exit', 'native-inlined', 'call-order', 'call-order-unproven', 'call-order-args', 'native-empty-callee', 'oracle-empty-call',
           'native-empty-body', 'native-missing-free', 'nrvo-copy', 'dtor-split-delete', 'nrvo-elision', 'array-new-check', 'falloff?', 'native-stale-result', 'literal-ret',
           'param-count', 'dead-init-call', 'dead-result'),
    'F2': ('vptr-unpaired', 'vptr-differs', 'vptr-store', 'thunk-binding', 'canned-this', 'ctor-this', 'layout-offset',
           'dyninit-no-atexit', 'native-vcall-oracle-direct', 'byvalue-arg-dtor', 'vslot-shift', 'vslot-differs', 'native-vslot-differs'),
    'F3': ('uninit-x86', 'uninit-arm', 'uninit-both', 'stub-outparam','store-width', 'x86-only-store', 'native-only-store',
           'ilp32-pointer', 'ilp32-wrap', 'native-null-read', 'callee-saved', 'split-global', 'literal-store',
           'native-garbage-arg', 'arg-stack-vs-object', 'arg-other-buffer', 'native-address', 'native-wild-private',
           'local-static', 'vptr-bytes', 'vptr-pool', 'ptrdiff-unaligned', 'local-index-range', 'canned-null-deref'),
    'F4': ('nan-bits', 'x87-precision', 'division', 'float', 'float-tolerance', 'float->int', 'x87-trig-inf'),
}.items():
    for c in cs:
        OWNER[c] = o
ORDER = ['S1-rules', 'S2-bodies', 'F1', 'F2', 'F3', 'F4', '?']


def load(art, head, shards, fetch=True):
    out = []
    for k in range(1, shards + 1):
        ref = 'refs/remotes/origin/reports/fz%d' % k
        if fetch:
            subprocess.run(['git', '-C', art, 'fetch', '-q', '--depth', '1', 'origin',
                            '+refs/heads/reports/fz%d:%s' % (k, ref)], capture_output=True)
        r = subprocess.run(['git', '-C', art, 'show', '%s:%s-fuzz.json' % (ref, head)], capture_output=True, text=True)
        out.append(json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None)
    return out


def table(summaries):
    by = collections.Counter()
    missing = [i + 1 for i, s in enumerate(summaries) if s is None]
    for s in summaries:
        if s:
            by.update((s.get('hard') or {}).get('by_cause') or {})
    groups = collections.defaultdict(collections.Counter)
    for c, n in by.items():
        groups[OWNER.get(c, '?')][c] += n
    total = sum(by.values())
    lines = ['| owner | hard | causes |', '|---|---:|---|']
    for o in ORDER:
        g = groups.get(o)
        if not g:
            continue
        lines.append('| %s | %d | %s |' % (o, sum(g.values()), ', '.join('%s %d' % kv for kv in g.most_common())))
    lines.append('| ALL | %d | %d shards%s |' % (total, len(summaries) - len(missing),
                                                  (', missing ' + ' '.join(map(str, missing))) if missing else ''))
    return '\n'.join(lines) + '\n', groups, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('head')
    ap.add_argument('--artifacts', default='/home/user/fnv-nx-build')
    ap.add_argument('--shards', type=int, default=8)
    ap.add_argument('--out')
    ap.add_argument('--no-fetch', action='store_true')
    a = ap.parse_args()
    t, _, _ = table(load(a.artifacts, a.head[:12], a.shards, not a.no_fetch))
    sys.stdout.write(t)
    if a.out:
        open(a.out, 'w').write(t)


if __name__ == '__main__':
    main()
