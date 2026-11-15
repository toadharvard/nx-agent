#!/usr/bin/env python3
"""Test of the game's compiler's `long double` (msvcmodel.patch, MSVCMODEL1): Visual C++ 2008 has long double as binary64, a type of
its own that mangles as 'O' (double: 'N'). ldbl/ldbl.cpp is compiled through the recipe (pinned compiler external, the product plugins,
plugbuild.py); its static_asserts hold sizeof/alignof, LDBL_MANT_DIG == 53, __LDBL_MAX__ == __DBL_MAX__ and the distinctness from double, so
a compiler that still has a 128-bit long double fails to compile. The MSVC names of the functions are read from the names plugin's
.fnvnx.sympair rows: ?ld@@YAONON@Z (long double 'O', double 'N'), as cl writes them. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_ldbl')


def predefines(cxx):
    """{name: value} of the macros the pinned compiler predefines (-dM -E of an empty C++ unit with the data-model flag of the root): a query of the
    compiler the recipe builds with, whose list is the complete one by construction (nothing is named here)"""
    r = subprocess.run([cxx, '-mabi=ilp32', '-x', 'c++', '-dM', '-E', '/dev/null'], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('the pinned compiler: ' + r.stderr[-500:])
    out = {}
    for l in r.stdout.splitlines():
        m = re.match(r'#define (\w+) ?(.*)$', l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def number(v):
    """the value of a float-constant macro without its spelling: casts and the type suffix removed (`((double)1.79e+308L)` and `1.79e+308L`)"""
    v = re.sub(r'\(\((?:long )?double\)', '(', v)
    v = re.sub(r'(?<=[0-9.])[LlFf]\b', '', v)
    return re.sub(r'[()\s]', '', v)


def main():
    td = tempfile.mkdtemp(prefix='test-ldbl-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        obj = os.path.join(td, 'ldbl.o')
        r = pb.compile(os.path.join(HERE, 'ldbl', 'ldbl.cpp'), obj, so, native=True)
        check('compiles (long double is 8 bytes, binary64, apart from double)', r.returncode == 0,
              '\n'.join(l for l in r.stderr.splitlines() if 'warning' not in l and 'note:' not in l)[:3000])
        if r.returncode == 0:
            rows = {l.split('\t')[0]: l.split('\t')[1] for l in pb.section(obj, '.fnvnx.sympair').splitlines()}
            check('long double mangles as O: ?ld@@YAONON@Z', rows.get('_Z2lded') == '?ld@@YAONON@Z',
                  rows)
            check('double still mangles as N: ?dd@@YANN@Z', rows.get('_Z2ddd') == '?dd@@YANN@Z', rows)
            check('the Itanium names keep long double (e) apart from double (d): two overloads, two MSVC names',
                  rows.get('_Z2ove') == '?ov@@YAXO@Z' and rows.get('_Z2ovd') == '?ov@@YAXN@Z', rows)
            # the mode: long double arithmetic is the hardware double (DFmode): d registers, no call of a TFmode entry of libgcc
            dis = pb.objdump(obj)
            body = '\n'.join(dis.get('_Z7l_arithee', []))
            check('long double + - * / are double instructions (DFmode: fadd/fsub/fmul/fdiv on d registers)',
                  all(re.search(r'\b%s\s+d\d' % op, body) for op in ('fadd', 'fsub', 'fmul', 'fdiv')), body[:1500])
            tf = {l.strip() for l in open(os.path.join(HERE, 'ldbl', 'libgcc_tf.txt'))} - {''}
            und = pb.undefined(obj)
            check('libgcc_tf.txt is the pinned libgcc\'s TF family (34 symbols, gen_libgcc_tf.py)', len(tf) == 34, len(tf))
            check('no undefined symbol of the object is a libgcc TFmode soft-float entry (arithmetic, comparisons, conversions, varargs, complex)',
                  not (und & tf), sorted(und & tf))
            ext = json.load(open(os.path.join(pb.REAL, pb.T + 'verify/guest_compiler.json')))
            co = os.path.join(pb.STORE, 'external', ext['external']['commit'] + '.co')
            pd = predefines(os.path.join(co, ext['cxx']))
            dbl = {k[len('__DBL_'):]: v for k, v in pd.items() if k.startswith('__DBL_')}
            ldbl = {k[len('__LDBL_'):]: v for k, v in pd.items() if k.startswith('__LDBL_')}
            check('every __LDBL_*__ predefine has its __DBL_*__ twin and the reverse (%d names)' % len(dbl), set(ldbl) == set(dbl),
                  sorted(set(ldbl) ^ set(dbl)))
            check('every __LDBL_*__ equals its __DBL_*__ counterpart (%d of %d)' % (len(dbl), len(dbl)),
                  all(number(ldbl[k]) == number(dbl[k]) for k in dbl if k in ldbl),
                  {k: (dbl[k], ldbl[k]) for k in dbl if k in ldbl and number(ldbl[k]) != number(dbl[k])})
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
