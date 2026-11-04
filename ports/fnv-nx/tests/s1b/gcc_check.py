#!/usr/bin/env python3
"""gcc_check.py - the patched units still compile with GCC (census flags, stream S1b-patches).

    gcc_check.py DECOMP BASE_TREE WORK [--only SRC,...] [--sse2neon DIR] [--jobs N]

BASE_TREE is a tree prepared by tools/prepare.py WITHOUT the S1b patches (or with them: the files with patches are
prepared again here). Every source file that has patches is run through all prepare rules (core, then the stream
modules, S1b included, in prepare's order) into WORK/tree (a symlink copy of BASE_TREE), then both versions are
compiled with the census flags (-fsyntax-only, tools/census.sh). Reports per file: patches applied / missed, GCC
errors before and after, and any error on a line a patch touched.
"""
import argparse
import difflib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
sys.path.insert(0, str(PORT / 'tools'))
import prepare  # noqa: E402

FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-fms-extensions', '-fshort-wchar', '-fpermissive',
         '-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks',
         '-ffp-contract=off', '-w', '-fmax-errors=200']


def prepared(path, rules):
    text = open(path, encoding='utf-8', errors='surrogateescape').read()
    stats = Counter()
    for r in rules:
        text = r(path, text, stats)
    return text, stats


def compile_errors(tree, src, sse2neon):
    cxx = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
    args = [cxx] + FLAGS + ['-include', str(PORT / 'shim/include/fnvnx_compat.h'), '-I' + str(PORT / 'shim/include'),
                            '-I' + sse2neon, '-I' + str(tree / 'include'), src]
    r = subprocess.run(args, cwd=tree, capture_output=True, text=True, errors='replace', timeout=900)
    return [l for l in r.stderr.splitlines() if ': error:' in l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('decomp'); ap.add_argument('base'); ap.add_argument('work')
    ap.add_argument('--only'); ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--sse2neon', default=os.environ.get('SSE2NEON', str(PORT / 'ext/sse2neon')))
    a = ap.parse_args()
    decomp, base, work = Path(a.decomp).resolve(), Path(a.base).resolve(), Path(a.work).resolve()
    sys.path.insert(0, str(PORT / 'tools' / 'rules'))
    import s1b_patches
    files = sorted({s.path for s in s1b_patches.load_sites()})
    if a.only:
        files = [f for f in files if f in a.only.split(',')]
    tree = work / 'tree'
    if not tree.exists():
        tree.mkdir(parents=True)
        for d in ('src', 'include', 'build'):
            subprocess.run(['cp', '-rs', str(base / d), str(tree / d)], check=True)
    rules = prepare.RULES + prepare.load_stream_rules()
    s1b = {'r_xinc1', 'r_ftol1', 'r_dbug1', 'r_ret1'}
    without = [r for r in rules if r.__name__ not in s1b]
    for rel in files:
        text, stats = prepared(str(decomp / rel), rules)
        dst = tree / rel
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        dst.write_text(text, encoding='utf-8', errors='surrogateescape')
        ref, _ = prepared(str(decomp / rel), without)
        sm = difflib.SequenceMatcher(None, ref.split('\n'), text.split('\n'), autojunk=False)
        changed = {j + 1 for op, _, _, j1, j2 in sm.get_opcodes() if op != 'equal' for j in range(j1, j2)}
        print('%-90s applied %s missed %s' % (rel, sum(v for k, v in stats.items() if k in ('XINC1', 'FTOL1', 'DBUG1', 'RET1')),
              sum(v for k, v in stats.items() if k.endswith('-MISS'))), flush=True)
        (work / (rel.replace('/', '_') + '.lines')).write_text(' '.join(map(str, sorted(changed))))

    def one(rel):
        before = compile_errors(base, rel, a.sse2neon)
        after = compile_errors(tree, rel, a.sse2neon)
        lines = set(map(int, (work / (rel.replace('/', '_') + '.lines')).read_text().split() or []))
        bare = lambda e: re.sub(r'^([^:]+):\d+:\d+:', r'\1:', e)      # patches may shift lines
        new = [e for e in after if bare(e) not in {bare(b) for b in before}]
        at = [e for e in after if (lambda m: m and int(m.group(1)) in lines)(re.match(r'^[^:]+:(\d+):', e))]
        return rel, before, after, new, at

    tot = Counter()
    with ThreadPoolExecutor(a.jobs) as pool:
        for rel, before, after, new, at in pool.map(one, files):
            tot['before'] += len(before); tot['after'] += len(after); tot['new'] += len(new)
            print('%-90s errors %d -> %d, new %d, on patched lines %d' % (rel, len(before), len(after), len(new), len(at)))
            for e in (at + [x for x in new if x not in at])[:12]:
                print('    ' + e[:300])
    print(dict(tot))


if __name__ == '__main__':
    main()
