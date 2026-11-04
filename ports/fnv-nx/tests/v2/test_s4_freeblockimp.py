#!/usr/bin/env python3
"""V2 check of S4 795c6be (platform/crt/msvc_names.c): FreeBlockImp, the decomp's `call [0x010b7eb0]`, is
MemoryManager::Deallocate on the global heap, i.e. FormHeap_Free (0x00401030). Read from the player's exe.

    FNVNX_EXE=<FalloutNV.exe> python3 tests/v2/test_s4_freeblockimp.py      (skip without the exe; nothing is copied)

  F1  the .rdata word at 0x010b7eb0 holds the code address the commit names (0x00cb9cb0)
  F2  that code is `mov ecx, <heap>; jmp <dealloc>`, both as named (0x011f6238, 0x00aa4060)
  F3  FormHeap_Free 0x00401030 calls the same dealloc with ecx = the getter 0x00401020's result = the same heap
  F4  every `call [0x010b7eb0]` in .text is guarded by a null test of its argument (the weak FreeBlockImp skips NULL);
      two sites set ecx to the object first (slot 2 of vftable 0x010b7ea8, devirtualized): the target ignores it
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'v1'))
from test_s1b_pop_boot import PE  # noqa: E402  (V1's section reader)


def main():
    exe = os.environ.get('FNVNX_EXE') or os.environ.get('PLAYER_EXE')
    if not exe or not os.path.isfile(exe):
        print('skip: no FNVNX_EXE')
        return 0
    import capstone
    pe, md = PE(exe), capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def ins(a, n):
        out = []
        for i in md.disasm(pe.read(a, 64), a):
            out.append((i.mnemonic, i.op_str))
            if len(out) >= n or i.mnemonic in ('ret', 'jmp'):
                break
        return out
    fails = 0

    def check(c, what):
        nonlocal fails
        print('%s %s' % ('ok' if c else 'FAIL', what))
        fails += not c
    w = struct.unpack('<I', pe.read(0x010b7eb0, 4))[0]
    check(w == 0x00cb9cb0, 'F1 [0x010b7eb0] = %#x' % w)
    check(ins(w, 2) == [('mov', 'ecx, 0x11f6238'), ('jmp', '0xaa4060')], 'F2 %s' % ins(w, 2))
    f = ins(0x00401030, 9)
    check(('call', '0x401020') in f and ('mov', 'ecx, eax') in f and ('call', '0xaa4060') in f
          and ins(0x00401020, 3)[2] == ('mov', 'eax, 0x11f6238'), 'F3 FormHeap_Free = Deallocate(global heap)')
    txt = next(s for s in pe.secs if s[0] == 0x00401000)
    data = pe.b[txt[2]:txt[2] + txt[1]]
    pat = b'\xff\x15' + struct.pack('<I', 0x010b7eb0)
    sites, i = [], data.find(pat)
    while i >= 0:
        sites.append(txt[0] + i)
        i = data.find(pat, i + 1)
    # each site: test eax, eax; je skip; push eax; call [..]
    # test eax, eax; je; push eax; [mov ecx, r32: a devirtualized slot-2 call of vftable 0x010b7ea8, whose target
    # overwrites ecx with the heap]; call [0x010b7eb0]
    guarded = [s for s in sites if (pe.read(s - 5, 5)[:3] == b'\x85\xc0\x74' and pe.read(s - 1, 1) == b'\x50') or
               (pe.read(s - 7, 7)[:3] == b'\x85\xc0\x74' and pe.read(s - 3, 1) == b'\x50' and pe.read(s - 2, 1) in
                (b'\x8b', b'\x89'))]
    check(sites and len(guarded) == len(sites), 'F4 %d call sites, %d null-guarded' % (len(sites), len(guarded)))
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
