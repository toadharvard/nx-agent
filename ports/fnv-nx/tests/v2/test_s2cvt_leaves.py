#!/usr/bin/env python3
"""V2 check of S2c-vt 09524da (tools/link/exevft.leaf_constant, fnvlink.emit_const_stub): a trapped slot whose exe
body is a constant x86 leaf gets `mov w0, #imm; ret`. That is the original's meaning only when the constant is a
value the native side reads alike: not a code address (the native would call x86 code) and not an x86 vftable
(native objects carry native vtables); data-image addresses are fine (the data image is mapped at its address).

    FNVNX_EXE=<FalloutNV.exe> python3 tests/v2/test_s2cvt_leaves.py      (skip without the exe; nothing is copied)

  L1  no constant leaf in any exe vftable slot returns a .text address
  L2  none returns the address of an x86 vftable (slot 0 after a COL pointer)
  L3  an x87 leaf (`fldz; ret`, `fld1; ret`: a float result) is not taken as an eax constant (synthetic bytes)
note: exe_slot_fill's target_for never takes an address's MSVC entry (__fnvnx_msvc_<a>, thisadj): a trap slot of a
secondary native vtable whose exe slot is a secondary-base entry would get the primary entry. No link has run the
fill yet (newest link defb010a2a1b has no slot_fill.tsv), so this is not counted.
"""
import os
import struct
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))


def main():
    exe = os.environ.get('FNVNX_EXE')
    if not exe or not os.path.isfile(exe):
        print('skip: no FNVNX_EXE')
        return 0
    import exevft
    import thisadj
    import fnvnx_pe
    img = fnvnx_pe.Image(fnvnx_pe.PE(open(exe, 'rb').read()))
    vfts = set()
    for m in img.maps:
        for i in range(m.file_bytes // 4 - 1):
            w, nxt = struct.unpack_from('<II', m.data, 4 * i)
            if 0x00fdf000 <= w < 0x02100000 and 0x00401000 <= nxt < 0x00fdf000:
                vfts.add(m.va + 4 * i + 4)
    slots = set()
    for _, ss in thisadj.vftables(exe):
        slots.update(ss)
    consts = {a: exevft.leaf_constant(exe, a) for a in sorted(slots)}
    consts = {a: v for a, v in consts.items() if isinstance(v, int)}
    code = sorted(a for a, v in consts.items() if 0x00401000 <= v < 0x00fdf000)
    vft = sorted(a for a, v in consts.items() if v in vfts)
    fails = 0
    for case, ok, what in (('L1', not code, '%d constant leaves, %d return a .text address %s' %
                            (len(consts), len(code), ['%08x' % a for a in code[:5]])),
                           ('L2', not vft, '%d return an x86 vftable %s' % (len(vft), ['%08x' % a for a in vft[:5]]))):
        print('%s %s %s' % ('ok' if ok else 'FAIL', case, what))
        fails += not ok
    # L3: leaf_constant on synthetic x87 bytes, through its own byte reader
    orig = exevft._text
    try:
        for body in (b'\xd9\xee\xc3', b'\xd9\xe8\xc3', b'\x55\x8b\xec\xd9\xee\x5d\xc3'):
            pe = type('P', (), {'image_base': 0, 'rva_to_off': lambda self, rva, n: 0})()
            exevft._text = lambda path, b=body: (pe, b + b'\xcc' * 32)
            v = exevft.leaf_constant('synthetic', 0x1000)
            ok = v is None
            print('%s L3 x87 leaf %s -> %r' % ('ok' if ok else 'FAIL', body.hex(), v))
            fails += not ok
    finally:
        exevft._text = orig
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
