#!/usr/bin/env python3
"""nobinding - an object set binds no symbol to an exe address (L1 CONTRACT; the completeness proof of contract/facts' native table, root contract/factscheck).

    nobinding.py --obj obj.json --bind bind.json --out proof.txt

obj.json / bind.json: the facts and the binding (bind.py) of the objects of the roots that contract/facts leaves out of its native set because they depend on it.
Such an object must contribute no row to native.tsv: a bound symbol (a name an object defines or references that N1 gave an exe address) is an error listing every
(object, symbol, address), exit 1, and no proof file.  The proof `<N> objects, 0 symbols bound` is the root's output: an empty exclusion is a table that is complete by
proof, not by omission.  Section entries of such objects (sympair, ...) are not a fault by themselves: the compiler plugins emit them for every native function; only a
binding to an exe address would put a row in the table."""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store


def judge(obj, bind):
    """-> (number of objects, [(object, symbol, address)] bound, sorted)."""
    return len({k[0] for k in obj}), sorted((o, s, a) for (o, s), a in bind['sym'].items())


def main():
    ap = argparse.ArgumentParser()
    for n in ('obj', 'bind', 'out'):
        ap.add_argument('--' + n, required=True)
    a = ap.parse_args()
    n, bound = judge(store.load(a.obj, 'obj'), store.load(a.bind, 'bind'))
    if bound:
        print('nobinding: %d symbols of excluded objects are bound to exe addresses:\n%s' % (len(bound), '\n'.join('%s %s %08x' % b for b in bound[:50])), file=sys.stderr)
        sys.exit(1)
    with open(a.out, 'w', newline='\n') as fh:
        fh.write('%d objects, 0 symbols bound\n' % n)
    print('excluded objects %d, bound 0' % n)


if __name__ == '__main__':
    main()
