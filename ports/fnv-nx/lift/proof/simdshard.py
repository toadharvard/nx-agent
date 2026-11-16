#!/usr/bin/env python3
"""One shard of a SIMD queue (header `# mode=simd`): the same queue format, shard rule, record file and resume as shapeprove.py, with simdcheck.check as the prover.
    simdshard.py --shapes QUEUE --shard K/N --out DIR [--slice S] [--limit L]
Rows are `shape_key<TAB>shape_sha`; the shard is the rows at position = K-1 mod N (shardok.rows).  A record is {shape, result, note, shape_sha, seconds}; result is simdcheck's
(proved | proved-by-cases | FAILED | bound | no-row | no-form | uncertified | ud-class).  A queue row whose shape_sha is not symform.shape_sha(key) of this tree (the subject of
the queue is not the tree that runs) is an `error` record, so such a shard never counts as complete.  A prover exception is an `error` record with its traceback hash.
simdcheck is bounded by proof/bounds.py Z3_RLIMIT only (no clock); its proved-query cache is the file SIMD_QCACHE (seeded by the farm from the queue's `# qcache=` branch)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import argparse
import hashlib
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import simdcheck  # noqa: E402
from fnvnx.lift.proof import symform, treeid, x86sys  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shapes', required=True); ap.add_argument('--shard', default='1/1'); ap.add_argument('--out', required=True)
    ap.add_argument('--slice', type=int, default=1); ap.add_argument('--limit', type=int); ap.add_argument('--cache', required=True)
    x86sys.add_arg(ap); ap.add_argument('--cpufeat', required=True, help='the CPUID feature-bit table (l3b-lift/cpuid-feature-bits): the class order reads it; its sha256 is in every record')
    a = ap.parse_args(); xsha = x86sys.configure(a.x86sys); from fnvnx.lift.fnvlift import inputs; inputs.configure(cpufeat=a.cpufeat); csha = hashlib.sha256(open(a.cpufeat, 'rb').read()).hexdigest(); simdcheck.S.F.configure(a.cache); k, n = [int(x) for x in a.shard.split('/')]; assert 1 <= k <= n
    os.makedirs(a.out, exist_ok=True); path = os.path.join(a.out, 'shapeprove_%d_of_%d.jsonl' % (k, n))
    rows = [l.rstrip('\n').split('\t') for l in open(a.shapes) if l.strip() and l[0] != '#'][::a.slice]
    mine = [r for i, r in enumerate(rows) if i % n == k - 1][:a.limit]
    tree = treeid.current(); done = treeid.resume(path, tree)
    print('shard %d/%d: %d shapes, %d already done' % (k, n, len(mine), len(done)), flush=True)
    with open(path, 'a') as out:
        for key, sha in mine:
            if key in done: continue
            t0 = time.time()
            try:
                if symform.shape_sha(key) != sha: raise ValueError('shape_sha of this tree differs from the queue row (the queue subject is not this tree)')
                res, info = simdcheck.check(key)
                rec = {'shape': key, 'result': res, 'note': ('' if info is None else str(info))[:300], 'shape_sha': sha}
            except Exception as e:                 # an exception on a shape is a record (error + the traceback hash), never a missing one
                tb = traceback.format_exc(); print(tb, file=sys.stderr, flush=True)
                rec = {'shape': key, 'result': 'error', 'note': ('%s: %s' % (type(e).__name__, e))[:300], 'traceback_sha': hashlib.sha256(tb.encode()).hexdigest()[:16], 'shape_sha': sha}
            rec['seconds'] = round(time.time() - t0, 2); rec['tree'] = tree; rec['x86sys_sha256'] = xsha; rec['cpufeat_sha256'] = csha
            out.write(json.dumps(rec, sort_keys=True) + '\n'); out.flush()
    rs = [json.loads(l) for l in open(path)]
    c = {}
    for r in rs: c[r['result']] = c.get(r['result'], 0) + 1
    print('shard %d/%d done: %d shapes  ' % (k, n, len(rs)) + ' '.join('%s=%d' % x for x in sorted(c.items())))
    return 1 if c.get('error') or len(rs) < len(mine) else 0


if __name__ == '__main__':
    sys.exit(main())
