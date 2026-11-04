#!/usr/bin/env python3
"""tso_scan.py <decomp root> [--tsv out.tsv] - where the decomp releases a spin lock with a plain store.

x86 is TSO: a plain store is a release, so the engine's RecursiveSpinLock (owner thread id at +0, recursion count at
+4; Lock 0x0040fbf0 through InterlockedCompareExchange, Unlock 0x0040fba0) needs no barrier there. On AArch64 a plain
store is not a release (docs/PERF.md 6.5). tools/link/overrides/spinlock_0040fba0.c fixes the out-of-line Unlock;
this scan lists the copies units wrote inline, which the override does not reach:

  * `release`: a decrement of a recursion count, then within 3 lines a store of 0 to an owner field
    (m_uiOwningThreadID / OwningThread / pCount / owner);
  * `call`: references to 0x0040fba0 by name (FUN_0040fba0), which the override covers.

Prints counts per kind and per unit (decomp paths and line numbers only, never code text).
"""
import os
import re
import sys

DEC = re.compile(r'(--\s*DAT_[0-9a-f]+\b|--\s*[\w.\->\[\]]*(RecursionCount|count)\b|[\w.\->]*(RecursionCount|count)\s*(=\s*[\w.\->]*'
                 r'(RecursionCount|count)\s*-\s*1|--)|!\s*[\w.\->]*RecursionCount)')
REL = re.compile(r'(m_uiOwningThreadID|OwningThreadID|\bpCount|\bowner)\s*=\s*0\s*;')
CALL = re.compile(r'\bFUN_0040fba0\b')


def scan(root):
    rows = []
    for d, _, files in os.walk(os.path.join(root, 'src')):
        for fn in files:
            if not fn.endswith(('.cpp', '.h', '.hpp', '.inl')):
                continue
            p = os.path.join(d, fn)
            rel = os.path.relpath(p, root)
            try:
                lines = open(p, encoding='utf-8', errors='replace').read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if REL.search(line) and any(DEC.search(lines[j]) for j in range(max(0, i - 3), i + 1)):
                    rows.append(('release', rel, i + 1))
                if CALL.search(line) and '@fnv' not in line and not line.lstrip().startswith('//'):
                    rows.append(('call', rel, i + 1))
    return rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rows = scan(sys.argv[1])
    kinds = {}
    for k, f, _ in rows:
        kinds.setdefault(k, set()).add(f)
    for k in ('release', 'call'):
        n = sum(1 for r in rows if r[0] == k)
        print('%-8s %4d sites in %3d files' % (k, n, len(kinds.get(k, ()))))
    print('inline releases by file:')
    per = {}
    for k, f, _ in rows:
        if k == 'release':
            per[f] = per.get(f, 0) + 1
    for f, n in sorted(per.items(), key=lambda x: (-x[1], x[0])):
        print('  %3d  %s' % (n, f))
    if '--tsv' in sys.argv:
        with open(sys.argv[sys.argv.index('--tsv') + 1], 'w') as o:
            o.write('#kind\tfile\tline\n')
            for r in rows:
                o.write('%s\t%s\t%d\n' % r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
