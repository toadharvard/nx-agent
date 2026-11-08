#!/usr/bin/env python3
"""plat_wine.py - the original's failed file opens, from a Wine run traced by tools/lockstep/fnvtrace.dll: the baseline
tools/perf/plat.py --original compares the port's failed opens (D25 #4) with.

    python3 -I tools/perf/plat_wine.py <wine.tsv>... --out original_opens.json

The trace list of the Wine run (tools/scenario/wine_scenario.sh's <trace list>, tools/lockstep/wine_trace.sh) needs
the line `kernel32!CreateFileA` (fnvtrace resolves dll!Export entries) and FNVTRACE_DEREF=256, so each call's first
stack word (lpFileName) is dumped. A failed open is a CreateFileA call whose return address lies in the exe image
(0x00400000..0x02100000: the game's own calls, its static CRT's fopen/_open included; Wine's internal ones are left
out) and that returned INVALID_HANDLE_VALUE. Each is keyed like platform/win32/platstat.cpp keys the port's: the
path class (bsa, esm, save, ini, other) and the FNV-1a hash of the path as the game passed it. No path text is
written. Writes fnvnx-plat-original/1. Exit 0; 3 when no trace has a CreateFileA call.
"""
import argparse
import json
import os
import sys

IMAGE = (0x00400000, 0x02100000)


def fnv1a(s):
    h = 2166136261
    for c in s:
        h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return '%08x' % h


def path_class(win):
    """platstat.cpp path_class(): lower case, '/' -> '\\', then by extension / the Saves folder"""
    low = ''.join(chr(c + 32) if 65 <= c <= 90 else ('\\' if c == 47 else chr(c)) for c in win)
    dot = low[low.rfind('.'):] if '.' in low else ''
    if dot == '.bsa':
        return 'bsa'
    if dot in ('.esm', '.esp'):
        return 'esm'
    if dot in ('.fos', '.bak', '.nvse') or '\\saves\\' in low:
        return 'save'
    if dot == '.ini':
        return 'ini'
    return 'other'


def parse(path):
    """[(path bytes, failed)] of the game's CreateFileA calls in one trace; seen: any CreateFileA call at all"""
    calls, out, seen = {}, [], False
    for line in open(path, errors='replace'):
        f = line.rstrip('\n').split('\t')
        if f[0] == 'call' and len(f) >= 16 and f[4] == 'CreateFileA':
            seen = True
            try:
                ret = int(f[-1], 16)
            except ValueError:
                continue
            if IMAGE[0] <= ret < IMAGE[1]:
                calls[f[1]] = None
        elif f[0] == 'arg' and len(f) >= 4 and f[1] in calls and f[2] == 's0' and calls[f[1]] is None:
            b = bytes.fromhex(f[3])
            calls[f[1]] = b.split(b'\0', 1)[0]
        elif f[0] == 'ret' and len(f) >= 6 and f[4] == 'CreateFileA' and f[1] in calls:
            name = calls.pop(f[1])
            if name:
                out.append((name, int(f[5], 16) & 0xFFFFFFFF == 0xFFFFFFFF))
    return out, seen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('traces', nargs='+')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    fails, total, seen = {}, 0, False
    for p in a.traces:
        evs, s = parse(p)
        seen |= s
        total += len(evs)
        for name, failed in evs:
            if failed:
                k = (path_class(name), fnv1a(name))
                fails[k] = fails.get(k, 0) + 1
    head = {'schema': 'fnvnx-plat-original/1', 'opens': total,
            'failed_opens': [{'class': c, 'hash': h, 'count': n} for (c, h), n in sorted(fails.items())],
            'traces': [os.path.basename(p) for p in a.traces], 'traced': seen}
    json.dump(head, open(a.out, 'w'), indent=1)
    print('plat_wine: %d opens by the game, %d distinct failed' % (total, len(fails)))
    return 0 if seen else 3


if __name__ == '__main__':
    sys.exit(main())
