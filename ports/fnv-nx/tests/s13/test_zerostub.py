#!/usr/bin/env python3
"""tests/s13/test_zerostub.py - native-zero-stub is not raised for a placeholder of a trivial exe leaf (S2 W6),
on hand-written x86 bytes (no exe content)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import retsrc  # noqa: E402
import causes  # noqa: E402

FAILS = 0


def check(cond, what):
    global FAILS
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    FAILS += 0 if cond else 1


class FakePE:
    def __init__(self, code):
        self.code = code

    def read(self, va, n):
        for base, b in self.code.items():
            if base <= va < base + len(b):
                return (b[va - base:] + b'\xcc' * n)[:n]
        raise ValueError(hex(va))


LEAF_AL = bytes.fromhex('55 8b ec 51 8a 45 ff 8b e5 5d c3'.replace(' ', ''))      # push ebp; ...; mov al,[ebp-1]; ret
LEAF_ARG = bytes.fromhex('55 8b ec 51 8b 45 08 8b e5 5d c3'.replace(' ', ''))     # mov eax,[ebp+8]
CALLS = bytes.fromhex('55 8b ec e8 00 00 00 00 5d c3'.replace(' ', ''))           # call
STORES = bytes.fromhex('8b 44 24 04 c7 00 01 00 00 00 c3'.replace(' ', ''))      # mov dword [eax], 1
retsrc._PE[:] = [FakePE({0x1000: LEAF_AL, 0x2000: LEAF_ARG, 0x3000: CALLS, 0x4000: STORES})]

check(retsrc.trivial_leaf(0x1000) is True, 'a leaf returning an uninitialised al is trivial')
check(retsrc.trivial_leaf(0x2000) is True, 'a leaf returning its argument is trivial')
check(retsrc.trivial_leaf(0x3000) is False, 'a function that calls is not trivial')
check(retsrc.trivial_leaf(0x4000) is False, 'a store through a non-frame pointer is not trivial')
check(causes._zero_stub_of_leaf({'arm_zero_stub': ['a'], 'arm_zero_stub_exe': ['00001000', '00002000']}),
      'placeholders of trivial leaves only: not native-zero-stub')
check(not causes._zero_stub_of_leaf({'arm_zero_stub': ['a'], 'arm_zero_stub_exe': ['00001000', '00003000']}),
      'one placeholder of a working function: still native-zero-stub')
check(not causes._zero_stub_of_leaf({'arm_zero_stub': ['a'], 'arm_zero_stub_exe': ['?']}),
      'unknown exe function: still native-zero-stub')
check(not causes._zero_stub_of_leaf({'arm_zero_stub': ['a']}), 'no exe addresses recorded: still native-zero-stub')
print('zerostub: %d failed' % FAILS)
sys.exit(1 if FAILS else 0)
