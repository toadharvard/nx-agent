#!/usr/bin/env python3
"""tests/s5 - emu_guest.py's host VM service (FNVNX_PLAT_VM_*, h_ext_call module 11 fn 4..7), the emulator's side of
toolchain/host/host_vm.inc: reserve at the hint when the range is free (below 4 GiB, outside the image window, not
mapped, not reserved), else 0; reserve without a hint picks a free range; the (0, 0) probe answers 0, not
FNVNX_EXT_NO_MODULE; commit maps zeroed RW (not X) pages; decommit zeroes them; release unmaps only the committed
pages. On a bare unicorn instance (no guest).

    python3 -I tests/s5/test_emu_vm.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import emu_guest  # noqa: E402
from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, UC_PROT_READ, UC_PROT_WRITE, Uc  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:400]))
    fails += not cond


def main():
    e = emu_guest.Emu.__new__(emu_guest.Emu)
    e.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    e.uc.mem_map(0x40000000, 0x100000, UC_PROT_READ | UC_PROT_WRITE)   # stands in for the guest heap
    ARGS = 0x40000000

    def call(fn, a0, a1):
        e.uc.mem_write(ARGS, struct.pack('<QQ', a0, a1))
        return e.h_ext_call(11, fn, ARGS, 2)

    ok('probe (0, 0) -> 0: the service exists', call(4, 0, 0) == 0)
    ok('other platform fns still FNVNX_EXT_NO_MODULE', e.h_ext_call(11, 99, 0, 0) == 0xFFFFFFFFFFFFFF01)
    a = call(4, 0xff000000, 0x600000)
    ok('reserve at a free hint', a == 0xff000000, hex(a))
    ok('the same range again: 0', call(4, 0xff200000, 0x100000) == 0)
    ok('a mapped range: 0', call(4, 0x40000000, 0x10000) == 0)
    ok('the image window: 0', call(4, 0x00400000, 0x10000) == 0)
    ok('past 4 GiB: 0', call(4, 0xfff00000, 0x200000) == 0)
    ok('unaligned hint rounds down to 64 KiB', call(4, 0x10012345, 0x1000) == 0x10010000)
    b = call(4, 0, 0x20000)
    ok('no hint: a free range', b and b >= 0x80000000 and b != a, hex(b))
    ok('nothing mapped before a commit', not any(r[0] <= a < r[1] for r in e.uc.mem_regions()))
    ok('commit inside the reservation', call(5, a + 0x1000, 0x2000) == 0)
    reg = [r for r in e.uc.mem_regions() if r[0] <= a + 0x1000 <= r[1]]
    ok('committed pages RW, not X', reg and reg[0][2] == UC_PROT_READ | UC_PROT_WRITE, reg)
    ok('fresh pages read zero', bytes(e.uc.mem_read(a + 0x1000, 0x2000)) == bytes(0x2000))
    e.uc.mem_write(a + 0x1000, b'xyz')
    ok('commit again over committed pages: kept', call(5, a, 0x4000) == 0 and bytes(e.uc.mem_read(a + 0x1000, 3)) == b'xyz')
    ok('commit outside any reservation fails', call(5, 0x90000000, 0x1000) != 0)
    ok('commit past the reservation end fails', call(5, a + 0x5ff000, 0x2000) != 0)
    ok('decommit zeroes', call(6, a + 0x1000, 0x1000) == 0 and bytes(e.uc.mem_read(a + 0x1000, 3)) == bytes(3))
    ok('release not at the base fails', call(7, a + 0x1000, 0) != 0)
    ok('release unmaps the committed pages only', call(7, a, 0) == 0 and
       not any(r[0] <= a + 0x1000 <= r[1] for r in e.uc.mem_regions()))
    ok('the range is free again', call(4, 0xff000000, 0x10000) == 0xff000000)
    ok('release of a reservation never committed', call(7, b, 0) == 0)
    print('test_emu_vm: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
