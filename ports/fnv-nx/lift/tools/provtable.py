#!/usr/bin/env python3
"""The density table of the lifted pack: merges the provenance side tables (tools/liftgen.py --shard K/N --prov -> NAME/prov_K.json, fnvlift/prov.py) of every image into
    TABLE/categories.tsv  category (root | site)  bytes  lines  share_pct  bytes_per_insn          (bytes_per_insn = bytes / all lifted instructions)
    TABLE/roots.tsv       root (the handler that dispatch ran, or lift2 / gs_guard)  bytes  share_pct  bytes_per_insn
    TABLE/forms.tsv       form key (mnemonic|operand classes|opcode)  insns  bytes  bytes_per_insn   (the bytes of the instruction's whole emitted text, from its label to its end)
    TABLE/families.tsv    shape family  insns  bytes  bytes_per_insn
    TABLE/images.tsv      image  entries  lifted  missing  functions  insns  text_bytes ; and shards.tsv: image shard texts_sha256
    TABLE/summary.json    totals and the top 10 categories
Every number is a sum over ALL shards of ALL images given (the totals are checked: the categories add up to the text bytes of every shard). Deterministic; no clocks or paths.
    provtable.py --stage DIR --out DIR     DIR holds NAME/prov_K.json for every image NAME"""
import argparse, collections, glob, json, os, sys


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--stage', required=True); A('--out', required=True)
    a = ap.parse_args()
    files = sorted(glob.glob(a.stage + '/*/prov_*.json'))
    if not files: sys.exit('provtable: no prov_K.json below %s' % a.stage)
    cats, forms, fams = (collections.defaultdict(lambda: [0, 0]) for _ in range(3))
    imgs = collections.defaultdict(lambda: collections.Counter()); shards = []
    for f in files:
        r = json.load(open(f)); name = os.path.basename(os.path.dirname(f))
        if r.get('format') != 'fnvnx-lift-prov/1': sys.exit('provtable: %s is not a prov table' % f)
        if sum(v[0] for v in r['cats'].values()) != r['text_bytes']: sys.exit('provtable: %s: the categories do not add up to its text bytes' % f)
        for dst, src in ((cats, r['cats']), (forms, r['forms']), (fams, r['fams'])):
            for k, v in src.items(): dst[k][0] += v[0]; dst[k][1] += v[1]
        c = imgs[name]
        for k in ('entries', 'lifted', 'missing', 'functions', 'insns', 'text_bytes'): c[k] += r[k] if k in r else 0
        shards.append((name, os.path.basename(f), r['shard'], r['texts_sha256']))
    text = sum(c['text_bytes'] for c in imgs.values()); insns = sum(c['insns'] for c in imgs.values())
    if sum(v[0] for v in cats.values()) != text: sys.exit('provtable: the categories do not add up to the text bytes of all images')
    os.makedirs(a.out, exist_ok=True)
    w = lambda n, rows: open(os.path.join(a.out, n), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    pct = lambda b: '%.3f' % (100.0 * b / text)
    bpi = lambda b: '%.3f' % (b / insns)
    ranked = sorted(cats.items(), key=lambda t: (-t[1][0], t[0]))
    w('categories.tsv', [('# category (root | site)', 'bytes', 'lines', 'share_pct', 'bytes_per_insn')] + [(k, v[0], v[1], pct(v[0]), bpi(v[0])) for k, v in ranked])
    roots = collections.defaultdict(int)
    for k, v in cats.items(): roots[k.split(' | ')[0]] += v[0]
    w('roots.tsv', [('# root', 'bytes', 'share_pct', 'bytes_per_insn')] + [(k, b, pct(b), bpi(b)) for k, b in sorted(roots.items(), key=lambda t: (-t[1], t[0]))])
    w('forms.tsv', [('# form key', 'insns', 'bytes', 'bytes_per_insn', 'share_pct')] + [(k, v[1], v[0], '%.2f' % (v[0] / v[1]), pct(v[0])) for k, v in sorted(forms.items(), key=lambda t: (-t[1][0], t[0]))])
    w('families.tsv', [('# shape family', 'insns', 'bytes', 'bytes_per_insn', 'share_pct')] + [(k, v[1], v[0], '%.2f' % (v[0] / v[1]), pct(v[0])) for k, v in sorted(fams.items(), key=lambda t: (-t[1][0], t[0]))])
    w('images.tsv', [('# image', 'entries', 'lifted', 'missing', 'functions', 'insns', 'text_bytes')] + [(n,) + tuple(c[k] for k in ('entries', 'lifted', 'missing', 'functions', 'insns', 'text_bytes')) for n, c in sorted(imgs.items())])
    w('shards.tsv', [('# image', 'file', 'shard', 'texts_sha256')] + sorted(shards))
    json.dump(dict(format='fnvnx-density-table/1', images=len(imgs), shards=len(shards), functions=sum(c['functions'] for c in imgs.values()), insns=insns, text_bytes=text,
                   bytes_per_insn=round(text / insns, 3), missing=sum(c['missing'] for c in imgs.values()),
                   top10=[dict(category=k, bytes=v[0], share_pct=float(pct(v[0])), bytes_per_insn=float(bpi(v[0]))) for k, v in ranked[:10]]),
              open(os.path.join(a.out, 'summary.json'), 'w'), sort_keys=True, indent=1)
    print('provtable: images %d, shards %d, functions %d, insns %d, text bytes %d (%.2f B/insn); categories %d, form keys %d' % (len(imgs), len(shards), sum(c['functions'] for c in imgs.values()), insns, text, text / insns, len(cats), len(forms)))


if __name__ == '__main__': main()
