#!/usr/bin/env python3
"""The re-prove queue after a prover change: every key that a set of old shards certified plus every key that a set of newer shards did not prove, deduplicated, as a shape queue (packqueue.py format).

    python3 -I reprove_queue.py UNIVERSE.tsv --old SHARDS... --new SHARDS... --pack-commit SHA --prover-commit SHA --out queue.tsv  (+ the packqueue toolchain arguments)

Keys outside UNIVERSE are reported (stderr) and dropped.  Code only: the key set and the queue are private artifacts (fnv-nx-build), never in this repository."""
import argparse, glob, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import certs_build, packqueue  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument('universe'); ap.add_argument('--old', nargs='+', required=True); ap.add_argument('--new', nargs='+', required=True)
    a, rest = ap.parse_known_args(argv)
    def files(ss):
        out = []
        for s in ss: out += sorted(glob.glob(os.path.join(s, '**', '*.jsonl'), recursive=True)) if os.path.isdir(s) else [s]
        return out
    keys = set()
    for f, i, r in certs_build.records(files(a.old)):
        if r.get('result') in ('proved', 'agree-except-declared'): keys.add(r['shape'])
    for f, i, r in certs_build.records(files(a.new)):
        if r.get('result') != 'proved': keys.add(r['shape'])
    rows = open(a.universe).read().split('\n')
    kept = [l for l in rows if l and (l[0] == '#' or l.split('\t')[0] in keys)]
    inu = {l.split('\t')[0] for l in kept if l[0] != '#'}
    print('reprove keys=%d in universe=%d outside=%d' % (len(keys), len(inu & keys), len(keys - inu)), file=sys.stderr)
    with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as t: t.write('\n'.join(kept) + '\n')
    try: return packqueue.main([t.name] + rest)
    finally: os.unlink(t.name)


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
