#!/usr/bin/env python3
"""The ea-basis closure of a pinned shape queue.

    python3 -I queue_basis.py QUEUE.tsv UNIVERSE.tsv SHARDS... --x86sys X86SYS.json --out QUEUE2.tsv

RULE: the basis shape of every far memory shape of the universe (sysspec_far.basis_key: the PUSH r/m32 shape with the same ModRM/SIB/displacement) is in the queue,
unless a record of SHARDS already proved it at the current shape_sha.  The far shapes are derived from the universe by sysspec_far (no list); the basis keys are derived from them.
A basis shape without a symbolic Form is reported (stderr) and not queued.  The queue id is recomputed over the rows (the header counts are kept, one line is added)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)


def main(argv):
    argv = list(argv); i = argv.index('--x86sys'); from fnvnx.lift.proof import x86sys; x86sys.configure(argv[i + 1]); del argv[i:i + 2]
    i = argv.index('--out'); out = argv[i + 1]; del argv[i:i + 2]
    queue, universe, shards = argv[0], argv[1], argv[2:]
    from fnvnx.lift.proof import sysspec, sysspec_far, symform, certs_build, shape
    hdr, rows = [], {}
    for ln in open(queue):
        if ln[0] == '#': hdr.append(ln.rstrip('\n'))
        elif ln.strip(): k, s = ln.rstrip('\n').split('\t'); rows[k] = s
    proved = {}
    for f in sum((sorted(glob.glob(os.path.join(s, '**', '*.jsonl'), recursive=True)) if os.path.isdir(s) else [s] for s in shards), []):
        for _, _, r in certs_build.records([f]):
            if r.get('result') == 'proved': proved[r['shape']] = r.get('dsl_sha')
    far = [ln.split('\t')[0] for ln in open(universe) if ln[0] != '#' and ln.strip()]
    added, noform = [], []
    for k in far:
        try:
            fk = sysspec.far_kind(k) if sysspec.mnemonic(k) in ('call', 'jmp', 'lcall', 'ljmp') else None
            if fk is None or fk[1]: continue                  # only far MEMORY forms take an effective address (the immediate forms have no basis)
            b = sysspec_far.basis_key(k)
        except Exception: continue
        if not b or b == '-' or b in rows or any(a == b for a in added): continue
        sha = symform.shape_sha(b) if certs_build.current_sha(b) else None
        if sha is None: noform.append(b); continue
        if proved.get(b) == sha: continue
        rows[b] = sha; added.append(b)
    q = sorted(rows.items())
    qid = hashlib.sha256(''.join('%s\t%s\n' % x for x in q).encode()).hexdigest()[:12]
    hdr[0] = '# fnvnx-shapequeue/1 queue_id=%s' % qid
    hdr.append('# ea-basis closure: +%d basis shapes of far memory shapes (queue_basis.py): %s' % (len(added), ' '.join(sorted(added))))
    with open(out, 'w') as fh: fh.write('\n'.join(hdr) + '\n' + ''.join('%s\t%s\n' % x for x in q))
    sys.stderr.write('basis added=%d noform=%s total=%d id=%s\n' % (len(added), noform, len(q), qid)); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
