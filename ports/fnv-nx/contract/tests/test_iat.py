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
# data rows: the `-` rule, and iat_data.tsv against import_keys.c
def brow(dll, name, conv, sym, ordinal=''):
    return {'dll': iat.norm_dll(dll), 'name': name, 'ordinal': ordinal, 'conv': conv, 'ret': '', 'args': '', 'native_symbol': sym, 'bind': ''}


db = [brow('kernel32.dll', 'Sleep', 'stdcall', 'x'), brow('msvcrt.dll', '_iob', 'data', 'fnvnx_iob'), brow('msvcrt.dll', '_fmode', 'data', '-'),
      brow('msvcrt.dll', '_commode', 'data', '-', '7'), brow('ntdll.dll', 'Qdata', 'data', 'fnvnx_q', '5')]
imp = iat.imported_set(db, [('KERNEL32', 'Sleep'), ('msvcrt.dll', '_iob')])
text, drows, left = iat.c_source(db, 'T' * 64, imp)
check('unimported `-` data rows are left out of the table', sorted(r['name'] for r in left), ['_commode', '_fmode'])
check('... and absent from import_keys.c', '_fmode' in text or '_commode' in text, False)
check('data rows of the table, sorted by (dll, name)', [r['name'] for r in drows], ['_iob', 'Qdata'])
try:
    iat.c_source(db, 'T' * 64, iat.imported_set(db, [('msvcrt.dll', '_fmode')]))
    fails.append('an imported `-` data row accepted')
except ValueError as e:
    check('an imported `-` data row is red and names dll!name', 'msvcrt.dll!_fmode' in str(e) and '_commode' not in str(e), True)
try:
    iat.c_source(db, 'T' * 64, iat.imported_set(db, [('msvcrt.dll', '#7')]))
    fails.append('a `-` data row imported by ordinal accepted')
except ValueError as e:
    check('a `-` data row imported by its ordinal is red', 'msvcrt.dll!_commode' in str(e), True)
dt = iat.data_tsv(drows, 'T' * 64)
check('iat_data.tsv: table_id line, then dll name ordinal kind native_symbol', dt.splitlines(),
      ['# table_id ' + 'T' * 64] + ['%s\t%s\t%s\tdata\t%s' % (r['dll'], r['name'], r['ordinal'], r['native_symbol']) for r in drows])
import re
c_syms = re.findall(r'FNVNX_IMPORT_DATA, \(uintptr_t\)&(\w+)\}', text)
check('iat_data.tsv rows = the data entries of import_keys.c, same order', [l.split('\t')[4] for l in dt.splitlines()[1:]], c_syms)
check('the table_id of both is the one given', ('"%s"' % ('T' * 64) in text, dt.splitlines()[0].endswith('T' * 64)), (True, True))
hdr = iat.key_header(db, 'T' * 64)
kt_rows = [l.split('\t') for l in iat.keys_tsv(db, 'T' * 64).splitlines()[1:]]
hrows = re.findall(r'\{"([^"]*)", "([^"]*)", 0x([0-9a-f]{8})u\},', hdr)
check('key header: one row per function row, sorted by (dll, name), the key of keys.tsv', sorted((d, n, int(k, 16)) for d, n, k in hrows),
      sorted((r[1], r[2], int(r[0], 16)) for r in kt_rows))
check('key header: a data row has no constant, the row count and table_id are written', ('_iob' in hdr, 'nrows = %d;' % len(hrows), ' table_id ' + 'T' * 64), (False, 'nrows = 1;', ' table_id ' + 'T' * 64))
full = iat.keys_tsv(db, 'T' * 64).splitlines()
sub1 = iat.imported_tsv(db, 'T' * 64, {('kernel32.dll', 'Sleep'), ('msvcrt.dll', '_iob')}).splitlines()
check('imported.tsv: the header of keys.tsv, then only the imported function rows (a data row never), keys.tsv order and columns', sub1, [full[0]] + [l for l in full[1:] if l.split('\t')[1:3] == ['kernel32.dll', 'Sleep']])
check('imported.tsv: nothing imported leaves the header only', iat.imported_tsv(db, 'T' * 64, set()), full[0] + '\n')
check('imported.tsv: every function row imported equals keys.tsv', iat.imported_tsv(db, 'T' * 64, {(r['dll'], r['name']) for r in db}).splitlines(), full)
for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
