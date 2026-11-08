#!/usr/bin/env python3
"""test_vtret.py - a returned vtable is the exe's vftable only by the link's own pairing (R4, farm baseline 588863:
vftable getters 0x00da0780, 0x00dfc400, 0x00dfe5e0, 0x00dfe9b0 return the native vtable of an addrmap V row).
  V1  a V row whose _ZTV the unit image defines pairs its address point with the exe vftable
  V2  a V row whose _ZTV is only a named stub (an unbound stand-in table) does not pair
  V3  runner._compare takes a `vtpair` (the strict map) for pointer results
"""
import inspect
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13vtret'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import engine  # noqa: E402
import runner  # noqa: E402

FAIL = 0


def check(name, cond, got):
    global FAIL
    FAIL += not cond
    print('%s: %s (%s)' % (name, 'ok' if cond else 'FAIL', got))


def main():
    uc = engine.UnitCache.__new__(engine.UnitCache)
    uc._vrows = [(0x010d460c, '_ZTV9Defined', 8), (0x010d461c, '_ZTV7Stubbed', 8)]
    uc._vft_by_class = {}
    uc.units = []
    img = types.SimpleNamespace(syms={'_ZTV9Defined': 0x0a197648}, stubs={0x09016db0: '_ZTV7Stubbed'})
    f = getattr(uc, 'vtable_map_defined', None)
    m = f(img) if f else {}
    check('V1', m.get(0x0a197650) == 0x010d460c, {hex(k): hex(v) for k, v in m.items()})
    check('V2', 0x09016db8 not in m, sorted(hex(k) for k in m))
    check('V3', 'vtpair' in inspect.signature(runner._compare).parameters, list(inspect.signature(runner._compare).parameters)[-3:])
    print('test_vtret: %d failed' % FAIL)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
