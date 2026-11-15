#!/usr/bin/env python3
"""objfacts_dir - obj.json of a directory tree of native objects (L1 CONTRACT; the recipe stage of contract/facts).

    objfacts_dir.py --objects <dir of <root>/<unit>.o> --out obj.json

The same per-object code as objfacts.py / objfacts_one.py (objfacts.scan_object), over every `*.o` under the directory, in sorted path order.  The object name of
an entry is the object's path relative to the directory (a unit path may repeat across roots; a path never does), so two roots never collide.  The objects of a
recipe root carry no exe address map (the address of a function comes from N1 through bind.py, which reads the `.fnvnx.sympair` rows), so the per-function
tuples are empty and the vftable set is empty: this tool collects the section entries (ztv, sympair, vtpair, n3), which are what bind.py and nativerows.py read.
Output: store kind `obj`.  A key present twice is an error (exit 1).  The output depends on the objects' bytes and relative paths only."""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objfacts, store


def collect(root):
    out = {}
    for d, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f.endswith('.o'):
                p = os.path.join(d, f)
                rel = os.path.relpath(p, root).replace(os.sep, '/')
                for k, v in objfacts.scan_object(p, {}, set()).items():
                    k = (rel,) + tuple(k[1:])
                    if k in out:
                        raise ValueError('duplicate key %s' % '\t'.join(map(str, k)))
                    out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--objects', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    objfacts.init_disasm()
    try:
        out = collect(a.objects)
    except ValueError as e:
        print('objfacts_dir: ' + str(e), file=sys.stderr)
        sys.exit(1)
    store.save(a.out, 'obj', out)
    print('objects with section entries: %d' % len({k[0] for k in out}))


if __name__ == '__main__':
    main()
