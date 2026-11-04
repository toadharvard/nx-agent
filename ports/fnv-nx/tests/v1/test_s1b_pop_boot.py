#!/usr/bin/env python3
"""test_s1b_pop_boot.py - V1 (D14) against S1b 20af359 (xinc1_pop_boot.s1b, the distance-1 boot callers), checked
on the owner's exe itself rather than on the pristine objects S1b used.

    FNVNX_EXE=/path/FalloutNV.exe python3 -I tests/v1/test_s1b_pop_boot.py   -> one line per claim, exit 1 on a defect
    (skips without FNVNX_EXE or capstone; the exe is agent-only game data, HANDOFF.md section 5)

Claims of the commit, each read from the exe's x86 code with capstone:
  P1  0x00559450 is a getter: it ends in a plain `ret` and reads no stack argument
  P2  0x0044ac40 ends in `ret 8` and reads [ebp+8] and [ebp+0xc]
  P3  0x00448420 three times: push 0; lea ecx,..; call 0x00559450; push eax; mov ecx,[0x01202d98]; call 0x0044ac40
  P4  0x004e3270 is a getter (plain `ret`)
  P5  0x004ebbc0 and 0x005b9f80 end in `ret 4`; 0x005b9f80 stores a float at +0x58
  P6  0x0086c010: push value (push ecx; fstp [esp]); push 0xc; call 0x004e3270; mov ecx,eax; call 0x004ebbc0; mov ecx,eax; call 0x005b9f80
Only addresses and mnemonics are in this file; the exe's bytes are read at run time.
"""
import os
import struct
import sys

try:
    import capstone
except ImportError:
    capstone = None


class PE:
    def __init__(self, path):
        self.b = open(path, 'rb').read()
        pe = struct.unpack_from('<I', self.b, 0x3c)[0]
        nsec = struct.unpack_from('<H', self.b, pe + 6)[0]
        opt = struct.unpack_from('<H', self.b, pe + 20)[0]
        self.base = struct.unpack_from('<I', self.b, pe + 24 + 28)[0]
        self.secs = []
        s = pe + 24 + opt
        for i in range(nsec):
            vsz, va, rsz, raw = struct.unpack_from('<IIII', self.b, s + 40 * i + 8)
            self.secs.append((self.base + va, max(vsz, rsz), raw))

    def read(self, va, n):
        for sva, sz, raw in self.secs:
            if sva <= va < sva + sz:
                o = raw + va - sva
                return self.b[o:o + n]
        raise KeyError(hex(va))


def func(pe, md, va, limit=4096):
    """Instructions from va up to and including the last `ret` before the next int3/nop padding (linear sweep)."""
    out = []
    code = pe.read(va, limit)
    for ins in md.disasm(code, va):
        out.append(ins)
        if ins.mnemonic == 'ret':
            nxt = code[ins.address - va + ins.size:ins.address - va + ins.size + 1]
            if nxt in (b'\xcc', b'\x90', b''):
                break
    return out


def rets(ins):
    return sorted({(i.op_str or '0') for i in ins if i.mnemonic == 'ret'})


def target(i):
    try:
        return int(i.op_str, 16)
    except ValueError:
        return None


def main():
    exe = os.environ.get('FNVNX_EXE')
    if not exe or not os.path.isfile(exe) or capstone is None:
        print('skip: FNVNX_EXE=%r capstone=%s' % (exe, capstone is not None))
        return 0
    pe = PE(exe)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    bad = []

    def case(cid, ok, what):
        print('%s: %s - %s' % (cid, 'ok' if ok else 'DEFECT', what))
        if not ok:
            bad.append(cid)

    def stack_reads(ins):
        return [i for i in ins if '[ebp + 8' in i.op_str or '[ebp + 0xc' in i.op_str or '[esp + 4' in i.op_str]

    f = func(pe, md, 0x00559450)
    case('P1', rets(f) == ['0'] and not stack_reads(f), '0x00559450 rets %s, stack reads %d' % (rets(f), len(stack_reads(f))))
    f = func(pe, md, 0x0044ac40)
    reads = {r for i in f for r in ('[ebp + 8]', '[ebp + 0xc]') if r in i.op_str}
    case('P2', rets(f) == ['8'] and len(reads) == 2, '0x0044ac40 rets %s, reads %s' % (rets(f), sorted(reads)))

    f = func(pe, md, 0x00448420, 16384)
    n = 0
    for k in range(len(f) - 5):
        w = f[k:k + 6]
        if (w[0].mnemonic == 'push' and w[0].op_str == '0' and w[1].mnemonic == 'lea' and w[1].op_str.startswith('ecx')
                and w[2].mnemonic == 'call' and target(w[2]) == 0x00559450 and w[3].mnemonic == 'push'
                and w[3].op_str == 'eax' and w[4].mnemonic == 'mov' and w[4].op_str == 'ecx, dword ptr [0x1202d98]'
                and w[5].mnemonic == 'call' and target(w[5]) == 0x0044ac40):
            n += 1
    calls = sum(1 for i in f if i.mnemonic == 'call' and target(i) == 0x0044ac40)
    case('P3', n == 3 and calls == 3, '0x00448420: %d of %d calls of 0x0044ac40 have the claimed shape' % (n, calls))

    f = func(pe, md, 0x004e3270)
    case('P4', rets(f) == ['0'] and not stack_reads(f), '0x004e3270 rets %s, stack reads %d' % (rets(f), len(stack_reads(f))))
    f1, f2 = func(pe, md, 0x004ebbc0), func(pe, md, 0x005b9f80)
    st58 = [i for i in f2 if i.mnemonic in ('fstp', 'fst', 'movss', 'mov') and '+ 0x58]' in i.op_str.split(',')[0]]
    case('P5', rets(f1) == ['4'] and rets(f2) == ['4'] and bool(st58),
         '0x004ebbc0 rets %s, 0x005b9f80 rets %s, store at +0x58: %s' % (rets(f1), rets(f2),
                                                                           [i.mnemonic for i in st58]))
    f = func(pe, md, 0x0086c010, 4096)
    seq = None
    for k in range(3, len(f) - 5):
        w = f[k:k + 6]
        # the float is pushed as `push ecx; fld ..; fstp dword ptr [esp]` (MSVC's float push), then the 0xc
        fpush = [i for i in f[k - 3:k] if i.mnemonic == 'fstp' and i.op_str == 'dword ptr [esp]']
        if (fpush and w[0].mnemonic == 'push' and w[0].op_str == '0xc'
                and w[1].mnemonic == 'call' and target(w[1]) == 0x004e3270
                and w[2].mnemonic == 'mov' and w[2].op_str == 'ecx, eax' and target(w[3]) == 0x004ebbc0
                and w[4].mnemonic == 'mov' and w[4].op_str == 'ecx, eax' and target(w[5]) == 0x005b9f80):
            seq = w
    case('P6', seq is not None, '0x0086c010 push v; push 0xc; call 0x004e3270; mov ecx,eax; call 0x004ebbc0; ...'
         + ('' if seq else ' (not found: %s)' % '; '.join('%s %s' % (i.mnemonic, i.op_str) for i in f[:24])))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
