"""tool_bridge.py - ask the code under test (tools/link abi.py / abi2.py) for its verdict and bridge on a hand-made
(reference, definition) pair, exactly the way fnvlink.resolve does for a bound reference."""
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
    -> (kind, classes, bridge asm or None)."""
    rs, ds = sigs.parse_msvc(ref_name, dem[ref_name]), sigs.parse_msvc(def_name, dem[def_name])

    def look(tab):
        return lambda t: (layout.Layout(*tab[t][:3], 0, tab[t][3]) if t in tab else None)
    v = abi.compare(rs, ds)
    if v.kind == 'same':
        return 'same', v.classes, None
    p2 = abi2.compare(rs, ds, look(lay_ref), look(lay_def))
    if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge'):
        nv = abi.Verdict()
        nv.kind, nv.classes, nv.notes, nv.plan = p2.kind, p2.classes, p2.notes, p2
        v = nv
    target = '__fnv_%08x' % addr
    if v.kind != 'bridge':
        return v.kind, v.classes, None
    bn = abi.bridge_name(addr, ref_name)
    if v.plan is not None:
        code = abi2.emit(bn, target, v.plan, 'ref | def')
        if code is None:
            return 'manual', v.classes, None
        return 'bridge', v.classes, code
    return 'bridge', v.classes, abi.emit_bridge(bn, target, v, 'ref | def')
