#!/usr/bin/env python3
"""Check of contract/l2consts.py (the `.fnvnx.const` records): every shape, and every error with its offset."""
import os, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): contract.X by its qualified name
from fnvnx.contract import l2consts as C

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def err(what, data, relocs, part, ptr=4):
    try:
        C.parse_consts(data, relocs, 'o.o:.fnvnx.const', ptr)
    except ValueError as e:
        if part not in str(e) or not str(e).startswith('o.o:.fnvnx.const: offset 0x'):
            fails.append('%s: message %r lacks %r' % (what, str(e), part))
        return
    fails.append('%s: no error' % what)


rec = lambda n, v: b'\0' * 4 + struct.pack('<I', n) + v + b'\0' * (-len(v) % 4)   # noqa: E731
R = lambda off, sym=1, add=0: (off, (sym, 1, add))   # noqa: E731

check('empty section', C.parse_consts(b'', {}, 'w', 4), [])
check('u32 value', C.parse_consts(rec(4, struct.pack('<I', 7)), dict([R(0)]), 'w', 4), [(0, 1, 0, struct.pack('<I', 7), None)])
check('void', C.parse_consts(rec(0, b''), dict([R(0)]), 'w', 4), [(0, 1, 0, b'', None)])
check('1-byte value is padded to 4', C.parse_consts(rec(1, b'\x05') + rec(0, b''), dict([R(0), R(12)]), 'w', 4),
      [(0, 1, 0, b'\x05', None), (12, 1, 0, b'', None)])
check('member addend kept', C.parse_consts(rec(0, b''), dict([R(0, 3, 16)]), 'w', 4)[0][1:3], (3, 16))
check('address value', C.parse_consts(rec(4, b'\0' * 4), dict([R(0), R(8, 5, 12)]), 'w', 4), [(0, 1, 0, b'\0' * 4, (5, 12))])
check('LP64 record', C.parse_consts(b'\0' * 8 + struct.pack('<I', 4) + struct.pack('<I', 9), dict([R(0)]), 'w', 8)[0][3], struct.pack('<I', 9))
check('consecutive void records: the next record\'s relocation is not a value relocation', C.parse_consts(rec(0, b'') * 2, dict([R(0), R(8)]), 'w', 4), [(0, 1, 0, b'', None), (8, 1, 0, b'', None)])
err('no function relocation', rec(4, b'\0' * 4), {}, 'no relocation')
err('truncated header', b'\0' * 6, dict([R(0)]), 'a record is')
err('length leaves the section', rec(4, b'\0' * 4)[:10], dict([R(0)]), 'leaves the section')
err('function pointer bytes not zero', b'\1' + rec(0, b'')[1:], dict([R(0)]), 'not zero')
err('second record malformed, offset named', rec(0, b'') + rec(0, b''), dict([R(0)]), 'offset 0x8')
err('stray relocation', rec(0, b''), dict([R(0), R(2)]), 'neither a record')
err('value relocation on a non-pointer length', rec(2, b'\0\0'), dict([R(0), R(8)]), 'not 4')
err('address value bytes not zero', rec(4, b'\1\0\0\0'), dict([R(0), R(8)]), 'not zero')
err('padding not zero', b'\0' * 4 + struct.pack('<I', 1) + b'\1\1\0\0', dict([R(0)]), 'padding')

if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('all passed')
