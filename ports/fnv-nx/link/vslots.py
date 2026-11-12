#!/usr/bin/env python3
"""vslots - gate `vslots`: every slot of a native vtable that claims an MSVC twin reaches the function the exe's vftable slot reaches (L2 SOURCE, ABI1).

    vslots.py --exe FalloutNV.exe --obj obj.json --bind bind.json        writes $GATE_OUT/vslots.json, exit 0 iff green

RULE.  For an address point bound to an exe vftable V (abi_native, gate `vtables` certifies the point itself) and its native slot j with exe
slot k = k(j) (the minus-one rule from the plugin's destructor kinds, abi_native.exe_index):
  S1  the slot's symbol is a function or a non-virtual this-adjusting thunk (Itanium special-name grammar); its callee c is the symbol
      itself or the thunk's `_Z<encoding>`;
  S2  c has an exe address a (the L1 bind: sympair name -> N1 pin);
  S3  the exe slot word w follows exact adjustor thunks (exevft.chain) to a final target T, and a == T.
  A native complete destructor (kind dtor.d1) is related to the exe slot through its deleting destructor, the next slot (kind dtor.d0): the
  slot of kind dtor.d1 is certified iff its D0 partner is (same k), and the D0 slot is judged by S1-S3.
UNIVERSE.  Every slot of every address point whose vtpair claim is `named`.  A slot of an unbound or inconsistent point is uncertified
(the structural reason is the item's reason), never skipped.  A slot with no relocation (zero word), a virtual-base or covariant thunk,
a callee without an exe address (a native-only function, a pure-virtual stub) is uncertified: the exe slot holds a real function, and
nothing in the facts relates the native symbol to it.
PROOF.  S3 is an equality of two addresses, the exe's by following only thunks whose bytes decode to `this -= n; jmp` (so the call
reaches T with the same arguments), the native one pinned by N1 (the pristine MSVC body of c equals the exe bytes at a).  Hence calling slot
j of the native vtable runs the code of the function at exe slot k, up to the this-adjustment, which gate `thisadj` certifies.
Open (REMOVE, owner L2): the plugin's kinds are right; removed by gate names against cl objects and gate vslots against the exe's vftables.
Input fact: the N1 pins (n1 certificate) certify the bind addresses.  What the code checks for a destructor: the slot's symbol of kind dtor.d0
(the slot after the one of kind dtor.d1, abi_native.exe_index) is judged by S1-S3 with the address the bind gives for its callee, and the
kind dtor.d1 slot is certified iff that partner is.  The code does not read the MSVC name of the symbol: which MSVC function (the sdd rule of
toolchain/sdd/fnvnx_sdd.cc puts `??_E`, the vector deleting destructor, in the one slot) that address is, is the bind's.  Dropped from the legacy tool:
pairing by "signature" with overloads in reverse declaration order, the position rule for the rest, the decomp-unit joins
(`units.json`), the pristine-object slot ranking, and the stubs that call through the image slot (all heuristics or repairs).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import abi_native as N
import exefacts
import exevft

NAME = 'vslots'


def check(obj, bind, vfts, img):
    facts = N.slot_facts(obj, bind, vfts, img)
    pair = {}                      # (point id, k) -> the slot fact of kind dtor.d0
    for f in facts:
        if f.kind == 'dtor.d0':
            pair[(N.point_id(f.point), f.k)] = f
    universe, red = [], {}
    for f in facts:
        universe.append(f.id)
        if f.err:
            red[f.id] = f.err
            continue
        g = pair.get((N.point_id(f.point), f.k), f) if f.kind == 'dtor.d1' else f
        why = ''
        if g.role[0] == 'unsupported':
            why = 'S1 ' + g.role[1]
        elif g.addr is None:
            why = 'S2 %s has no exe address' % g.callee
        elif g.addr != g.target:
            why = 'S3 native callee is %08x, exe slot %d reaches %08x' % (g.addr, g.k, g.target)
        if why:
            red[f.id] = why
    return sorted(universe), red


def main(argv=None, image=None):
    """IMAGE: the exe as an exefacts.Image when the caller already holds its bytes (a test's byte-built scene); otherwise
    --exe is read through exepin.load_exe, the one way a file becomes an Image."""
    ap = N.common_args(argparse.ArgumentParser())
    a = ap.parse_args(argv)
    try:
        obj, bind = N.load(a.obj, a.bind)
        img = image if image is not None else exefacts.Image(a.exe)
        uni, red = check(obj, bind, exevft.scan(img), img)
    except (ValueError, OSError) as e:
        print('vslots: %s' % e, file=sys.stderr)
        return 2
    return N.finish(NAME, uni, red, 'slots of named address points')


if __name__ == '__main__':
    sys.exit(main())
