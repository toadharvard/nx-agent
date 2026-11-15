#!/usr/bin/env python3
"""Facts stage FACTS: the one facts directory of the lifted image set, assembled from the per-image stages.
    factsassemble.py --table MODULES_TSV --funcs funcs.tsv --exe DIR --module NAME=DIR [--module ...] --out FACTS --gates GATES
RULE (image set): the images are the exe and every row of the loader's module table (modules.tsv, one row per module file of the declared file set, by the loader's own rule). The module stages given
here must be exactly the rows of the table: a row with no stage, a stage that is no row, or a stage given twice is an error naming all of them. Layout (the layout the facts readers take): the exe's files at the top,
funcs.tsv at the top, one directory <module name without .dll, lower case> per row with the module stage's files. DIR of a module stage holds <name>/ with the files modlift wrote; the exe stage DIR holds the files.
GATES: the verdict facts of every image, so that a red gate is never lost: GATES/<image>/gate_codemap.json and gate_pcsrc.json copied byte for byte (image `exe` for the exe), and GATES/summary.tsv, one row per image in the order exe, then
the table's rows by name: image, status (gate_codemap.json), universe, certified, uncertified, entries_on_data (gate_codemap.json), pcsrc_red (gate_pcsrc.json `red`), s4_violations (pcsrc_s4.json), and a last row `total` with the sums. A gate file that is missing is an error.
Nothing is chosen or computed here: files are copied byte for byte (the summary only reads and adds numbers)."""
import argparse
import json
import os
import shutil
import sys


def rows(path):
    out = []
    for l in open(path):
        if l.strip() and l[0] != '#': out.append(l.split('\t')[0])
    return out


def gates(exe, mods, out):
    imgs = [('exe', exe)] + [(n.lower(), os.path.join(d, n.lower())) for n, d in sorted(mods, key=lambda x: x[0].lower())]
    os.makedirs(out); tot = [0, 0, 0, 0, 0, 0]; sts = []; lines = ['# image\tstatus\tuniverse\tcertified\tuncertified\tentries_on_data\tpcsrc_red\ts4_violations\n']
    for n, d in imgs:
        miss = [f for f in ('gate_codemap.json', 'gate_pcsrc.json', 'pcsrc_s4.json') if not os.path.isfile(os.path.join(d, f))]
        if miss: sys.exit('factsassemble: %s: missing %s' % (n, ' '.join(miss)))
        os.makedirs(os.path.join(out, n))
        for f in ('gate_codemap.json', 'gate_pcsrc.json'): shutil.copy(os.path.join(d, f), os.path.join(out, n, f))
        c = json.load(open(os.path.join(d, 'gate_codemap.json'))); p = json.load(open(os.path.join(d, 'gate_pcsrc.json'))); s4 = json.load(open(os.path.join(d, 'pcsrc_s4.json')))
        v = [c['universe'], c['certified'], c['uncertified'], c.get('entries_on_data', 0), p['red'], s4['s4_violations']]
        sts += [c['status'], p['status']]; tot = [a + b for a, b in zip(tot, v)]; lines.append('%s\t%s\t%s\n' % (n, c['status'], '\t'.join(map(str, v))))
    lines.append('total\t%s\t%s\n' % ('red' if 'red' in sts else 'green', '\t'.join(map(str, tot))))
    open(os.path.join(out, 'summary.tsv'), 'w').write(''.join(lines)); print(''.join(lines), end='')


def main(table, funcs, exe, mods, out, gout):
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
    gates(exe, mods, gout)
    print('factsassemble: exe + %d modules + funcs' % len(mods))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--table', required=True); ap.add_argument('--funcs', required=True); ap.add_argument('--exe', required=True)
    ap.add_argument('--module', action='append', default=[]); ap.add_argument('--out', required=True); ap.add_argument('--gates', required=True); a = ap.parse_args()
    mods = []
    for m in a.module:
        if '=' not in m: sys.exit('factsassemble: --module NAME=DIR, got %r' % m)
        mods.append(tuple(m.split('=', 1)))
    main(a.table, a.funcs, a.exe, mods, a.out, a.gates)
