"""tool_bridge.py - ask the code under test (tools/link abi.py / abi2.py) for its verdict and bridge on a hand-made
(reference, definition) pair, the way fnvlink.resolve decides for a bound reference (mirrors resolve at J-0081:
abi2 replaces abi's verdict unless abi bridges a narrow-extend pair; an x86-inconsistent ecx-not-passed pair still
gets the bridge of the moves it has)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import abi     # noqa: E402
import abi2    # noqa: E402
import layout  # noqa: E402
import sigs    # noqa: E402


def verdict(ref_name, def_name, dem, lay_ref, lay_def, addr=0x00c0ffee):
    """dem: {name: llvm-undname text}; lay_*: {canonical type name: (size, align, trivial, cls)}.
    -> (kind, classes, bridge asm or None). kind is the listed verdict; a bridge comes with 'bridge' and with an
    ecx-not-passed 'x86-inconsistent' pair that has moves."""
    rs, ds = sigs.parse_msvc(ref_name, dem[ref_name]), sigs.parse_msvc(def_name, dem[def_name])

    def look(tab):
        return lambda t: (layout.Layout(*tab[t][:3], 0, tab[t][3]) if t in tab else None)
    v = abi.compare(rs, ds)
    if v.kind == 'same':
        return 'same', v.classes, None
    p2 = abi2.compare(rs, ds, look(lay_ref), look(lay_def))
    if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge') and \
            not (v.kind == 'bridge' and 'narrow-extend' in v.classes):
        nv = abi.Verdict()
        nv.kind, nv.classes, nv.notes, nv.plan = p2.kind, p2.classes, p2.notes, p2
        v = nv
    target = '__fnv_%08x' % addr
    ekind = 'bridge' if v.kind == 'x86-inconsistent' and 'ecx-not-passed' in v.classes and \
        (getattr(v, 'moves', None) or getattr(v, 'plan', None) is not None) else v.kind
    if ekind != 'bridge':
        return v.kind, v.classes, None
    bn = abi.bridge_name(addr, ref_name)
    if getattr(v, 'plan', None) is not None:
        code = abi2.emit(bn, target, v.plan, 'ref | def')
        if code is None:
            return 'manual', v.classes, None
        return v.kind, v.classes, code
    return v.kind, v.classes, abi.emit_bridge(bn, target, v, 'ref | def')
