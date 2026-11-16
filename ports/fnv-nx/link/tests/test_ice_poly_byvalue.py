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
"""Expected-to-build case: a polymorphic struct passed and returned by value (fixtures/ice_poly_byvalue.cc) must compile with the guest compiler and all 8
plugins (the product configuration).  RED until the fix lands: today the compile stage dies with `internal compiler error: in expand_expr_addr_expr_1,
at expr.cc:8710` during RTL pass expand.  Measured (before a plugin subset stopped being buildable: every guest root loads the full set) by building the same source as a lifted-origin toy root with plugin subsets (realobj.build): no plugin
-> green; all 8 -> ICE; each one of the 8 removed in turn -> ICE except `fnvnx_order` removed -> green; each plugin alone -> green except `fnvnx_order`
alone -> ICE.  So the cause is fnvnx_order (`object=msvc`: the by-value polymorphic object is passed by invisible reference and the copy
`__ct_comp(<retval>, x)` takes the address of the RESULT_DECL `<retval>` inside expand_builtin).  Run: python3 -I <this file>."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import realobj

SRC = open(os.path.join(realobj.FIXDIR, 'ice_poly_byvalue.cc')).read()
fails = []


def check(what, res):
    if res['status'] != 'green':
        fails.append('%s: %s' % (what, ' | '.join(l for l in res['failure']['stderr'] if 'internal compiler error' in l) or res['failure']))


check('all 8 plugins (native root)', realobj.build({'ice': SRC}))
check('all 8 plugins (lifted-origin root)', realobj.build({'ice': SRC}, lifted=True))
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
