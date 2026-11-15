#!/usr/bin/env python3
"""Test of the game's compiler's `long double` (msvcmodel.patch, MSVCMODEL1): Visual C++ 2008 has long double as binary64, a type of
its own that mangles as 'O' (double: 'N'). ldbl/ldbl.cpp is compiled through the recipe (pinned compiler external, the product plugins,
plugbuild.py); its static_asserts hold sizeof/alignof, LDBL_MANT_DIG == 53, __LDBL_MAX__ == __DBL_MAX__ and the distinctness from double, so
a compiler that still has a 128-bit long double fails to compile. The MSVC names of the functions are read from the names plugin's
.fnvnx.sympair rows: ?ld@@YAONON@Z (long double 'O', double 'N'), as cl writes them. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_ldbl')


def main():
    td = tempfile.mkdtemp(prefix='test-ldbl-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        obj = os.path.join(td, 'ldbl.o')
        r = pb.compile(os.path.join(HERE, 'ldbl', 'ldbl.cpp'), obj, so)
        check('compiles (long double is 8 bytes, binary64, apart from double)', r.returncode == 0,
              '\n'.join(l for l in r.stderr.splitlines() if 'warning' not in l and 'note:' not in l)[:3000])
        if r.returncode == 0:
            rows = {l.split('\t')[0]: l.split('\t')[1] for l in pb.section(obj, '.fnvnx.sympair').splitlines()}
            check('long double mangles as O: ?ld@@YAONON@Z', rows.get('_Z2lded') == '?ld@@YAONON@Z',
                  rows)
            check('double still mangles as N: ?dd@@YANN@Z', rows.get('_Z2ddd') == '?dd@@YANN@Z', rows)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
