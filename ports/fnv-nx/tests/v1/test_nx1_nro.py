#!/usr/bin/env python3
"""test_nx1_nro.py - V1 (D14) against NX1 5256786: toolchain/tools/elf2nro.py and tests/nx1/check_nro.py.

    python3 -I tests/v1/test_nx1_nro.py      -> one line per case, exit 1 when a case shows a defect

Synthetic inputs only (tests/nx1/fixture). Layout reference: switchbrew NRO (NroStart 0x00: u32 branch, u32 MOD0
offset; NroHeader 0x10..0x80: "NRO0", version, size, flags, 3 x {file_off, size} at 0x20, bss at 0x38, module id at
0x40) and nx-hbloader loadNro(): text R-X, rodata R--, data+bss RW- before the jump, so libnx's __nx_dynamic (which
runs after it, in crt0) faults on any relocation it writes outside data+bss.

N1  the packed fixture's header fields sit at the documented offsets and the image is page aligned (elf2nro).
N2  check_nro passes no NRO whose RELATIVE relocation targets rodata: hbloader maps rodata R--, the store faults.
N3  check_nro passes no NRO whose header size is not page aligned (hbloader maps the image by pages).
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tests', 'nx1'))
sys.path.insert(0, os.path.join(PORT, 'toolchain', 'tools'))
import check_nro  # noqa: E402
import elf2nro  # noqa: E402
import nxelf  # noqa: E402


def main():
    if not shutil.which('aarch64-linux-gnu-as'):
        print('skip: aarch64-linux-gnu binutils missing')
        return 0
    t = tempfile.mkdtemp(prefix='v1-nx1-')
    bad = 0

    def case(cid, ok, why):
        nonlocal bad
        print('%s %s' % (cid, 'ok' if ok else 'DEFECT: ' + why))
        bad += not ok
    try:
        elf = os.path.join(t, 'fx.elf')
        subprocess.run([os.path.join(PORT, 'tests', 'nx1', 'fixture', 'build.sh'), elf], check=True)
        e = nxelf.read(elf)
        good = elf2nro.build_nro(e, None, None)
        loads = [p for p in e.phdrs if p.type == nxelf.PT_LOAD]
        magic, ver, size, flags = struct.unpack_from('<4sIII', good, 0x10)
        segs = [struct.unpack_from('<II', good, 0x20 + 8 * i) for i in range(3)]
        mod = struct.unpack_from('<I', good, 4)[0]
        n1 = (magic == b'NRO0' and ver == 0 and size == len(good) and size % 0x1000 == 0
              and [s[0] for s in segs] == [p.vaddr for p in loads[:3]]
              and all(s[1] % 0x1000 == 0 and s[1] >= p.filesz for s, p in zip(segs, loads))
              and good[mod:mod + 4] == b'MOD0' and good[0x40:0x60] == (e.build_id() or b'')[:0x20].ljust(0x20, b'\0'))
        case('N1', n1, 'header %r v%d size %#x segs %s mod0 %#x' % (magic, ver, size, segs, mod))

        nro = os.path.join(t, 'fx.nro')
        dyn = next(p for p in e.phdrs if p.type == nxelf.PT_DYNAMIC).vaddr

        def add_dyn(b, tag, val):
            i = dyn
            while struct.unpack_from('<q', b, i)[0] != 0:
                i += 16
            struct.pack_into('<qQqQ', b, i, tag, val, 0, 0)

        # N2: one R_AARCH64_RELATIVE (1027) whose target is the first word of rodata
        b = bytearray(good)
        ra = segs[1][0] + segs[1][1] - 0x100          # the table in rodata padding (readable: fine)
        struct.pack_into('<QQq', b, ra, segs[1][0], 1027, 0)
        add_dyn(b, 7, ra)
        add_dyn(b, 8, 24)
        add_dyn(b, 9, 24)
        open(nro, 'wb').write(b)
        c = check_nro.check(nro, None, None, None, False)
        case('N2', not c.passed, 'check_nro passes a RELATIVE relocation into rodata (%#x, mapped R-- by hbloader): '
             'dyn.rela_targets accepts rodata' % segs[1][0])

        # N3: header size not page aligned (data segment size 0x1004)
        b = bytearray(good)
        struct.pack_into('<I', b, 0x34, segs[2][1] + 4)
        struct.pack_into('<I', b, 0x18, size + 4)
        b += bytes(4)
        open(nro, 'wb').write(b)
        c = check_nro.check(nro, None, None, None, False)
        case('N3', not c.passed, 'check_nro passes header size %#x' % (size + 4))
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('%d defects' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
