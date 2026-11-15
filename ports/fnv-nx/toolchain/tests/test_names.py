#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc
# reads: ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/gcc ports/fnv-nx/toolchain/layout/crossing_terms.inc
# reads: ports/fnv-nx/toolchain/layout/decltype_spell.inc ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json
# reads: ports/fnv-nx/toolchain/msvcmodel/build.sh ports/fnv-nx/toolchain/msvcmodel/msvcmodel.patch ports/fnv-nx/toolchain/names/fnvnx_names.cc
# reads: ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc
# reads: ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/toolchain/tests ports/fnv-nx/verify
"""Test of toolchain/names/fnvnx_names.cc (NAMES1) against cl's own symbol table, built from the tracked source with the argv
of the recipe's plugin stage (plugbuild.py).

names/names.cpp is compiled by the recipe's guest compiler with the sdd and names plugins; the MSVC names of its rows (.fnvnx.sympair,
.fnvnx.vtpair, read in-process from the object) are compared with names/names.expect, the symbol table MSVC 2008 SP1 itself
wrote for the same file (recorded once: cl /nologo /c /EHsc /Od /GR on names.cpp, then the object's external symbols; no cl
and no Wine run here). Both directions are checked. What is left out is left out by class, each with its reason, and each
class's exact size is asserted, so a name that starts or stops matching fails the test:
  cl only   ??_8 (vbtables), ??4 (implicit assignment cl emits for an exported class), ??_G / ??_E of a class
            without a virtual destructor (cl's helpers for delete), vtordisp thunks ($4): the exe's own, no GCC twin
  nodecor   what the plugin itself marks `nodecor:<reason>` (GCC's trees do not hold what the name needs: see fnvnx_names.cc); the cl names it
            therefore has no row for are counted against those rows, never listed
The compiler is the guest compiler msvcmodel: size_t is `unsigned int` as for cl, so there is no size_t hole.
An unknown plugin argument is an error. No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_names')
FIX = os.path.join(HERE, 'names', 'names.cpp')

def cl_only(n, obj):
    """cl's names that have no GCC twin, by structure: vbtables (??_8), the implicit assignment cl writes for an exported class (??4), vtordisp thunks ($4
    in the encoded function type), and the ??_G / ??_E helpers of a class whose table in the object has no destructor slot (the object's own table:
    a `.fnvnx_sdd` entry is the slot of the virtual destructor; cl writes the helpers for delete, GCC has no function for such a class)"""
    if n.startswith(('??_8', '??4')) or '@@$4' in n:
        return True
    m = re.match(r'\?\?_[GE]([A-Za-z_0-9]+)@@', n)
    if not m:
        return False
    st = pb.symbols_of(obj)
    tv = '_ZTV%d%s' % (len(m.group(1)), m.group(1))
    return tv not in st or not any(w.endswith('.fnvnx_sdd') for w in pb.words(obj, tv))


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
    names_vt = {r[2] for r in vt if r[2] and not r[2].startswith('nodecor:')}
    got = named | names_vt
    want = {n for n in expect if not cl_only(n, obj)}
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
    unnamed = [r for r in rows if r[2].startswith('nodecor:') and r[2] != 'nodecor:vtordisp']
    reasons = {}
    for r in unnamed: reasons.setdefault(r[2], []).append(r[0])
    check('a symbol with no decoration source carries a typed reason: nested-block statics and classes `localscope`, a function-type parameter whose declared form is lost `fnparmform`',
          set(reasons) == {'nodecor:localscope', 'nodecor:fnparmform'} and len(reasons['nodecor:localscope']) == 4, reasons)
    vtor = sorted(r[0] for r in rows if r[2] == 'nodecor:vtordisp')
    check('a destructor thunk of a class with virtual bases is `vtordisp` (cl\'s vtordisp needs the exe\'s vbptr layout)', len(vtor) == 4 and all('fnvnx_sdd' in x for x in vtor), vtor)
    missing = want - got
    check('the cl names that have no row are exactly as many as the rows the plugin marks unnamed', len(missing) == len(unnamed),
          {'cl names without a row': sorted(missing), 'unnamed rows': [(r[0], r[2]) for r in unnamed]})
    # a function the unit defines that GCC wrote nowhere (inlined at every use) has a row of kind noemit, no symbol in the object, and is a cl name
    noemit = [r for r in rows if r[2] == 'noemit']
    check('every noemit row is a function with no symbol in the object and a name cl has', noemit and all(r[0] not in st and r[1] in expect for r in noemit),
          [(r[0], r[1]) for r in noemit if r[0] in st or r[1] not in expect] or 'no noemit row')
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


def sections(td, so):
    """every unit the plugin compiles carries its records, even when the unit names nothing (empty, or a body that is #if'd out); and the universe of
    the rows is the object's own symbols: a template pattern (variadic, a templated conversion operator, a literal operator template) has no symbol,
    so it has no row, instantiated or not, and no row is `nodecor:`"""
    for fx in ('empty', 'ifdef_out'):
        o = os.path.join(td, fx + '.o')
        r = pb.compile(os.path.join(HERE, 'names', fx + '.cpp'), o, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('%s: compiles' % fx, r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            have = pb.elfobj.read(o)
            check('%s: the unit carries .fnvnx.sympair, empty' % fx, have.section('.fnvnx.sympair') == b'', have.section('.fnvnx.sympair'))
    for inst in (False, True):
        o = os.path.join(td, 'pattern%d.o' % inst)
        r = pb.compile(os.path.join(HERE, 'names', 'pattern.cpp'), o, so, flags=pb.PRODUCT_FLAGS + ['-O0'] + (['-DINSTANTIATE'] if inst else []))
        check('pattern (instantiated: %s): compiles' % inst, r.returncode == 0, r.stderr[-2000:])
        if r.returncode:
            continue
        rows = rows_of(o, '.fnvnx.sympair')
        st = pb.symbols_of(o)
        bad = [x for x in rows if x[2].startswith('nodecor:')]
        check('pattern (instantiated: %s): no unnamed row' % inst, not bad, bad)
        outside = [x[0] for x in rows if x[0] not in st]
        check('pattern (instantiated: %s): every row is a symbol of the object' % inst, not outside or all(x[2] == 'noemit' for x in rows if x[0] in outside), outside)
        got = {x[1] for x in rows}
        check('pattern (instantiated: %s): types and operators cl 15.00 has no name for get the later MSVC ABI\'s (clang MicrosoftMangle.cpp llvmorg-18.1.8): '
              '__m128, a generic vector, __m64, char16_t, char32_t, nullptr_t, __int128, unsigned __int128; a literal operator `?__K`' % inst,
              {'?tv@@YAXT__m128@@T?$__vector@H$03@__clang@@T__m64@@_S_U$$T_L_M@Z', '??__K_u@@YA_K_K@Z'} <= got
              and (not inst or '??$?__K_t@$0DB@$0DC@@@YAHXZ' in got), sorted(got))
        if inst:
            check('pattern instantiated: its instantiations are named', {x[1] for x in rows} >= {'??$count@HN@@YAHHN@Z', '??$?BH@C@@QBEHXZ', '??$n@HH@C@@QBEHHH@Z', '??$both@H@?$Pack@HD@@SAHH@Z'}, rows)


def main():
    td = tempfile.mkdtemp(prefix='test-names-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        obj = os.path.join(td, 't.o')
        r = pb.compile(FIX, obj, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('compiles', r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            checks(obj)
        sections(td, so)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_names-mode=1'])
        check('a plugin argument is an error: the recipe fixes them', pb.argument_refused(r, 'names'), r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
