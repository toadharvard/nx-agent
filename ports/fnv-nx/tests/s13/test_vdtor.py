#!/usr/bin/env python3
"""tests/s13/test_vdtor.py - destructor shapes the harness did not see (difffuzz-20, vtable), synthetic x86 code
and hand-built RTTI only (no decomp, no exe bytes).

  deleting-wrapper-frameless  a scalar deleting destructor that tests its flags word at [esp+4] before any push and
                              stores its vftable between the test and the `je` (exeempty.deleting_wrapper); the
                              final vptr word of a destroyed object is representation (hkBuffer / hkObj helpers,
                              0x00d60bd0, 0x00dc9fd0)
  dtor-via-callee             a destructor whose vftable chain runs in another function: a thin wrapper whose only
                              call is a destructor chain, or a body that stores its class's vftable and jumps to the
                              base destructor, whose first store is a base class's (exeempty.dtor_via_callee;
                              0x00b43c50, 0x00a3d120). A constructor-shaped body, an unrelated callee and a call that
                              is no chain stay out.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get('DIFFFUZZ_DIR') or os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import exeempty   # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


BASE = 0x00401000
SIZE = 0x40000


class FakePE:
    def __init__(self, mem):
        self.mem = mem

    def section_of(self, va):
        if BASE <= va < BASE + SIZE:
            return (BASE, SIZE, 0x60000020, bytes(self.mem.get(BASE + i, 0) for i in range(SIZE)))
        return None

    def read(self, a, n):
        return bytes(self.mem.get(a + i, 0) for i in range(n))


def put(mem, a, data):
    for i, x in enumerate(data):
        mem[a + i] = x
    return a + len(data)


def u32(v):
    return struct.pack('<I', v & 0xffffffff)


def rel(frm_end, to):
    return u32(to - frm_end)


def build_exe():
    mem = {}

    def cls(vft, td, chd, names, bases_tds):
        col = vft - 0x40
        put(mem, vft - 4, u32(col))
        put(mem, col, u32(0) + u32(0) + u32(0) + u32(td) + u32(chd))
        put(mem, td, u32(0) + u32(0) + names[0].encode() + b'\0')
        arr = chd + 0x20
        put(mem, chd, u32(0) + u32(0) + u32(len(bases_tds)) + u32(arr))
        for i, t in enumerate(bases_tds):
            bcd = chd + 0x40 + 0x10 * i
            put(mem, arr + 4 * i, u32(bcd))
            put(mem, bcd, u32(t))
    cls(0x410010, 0x411000, 0x412000, ['.?AVBase@@'], [0x411000])
    cls(0x410210, 0x411200, 0x412200, ['.?AVLeaf@@'], [0x411200, 0x411000])
    cls(0x410310, 0x411300, 0x412300, ['.?AVOther@@'], [0x411300])
    # frameless deleting destructor: test byte ptr [esp+4], 1; push esi; mov esi, ecx; mov [esi], Leaf; je +6;
    # push esi; call X; mov eax, esi; pop esi; ret 4
    a = 0x401000
    put(mem, a, bytes.fromhex('f6442404' '01' '56' '8bf1' 'c706') + u32(0x410210) + bytes.fromhex('7406' '56') +
        b'\xe8' + rel(a + 17 + 5, 0x401800) + bytes.fromhex('8bc6' '5e' 'c20400'))
    # the same without the flag test
    a = 0x401050
    put(mem, a, bytes.fromhex('56' '8bf1' 'c706') + u32(0x410210) + bytes.fromhex('56') + b'\xe8' +
        rel(a + 11 + 5, 0x401800) + bytes.fromhex('8bc6' '5e' 'c20400'))
    # destructor body: [esi] = Leaf, then [esi] = Base; ret
    put(mem, 0x401300, bytes.fromhex('c706') + u32(0x410210) + bytes.fromhex('c706') + u32(0x410010) + b'\xc3')
    # thin wrapper: push ebp; mov ebp, esp; push ecx; mov [ebp-4], ecx; mov ecx, [ebp-4]; call 0x401300; mov esp, ebp;
    # pop ebp; ret
    a = 0x401100
    put(mem, a, bytes.fromhex('55' '8bec' '51' '894dfc' '8b4dfc') + b'\xe8' + rel(a + 10 + 5, 0x401300) +
        bytes.fromhex('8be5' '5d' 'c3'))
    # the same wrapper around a function that is no chain (one store)
    put(mem, 0x401340, bytes.fromhex('c706') + u32(0x410210) + b'\xc3')
    a = 0x401140
    put(mem, a, bytes.fromhex('55' '8bec' '51' '894dfc' '8b4dfc') + b'\xe8' + rel(a + 10 + 5, 0x401340) +
        bytes.fromhex('8be5' '5d' 'c3'))
    # tail form: mov [ecx], Leaf; jmp 0x401320 where mov [ecx], Base; ret
    a = 0x401200
    put(mem, a, bytes.fromhex('c701') + u32(0x410210) + b'\xe9' + rel(a + 6 + 5, 0x401320))
    put(mem, 0x401320, bytes.fromhex('c701') + u32(0x410010) + b'\xc3')
    # constructor-shaped tail: mov [ecx], Base; jmp where mov [ecx], Leaf
    a = 0x401240
    put(mem, a, bytes.fromhex('c701') + u32(0x410010) + b'\xe9' + rel(a + 6 + 5, 0x401360))
    put(mem, 0x401360, bytes.fromhex('c701') + u32(0x410210) + b'\xc3')
    # tail to an unrelated class
    a = 0x401280
    put(mem, a, bytes.fromhex('c701') + u32(0x410210) + b'\xe9' + rel(a + 6 + 5, 0x401380))
    put(mem, 0x401380, bytes.fromhex('c701') + u32(0x410310) + b'\xc3')
    return mem


exeempty._PE[:] = [FakePE(build_exe())]
for _c in ('_WRAP', '_CHAIN', '_VIA'):
    if hasattr(exeempty, _c):
        getattr(exeempty, _c).clear()

check(exeempty.deleting_wrapper(0x401000),
      'deleting-wrapper-frameless: test [esp+4], 1; push; mov; mov [esi], vft; je .. call .. ret 4 is one')
check(not exeempty.deleting_wrapper(0x401050), 'deleting-wrapper-frameless: no flag test, no wrapper')
try:
    check(exeempty.dtor_via_callee(0x401100), 'dtor-via-callee: a thin wrapper whose only call is a destructor chain')
    check(not exeempty.dtor_via_callee(0x401140), 'dtor-via-callee: a wrapper around a function that is no chain')
    check(exeempty.dtor_via_callee(0x401200), 'dtor-via-callee: Leaf vftable, then jmp to a body storing Base')
    check(not exeempty.dtor_via_callee(0x401240), 'dtor-via-callee: Base vftable then a jmp to Leaf is a constructor')
    check(not exeempty.dtor_via_callee(0x401280), 'dtor-via-callee: a jmp to an unrelated class stays out')
    check(not exeempty.dtor_via_callee(0x401300), 'dtor-via-callee: a chain body itself is dtor_chain, not a wrapper')
except AttributeError as e:
    check(False, 'dtor-via-callee: %s' % e)

if FAIL:
    print('FAILED: %d' % len(FAIL))
    sys.exit(1)
print('ok')
