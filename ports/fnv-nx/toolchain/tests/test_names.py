#!/usr/bin/env python3
"""Test of toolchain/names/fnvnx_names.cc (NAMES1) against cl's own symbol table, built from the tracked source with the argv
of the recipe's plugin stage (plugbuild.py).

names/names.cpp is compiled by the recipe's guest compiler with the sdd and names plugins; the MSVC names of its rows (.fnvnx.sympair,
.fnvnx.vtpair, read in-process from the object) are compared with names/names.expect, the symbol table MSVC 2008 SP1 itself
wrote for the same file (recorded once: cl /nologo /c /EHsc /Od /GR on names.cpp, then the object's external symbols; no cl
and no Wine run here). Both directions are checked. What is left out is left out by class, each with its reason, and each
class's exact size is asserted, so a name that starts or stops matching fails the test:
  cl only   ??_8 (vbtables), ??4 (implicit assignment cl emits for an exported class), ??_G / ??_E of a class
            without a virtual destructor (cl's helpers for delete), vtordisp thunks ($4): the exe's own, no GCC twin
  unnamed   what the plugin itself marks `unnamed:<reason>` (GCC's trees do not hold what the name needs: see fnvnx_names.cc); the cl names it
            therefore has no row for are counted against those rows, never listed
The compiler is the guest compiler msvcmodel: size_t is `unsigned int` as for cl, so there is no size_t hole.
An unknown plugin argument is an error. No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_names')
FIX = os.path.join(HERE, 'names', 'names.cpp')

def cl_only(n):
    return (n.startswith(('??_8', '??4')) or '@@$4' in n
            or n in ('??_GMem@@QAEPAXI@Z', '??_EMem@@QAEPAXI@Z', '??_ELC@@QAEPAXI@Z', '??_GLC@@QAEPAXI@Z'))




def rows_of(obj, name):
    return [l.split('\t') for l in pb.section(obj, name).splitlines()]


def checks(obj):
    expect = {}
    for l in open(os.path.join(HERE, 'names', 'names.expect')):
        if not l.startswith('#') and l.strip():
            n, k = l.rstrip('\n').split('\t')
            expect.setdefault(n, set()).add(k)
    rows = rows_of(obj, '.fnvnx.sympair')
    vt = rows_of(obj, '.fnvnx.vtpair')
    defined = {s[0] for s in pb.elfobj.read(obj).symbols() if s[3] != 0 and s[1] in (1, 2) and s[0]}
    kinds = {}
    for sym, ms, kind, df in rows: kinds.setdefault(kind.split(':')[0], []).append((sym, ms, kind))
    named = {ms for sym, ms, kind, df in rows if ms and kind not in ('thunk',)}
    names_vt = {r[2] for r in vt if r[2] and not r[2].startswith('unnamed')}
    got = named | names_vt
    want = {n for n in expect if not cl_only(n)}
    aps = {'%s.fnvnx_ap%s' % (r[0], r[1]) for r in vt}
    norow = {d for d in defined - {r[0] for r in rows} - aps if not d.startswith('_ZTC')}     # construction tables: no twin
    check('every defined symbol of the object has a row (an address point: a .fnvnx.vtpair row)', not norow, sorted(norow))
    # emitted function symbols and rows, both directions: the functions the object defines with external or weak binding (a static or unnamed-namespace
    # function has internal linkage, which is not in the contract, and a comdat group's own alias `...C5` is local: neither is a name cl writes)
    st = pb.symbols_of(obj)
    emitted = {n for n, v in st.items() if v['type'] == 'FUNC' and v['bind'] in ('GLOBAL', 'WEAK') and v['sec'] not in (None, 'UND') and v['size'] > 0}
    rowsyms = {r[0] for r in rows}
    check('every function the object defines has a row', emitted <= rowsyms, sorted(emitted - rowsyms))
    ghost = sorted(r[0] for r in rows if r[3] == 'def' and r[2].split(':')[0] not in ('data', 'vtable') and r[0] not in emitted and r[0] in st
                   and st[r[0]]['type'] == 'FUNC')
    check('every row of a function is a function the object defines (or an undefined reference)', not ghost, ghost)
    # the plugin's own account of what it does not name: unnamed rows (not the destructor thunks of classes with virtual bases, which cl never has: cl_only)
    unnamed = [r for r in rows if r[2].startswith('unnamed') and 'thunk' not in r[2]]
    missing = want - got
    check('the cl names that have no row are exactly as many as the rows the plugin marks unnamed', len(missing) == len(unnamed),
          {'cl names without a row': sorted(missing), 'unnamed rows': [(r[0], r[2]) for r in unnamed]})
    extra = {ms for sym, ms, kind, df in rows if ms and kind not in ('thunk',)} - set(expect)
    # the names GCC's object has and cl's has not, by the class of the row: the deleting destructor (cl writes ??_G only where its own code needs it)
    # and the global operator new[]/delete[] (undefined references from the ??_E bodies)
    klass = {(ms, kind, df) for sym, ms, kind, df in rows if ms in extra}
    check('a name of the plugin that cl does not have belongs to the class of a deleting destructor or an undefined global new[]/delete[]',
          all(k == 'dtor.d0' or (k == 'func' and df == 'ref') for ms, k, df in klass), sorted(klass))
    by = {ms: kind for sym, ms, kind, df in rows}
    check('the vector deleting destructor of a primary table is ??_E', by.get('??_ED1@@UAEPAXI@Z') == 'dtor.sdd', by.get('??_ED1@@UAEPAXI@Z'))
    check('... of a secondary table is cl\'s adjustor thunk', by.get('??_ED1@@W7AEPAXI@Z') == 'dtor.sdd.thunk', by.get('??_ED1@@W7AEPAXI@Z'))
    check('a virtual base class: the complete destructor is ??_D', by.get('??_DVD@@QAEXXZ') == 'dtor.d1', by.get('??_DVD@@QAEXXZ'))
    internal = sorted(s for s, m, k in kinds.get('internal', []))
    check('a user-written member of a local class is internal for cl: every row of kind internal is a function or a static of a local class, and cl has no name for any',
          internal and all(n.startswith('_ZZ') for n in internal) and not [1 for s, m, k in kinds.get('internal', []) if m], internal)
    vtnames = {(r[0], r[1]): r[2] for r in vt}
    check('address points of a table group are cl\'s vftables', vtnames.get(('_ZTV2D1', '8')) == '??_7D1@@6BB1@@@'
          and '??_7D1@@6BB2@@@' in names_vt and '??_7D1@@6BB3@@@' in names_vt and '??_7V1@@6B0@@' in names_vt
          and '??_7VD@@6BVB@@@' in names_vt, sorted(vtnames.items()))
    check('every ??_7 of cl is an address point', {n for n in expect if n.startswith('??_7')} <= names_vt,
          sorted({n for n in expect if n.startswith('??_7')} - names_vt))


def main():
    td = tempfile.mkdtemp(prefix='test-names-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        obj = os.path.join(td, 't.o')
        r = pb.compile(FIX, obj, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('compiles', r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            checks(obj)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_names-mode=1'])
        check('a plugin argument is an error: the recipe fixes them', r.returncode != 0 and 'arguments must be' in r.stderr and 'fnvnx_names' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
