#!/usr/bin/env python3
"""The pinned shape queue of one lifted pack.

    python3 -I packqueue.py UNIVERSE.tsv SHARDS... --pack-commit SHA --prover-commit SHA --out queue.tsv

UNIVERSE.tsv = packuniverse.py output: the shapes of EVERY lifted instruction of the pack (computed on the pack's own labels, no form-name matching).
queue = the integer-family shapes of the universe that have a symbolic Form (certs_build.forms_of) and are NOT already proved for the pinned IR at the same
shape_sha in SHARDS (a record whose model_sha is the pin or whose closure_sha is unchanged: certs_build.model_valid).  A record of a prover before DEF0 / DEFPRIOR is
excluded here as well: it migrates (shapeprove.py --migrate), it is not proved again.
Rows: shape_key<TAB>shape_sha, sorted by key (shapeprove.py takes the shapes whose position is = K-1 mod N); header lines '# ...' pin the inputs and give the counts
(per family: the universe, the queue, and the shapes no prover states: every non-integer family, integer shapes without a Form)."""
import argparse
import collections
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import certs_build, symform  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument('universe'); ap.add_argument('shards', nargs='*'); ap.add_argument('--pack-commit', required=True)
    ap.add_argument('--prover-commit', required=True); ap.add_argument('--out', required=True); ap.add_argument('--noform'); a = ap.parse_args(argv)
    pin = certs_build.pinned_model()
    fam, hdr, first = {}, [], {}
    with open(a.universe) as fh:
        for ln in fh:
            if ln[0] == '#': hdr.append(ln.rstrip('\n')); continue
            p = ln.rstrip('\n').split('\t'); fam[p[0]] = (p[1], int(p[3])); first[p[0]] = p[4]
    forms = certs_build.forms_of(a.universe)
    files = []
    for s in a.shards: files += sorted(glob.glob(os.path.join(s, '**', '*.jsonl'), recursive=True)) if os.path.isdir(s) else [s]
    done = set()
    for f, i, r in certs_build.records(files):
        k = r.get('shape')
        if r.get('result') == 'proved' and k in forms and r.get('dsl_sha') == forms[k] and certs_build.model_valid(r, pin): done.add(k)
    q = sorted((k, d) for k, d in forms.items() if k not in done)
    qid = hashlib.sha256(''.join('%s\t%s\n' % x for x in q).encode()).hexdigest()[:12]
    cu = collections.Counter(f for f, _ in fam.values()); sites = collections.Counter()
    for f, c in fam.values(): sites[f] += c
    noform = sorted(k for k, (f, _) in fam.items() if f == 'int' and k not in forms)
    lines = ['# fnvnx-shapequeue/1 queue_id=%s' % qid,
             '# pack_commit=%s' % a.pack_commit, '# prover_commit=%s' % a.prover_commit, '# ir_sha256=%s' % pin,
             '# universe: %s' % ' '.join(h[2:] for h in hdr[:1]),
             '# universe shapes: ' + ' '.join('%s=%d' % x for x in sorted(cu.items())) + ' total=%d' % sum(cu.values()),
             '# universe instruction addresses: ' + ' '.join('%s=%d' % x for x in sorted(sites.items())) + ' total=%d' % sum(sites.values()),
             '# int shapes with a Form=%d  already proved at the same shape_sha (migrate, not queued)=%d  QUEUE: int=%d x87=0 simd=0 sys=0 total=%d' % (len(forms), len(done), len(q), len(q)),
             '# no prover states: x87=%d simd=%d sys=%d (shapes; the DSL has no builders for them yet) and int without a Form=%d (shapes, %d addresses)' % (cu['x87'], cu['simd'], cu['sys'], len(noform), sum(fam[k][1] for k in noform)),
             '# shards read: %d files, %d records' % (len(files), sum(1 for _ in certs_build.records(files)))]
    with open(a.out, 'w') as fh:
        fh.write('\n'.join(lines) + '\n' + ''.join('%s\t%s\n' % x for x in q))
    if a.noform:
        with open(a.noform, 'w') as fh: fh.write('# int shapes of the universe without a symbolic Form: shape_key<TAB>addresses<TAB>first_address\n' + ''.join('%s\t%d\t%s\n' % (k, fam[k][1], first[k]) for k in noform))
    sys.stdout.write('\n'.join(lines) + '\n'); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
