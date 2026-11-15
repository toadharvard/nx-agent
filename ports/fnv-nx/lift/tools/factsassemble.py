#!/usr/bin/env python3
"""Facts stage FACTS: the one facts directory of the lifted image set, assembled from the per-image stages.
    factsassemble.py --table MODULES_TSV --funcs funcs.tsv --exe DIR --module NAME=DIR [--module ...] --out FACTS
RULE (image set): the images are the exe and every row of the loader's module table (modules.tsv, one row per module file of the declared file set, by the loader's own rule). The module stages given
here must be exactly the rows of the table: a row with no stage, a stage that is no row, or a stage given twice is an error naming all of them. Layout (the layout the facts readers take): the exe's files at the top,
funcs.tsv at the top, one directory <module name without .dll, lower case> per row with the module stage's files. DIR of a module stage holds <name>/ with the files modlift wrote; the exe stage DIR holds the files.
Nothing is chosen or computed here: files are copied byte for byte."""
import argparse
import os
import shutil
import sys


def rows(path):
    out = []
    for l in open(path):
        if l.strip() and l[0] != '#': out.append(l.split('\t')[0])
    return out


def main(table, funcs, exe, mods, out):
    want = sorted(os.path.splitext(n)[0].lower() for n in rows(table)); got = sorted(n.lower() for n, _ in mods)
    bad = ['table row without a stage: %s' % n for n in want if n not in got] + ['stage that is no table row: %s' % n for n in got if n not in want] + ['stage given twice: %s' % n for n in sorted(set(g for g in got if got.count(g) > 1))]
    if bad: sys.exit('factsassemble: the module stages are not the rows of the module table:\n  ' + '\n  '.join(bad))
    os.makedirs(out)
    for f in sorted(os.listdir(exe)): shutil.copy(os.path.join(exe, f), os.path.join(out, f))
    shutil.copy(funcs, os.path.join(out, 'funcs.tsv'))
    for n, d in sorted(mods, key=lambda x: x[0].lower()):
        src = os.path.join(d, n.lower())
        if not os.path.isdir(src): sys.exit('factsassemble: %s: the stage directory %s has no %s/' % (n, d, n.lower()))
        shutil.copytree(src, os.path.join(out, n.lower()))
    print('factsassemble: exe + %d modules + funcs' % len(mods))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--table', required=True); ap.add_argument('--funcs', required=True); ap.add_argument('--exe', required=True)
    ap.add_argument('--module', action='append', default=[]); ap.add_argument('--out', required=True); a = ap.parse_args()
    mods = []
    for m in a.module:
        if '=' not in m: sys.exit('factsassemble: --module NAME=DIR, got %r' % m)
        mods.append(tuple(m.split('=', 1)))
    main(a.table, a.funcs, a.exe, mods, a.out)
