#!/usr/bin/env python3
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
