#!/usr/bin/env python3
"""objfacts_merge - union of the per-object files of objfacts_one.py into obj.json (merge stage of objfacts).

    objfacts_merge.py --inputs <dir of *.obj.json> --out obj.json

Reads the files in sorted file-name order and unions their entries; the result is byte-identical to objfacts.py's
output for the same objects (store kind `obj` is canonical: no order of the inputs reaches the bytes).  A key present
in two inputs is an error: exit status 1, the key named.  Prints the same summary line as objfacts.py.
"""
import argparse, glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store


def merge(paths):
    out, where = {}, {}
    for p in sorted(paths):
        for k, v in store.load(p, 'objone').items():
            if k in out:
                raise ValueError('duplicate key %s in %s and %s' % ('\t'.join(map(str, k)), where[k], p))
            out[k] = v
            where[k] = p
    return out


def summary(out):
    fns = [v for k, v in out.items() if store.is_function_key(k)]
    return 'native functions with an exe address: %d; with vtable values: %d; vtable sites: %d (not stored plainly: %d)' % (
        len(fns), sum(1 for v in fns if v[0]), sum(len(v[0]) for v in fns), sum(1 for v in fns for s in v[0] if s[3] is None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inputs', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    try:
        out = merge(sorted(glob.glob(os.path.join(a.inputs, '*.obj.json'))))
    except ValueError as e:
        print('objfacts_merge: ' + str(e), file=sys.stderr)
        sys.exit(1)
    store.save(a.out, 'obj', out)
    print(summary(out))


if __name__ == '__main__':
    main()
