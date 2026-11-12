#!/usr/bin/env python3
"""thisadj - gate `thisadj`: the `this` adjustment a native vtable slot applies equals the one the exe's vftable entry implies (L2 SOURCE, ABI1).

    thisadj.py --exe FalloutNV.exe --obj obj.json --bind bind.json        writes $GATE_OUT/thisadj.json, exit 0 iff green

BACKGROUND.  MSVC enters a virtual function with `this` at the subobject it was compiled for; a vftable at COL offset d reaches it directly
(the function adjusts for itself) or through `sub ecx, n; jmp` adjustor thunks.  GCC compiles every function for its own class's subobject and
reaches it from a secondary vtable through `_ZThn<n>_` thunks.  A native slot and the exe slot it stands for must deliver the same pointer.
RULE.  Per slot (abi_native.slot_facts; the slot is related to an exe slot k):
  native adjustment  n_native = 0 for a function, `adj` of a non-virtual thunk (the Itanium call-offset: this_callee = this - adj);
  exe adjustment     n_exe = the sum of the exact thunks on the exe slot's chain (exevft.chain) + e(T), where T is the chain's final target and
                     e(T) is T's own entry convention: the COL offsets D(T) of the vftables that hold T directly (exevft.entry_offsets)
                       0 in D(T)       -> e = 0   (T is entered with this at its class's subobject)
                       D(T) = {d'}, d' > 0 -> e = d'  (T is a secondary entry: MSVC compiled it for the subobject at d')
                       otherwise (T is never held directly, or held at several secondary offsets without a primary one) -> undecidable.
  Certified iff n_native == n_exe.  Undecidable e(T) is UNCERTIFIED (reason `entry convention of T undecidable`), a virtual-base or covariant
  thunk is UNCERTIFIED, never passed.  The slots of a complete/deleting destructor pair carry the same n_exe (one exe slot).
UNIVERSE.  Every slot of every address point whose vtpair claim is `named` (same universe as gate `vslots`); slots of unbound or inconsistent
points are uncertified with the structural reason.
PROOF.  Both sides compute "this_callee - this_vtable_subobject" for the same callee: natively it is the thunk's call-offset by the ABI definition;
in the exe it is the decoded thunk adjustments, plus the displacement of the subobject T expects relative to the vftable-class subobject, which is
d' when T is entered at its only secondary vftable and 0 when it also stands in a primary one.  Equal numbers mean the same pointer reaches
the same body.
Open (no owner assigned, reported to the orchestrator): that a function has one entry convention, read from the vftables that hold it
directly (MSVC's secondary-entry scheme), is not derivable from the COL offsets alone; the gate decides only the cases above and leaves the
rest uncertified.  Open (REMOVE, owner L2): the plugin's kinds (abi_native), removed by gate names against cl objects and gate vslots against the exe's
vftables.  Input fact: N1 pins (the n1 certificate) certify the bind addresses.  Dropped from the legacy tool: `secondary_entries` taking min() of several offsets, the `_ZThn` name
construction by string splicing (`thunk_name`), the symbol-to-vtable search by parsing the object a second time (`slot_otts`).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import abi_native as N
import exefacts
import exevft

NAME = 'thisadj'


def entry_convention(direct, t):
    """e(T) per the rule above, or None when undecidable."""
    d = direct.get(t, [])
    if 0 in d:
        return 0
    if len(d) == 1:
        return d[0]
    return None


def check(obj, bind, vfts, img):
    universe, red = [], {}
    for f in N.slot_facts(obj, bind, vfts, img):
        universe.append(f.id)
        if f.err:
            red[f.id] = f.err
        elif f.role[0] == 'unsupported':
            red[f.id] = f.role[1]
        else:
            e = entry_convention(f.direct, f.target)
            n_native = 0 if f.role[0] == 'fn' else f.role[1]
            if e is None:
                red[f.id] = 'entry convention of %08x undecidable' % f.target
            elif n_native != f.adj + e:
                red[f.id] = 'native adjusts this by %d, the exe slot %d implies %d (thunks %d + entry %d)' % (n_native, f.k, f.adj + e, f.adj, e)
    return sorted(universe), red


def main(argv=None):
    ap = N.common_args(argparse.ArgumentParser())
    a = ap.parse_args(argv)
    try:
        obj, bind = N.load(a.obj, a.bind)
        img = exefacts.Image(a.exe)
        uni, red = check(obj, bind, exevft.scan(img), img)
    except (ValueError, OSError) as e:
        print('thisadj: %s' % e, file=sys.stderr)
        return 2
    return N.finish(NAME, uni, red, 'slots of named address points')


if __name__ == '__main__':
    sys.exit(main())
