#!/usr/bin/env python3
"""Test of lift/tools/exitgen.py's join of native symbols to the objects' own .fnvnx.defdisp rows (the descriptor is never an input of the rows).
The objects are the recipe's (plugbuild.py: sdd + names + dispatch plugins, the product flags); the fixture is exitgen_defdisp/fixture.cpp.
Checked on the real objects, through the command line: one well-formed set of names generates the exits (exit 0); a name with no defdisp row, a name whose
row is malformed (the object's section rewritten with objcopy) and a name with two differing rows each fail with exit != 0 and the error NAMES the name; every
bad name is listed in one run; byte-identical rows of two objects count as one row; nothing is generated on an error.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'lift', 'tools'))
import plugbuild as pb  # noqa: E402
import exitgen  # noqa: E402

check = pb.Checks('test_exitgen_defdisp')
FIX = os.path.join(HERE, 'exitgen_defdisp', 'fixture.cpp')
EXITGEN = os.path.join(HERE, '..', '..', 'lift', 'tools', 'exitgen.py')
GOOD = ['ok_i', 'ok_s8', 'ok_big', 'ok_d']


def run(td, rows, objs, out='exits.cc'):
    rf = os.path.join(td, 'rows.tsv')
    open(rf, 'w').write(''.join(rows))
    o = os.path.join(td, out)
    if os.path.exists(o):
        os.remove(o)
    r = subprocess.run([sys.executable, '-I', EXITGEN, '--rows', rf, '--out', o] + [x for ob in objs for x in ('--obj', ob)], capture_output=True, text=True, env={})
    return r, os.path.exists(o)


def rewrite(td, obj, name, rows):
    """a copy of obj whose .fnvnx.defdisp section holds exactly ROWS (objcopy: rewriting a section, not compiling)"""
    sec = os.path.join(td, name + '.sec')
    open(sec, 'w').write(''.join(r + '\n' for r in rows))
    out = os.path.join(td, name + '.o')
    r = subprocess.run(['aarch64-linux-gnu-objcopy', '--update-section', '.fnvnx.defdisp=' + sec, obj, out], capture_output=True, text=True)
    check('objcopy %s' % name, r.returncode == 0, r.stderr)
    return out


def main():
    td = tempfile.mkdtemp(prefix='test-exitgen-defdisp-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        obj = os.path.join(td, 'f.o')
        r = pb.compile(FIX, obj, so)
        check('the fixture compiles through the recipe', r.returncode == 0, r.stderr[:2000])
        if r.returncode:
            return check.done()
        dd = exitgen.defdisp_table(exitgen.read_defdisp(obj))
        check('every fixture function has exactly one defdisp row', all(len(dd.get(n, ())) == 1 for n in GOOD), repr(dd))
        want = {'ok_i': 'stack:i:i', 'ok_s8': 'stack:a8Rg:i', 'ok_big': 'stack:a12Hg:i', 'ok_d': 'stack:d:d,i'}
        check('the rows are the grammar a<N><X><Y> of the definitions\' own types', all(dd[n] == {d} for n, d in want.items()), repr(dd))
        rows = ['%08x\t%s\tcaller\n' % (0x401000 + 16 * k, n) for k, n in enumerate(GOOD)]
        r, made = run(td, rows, [obj])
        check('a well-formed set of names generates the exits, exit 0', r.returncode == 0 and made and 'X_00401000' in open(os.path.join(td, 'exits.cc')).read(), r.stderr)
        r, made = run(td, rows + ['00402000\tno_such_function\tcaller\n'], [obj])
        check('a name with no defdisp row: exit != 0, the name in the error, nothing generated',
              r.returncode != 0 and 'no_such_function' in r.stderr and 'no .fnvnx.defdisp row' in r.stderr and not made, r.stderr)
        bad = rewrite(td, obj, 'bad', ['ok_i stack:a8Qg:i', 'ok_s8 stack:a8Rg:i', 'ok_big stack:a12Hg:i', 'ok_d stack:d:d,i'])
        r, made = run(td, rows, [bad])
        check('a malformed descriptor: exit != 0, the name and the reason in the error, nothing generated',
              r.returncode != 0 and 'ok_i' in r.stderr and 'bad return class' in r.stderr and not made, r.stderr)
        worse = rewrite(td, obj, 'worse', ['ok_i'])
        r, made = run(td, rows, [worse])
        check('a section row that is not `name descriptor`: exit != 0, nothing generated', r.returncode != 0 and 'a row is' in r.stderr and not made, r.stderr)
        other = rewrite(td, obj, 'other', ['ok_i stack:i:q'])
        r, made = run(td, rows, [obj, other])
        check('two differing rows of one name: exit != 0, the name and both rows in the error', r.returncode != 0 and 'ok_i' in r.stderr and 'differing' in r.stderr
              and 'stack:i:i' in r.stderr and 'stack:i:q' in r.stderr and not made, r.stderr)
        r, made = run(td, rows, [obj, rewrite(td, obj, 'same', ['ok_i stack:i:i'])])
        check('byte-identical rows of two objects count as one row', r.returncode == 0 and made, r.stderr)
        r, made = run(td, rows + ['00402000\tno_row_a\tcaller\n', '00402010\tno_row_b\tcaller\n'], [bad])
        check('every bad name is listed in one run', all(x in r.stderr for x in ('ok_i', 'no_row_a', 'no_row_b')) and not made, r.stderr)
        r, made = run(td, ['00401000\tok_i\tstack:i:q\tcaller\n'], [obj])
        check('a carried descriptor that differs from the row: error', r.returncode != 0 and 'defdisp row says' in r.stderr and not made, r.stderr)
        r, made = run(td, ['00401000\tok_i\tstack:i:i\tcaller\n'], [obj])
        check('a carried descriptor equal to the row: accepted', r.returncode == 0 and made, r.stderr)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
