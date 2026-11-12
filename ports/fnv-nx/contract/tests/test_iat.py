#!/usr/bin/env python3
"""Synthetic check of iat.py: key rule, data slots, module exports, totality."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import iat

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


bridge = [('kernel32.dll', 'Sleep', 'stdcall'), ('advapi32.dll', 'RegCloseKey', 'stdcall'), ('msvcrt.dll', '_iob', 'data'),
          ('wsock32.dll', '#23', 'stdcall')]
keys = iat.system_keys(bridge)
check('keys', keys, {('advapi32.dll', 'RegCloseKey'): iat.IMPORT_BASE, ('kernel32.dll', 'Sleep'): iat.IMPORT_BASE + iat.STEP,
                     ('wsock32.dll', '#23'): iat.IMPORT_BASE + 2 * iat.STEP})
check('sentinel outside keys', iat.SENTINEL < iat.IMPORT_BASE and iat.IMPORT_BASE + len(bridge) * iat.STEP <= iat.LIMIT, True)
imports = {0x1000: ('KERNEL32.dll', 'Sleep'), 0x1004: ('msvcrt.dll', '_iob'), 0x1008: ('mod.dll', 'f'), 0x100c: ('wsock32.dll', '#23')}
rows, bad = iat.table(imports, bridge, {'mod.dll': {'f': 0x30001234}})
check('rows', rows, [(0x1000, 'kernel32.dll', 'Sleep', 'key', iat.IMPORT_BASE + iat.STEP), (0x1004, 'msvcrt.dll', '_iob', 'data', None),
                     (0x1008, 'mod.dll', 'f', 'module', 0x30001234), (0x100c, 'wsock32.dll', '#23', 'key', iat.IMPORT_BASE + 2 * iat.STEP)])
check('no bad', bad, [])
rows, bad = iat.table({0x1000: ('x.dll', 'g'), 0x1004: ('mod.dll', 'nope')}, bridge, {'mod.dll': {'f': 1}})
check('bad', bad, [(0x1000, 'x.dll', 'g', 'no provider'), (0x1004, 'mod.dll', 'nope', 'not an export of the module')])
try:
    iat.system_keys([('a.dll', 'f', 'stdcall')] * 2)
    fails.append('duplicate bridge row accepted')
except ValueError:
    pass
try:
    iat.table({}, [('mod.dll', 'f', 'stdcall')], {'mod.dll': {}})
    fails.append('a dll that is both a module and a bridge dll accepted')
except ValueError:
    pass
ob = [('wsock32.dll', 'recv', 'stdcall', '16'), ('wsock32.dll', 'send', 'stdcall', '19'), ('k.dll', 'named', 'stdcall', '')]
rows, bad = iat.table({0x10: ('WSOCK32.dll', '#19'), 0x14: ('wsock32.dll', '#7')}, ob, {})
check('by ordinal', (rows, bad), ([(0x10, 'wsock32.dll', 'send', 'key', iat.IMPORT_BASE + 2 * iat.STEP)], [(0x14, 'wsock32.dll', '#7', 'no provider')]))
try:
    iat.table({}, ob + [('wsock32.dll', 'other', 'stdcall', '16')], {})
    fails.append('one ordinal with two names accepted')
except ValueError:
    pass
for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
