#!/usr/bin/env python3
"""vtables - gate `vtables`: every native vtable address point that claims an MSVC twin has the exe's vftable structure (L2 SOURCE, ABI1).

    vtables.py --exe FalloutNV.exe --obj obj.json --bind bind.json        writes $GATE_OUT/vtables.json, exit 0 iff green

RULE.  A native `_ZTV` group is the Itanium image of a class's vtables; the exe holds the MSVC image.  They describe the same class
iff, for the group of an object O and its address points (ap, d):
  V1  every address point has a vtpair row (the plugin says something about every one), and a `named` row is bound to an exe vftable
      address by the L1 bind (vtpair name -> N1 pin);
  V2  that address is an RTTI vftable of the exe (exevft.scan);
  V3  the three offsets agree: d (minus the offset-to-top of the native group), the vtpair row's vfptr offset, and the COL offset of
      the exe vftable;
  V4  slot count: the native point has exactly (exe slots + number of deleting destructors) slots (abi_native.exe_index: the minus-one
      rule from the plugin's destructor kinds); a destructor pair that is not D1,D0 adjacent is a violation;
  V5  completeness: the exe vftables of the class (same RTTI TypeDescriptor name as the vftable bound at d = 0) are exactly the vftables
      bound by the named address points of the group - no exe vftable of the class is left unbound, none is bound twice.
UNIVERSE.  Every address point of every `_ZTV` of every object of obj.json whose claim is `named` or `missing` (a `missing` one is
uncertified: the plugin must state a twin or its absence for every address point).  Address points whose row says `unnamed:<reason>`
are outside the universe: the compiler asserts the class has no MSVC vftable (Open, REMOVE, owner L2: the plugin's kinds), they are counted in `detail`.
PROOF.  V1-V5 are equalities between structures read from the PE (RTTI, COL, slot run), from the object (`_ZTV` words and relocations) and
from the plugin's rows; each is decided on those structures alone.  If all five hold for every named point of a group, the group has
the primary and every secondary vftable of the class once, each at the exe's subobject offset and slot count; V5 turns "all named
points are consistent" into "no vftable is missing" without a list.
Open (REMOVE, owner L2): unnamed means twinless and the plugin's kinds are right; removed by gate names against cl objects and gate vslots
against the exe's vftables.  Input fact: the bind addresses are certified by the N1 certificate (n1).  Open (REMOVE, owner L1 exefacts):
completeness of the RTTI scan is not yet a checked fact (a vftable exefacts's RTTI scan cannot see is not in V5's set).  Classes with
virtual bases: their COL offsets are not subobject offsets, so such groups are uncertified by V3 or V5 unless the structure agrees as
written, never passed.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import abi_native as N
import exefacts
import exevft

NAME = 'vtables'


def check(obj, bind, vfts):
    """-> (universe ids, {id: reason}, number of unnamed points)"""
    pts = N.groups(obj, bind)
    universe, red, unnamed = [], {}, 0
    by_class = collections.defaultdict(list)
    for v, e in vfts.items():
        by_class[e.name].append(v)
    grp = collections.defaultdict(list)
    for p in pts:
        grp[(p.object, p.ztv)].append(p)
    for p in pts:
        if p.claim == 'unnamed':
            unnamed += 1
            continue
        i = N.point_id(p)
        universe.append(i)
        if p.claim == 'missing':
            red[i] = 'V1 no vtpair row'
            continue
        if p.vft is None:
            red[i] = 'V1 %s is not bound to an exe vftable' % p.msvc
            continue
        e = vfts.get(p.vft)
        if e is None:
            red[i] = 'V2 %08x is not an RTTI vftable of the exe' % p.vft
        elif not (p.d == p.vfptr == e.off):
            red[i] = 'V3 offsets: native %d, vtpair %s, exe COL %d' % (p.d, p.vfptr, e.off)
        else:
            try:
                k, _ = N.exe_index(obj, p)
            except ValueError as ex:
                red[i] = 'V4 ' + str(ex)
                continue
            want = (k[-1] + 1) if k else 0
            if want != len(e.slots):
                red[i] = 'V4 native point has %d slots (%d exe slots expected), exe vftable has %d' % (len(p.slots), want, len(e.slots))
    for key, ps in sorted(grp.items()):
        named = [p for p in ps if p.claim == 'named']
        prim = [p for p in named if p.d == 0 and p.vft in vfts]
        if not named or not prim:
            continue
        cls = vfts[prim[0].vft].name
        bound = collections.Counter(p.vft for p in named if p.vft is not None)
        have = sorted(by_class[cls])
        bad = [v for v in have if bound[v] != 1] + [v for v in bound if v not in have]
        if bad:
            for p in named:
                i = N.point_id(p)
                if i not in red:
                    red[i] = 'V5 vftables of %s not bound exactly once: %s' % (cls, ' '.join('%08x' % v for v in sorted(set(bad))))
    return sorted(universe), red, unnamed


def main(argv=None, image=None):
    """IMAGE: the exe as an exefacts.Image when the caller already holds its bytes (a test's byte-built scene); otherwise
    --exe is read through exepin.load_exe, the one way a file becomes an Image."""
    ap = N.common_args(argparse.ArgumentParser())
    a = ap.parse_args(argv)
    try:
        obj, bind = N.load(a.obj, a.bind)
        vfts = exevft.scan(image if image is not None else exefacts.Image(a.exe))
    except (ValueError, OSError) as e:
        print('vtables: %s' % e, file=sys.stderr)
        return 2
    uni, red, un = check(obj, bind, vfts)
    return N.finish(NAME, uni, red, '%d address points claim no twin (unnamed rows)' % un)


if __name__ == '__main__':
    sys.exit(main())
