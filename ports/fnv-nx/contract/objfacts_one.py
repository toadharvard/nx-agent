#!/usr/bin/env python3
"""objfacts_one - the facts of ONE native object (per-object stage of objfacts; L1 CONTRACT, L4 stage sandbox).

    objfacts_one.py --object <one .o> --vft vft.json --addrmap <addrmap.tsv> --out <X.obj.json>

Exactly the per-function tuples and the `(object, 'ztv' | 'sympair' | 'vtpair' | 'n3')` entries that objfacts.py computes for this object (same
code: objfacts.scan_object), keyed by (object basename, address) as there.  Output: store kind `objone`
(the encoding of kind `obj`).  Inputs: the object, the vftable address list (exefacts.py --vft-out: a sorted JSON list, small, so the
stage does not load the whole fact base), the address-map rows of this object.
objfacts_merge.py unions the per-object files into obj.json.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import objfacts, store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--object', required=True)
    ap.add_argument('--vft', required=True)
    ap.add_argument('--addrmap', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    import json
    vft = set(json.load(open(a.vft)))
    objfacts.init_disasm()
    ob = os.path.basename(a.object)
    out = objfacts.scan_object(a.object, objfacts.load_addrmap_tsv(a.addrmap, ob), vft)
    store.save(a.out, 'objone', out)


if __name__ == '__main__':
    main()
