#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc
# reads: ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/layout/crossing_terms.inc ports/fnv-nx/toolchain/layout/decltype_spell.inc
# reads: ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/toolchain/names/fnvnx_names.cc
# reads: ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc
# reads: ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/verify
"""Test of the rule "order orders only memory another thread can reach" (toolchain/order/fnvnx_order.cc, LOCAL; R9 docs/ORDERING.md).
A local is shared iff the function's GIMPLE takes its address (address_taken_scan: an ADDR_EXPR operand, the hidden result pointer of
a call that returns in memory, an asm operand), NOT iff the front end set TREE_ADDRESSABLE (it sets it for every C++ aggregate local).
Fixture fixtures/order_local.cc, built by the recipe with the full plugin set (realobj):
  norm            the ds3d pattern: an HFA returned by value, built in a result temporary, a parameter copied on the other path: no
                  ordered access, no fence, and gate_order certifies every access (they are frame accesses outside any recorded variable)
  plain_local     a struct local only read and written by name: no memory access at all
  escaped         a local whose address is passed to a function: its stores are `stlr`, its address is recorded, gate_order certifies all
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import gate_order  # noqa: E402
import realobj  # noqa: E402
import test_sdd_order as T  # noqa: E402

fails = []


def check(what, ok, got=''):
    if not ok:
        fails.append('%s: %s' % (what, got))


def main():
    obj = realobj.objects(['order_local'])['order_local']
    fns = T.functions(obj)
    rows = gate_order.scan_object(obj)[0]
    for fn in ('_Z4norm1VS_', '_Z11plain_localf', '_Z7escapedf'):
        bad = [(i, k) for i, k in rows if i.split('+')[0] == fn and k]
        check(fn + ': every item certified by gate_order', not bad, bad)
    ordered = lambda fn: [m for o, m, a in fns[fn] if m in ('ldar', 'stlr') or m.startswith('dmb')]   # noqa: E731
    check('norm: no ordered access and no fence', not ordered('_Z4norm1VS_'), ordered('_Z4norm1VS_'))
    check('plain_local: no memory access', not [1 for o, m, a in fns['_Z11plain_localf'] if re.match(r'(ldr|str|ldp|stp|ldar|stlr)', m)], fns['_Z11plain_localf'])
    check('escaped: the three stores of the local are stlr', [m for o, m, a in fns['_Z7escapedf']].count('stlr') == 3, fns['_Z7escapedf'])
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: unshared locals are plain, an escaped local is ordered, gate_order certifies all')
    return 0


if __name__ == '__main__':
    sys.exit(main())
