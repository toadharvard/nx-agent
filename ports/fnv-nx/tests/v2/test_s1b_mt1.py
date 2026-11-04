#!/usr/bin/env python3
"""V2 check of S1b 994d52c / 3e41843 (patch class MT1, patches/mt1_*.s1b): "every hunk behind FNVNX_MT1_ON, so with
the gate off the code is the decomp's". The rule is applied to the decomp's own sources (FNV_DECOMP, read only) and
each hunk's result is read: a plain hunk must become `if (FNVNX_MT1_ON) { new } else { old }` with `old` the hunk's
original text; a `guarded` / `wrap` hunk must mention FNVNX_MT1_ON in its replacement so that the gate's 0 gives the
original condition / statement (checked: `!FNVNX_MT1_ON ||` in a condition, or `if (FNVNX_MT1_ON)` around a block).

    python3 tests/v2/test_s1b_mt1.py      (skip without ext/fnv_decomp)
"""
import importlib.util
import os
import re
import sys
from collections import Counter

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))


def hunks(path):
    text = open(path).read()
    for site in re.split(r'^(?=@@ MT1 )', text, flags=re.M)[1:]:
        head = site.split('\n', 1)[0].split()
        for m in re.finditer(r'^<<<\n(.*?)\n=== ?(\w*)\n(.*?)\n>>>', site, re.S | re.M):
            yield head[2], head[3], m.group(1), m.group(2), m.group(3)


def main():
    dec = os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp'))
    if not os.path.isdir(os.path.join(dec, 'src')):
        print('skip: no decomp checkout')
        return 0
    sys.path.insert(0, os.path.join(PORT, 'tools'))
    spec = importlib.util.spec_from_file_location('s1b', os.path.join(PORT, 'tools', 'rules', 's1b_patches.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    fails = n = 0
    outs = {}
    for f in sorted(os.listdir(os.path.join(PORT, 'patches'))):
        if not (f.startswith('mt1_') and f.endswith('.s1b')):
            continue
        for src, addr, old, kind, new in hunks(os.path.join(PORT, 'patches', f)):
            n += 1
            p = os.path.join(dec, src)
            if src not in outs:
                outs[src] = m.r_mt1(p, open(p).read(), Counter())
            out = outs[src]
            if kind == '':
                ok = ('if (FNVNX_MT1_ON) { /* fnv-nx MT1 */\n%s\n} else {\n%s\n}' % (new, old)) in out
            else:
                ok = 'FNVNX_MT1_ON' in new and new in out and (
                    '!FNVNX_MT1_ON ||' in new or re.search(r'if \(FNVNX_MT1_ON\)', new) is not None)
            print('%s %s %s %s hunk%s' % ('ok' if ok else 'FAIL', f, src.split('/')[-1], addr,
                                          ' (%s)' % kind if kind else ' (plain: if/else around the original)'))
            fails += not ok
    print('ok (%d hunks)' % n if not fails and n else 'FAILED')
    return 1 if fails or not n else 0


if __name__ == '__main__':
    sys.exit(main())
