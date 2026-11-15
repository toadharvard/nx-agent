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
"""Test of toolchain/sdd/fnvnx_sdd.cc (SDD2: one vtable slot per virtual destructor), built from the tracked source with the
argv of the recipe's plugin stage (plugbuild.py). Compiles sdd/sdd.cpp (plain C++) with the product flags and checks the
object (objdump -dr, readelf, section bytes of the recipe's object): table contents in exe order, the slot each virtual call loads, destructor flags, pointers to virtual members,
address-point symbols, the table map; an unknown plugin argument is an error. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_sdd')
FIX = os.path.join(HERE, 'sdd', 'sdd.cpp')


def slot_loads(obj, sym):
    """byte offsets of the slot loads of the virtual calls and deletes of SYM: every call loads the vptr and then the slot, both as
    `ldar w, [x]` (R9: sdd and fnvnx_order create them ordered); the slot load is the second of each pair, its offset the `add` right before it"""
    s = pb.insns(obj, sym)
    ms = list(re.finditer(r'(?:add x(\d+), x\d+, #(0x[0-9a-f]+|\d+) )?ldar w\d+, \[x(\d+)\]', s))
    return [int(m.group(2), 0) if m.group(1) and m.group(1) == m.group(3) else 0 for m in ms[1::2]]


def relocs_in(obj, sym):
    """[(type, symbol)] of the relocations that fall inside function SYM of the object"""
    s = pb.symbols_of(obj)[sym]
    return [(t, n) for o, t, n, a in pb.relocs_of(obj).get(s['sec'], []) if s['value'] <= o < s['value'] + s['size']]


def checks(obj):
    syms = pb.symbols_of(obj)
    ins = lambda f: pb.insns(obj, f)  # noqa: E731
    w = lambda s: pb.words(obj, s)  # noqa: E731
    # struct A { virtual int f(); virtual ~A(); virtual int g(); }: slots f 0, ??_G 1, g 2
    check('table M: exe order, one destructor slot, the secondary table too',
          w('_ZTV1M') == ['0', '_ZTI1M', '_ZN1A1fEv', '_ZN1MD1Ev.fnvnx_sdd', '_ZN1A1gEv', '_ZN1M1kEv', '_ZN1M4lastEv',
                          '-8', '_ZTI1M', '_ZThn8_N1MD1Ev.fnvnx_sdd', '_ZThn8_N1M1kEv'], str(w('_ZTV1M')))
    check('delete p: slot 1, flags 1', slot_loads(obj, '_Z5del_aP1A') == [4] and re.search(r'mov w1, #0x1\b', ins('_Z5del_aP1A')))
    check('p->~A(): slot 1, flags 0', slot_loads(obj, '_Z9destroy_aP1A') == [4] and re.search(r'mov w1, #0x0\b', ins('_Z9destroy_aP1A')))
    check('delete through the second base: its slot 0', slot_loads(obj, '_Z6del_s2P2S2') == [0])
    check('g(): slot 2', slot_loads(obj, '_Z6call_gP1A') == [8], str(slot_loads(obj, '_Z6call_gP1A')))
    check('k() through S2: slot 1', slot_loads(obj, '_Z6call_kP2S2') == [4])
    check('M::last(): slot 4', slot_loads(obj, '_Z9call_lastP1M') == [16])
    check('pointer to virtual member: constant', w('table') == ['8', '1', '0', '1'], str(w('table')))
    check('pointer to virtual member: comparison', re.search(r'cmp w0, #0x8\b', ins('_Z2eqM1AFivE')))
    check('pointer to virtual member: template argument', slot_loads(obj, '_Z5use_tP1A') == [8])
    check('instantiated class template', slot_loads(obj, '_Z6use_ttP2TTIiE') == [4] and slot_loads(obj, '_Z6del_ttP2TTIiE') == [0])
    uj = slot_loads(obj, '_Z5use_jP1J')
    check('inline member bodies: g() slot 2, J::w() slot 3', uj == [8, 12], str(uj))
    ap8, ap36, tv = syms['_ZTV1M.fnvnx_ap8'], syms['_ZTV1M.fnvnx_ap36'], syms['_ZTV1M']
    check('address points: own symbols', ap8['value'] == tv['value'] + 8 and ap36['value'] == tv['value'] + 36 and ap8['sec'] == ap36['sec'] == tv['sec'],
          str((ap8, ap36, tv)))
    ctor = [(t, n) for t, n in relocs_in(obj, '_ZN1MC2Ev')]
    # the product flags include -fPIC (flags.semantics of verify/guest_compiler.json): a symbol address is GOT-relative, one ADR_GOT_PAGE and one
    # LD32_GOT_LO12_NC per reference.  The constructor writes the vptr of every address point it constructs (the inlined base constructors' too)
    aps = sorted({n for t, n in ctor if '.fnvnx_ap' in n})
    pairs = all(sorted(t for t, n in ctor if n == a_) == ['R_AARCH64_P32_ADR_GOT_PAGE', 'R_AARCH64_P32_LD32_GOT_LO12_NC'] for a_ in aps)
    check('constructor: one GOT-relative relocation pair per address point, no immediate and no reference to a table itself',
          {'_ZTV1M.fnvnx_ap36', '_ZTV1M.fnvnx_ap8'} <= set(aps) and pairs and not [1 for t, n in ctor if '.fnvnx_ap' not in n and n.startswith('_ZTV')], str(ctor))
    th = ins('_ZN1MD1Ev.fnvnx_sdd')
    check('??_E flags 0: the destructor; flags 1: the deleting destructor; returns this',
          'tst w1, #0x3 b.ne L1 bl _ZN1MD1Ev b L5 L1: mov w20, w0 tbnz w21, #1, L2 bl _ZN1MD0Ev b L5' in th
          and 'L5: mov x0, x19' in th, th)
    check('??_E flags 2: n = the word before the array, elements last first, then the global operator delete on bit 0 (cl: ??3)',
          'L2: ldur w22, [x20, #-4] L3: cbz w22, L4 sub w22, w22, #0x1 ldr w9, =16 madd w0, w22, w9, w20 bl _ZN1MD1Ev b L3 '
          'L4: tbz w21, #0, L5 sub w0, w20, #0x4 bl _ZdlPv' in th, th)
    th2 = ins('_ZThn8_N1MD1Ev.fnvnx_sdd')
    check('??_E of the second base: the thunk for flags 0, the adjusted object otherwise',
          'bl _ZThn8_N1MD1Ev b L5 L1: ldr w9, =-8 add w0, w0, w9 mov w20, w0 tbnz w21, #1, L2 bl _ZN1MD0Ev' in th2, th2)
    th3 = ins('_ZN2VBD1Ev.fnvnx_sdd')
    check("??_E frees with the class's own operator delete[]", 'ldr w9, =12 madd' in th3 and 'bl _ZN2VBdaEPv' in th3, th3)
    th4 = ins('_ZN2HKD1Ev.fnvnx_sdd')
    check('placement delete[] only (and a scalar operator delete of the class): the global operator delete, as cl',
          'tbz w21, #0, L5 sub w0, w20, #0x4 bl _ZdlPv' in th4 and '_ZN2HKdlEPv' not in th4.split('L2:')[1], th4)
    th5 = ins('_ZN2SZD1Ev.fnvnx_sdd')
    check('sized delete[]: the member, with the size of one element (cl passes sizeof(the class))',
          re.search(r'tbz w21, #0, L5 ldr w1, =12 sub w0, w20, #0x4 bl _ZN2SZdaEPvj\b', th5), th5)
    na = ins('_Z6new_a8i')
    check('COOKIE1: the array of a class aligned to 8 starts 4 bytes into its block',
          re.search(r'add w\d+, w\d+, #0x4\b', na) and not re.search(r'add w\d+, w\d+, #0x8\b', na), na)
    rel = pb.relocs_of(obj)
    d0 = sorted((sec, n) for sec, rs in rel.items() if sec.startswith('.data.rel.ro._ZTV') for o, t, n, a in rs if 'D0Ev' in n)
    check('no deleting destructor in any table', not d0, d0)
    check('deleting destructors are written out', all(syms.get(n, {}).get('type') == 'FUNC' and syms[n]['sec'] not in ('UND', None) and syms[n]['size'] > 0
                                                     for n in ('_ZN1MD0Ev', '_ZN1BD0Ev')))
    vm = [l for l in pb.section(obj, '.fnvnx.vtmap').splitlines(True) if l.startswith('_ZTV1M')]
    check('table map: one line per address point with its base path',
          vm == ['_ZTV1M\t8\t0\t1M\t1M\n', '_ZTV1M\t36\t8\t1M\t1M;2S2\n'], str(vm))


def crossing(obj):
    """.fnvnx.crossing has a row for EVERY function the unit pairs with an MSVC name in .fnvnx.sympair (one universe: the row is made where the pair is made),
    so every destructor variant is covered whatever emits it: the complete/base/deleting ones GCC emits (dtor.d0/d1/d2, in a class with a virtual base too), the
    vector deleting destructor ??_E the plugin writes as assembly (dtor.sdd, its this-adjusting thunk dtor.sdd.thunk), and the ones inlined everywhere (noemit)"""
    pairs = [l.split('\t') for l in pb.section(obj, '.fnvnx.sympair').splitlines()]
    cross = {l.split('\t')[0] for l in pb.section(obj, '.fnvnx.crossing').splitlines()}
    boundary = [p for p in pairs if len(p) == 4 and p[1] and p[2] != 'data']
    missing = [p[0] for p in boundary if p[0] not in cross]
    check('crossing: every sympair row with an MSVC name has a crossing row', boundary and not missing, missing[:10])
    kinds = {p[2] for p in boundary}
    for k in ('dtor.d0', 'dtor.d1', 'dtor.d2', 'dtor.sdd', 'dtor.sdd.thunk', 'noemit'):
        check('crossing: the fixture has a %s row and it has its crossing row' % k, k in kinds and all(p[0] in cross for p in boundary if p[2] == k), sorted(kinds))
    vb = [p for p in boundary if p[2].startswith('dtor') and p[1].startswith('??_EVB@@')]
    check('crossing: the vector deleting destructor of a class with a virtual base (VB) is paired and has its row', vb and all(p[0] in cross for p in vb), vb)


def main():
    td = tempfile.mkdtemp(prefix='test-sdd-')
    try:
        so = pb.build('sdd', td)
        out = os.path.join(td, 't.o')
        r = pb.compile(FIX, out, [so])
        check('compiles', r.returncode == 0, r.stderr[:3000])
        if r.returncode == 0:
            checks(out)
            crossing(out)
        # THE TREES KEEP THE REAL TABLE: a class with a virtual base is constructed through a VTT and a construction vtable; the accesses before
        # an address point (vbase offset, offset-to-top, RTTI: inside the table) must neither warn (the address point's own symbol is one slot
        # long, so GCC's bounds analysis would flag every such access) nor become a relocation with a negative addend against that symbol.
        # The address point's symbol is written where the assembler text is made (ap_integer) and in the code after the last analysis.
        vo = os.path.join(td, 'vb.o')
        r = pb.compile(os.path.join(HERE, 'sdd', 'vbase_bounds.cpp'), vo, [so], args=['-Wall', '-Werror=array-bounds'])
        check('virtual base: compiles with -Werror=array-bounds', r.returncode == 0, r.stderr[:1500])
        if r.returncode == 0:
            neg = [(t, n, a) for rl in pb.relocs_of(vo).values() for o, t, n, a in rl if '.fnvnx_ap' in n and a < 0]
            check('virtual base: no relocation against an address point with a negative addend', not neg, str(neg[:3]))
            vt = [n for rl in pb.relocs_of(vo).values() for o, t, n, a in rl if '.fnvnx_ap' in n]
            check('virtual base: the VTT and the code name address points by their own symbols', any(n.startswith('_ZTT') or '.fnvnx_ap' in n for n in vt) and vt, str(vt[:3]))
        r = pb.compile(FIX, os.path.join(td, 'x.o'), [so], args=['-fplugin-arg-fnvnx_sdd-mode=1'])
        check('an unknown plugin argument is an error', pb.argument_refused(r, 'sdd'), r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
