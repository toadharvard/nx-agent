#!/usr/bin/env python3
"""Test of toolchain/names/fnvnx_names.cc (NAMES1) against cl's own symbol table, built from the tracked source with the argv
of the recipe's plugin stage (plugbuild.py).

names/names.cpp is compiled by the target g++ with the sdd and names plugins; the MSVC names of its rows (.fnvnx.sympair,
.fnvnx.vtpair, read in-process from the object) are compared with names/names.expect, the symbol table MSVC 2008 SP1 itself
wrote for the same file (recorded once: cl /nologo /c /EHsc /Od /GR on names.cpp, then the object's external symbols; no cl
and no Wine run here). Both directions are checked. What is left out is left out by class, each with its reason, and each
class's exact size is asserted, so a name that starts or stops matching fails the test:
  cl only   ??_8 (vbtables), ??4 (implicit assignment cl emits for an exported class), ??_G / ??_E of a class
            without a virtual destructor (cl's helpers for delete), vtordisp thunks ($4): the exe's own, no GCC twin
  holes     HOLES below: what GCC's trees do not hold (see fnvnx_names.cc)
The distro target compiler has a 64-bit size_t under ILP32 (`unsigned long`), cl's is `unsigned int`: that hole is stated.
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

# name cl gives -> why the plugin cannot: each is a class the gate (link/gate_names.py) counts over the program
HOLES = {
    # size_t is `unsigned long` for aarch64 ILP32 GCC (K), `unsigned int` for cl (I)
    '??2@YAPAXI@Z': 'size_t', '??2Mem@@SAPAXI@Z': 'size_t', '??2Mem@@SAPAXIH@Z': 'size_t', '??_UMem@@SAPAXI@Z': 'size_t',
    # a function parameter and an array parameter inside a function TYPE (not a declaration)
    '?f8@@YAXP6AXH@ZP6AHUS@@PAVC@@@ZP6AXH@ZP6AXQAH@Z6Q6AXH@Z@Z': 'function type',
    # a static in a block below the function's outermost one
    '?b@?2??m@LS@@QAEHXZ@4HA': 'scope', '?e@?4??ls1@@YAHH@Z@4HA': 'scope', '?g@?7??ls1@@YAHH@Z@4HA': 'scope',
    '?h@?9??ls1@@YAHH@Z@4HA': 'scope',
}


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
    unnamed = sorted(k for s, m, k in kinds.get('unnamed', []))
    check('the only unnamed rows: 4 nested-scope statics, 4 destructor thunks of classes with virtual bases',
          unnamed == ['unnamed:destructor thunk in a class with virtual bases'] * 4 + ['unnamed:local scope number'] * 4, kinds.get('unnamed'))
    missing = want - got
    holes = HOLES
    check('cl names the plugin does not produce are exactly the stated holes', missing == set(holes),
          {'unexpected': sorted(missing - set(holes)), 'now matching': sorted(set(holes) - missing)})
    extra = got - set(expect)
    # the plugin's side of the size_t and function-type holes (4 + ::operator delete[] the SDD function calls + f8)
    check('names of the plugin that cl does not have are the same holes seen from GCC',
          sorted(extra) == sorted(['??2@YAPAXK@Z', '??2Mem@@SAPAXK@Z', '??2Mem@@SAPAXKH@Z', '??_UMem@@SAPAXK@Z', '??_U@YAPAXK@Z'] + [
                                   '?f8@@YAXP6AXH@ZP6AHUS@@PAVC@@@Z0P6AXPAH@Z5Q6AXH@Z@Z',
                                   # GCC's own: D1's ??_G (cl wrote only ??_E here), delete[] called by the ??_E bodies
                                   '??_GD1@@UAEPAXI@Z', '??_V@YAXPAX@Z']), sorted(extra))
    by = {ms: kind for sym, ms, kind, df in rows}
    check('the vector deleting destructor of a primary table is ??_E', by.get('??_ED1@@UAEPAXI@Z') == 'dtor.sdd', by.get('??_ED1@@UAEPAXI@Z'))
    check('... of a secondary table is cl\'s adjustor thunk', by.get('??_ED1@@W7AEPAXI@Z') == 'dtor.sdd.thunk', by.get('??_ED1@@W7AEPAXI@Z'))
    check('a virtual base class: the complete destructor is ??_D', by.get('??_DVD@@QAEXXZ') == 'dtor.d1', by.get('??_DVD@@QAEXXZ'))
    check('a user-written member of a local class is internal for cl',
          sorted(s for s, m, k in kinds.get('internal', [])) == ['_ZZ3ls1iEN1L2lmEv', '_ZZ3ls1iEN1L2lvEv', '_ZZZ3ls1iEN1L2lmEvE1q'],
          kinds.get('internal'))
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
        r = pb.compile(FIX, obj, so, flags=pb.PRODUCT_FLAGS + ['-O0', '-fkeep-inline-functions'])
        check('compiles', r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            checks(obj)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_names-mode=1'])
        check('an unknown plugin argument is an error', r.returncode != 0 and 'unknown plugin argument' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
