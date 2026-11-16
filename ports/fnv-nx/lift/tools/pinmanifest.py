#!/usr/bin/env python3
"""pinmanifest.py: manifest.json of the published lift-facts pin, derived from the assembled facts directory (factsassemble.py layout) and the loader's module table; nothing is typed by hand.
    pinmanifest.py --table MODULES_TSV --facts DIR --lifter COMMIT --gamedata NAME=COMMIT:TREE_SHA256[,...] --out manifest.json
Images: the exe (files at the top of DIR, as factsassemble lays them out) and every row of the table (DIR/<name without .dll, lower case>/). Per image: the table's sha256 and base, the file names present, the universe row count, the
status totals and the attr totals (the flags column `status,attr,...` split by the vocabulary of flagvocab.py). `universe_flags` is flagvocab.vocab(). `modules_tsv_sha256` is the sha256 of the table file.
Fails when an image has no universe.tsv, or a row's flags are outside the vocabulary (flagvocab.check)."""
import argparse, hashlib, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flagvocab

FILES = ('universe.tsv', 'ranges.tsv', 'retn.tsv', 'sources.tsv', 'roots.tsv', 'ehtables.json', 'noreturn.tsv')


def table_rows(path):
    rows = []
    for l in open(path):
        if l.strip() and l[0] != '#': rows.append(l.rstrip('\n').split('\t'))
    return rows


def image_entry(name, d, row):
    u = os.path.join(d, 'universe.tsv')
    if not os.path.isfile(u): sys.exit('pinmanifest: %s: no universe.tsv in %s' % (name, d))
    v = flagvocab.vocab(); bad = flagvocab.check(u, v)
    if bad: sys.exit('pinmanifest: %s: %d rows outside the flag vocabulary, first: %s' % (name, len(bad), bad[0]))
    st = Counter(); at = Counter(); n = 0
    for l in open(u):
        if l[0] == '#' or not l.strip(): continue
        toks = l.rstrip('\n').split('\t')[3].split(','); n += 1; st[toks[0]] += 1
        for t in toks[1:]: at[t.split('=')[0] + ('=' if '=' in t else '')] += 1
    e = {'image': name, 'universe_rows': n, 'status': dict(sorted(st.items())), 'attr': dict(sorted(at.items())), 'files': sorted(f for f in FILES if os.path.isfile(os.path.join(d, f)))}
    if row: e.update(sha256=row[1], base=row[4] if len(row) > 4 else None)
    return e


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--table', required=True); ap.add_argument('--facts', required=True); ap.add_argument('--lifter', required=True); ap.add_argument('--gamedata', required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args()
    rows = table_rows(a.table)
    imgs = [image_entry('exe', os.path.join(a.facts, 'exe') if os.path.isdir(os.path.join(a.facts, 'exe')) else a.facts, None)]
    for r in sorted(rows, key=lambda r: r[0].lower()):
        n = os.path.splitext(r[0])[0].lower()
        if n == 'falloutnv': continue      # the exe row of the table: the exe image above
        imgs.append(image_entry(n, os.path.join(a.facts, n), r))
    gd = {}
    for kv in a.gamedata.split(','):
        k, v = kv.split('='); c, t = v.split(':'); gd[k] = {'commit': c, 'tree_sha256': t}
    m = {'format': 'fnvnx-liftfacts-manifest/2', 'lifter_commit': a.lifter, 'modules_tsv_sha256': hashlib.sha256(open(a.table, 'rb').read()).hexdigest(), 'gamedata': gd,
         'universe_flags': flagvocab.vocab(), 'images': imgs, 'totals': {'images': len(imgs), 'universe_rows': sum(i['universe_rows'] for i in imgs)}}
    json.dump(m, open(a.out, 'w'), indent=1, sort_keys=True); open(a.out, 'a').write('\n')
    print('manifest: %d images, %d universe rows, modules_tsv_sha256 %s' % (len(imgs), m['totals']['universe_rows'], m['modules_tsv_sha256']))


if __name__ == '__main__': main()
