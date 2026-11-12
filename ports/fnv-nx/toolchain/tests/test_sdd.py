#!/usr/bin/env python3
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


def decode_load(obj, fn, off):
    """the vtable offset that the load instruction at byte OFF of function FN (a section offset, as objdump prints) addresses, read from the machine code:
    `ldr wN, [xM, #imm]` -> imm, `ldr wN, [xM]` -> 0, `ldar wN, [xM]` -> the immediate of the `add xM, xK, #imm` in front of it, or 0 when that is
    not an add into xM.  None when the instruction is not such a load."""
    ins = []
    for l in pb.objdump(obj)[fn]:
        m = re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)', l)
        if m:
            ins.append((int(m.group(1), 16), m.group(2), re.sub(r'\s*//.*$', '', m.group(3)).strip()))
    k = next((n for n, x in enumerate(ins) if x[0] == off), None)
    if k is None:
        return None
    _, mn, ops = ins[k]
    m = re.match(r'w\d+, \[(x\d+)(?:, #(-?(?:0x)?[0-9a-f]+))?\]$', ops)
    if not m or mn not in ('ldr', 'ldar'):
        return None
    if mn == 'ldr':
        return int(m.group(2) or '0', 0)
    if k:
        a = re.match(r'(?:add)\s+(x\d+), x\d+, #(0x[0-9a-f]+|\d+)$', ins[k - 1][1] + ' ' + ins[k - 1][2])
        if a and a.group(1) == m.group(1):
            return int(a.group(2), 0)
    return 0


def vcalls(obj):
    """section .fnvnx.vcall of the object: [(function symbol, section, byte offset of the labelled load, slot, kind)], from the section's bytes and the
    relocations of its first two words of each 16-byte record (label = section + offset, function symbol)"""
    data = pb.elfobj.read(obj).section('.fnvnx.vcall') or b''
    rel = {o: (n, a) for o, t, n, a in pb.relocs_of(obj).get('.fnvnx.vcall', [])}
    out = []
    for r in range(0, len(data), 16):
        (sec, off), (fn, _) = rel[r], rel[r + 4]
        out.append((fn, sec, off, int.from_bytes(data[r + 8:r + 12], 'little'), int.from_bytes(data[r + 12:r + 16], 'little')))
    return sorted(out)


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
    # VCALL1: every virtual call, destructor-slot call and delete-expression has one record; the label sits on the slot load, whose decoded
    # offset is slot * 4 (psz).  Expected slots: MSVC's layout rule (declaration order, bases first, an override keeps its base's slot; an override of a
    # non-primary base's virtual also takes a slot in the primary table, so M::k is 3 and M::last 4):
    #   A: f 0, ~A 1, g 2.  S2: ~S2 0, k 1.  J: A's, w 3.  TT<int>: ~TT 0, z 1.   kind 0 virtual call, 1 `p->~A()` (flags 0), 2 `delete p` (flags 1)
    want = {'_Z5del_aP1A': [(1, 2)], '_Z5del_bP1B': [(1, 2)], '_Z6del_s2P2S2': [(0, 2)], '_Z9destroy_aP1A': [(1, 1)],
            '_Z6call_gP1A': [(2, 0)], '_Z6call_kP2S2': [(1, 0)], '_Z9call_lastP1M': [(4, 0)], '_Z6use_ttP2TTIiE': [(1, 0)],
            '_Z6del_ttP2TTIiE': [(0, 2)], '_Z5use_jP1J': [(2, 0), (3, 0)]}
    vc = vcalls(obj)
    got = {}
    for fn, sec, off, slot, kind in vc:
        got.setdefault(fn, []).append((slot, kind))
    check('table .fnvnx.vcall: exactly the calls of the fixture, each with the slot and kind of the layout rule',
          {f: sorted(v) for f, v in got.items()} == want, str(got))
    for fn, sec, off, slot, kind in vc:
        s_ = syms[fn]
        dec = decode_load(obj, fn, off)
        check('%s: the label at .text+%d is a slot load whose offset is slot %d * 4' % (fn, off, slot),
              sec == s_['sec'] and s_['value'] <= off < s_['value'] + s_['size'] and dec == slot * 4, '%s: decoded %s' % (fn, dec))
    check('flags: delete passes 1, p->~A() passes 0', re.search(r'mov w1, #0x1\b', ins('_Z5del_aP1A')) and re.search(r'mov w1, #0x0\b', ins('_Z9destroy_aP1A')))
    check('pointer to virtual member: constant', w('table') == ['8', '1', '0', '1'], str(w('table')))
    check('pointer to virtual member: comparison', re.search(r'cmp w0, #0x8\b', ins('_Z2eqM1AFivE')))
    check('pointer to virtual member: template argument (the folded call loads slot 2)',
          re.search(r'add x\d+, x\d+, #0x8 ldar w\d+, \[x\d+\]', ins('_Z5use_tP1A')), ins('_Z5use_tP1A'))
    ap8, ap36, tv = syms['_ZTV1M.fnvnx_ap8'], syms['_ZTV1M.fnvnx_ap36'], syms['_ZTV1M']
    check('address points: own symbols', ap8['value'] == tv['value'] + 8 and ap36['value'] == tv['value'] + 36 and ap8['sec'] == ap36['sec'] == tv['sec'],
          str((ap8, ap36, tv)))
    ctor = [(t, n) for t, n in relocs_in(obj, '_ZN1MC2Ev')]
    vt = sorted({n for t, n in ctor if t == 'R_AARCH64_P32_LD32_GOT_LO12_NC' and n.startswith('_ZTV')})
    check('constructor (with the inlined base constructors): every vtable address is an address-point symbol, M\'s two among them, no immediate',
          vt == ['_ZTV1A.fnvnx_ap8', '_ZTV1M.fnvnx_ap36', '_ZTV1M.fnvnx_ap8', '_ZTV2S2.fnvnx_ap8']
          and not [1 for t, n in ctor if n.startswith('_ZTV') and '.fnvnx_ap' not in n], str(ctor))
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


def main():
    td = tempfile.mkdtemp(prefix='test-sdd-')
    try:
        so = pb.build('sdd', td)
        out = os.path.join(td, 't.o')
        r = pb.compile(FIX, out, [so])
        check('compiles', r.returncode == 0, r.stderr[:3000])
        if r.returncode == 0:
            checks(out)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), [so], args=['-fplugin-arg-fnvnx_sdd-mode=1'])
        check('an unknown plugin argument is an error', r.returncode != 0 and 'arguments must be' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
