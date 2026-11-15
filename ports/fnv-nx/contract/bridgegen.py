#!/usr/bin/env python3
"""bridgegen - the rows table (stage A of the bridge table): bridge.tsv without any symbol table (contract/bridgetable.derive with syms=None).

    bridgegen.py --names sys_names.tsv --specs DIR --name-table msvcrt_names.tsv --va msvcrt_va.tsv --out bridge.tsv [--extra rows.tsv ...]

DIR holds the Wine 9.0 `<dll>.spec` files (dlls/<dll>/<dll>.spec copied by name); `--extra` files are 8-column rows of other owners (COM interfaces,
the d3d9 exports), each replacing the derived row of the same (dll, name).  The output is the 8-column table with the header line iat.py reads;
a row binds by rule (sym, data, lookup, l3), so the table does not depend on the product's objects and its sha256 is the table_id of the keys.
A violated precondition (a name missing from its spec, a stub, a forward that cannot be followed, a duplicate in the extra rows) is a stage error."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridgetable  # noqa: E402


def read(path):
    with open(path, encoding='latin1') as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', required=True)
    ap.add_argument('--specs', required=True)
    ap.add_argument('--name-table', required=True)
    ap.add_argument('--va', required=True)
    ap.add_argument('--extra', action='append', default=[])
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    rows, errors, _ = bridgetable.derive(read(a.names), lambda d: read(os.path.join(a.specs, d + '.spec')), None,
                                         bridgetable.read_name_table(read(a.name_table)), [read(p) for p in a.extra],
                                         bridgetable.read_va(read(a.va)))
    if errors:
        sys.exit('bridgegen: %d errors, first: %s' % (len(errors), errors[0]))
    with open(a.out, 'w', newline='\n') as fh:
        fh.write('dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n')
        for r in rows:
            fh.write('\t'.join(r) + '\n')
    print('%d rows' % len(rows))


if __name__ == '__main__':
    main()
