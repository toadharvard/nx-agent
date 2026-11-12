#!/usr/bin/env python3
"""Aggregator of the shape-prover shards: certs.tsv, the file proof/formgate.py reads (schema: the README of the artifacts branch l3b-lift/certs).

    python3 -I certs_build.py --id                                  the model id (first 12 hex of the pinned IR sha256)
    python3 -I certs_build.py --shards PATH [PATH ...] --out certs.tsv [--report FILE]
    python3 -I certs_build.py --coverage SHAPES.tsv --shards PATH [PATH ...] [--out MISSING]   global coverage: every int key with a Form has one decided record at the current (shape_sha, IR sha); exit 1 and the list when keys are MISSING
    python3 -I certs_build.py --queue SHAPES.tsv [--shards PATH ...] --out QUEUE   the encodings that still need a proof (see below); prints `queue <id> <n>`
PATH = a shapeprove_<K>_of_<N>.jsonl file or a directory searched (sorted, recursively) for such files.

CERTIFICATE KEY RULE (one rule, per encoding, independent of the shard, the run and every other encoding): a record is a valid certificate of a
shape iff ALL THREE match
    shape key   = the key of the record's shape,
    shape_sha   = symform.shape_sha(key) = the sha256 of the symbolic Form that the CURRENT DSL builds for that shape (record field dsl_sha),
    IR sha256   = the pinned x86_proof.ir sha256 (proof/sail_pins.txt; record field model_sha)
and result == proved.  Nothing else enters: no run id, no hash of the DSL sources.  A DSL edit therefore voids exactly the certificates of the shapes whose
Form changed (their shape_sha differs); a record that does not match is STALE: it is ignored (counted in the output line), it is never a refusal, it can
not become a row.  proof/formgate.py applies the same test to the rows (the same symform.shape_sha), so a row that went stale after it was written is
refused there too.
Only proved shapes get a row (one row per proved shape: formgate counts nothing else).  The run is REFUSED (exit 2, no output file) when the records
are contradictory: the same current (shape, shape_sha, IR sha) is `proved` in one record and FAILED(...) in another.
Row: shape_key, family, dsl_sha (= symform.shape_sha(shape)), model_sha, `sail`, `proved`.
(The link between the symbolic Form and the lifter's concrete Forms is proof/symcheck.py, over every instance of the image; it is not part of a certificate.)
Rows are unique and sorted; the output depends on the set of records only (not on shard order, file names or time).  Needs capstone; no z3, no clock.

--queue: the shapes of SHAPES.tsv (family int, a symbolic Form exists) that have NO valid certificate in the shards, sorted; the farm proves exactly these
(farm/farm_shapeprove.sh).  The queue id is the first 12 hex of sha256 over `key TAB shape_sha` of the queue lines: the same queue, the same id."""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT)
from proof import prove, shape, symform  # noqa: E402

HEADER = '# shape_key\tfamily\tdsl_sha\tmodel_sha\treference\tresult\n'


class Refused(Exception):
    """a shard record does not belong to the current code / pinned model"""


def pinned_model():
    with open(os.path.join(HERE, 'sail_pins.txt')) as fh:
        for ln in fh:
            f = ln.split()
            if len(f) == 4 and f[0] == 'x86_proof.ir':
                return f[3]
    raise Refused('no x86_proof.ir pin in sail_pins.txt')


def run_id():
    return pinned_model()[:12]


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


_SHA = {}


def current_sha(key, cache=_SHA):
    """symform.shape_sha(key) of the current DSL, None when the shape has no symbolic Form"""
    if key not in cache:
        try:
            cache[key] = symform.shape_sha(key)
        except (KeyError, AssertionError, symform.SymbolicBranch, symform.FieldDecodeError, shape.ShapeError):
            cache[key] = None
    return cache[key]


def scan(files):
    """-> (proved {key: (dsl_sha, ...)} valid certificates, counts {result or 'stale-dsl'/'stale-model': n}, status {(key, dsl, model): best result}).
    Records that do not match (shape, current shape_sha, pinned IR) are counted stale and ignored.  Raises Refused on a contradiction."""
    model = pinned_model()
    with open(os.path.join(HERE, 'formgate_inputs.json')) as fh:
        if json.load(fh)['model_sha256'] != model:
            raise Refused('formgate_inputs.json model_sha256 differs from the x86_proof.ir pin of sail_pins.txt')
    proved, counts, status = {}, {}, {}
    for f, i, r in records(files):
        where = '%s:%d' % (f, i)
        res = r.get('result')
        if res in ('no-form', 'builder-branches-on-field', 'field-decode', 'decode'):
            counts[res] = counts.get(res, 0) + 1
            continue                                            # no obligation ran: no Form, no model sha
        key = r['shape']
        if r.get('model_sha') != model:
            counts['stale-model'] = counts.get('stale-model', 0) + 1
            continue
        if r.get('obligations') != list(prove.OBLIGATIONS):          # a record of a prover that did not check DEF0 (every undefined flag = 0) certifies less than this one
            counts['stale-obligations'] = counts.get('stale-obligations', 0) + 1
            continue
        if r.get('dsl_sha') is None or r.get('dsl_sha') != current_sha(key):
            counts['stale-dsl'] = counts.get('stale-dsl', 0) + 1
            continue
        counts[res] = counts.get(res, 0) + 1
        k3 = (key, r['dsl_sha'], model)
        ok = res == 'proved'
        if res.startswith('FAILED') and status.get(k3) == 'proved' or ok and status.get(k3, '').startswith('FAILED'):
            raise Refused('%s: %s is proved in one record and %s in another' % (where, key, res if not ok else status[k3]))
        if ok or k3 not in status:
            status[k3] = res
        if ok:
            proved[key] = r['dsl_sha']
    return proved, counts, status


def build(files):
    """-> (sorted rows, counts of results, notes).  Raises Refused."""
    model = pinned_model()
    md = shape.decoder()
    proved, counts, status = scan(files)
    rows = {}
    for key, dsl in proved.items():
        rep = shape.rep_bytes(key)
        ins = list(md.disasm(rep, shape.ADDR))
        if len(ins) != 1 or ins[0].size != len(rep) or shape.shape_key(ins[0]) != key:
            raise Refused('the representative of %s is not the shape' % key)
        rows[key] = '%s\t%s\t%s\t%s\tsail\tproved\n' % (key, shape.family(ins[0]), dsl, model)
    return [rows[k] for k in sorted(rows)], counts, status


def forms_of(shapes_path):
    """{shape key: current shape_sha}: the integer-family shapes of the shapes file that have a symbolic Form (the set the farms must cover)"""
    md = shape.decoder()
    out = {}
    with open(shapes_path) as fh:
        for ln in fh:
            if not ln.strip() or ln[0] == '#':
                continue
            k = ln.split()[0]
            ins = list(md.disasm(shape.rep_bytes(k), shape.ADDR))
            if len(ins) != 1 or ins[0].size != len(shape.rep_bytes(k)):
                continue                                        # shapeprove reports it as `decode`
            try:
                ck = shape.shape_key(ins[0])
                fam = shape.family(ins[0])
            except shape.ShapeError:
                continue
            d = current_sha(ck)
            if fam == 'int' and d is not None:
                out[ck] = d
    return out


def queue(shapes_path, files):
    """-> (queue id, [(key, shape_sha)] sorted): integer-family shapes of the shapes file with a symbolic Form and no valid certificate in the shards"""
    proved, _, _ = scan(files)
    q = sorted((k, d) for k, d in forms_of(shapes_path).items() if proved.get(k) != d)
    h = hashlib.sha256(''.join('%s\t%s\n' % x for x in q).encode()).hexdigest()[:12]
    return h, q


def coverage(shapes_path, files):
    """GLOBAL coverage of the farm run, independent of how any farm split its queue: every shape key with a Form must have EXACTLY ONE decided result at (key, current shape_sha, pinned IR sha)
    (several records of the same result are duplicates; two different results are `Refused` by scan).  -> (total, {result: n}, [keys with no record], duplicates)"""
    model = pinned_model()
    want = forms_of(shapes_path)
    seen = {}
    for f, i, r in records(files):
        if r.get('shape') in want and r.get('model_sha') == model and r.get('dsl_sha') == want[r['shape']] and r.get('result') not in ('no-form', 'builder-branches-on-field', 'field-decode', 'decode'):
            seen.setdefault(r['shape'], []).append(r['result'])
    scan(files)                                                       # contradictions are refused
    res = {}
    for k, v in seen.items():
        for x in set(v):
            res[x] = res.get(x, 0) + 1
    return len(want), res, sorted(k for k in want if k not in seen), sum(len(v) - 1 for v in seen.values())


def main(argv):
    ap = argparse.ArgumentParser(prog='certs_build')
    ap.add_argument('--id', action='store_true')
    ap.add_argument('--shards', nargs='+')
    ap.add_argument('--out')
    ap.add_argument('--queue')
    ap.add_argument('--report')
    ap.add_argument('--coverage')
    a = ap.parse_args(argv)
    if a.id:
        sys.stdout.write(run_id() + '\n')
        return 0
    if a.coverage:
        try:
            total, res, missing, dups = coverage(a.coverage, shard_files(a.shards or []))
        except Refused as e:
            sys.stderr.write('certs_build: REFUSED: %s\n' % e)
            return 2
        sys.stdout.write('coverage: %d keys with a Form; decided %d (%s); duplicate records %d; MISSING %d\n' % (total, total - len(missing), ' '.join('%s=%d' % x for x in sorted(res.items())), dups, len(missing)))
        if a.out:
            with open(a.out, 'w', newline='\n') as fh:
                fh.writelines(k + '\n' for k in missing)
        return 1 if missing else 0
    if a.queue:
        if not a.out:
            sys.stderr.write('certs_build: --queue SHAPES --out FILE\n')
            return 2
        try:
            qid, q = queue(a.queue, shard_files(a.shards or []))
        except Refused as e:
            sys.stderr.write('certs_build: REFUSED: %s\n' % e)
            return 2
        with open(a.out, 'w', newline='\n') as fh:
            fh.writelines(k + '\n' for k, _ in q)
        sys.stdout.write('queue %s %d\n' % (qid, len(q)))
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
