#!/usr/bin/env python3
"""W28 (S2-bodies top-down 3 / 18 / 25 / 26 / 90 / 102): hand-made fixtures, no game data.

    python3 tools/link/test_w28.py

* the import slots 0x00fdf2ec / 0x00fdf3d4 / 0x00fdf380 bind to the platform's implementations (imports.tsv + exports.cpp);
* vftable_import: an `__imp_` name the recipe put on a slot of an exe vftable binds to the native the slot's word holds
  (arity checked), and stays as it was when the slot has no native;
* the value-anchor names (`g_target_<addr>`, `*_value_anchor`) are recognised and nothing else is.
"""
import collections
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

PORT = os.path.dirname(os.path.dirname(HERE))
FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


ex = fnvlink.load_exports(os.path.join(PORT, 'platform', 'win32', 'exports.cpp'))
SYN = os.path.join(PORT, 'tests', 'imports_synth.tsv')   # synthetic IAT table: the real one is private (fnv-nx-build)
im = fnvlink.load_imports(SYN, ex)
check(im.get(0x00fdf2ec) == 'GetClassLongA', 'IAT 0x00fdf2ec -> GetClassLongA (top-down 3)')
check(im.get(0x00fdf3d4) == 'D3DXSaveTextureToFileA', 'IAT 0x00fdf3d4 -> D3DXSaveTextureToFileA (top-down 25)')
check(im.get(0x00fdf380) == 'fnvnx_ws2_fdisset', 'IAT 0x00fdf380 -> __WSAFDIsSet (top-down 102)')
iat = fnvlink.load_iat(SYN)
check(0x010c4cec not in iat and 0x010c4cec not in im and 0x010b7eb0 not in iat,
      'a `(not an IAT slot)` mislabel row is neither an IAT slot nor an import, so vftable_import binds it (5fd996a4 rank 12 Free)')
check(0x00fdf2ec in iat, 'real IAT rows stay')

# vftable_import on a synthetic exe vftable slot
A = 0x00aa4060
native = {A: {'msvc': '?Deallocate@MemoryManager@@QAEXPAX@Z'}}
msvc = lambda n: types.SimpleNamespace(kind='func', raw_params=['void*'])
isig = types.SimpleNamespace(params=['void*'])
keep = ('import', 'exact', 0x010c4cec, None, None)
stats, used = collections.Counter(), set()
got = fnvlink.vftable_import(None, 'n', isig, 0x010c4cec, native, {}, {}, used, stats, msvc, {0x010c4cec: A}, None, keep)
check(got == ('addr', 'import/vftable-slot', A, None, None) and stats['import_vftable_slot'] == 1,
      'a slot whose word is a native of the same arity binds by address (top-down 18)')
isig2 = types.SimpleNamespace(params=['void*', 'int'])
got = fnvlink.vftable_import(None, 'n', isig2, 0x010c4cec, native, {}, {}, used, stats, msvc, {0x010c4cec: A}, None, keep)
check(got == keep, 'another arity is no evidence: the reference stays as it was')
got = fnvlink.vftable_import(None, 'n', isig, 0x010c4cec, {}, {}, {}, used, stats, msvc, {0x010c4cec: A}, None, keep)
check(got == keep, 'a slot word with no native stays as it was')
check(fnvlink.vftable_import(None, 'n', isig, 0x1234, native, {}, {}, used, stats, msvc, {}, None, keep) == keep,
      'an address that is no exe vftable slot stays as it was')

# value anchors
for n in ('g_fnv_00e9ecc0_value_anchor', 'g_target_00d53c90'):
    check(bool(fnvlink.ANCHOR_NAME.search(n)), '%s is a value anchor' % n)
for n in ('g_target', 'g_target_00d53c9', 'g_memoryManager', 'value_anchor_x', 'g_pivotXf'):
    check(not fnvlink.ANCHOR_NAME.search(n), '%s is not' % n)
check(fnvlink.ANCHOR_SYM % 0x65 == '__fnvnx_anchor_00000065', 'anchor symbol spelling')

# bodies_slot_renames + rewrite_refs: the thunk object of a host takes the host's slot-fill binding (top-down 38, 42-50)
class U:
    def __init__(self, source, host):
        self.source, self.host = source, host
host = U(source='h.cpp', host=None)
vt = U(source='h.cpp#vtables', host='h.cpp')
bd = U(source='h.cpp#bodies7', host='h.cpp')
other = U(source='o.cpp#bodies8', host='o.cpp')
changed = {vt: {'_ZN1A1fEv': '__fnv_00483710'}}
addr_of = {('h.cpp#vtables', '_ZN1A1fEv'): 0x483710}
rebound = set()
n = fnvlink.bodies_slot_renames([host, vt, bd, other], changed, addr_of, {}, rebound,
                                lambda u: ['_ZN1A1fEv', '_ZN1A1gEv'])
check(n == 1 and changed.get(bd) == {'_ZN1A1fEv': '__fnv_00483710'} and other not in changed and vt in changed,
      "the host's bodies object takes the binding of the names it leaves undefined, nobody else")
check(addr_of[('h.cpp#bodies7', '_ZN1A1fEv')] == 0x483710 and ('h.cpp#bodies7', '_ZN1A1fEv') in rebound,
      'address and rebound recorded')
check(fnvlink.bodies_slot_renames([bd], {vt: {'_ZN1A1fEv': 't'}}, {('h.cpp#vtables', '_ZN1A1fEv'): 1},
                                  {'h.cpp#bodies7': {'_ZN1A1fEv': 'x'}}, set(), lambda u: ['_ZN1A1fEv']) == 0,
      'a name the object already renames is left alone')
import tempfile
d = tempfile.mkdtemp()
rp = os.path.join(d, 'refs.tsv')
open(rp, 'w').write('h.cpp#bodies7\t_ZN1A1fEv\tunresolved\t\t\t\t\n'
                    'h.cpp#bodies7\t_ZN1A1gEv\tunresolved\t\t\t\t\n'
                    'h.cpp\t_ZN1A1fEv\tunresolved\t\t\t\t\n')
fnvlink.rewrite_refs(rp, {'h.cpp#bodies7': {'_ZN1A1fEv': '__fnv_00483710'}}, {('h.cpp#bodies7', '_ZN1A1fEv'): 0x483710},
                     {('h.cpp#bodies7', '_ZN1A1fEv')})
rows = [l.split('\t')[:6] for l in open(rp)]
check(rows[0][2:6] == ['vslot-exe', 'exe-slot', '00483710', '__fnv_00483710'] and rows[1][2] == 'unresolved'
      and rows[2][2] == 'unresolved', 'refs.tsv: the bodies row becomes vslot-exe; other rows are untouched')

# bodycause.local_calls: a same-section call has no relocation (top-down harness rows were mislabelled harness-only)
import bodycause  # noqa: E402
dis = ['00000000 <_ZL6Finishv>:', '   0:\tadrp\tx1, 0 <g_X>', '\t\t\t0: R_AARCH64_P32_ADR_GOT_PAGE\tg_X', '   4:\tret',
       '', '00000010 <__fnv_00001000>:', '  10:\tbl\t0 <_ZL6Finishv>', '  14:\tbl\t0 <ext>', '\t\t\t14: R_AARCH64_P32_CALL26\text',
       '  18:\tb\t8 <__fnv_00001000+0x8>', '  1c:\tret']
check(bodycause.local_calls(dis) == [('__fnv_00001000', '_ZL6Finishv'), ('__fnv_00001000', '__fnv_00001000')],
      'local_calls: the unrelocated bl / b are edges, the relocated bl is not')

print('FAIL: %d' % len(FAILS) if FAILS else 'all ok')
sys.exit(1 if FAILS else 0)
