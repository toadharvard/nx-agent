#!/usr/bin/env python3
"""Aggregator of the shape-prover shards: certs.tsv, the file proof/formgate.py reads (schema: the README of the artifacts branch l3b-lift/certs).

    python3 -I certs_build.py --id                                  the run id <dsl id>-<model id> (names the farm branches l3b-lift/shapeprove/<id>/<K>of<N>)
    python3 -I certs_build.py --shards PATH [PATH ...] --out certs.tsv [--report FILE]
PATH = a shapeprove_<K>_of_<N>.jsonl file or a directory searched (sorted, recursively) for such files.

dsl id   = first 12 hex of sha256 over the sources that define the Forms: fnvlift/dsl.py, proof/forms.py, proof/forms_*.py, proof/symform.py, proof/shape.py
           (name + NUL + bytes + NUL, in sorted name order).  model id = first 12 hex of the pinned sha256 of x86_proof.ir (proof/sail_pins.txt, = the
           model_sha256 of formgate_pins.json).
Only proved shapes get a row (one row per proved shape: formgate counts nothing else).  Every record of every shard is checked against the CURRENT code,
and the whole run is REFUSED (exit 2, no output file) when a check fails:
  * model_sha of the record == the pinned IR sha256;
  * dsl_sha of the record == symform.shape_sha(shape), the symbolic Form of the current builders (a shard made by other Form code is refused);
  * the same (shape, dsl_sha, model_sha) must not be `proved` in one record and FAILED(...) in another.
Row: shape_key, family, dsl_sha (= symform.shape_sha(shape), the field formgate.dsl_sha computes), model_sha, `sail`, `proved`.
(The link between the symbolic Form and the lifter's concrete Forms is proof/symcheck.py, over every instance of the image; it is not part of a certificate.)
Rows are unique and sorted; the output depends on the set of records only (not on shard order, file names or time).  Needs capstone; no z3, no clock."""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT)
from proof import shape, symform  # noqa: E402

HEADER = '# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n'


class Refused(Exception):
    """a shard record does not belong to the current code / pinned model"""


def dsl_files():
    names = [os.path.join('fnvlift', 'dsl.py'), os.path.join('proof', 'forms.py'), os.path.join('proof', 'symform.py'), os.path.join('proof', 'shape.py')]
    names += [os.path.join('proof', f) for f in sorted(os.listdir(HERE)) if f.startswith('forms_') and f.endswith('.py')]
    return sorted(names)


def dsl_id():
    h = hashlib.sha256()
    for n in dsl_files():
        with open(os.path.join(LIFT, n), 'rb') as fh:
            h.update(n.encode() + b'\0' + fh.read() + b'\0')
    return h.hexdigest()[:12]


def pinned_model():
    with open(os.path.join(HERE, 'sail_pins.txt')) as fh:
        for ln in fh:
            f = ln.split()
            if len(f) == 4 and f[0] == 'x86_proof.ir':
                return f[3]
    raise Refused('no x86_proof.ir pin in sail_pins.txt')


def run_id():
    return '%s-%s' % (dsl_id(), pinned_model()[:12])


def shard_files(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                out += [os.path.join(root, f) for f in sorted(files) if f.startswith('shapeprove_') and f.endswith('.jsonl')]
        else:
            out.append(p)
    return out


def records(files):
    for f in files:
        with open(f) as fh:
            for i, ln in enumerate(fh, 1):
                if ln.strip():
                    yield f, i, json.loads(ln)


def build(files):
    """-> (sorted rows, counts of results, notes).  Raises Refused."""
    model = pinned_model()
    with open(os.path.join(HERE, 'formgate_inputs.json')) as fh:
        if json.load(fh)['model_sha256'] != model:
            raise Refused('formgate_inputs.json model_sha256 differs from the x86_proof.ir pin of sail_pins.txt')
    md = shape.decoder()
    rows, status, counts, sym = {}, {}, {}, {}
    for f, i, r in records(files):
        where = '%s:%d' % (f, i)
        res = r.get('result')
        counts[res] = counts.get(res, 0) + 1
        if res in ('no-form', 'builder-branches-on-field', 'field-decode', 'decode'):
            continue                                            # no obligation ran: no Form, no model sha
        key = r['shape']
        if r.get('model_sha') != model:
            raise Refused('%s: model_sha %s is not the pinned %s' % (where, r.get('model_sha'), model))
        if key not in sym:
            sym[key] = symform.shape_sha(key)
        if r.get('dsl_sha') != sym[key]:
            raise Refused('%s: dsl_sha %s is not the symbolic Form sha of the current code (%s) for %s' % (where, r.get('dsl_sha'), sym[key], key))
        k3 = (key, r['dsl_sha'], model)
        proved = res == 'proved'
        if res.startswith('FAILED') and status.get(k3) == 'proved' or proved and status.get(k3, '').startswith('FAILED'):
            raise Refused('%s: %s is proved in one record and %s in another' % (where, key, res if not proved else status[k3]))
        if proved or k3 not in status:
            status[k3] = res
        if not proved:
            continue
        rep = shape.rep_bytes(key)
        ins = list(md.disasm(rep, shape.ADDR))
        if len(ins) != 1 or ins[0].size != len(rep) or shape.shape_key(ins[0]) != key:
            raise Refused('%s: the representative of %s is not the shape' % (where, key))
        rows[key] = '%s\t%s\t%s\t%s\tsail\tproved\n' % (key, shape.family(ins[0]), r['dsl_sha'], model)
    return [rows[k] for k in sorted(rows)], counts, status


def main(argv):
    ap = argparse.ArgumentParser(prog='certs_build')
    ap.add_argument('--id', action='store_true')
    ap.add_argument('--shards', nargs='+')
    ap.add_argument('--out')
    ap.add_argument('--report')
    a = ap.parse_args(argv)
    if a.id:
        sys.stdout.write(run_id() + '\n')
        return 0
    if not a.shards or not a.out:
        sys.stderr.write('certs_build: --shards PATH... and --out FILE are required (or --id)\n')
        return 2
    files = shard_files(a.shards)
    try:
        rows, counts, status = build(files)
    except Refused as e:
        sys.stderr.write('certs_build: REFUSED: %s\n' % e)
        return 2
    with open(a.out, 'w', newline='\n') as fh:
        fh.write(HEADER)
        fh.writelines(rows)
    if a.report:
        with open(a.report, 'w', newline='\n') as fh:
            fh.write('# shape_key\tdsl_sha\tmodel_sha\tbest result in the shards\n')
            for k3 in sorted(status):
                if status[k3] != 'proved':
                    fh.write('%s\t%s\t%s\t%s\n' % (k3[0], k3[1], k3[2], status[k3]))
    sys.stdout.write('certs_build: %d shard files, %d proved rows; records by result: %s\n' % (len(files), len(rows), ' '.join('%s=%d' % x for x in sorted(counts.items()))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
