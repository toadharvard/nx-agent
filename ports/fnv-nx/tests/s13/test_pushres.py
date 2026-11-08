#!/usr/bin/env python3
"""test_pushres.py - `push ecx` that a float argument overwrites is a reservation, not ecx passed on (R2, farm
baseline 588863: 0x00706f50, 0x00b799a0, 0x00e0c700).
  P1  push ecx; fld [eax]; fstp dword [esp]; call; add esp, 4   -> reservation (not a call argument)
  P2  push ecx; fld [eax]; fstp dword [esp + 4]? (another slot)  -> push eax first: ecx's slot untouched, argument
  P3  push eax; push ecx; push esi; call; add esp, 0xc           -> call argument (0x00d409b0, R4 flow)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import capstone  # noqa: E402
import classify  # noqa: E402

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CALL = bytes.fromhex('e800000000')
FAIL = 0


def ins(code):
    return list(MD.disasm(code, 0x401000))


def check(name, got, want):
    global FAIL
    ok = got == want
    FAIL += not ok
    print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want))


def main():
    # push ecx; fld dword [eax]; fstp dword [esp]; call; add esp, 4
    p1 = bytes.fromhex('51' 'd900' 'd91c24') + CALL + bytes.fromhex('83c404')
    check('P1', classify._push_is_call_arg(ins(p1), 0), False)
    # push ecx; push eax; fld [eax]; fstp dword [esp] (eax's slot); call; add esp, 8: ecx stays an argument
    p2 = bytes.fromhex('51' '50' 'd900' 'd91c24') + CALL + bytes.fromhex('83c408')
    check('P2', classify._push_is_call_arg(ins(p2), 0), True)
    # push eax; push ecx; push esi; call; add esp, 0xc (ecx at index 1)
    p3 = bytes.fromhex('50' '51' '56') + CALL + bytes.fromhex('83c40c')
    check('P3', classify._push_is_call_arg(ins(p3), 1), True)
    # push ecx; mov dword [esp], 0x3f800000; call; add esp, 4
    p4 = bytes.fromhex('51' 'c7042400 00803f') + CALL + bytes.fromhex('83c404')
    check('P4', classify._push_is_call_arg(ins(p4), 0), False)
    print('test_pushres: %d failed' % FAIL)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
