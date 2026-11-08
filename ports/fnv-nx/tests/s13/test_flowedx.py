#!/usr/bin/env python3
"""test_flowedx.py - an edx the exe callee never reads is no argument (EQ, flow: 0x004eaa00 -> 0x00ba3390).

The tree declares 0x00ba3390 as `__fastcall (AccumTaskReceiver *, void *, ...)`: the unnamed second parameter is edx,
a placeholder (the link's `edx-dummy` bridge class, 587 entries). MSVC's callers leave whatever edx held; the native
passes its first real argument there. retsrc.edx_unread proves from the exe that the code never reads edx.
  X1 never touches edx: unread        X2 reads edx: read
  X3 overwrites edx before reading: unread     X4 writes only dl, then reads edx: read
  X5 jumps to a leaf that reads edx: read      X6 jumps to one that does not: unread
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ['FNVNX_DFWORK'] = tempfile.mkdtemp(prefix='s13flowedx')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import retsrc  # noqa: E402

BASE = 0x00401000


class FakePE:
    def __init__(self, code):
        self.data = bytes(code) + b'\xcc' * 64

    def section_of(self, a):
        return (BASE, len(self.data), 0x20000000, self.data)


def run(code):
    retsrc._PE[:] = [FakePE(code)]
    retsrc._EDX.clear()
    f = getattr(retsrc, 'edx_unread', None)
    return f(BASE) if f else 'missing'


def main():
    leaf_read = bytes.fromhex('89d0' 'c3')            # mov eax, edx; ret       at BASE + 0x20
    leaf_none = bytes.fromhex('31c0' 'c3')            # xor eax, eax; ret
    pad = lambda b, n: b + b'\xcc' * (n - len(b))     # noqa: E731
    cases = [
        ('X1', bytes.fromhex('55' '8bec' '8b4508' '5d' 'c20400'), True),
        ('X2', bytes.fromhex('8bc2' 'c3'), False),
        ('X3', bytes.fromhex('ba01000000' '8bc2' 'c3'), True),
        ('X4', bytes.fromhex('b201' '8bc2' 'c3'), False),
    ]
    for name, leaf, want in (('X5', leaf_read, False), ('X6', leaf_none, True)):
        main_ = bytes.fromhex('e9') + (0x20 - 5).to_bytes(4, 'little', signed=True) + b'\xc3'
        cases.append((name, pad(main_, 0x20) + leaf, want))
    fails = 0
    for name, code, want in cases:
        got = run(code)
        ok = got is want
        fails += not ok
        print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want))
    print('test_flowedx: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
