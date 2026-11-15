#!/usr/bin/env python3
"""The one tool of the `generated` steps of the product root lift/pack (its declared `lifted_by`, recipe: every C++ unit of a lifted root is an output of a step of that one tool):
    packstage.py copy ...      packcopy.py   the bodies u/*.cpp of the external
    packstage.py imp ...       impgen.py     imp.cpp and the import rows from the loader's BRIDGE_TSV
    packstage.py entries ...   packentries.py entries.tsv = the external's rows + the import rows + table_id
    packstage.py disp ...      dispgen.py    disp.S from entries.tsv
Each name runs the named tool's own main with the remaining arguments, unchanged."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the tool root: a tool imports its siblings from its own root (a stage runs it under python3 -I)


def main(argv):
    if not argv or argv[0] not in ('copy', 'imp', 'entries', 'disp') or argv[0] in ('-h', '--help'):
        sys.stderr.write(__doc__ + '\n'); return 2
    sub, rest = argv[0], argv[1:]
    if sub == 'copy':
        import cli, packcopy
        (a, b), _ = cli.parse('usage: packstage.py copy PACK OUT', rest, (2, 2)); packcopy.main(a, b); return 0
    if sub == 'entries':
        import cli, packentries
        (a, b, c, d), _ = cli.parse('usage: packstage.py entries PACK_ENTRIES IMP_ENTRIES BRIDGE_TSV OUT', rest, (4, 4)); packentries.main(a, b, c, d); return 0
    if sub == 'disp':
        import dispgen
        dispgen.main(rest); return 0
    import impgen
    sys.argv = ['impgen.py'] + rest; impgen.main(); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
