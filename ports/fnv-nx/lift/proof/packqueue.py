#!/usr/bin/env python3
"""The pinned shape queue of one lifted pack.

    python3 -I packqueue.py UNIVERSE.tsv SHARDS... --pack-commit SHA --prover-commit SHA --out queue.tsv

UNIVERSE.tsv = packuniverse.py output: the shapes of EVERY lifted instruction of the pack (computed on the pack's own labels, no form-name matching).
queue = the integer-family shapes of the universe that have a symbolic Form (certs_build.forms_of) and are NOT already proved for the pinned IR at the same
shape_sha in SHARDS (a record whose model_sha is the pin or whose closure_sha is unchanged: certs_build.model_valid).  A record of a prover before DEF0 / DEFPRIOR is
excluded here as well: it migrates (shapeprove.py --migrate), it is not proved again.
Rows: shape_key<TAB>shape_sha, sorted by key (shapeprove.py takes the shapes whose position is = K-1 mod N); header lines '# ...' pin the inputs and give the counts
(per family: the universe, the queue, and the shapes no prover states: every non-integer family, integer shapes without a Form)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import argparse
import collections
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fnvnx.lift.proof import certs_build, model_sail, symform, x86sys  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument('universe'); ap.add_argument('shards', nargs='*'); ap.add_argument('--pack-commit', required=True)
    ap.add_argument('--prover-commit', required=True); ap.add_argument('--out', required=True); ap.add_argument('--noform')
    ap.add_argument('--sail-work', required=True); ap.add_argument('--sail-ir', required=True); ap.add_argument('--isla', required=True); ap.add_argument('--isla-cfg', required=True); x86sys.add_arg(ap)
    ap.add_argument('--mode', choices=('int', 'simd'), default='int', help='simd: the queue of the SIMD family (header `# mode=simd`): every SIMD shape with a symbolic Form and no complete non-error record of --tree')
    ap.add_argument('--tree', help='simd: the commit of the prover tree the records must come from (record `tree`)'); ap.add_argument('--qcache', help='simd: BRANCH:FILE of the proved-query cache that seeds the farms (header)')
    ap.add_argument('--cpumodel-file', help='simd: the declared CPU record verify/cpuid_model.json (the class order reads it)'); ap.add_argument('--cpumodel-ref', help='simd: BRANCH:FILE where the farms fetch that record (header)')
    a = ap.parse_args(argv)
    xsha = x86sys.configure(a.x86sys)
    model_sail.configure(a.sail_work, a.sail_ir, a.isla, a.isla_cfg)
    pin = certs_build.pinned_model()
    fam, hdr, first = {}, [], {}
    with open(a.universe) as fh:
        for ln in fh:
            if ln[0] == '#': hdr.append(ln.rstrip('\n')); continue
            p = ln.rstrip('\n').split('\t'); fam[p[0]] = (p[1], int(p[3])); first[p[0]] = p[4]
    simd = a.mode == 'simd'
    if simd:
        if not (a.tree and a.cpumodel_file and a.cpumodel_ref): ap.error('--mode simd needs --tree, --cpumodel-file and --cpumodel-ref')
        from fnvnx.lift.fnvlift import inputs; inputs.configure(cpumodel=a.cpumodel_file)        # the class order (simdcheck.cleared, proof/cpufeat.py) reads the declared CPU record
    forms = certs_build.forms_of(a.universe, ('simd',) if simd else ('int', 'sys'))
    files = []
    for s in a.shards: files += sorted(glob.glob(os.path.join(s, '**', '*.jsonl'), recursive=True)) if os.path.isdir(s) else [s]
    done = set()
    for f, i, r in certs_build.records(files):
        k = r.get('shape')
        if simd:       # complete = a non-error record of THIS tree at the current shape_sha (a timeout / no-row / ud-class record is a result; an error record is not)
            if k in forms and r.get('result') != 'error' and r.get('tree') == a.tree and r.get('shape_sha') == forms[k]: done.add(k)
        elif r.get('result') == 'proved' and k in forms and r.get('dsl_sha') == forms[k] and certs_build.model_valid(r, pin): done.add(k)
    q = sorted((k, d) for k, d in forms.items() if k not in done)
    qid = hashlib.sha256(''.join('%s\t%s\n' % x for x in q).encode()).hexdigest()[:12]
    cu = collections.Counter(f for f, _ in fam.values()); sites = collections.Counter()
    for f, c in fam.values(): sites[f] += c
    noform = sorted(k for k, (f, _) in fam.items() if f in (('simd',) if simd else ('int', 'sys')) and k not in forms)
    xhdr = []
    if simd:
        with open(a.cpumodel_file, 'rb') as fh: csha = hashlib.sha256(fh.read()).hexdigest()
        xhdr = ['# mode=simd', '# subject=%s' % a.tree] + (['# qcache=%s' % a.qcache] if a.qcache else []) + ['# cpumodel=%s' % a.cpumodel_ref, '# cpumodel_sha256=%s' % csha]
    lines = ['# fnvnx-shapequeue/1 queue_id=%s' % qid] + xhdr + [
             '# pack_commit=%s' % a.pack_commit, '# prover_commit=%s' % a.prover_commit, '# ir_sha256=%s' % pin, '# x86sys_sha256=%s' % xsha,
             '# universe: %s' % ' '.join(h[2:] for h in hdr[:1]),
             '# universe shapes: ' + ' '.join('%s=%d' % x for x in sorted(cu.items())) + ' total=%d' % sum(cu.values()),
             '# universe instruction addresses: ' + ' '.join('%s=%d' % x for x in sorted(sites.items())) + ' total=%d' % sum(sites.values()),
             '# %s shapes with a Form=%d  already done (%s)=%d  QUEUE: %s=%d total=%d' % (a.mode, len(forms), 'complete non-error record of the tree' if simd else 'proved at the same shape_sha: migrate, not queued', len(done), a.mode, len(q), len(q)),
             '# no prover states: x87=%d simd=%d sys=%d (shapes; the DSL has no builders for them yet) and %s without a Form=%d (shapes, %d addresses)' % (cu['x87'], cu['simd'], cu['sys'], a.mode, len(noform), sum(fam[k][1] for k in noform)),
             '# shards read: %d files, %d records' % (len(files), sum(1 for _ in certs_build.records(files)))]
    with open(a.out, 'w') as fh:
        fh.write('\n'.join(lines) + '\n' + ''.join('%s\t%s\n' % x for x in q))
    if a.noform:
        with open(a.noform, 'w') as fh: fh.write('# int shapes of the universe without a symbolic Form: shape_key<TAB>addresses<TAB>first_address\n' + ''.join('%s\t%d\t%s\n' % (k, fam[k][1], first[k]) for k in noform))
    sys.stdout.write('\n'.join(lines) + '\n'); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
