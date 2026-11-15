#!/usr/bin/env python3
"""nativerows - the native bodies bound to exe code addresses: the rows of rt/dispatch.c's table (L1 CONTRACT; one source, the facts bind.py and bodysel.py already hold).

    nativerows.py --obj obj.json --bind bind.json --bodysel bodysel.tsv --out native.tsv      (prints counts only)

native.tsv: `address(8 hex)<TAB>symbol`, sorted by address, one row per exe address whose body is NATIVE (bodysel.tsv: verdict native).  The symbol is the one a native
root DEFINES for that address: the rows of `.fnvnx.sympair` marked `def` (obj.json), with the address bind.json gave it (the compiler plugin's MSVC name through the N1
certificate).  lift/tools/dispgen.py --prefix fnvnx_native --allow-empty turns the rows into the table `fnvnx_native_disp_table`, which fnvnx_dispatch consults first.
RULE (total: every native address is judged): exactly one defined symbol of the objects carries the address -> its row.  None (a native verdict with no definition
in the objects) or several different symbols (two names for one exe address, e.g. both constructor variants) is an ERROR listing every such address: the table has
no rule to choose between bodies, and a guess would be a heuristic.  A symbol defined in several objects (inline functions) counts once.
The output is a function of the three input files only (rows sorted; no clock, no path)."""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

FUNC_KINDS = ('func', 'ctor.c1', 'ctor.c2', 'dtor.d2', 'dtor.d1', 'dtor.d0', 'dtor.sdd')


def native_rows(obj, bind_sym, bodysel):
    """obj: store `obj` ({(object, section kind): rows, ...}); bind_sym: bind's {(object, symbol): address}; bodysel: {address: verdict} -> [(address, symbol)]."""
    defs = {(ob, r[0]) for (ob, k), rows in obj.items() if k == 'sympair' for r in rows if r[3] == 'def' and r[2] in FUNC_KINDS}
    by = {}
    for (ob, sym), a in bind_sym.items():
        if (ob, sym) in defs:
            by.setdefault(a, set()).add(sym)
    rows, err = [], []
    for a in sorted(a for a, v in bodysel.items() if v == 'native'):
        s = sorted(by.get(a, ()))
        if len(s) == 1:
            rows.append((a, s[0]))
        else:
            err.append('%08x: %s' % (a, ' '.join(s) if s else 'native verdict, no defined symbol'))
    if err:
        raise ValueError('%d native addresses without exactly one defined symbol:\n%s' % (len(err), '\n'.join(err)))
    return rows


def read_bodysel(path):
    out = {}
    for ln in open(path):
        p = ln.rstrip('\n').split('\t')
        if len(p) >= 2 and p[0][:1] != '#':
            out[int(p[0], 16)] = p[1]
    return out


def main():
    ap = argparse.ArgumentParser()
    for n in ('obj', 'bind', 'bodysel', 'out'):
        ap.add_argument('--' + n, required=True)
    a = ap.parse_args()
    try:
        rows = native_rows(store.load(a.obj, 'obj'), store.load(a.bind, 'bind')['sym'], read_bodysel(a.bodysel))
    except ValueError as e:
        print('nativerows: ' + str(e), file=sys.stderr)
        sys.exit(1)
    with open(a.out, 'w', newline='\n') as fh:
        fh.write(''.join('%08x\t%s\n' % r for r in rows))
    print('native rows %d' % len(rows))


if __name__ == '__main__':
    main()
