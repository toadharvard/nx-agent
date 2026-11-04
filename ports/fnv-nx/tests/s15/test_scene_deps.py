#!/usr/bin/env python3
"""Self-test of tools/perf/scene_deps.py (stream S15-scene, docs/PERF.md 6.12) on synthetic x86 code, no exe needed:

  * a typed vcall (`mov eax, [ecx]; mov edx, [eax+0xa4]; call edx` on `this`) resolves to the slot of the class and
    of every class deriving from it, and to no sibling class;
  * a field load (NiNode child array at +0xa0, indexed) types the child as NiAVObject;
  * an untyped receiver's NiObject-base virtual is the base-virtual class, not walked;
  * global stores, loads, `lock` forms and address-taken globals are recorded with their access;
  * a --stop boundary is listed and not walked; a jmp to a known start is a tail call.

    python3 -I tests/s15/test_scene_deps.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'perf'))
try:
    import scene_deps as sd
except ImportError as e:   # capstone / pefile missing
    print('skip: %s' % e)
    sys.exit(0)

fails = 0


def check(ok, what):
    global fails
    print('%s %s' % ('ok  ' if ok else 'FAIL', what))
    fails += not ok


TEXT, DATA = 0x401000, 0x1100000


class FakeExe:
    """the parts of scene_deps.Exe the walker uses, over a bytes blob at TEXT"""

    def __init__(self, code):
        self.code = code
        self.iat = {}
        self.text = (TEXT, len(code), 0, len(code))
        self.dat = (DATA, 0x1000, 0, 0)

    def read(self, va, n):
        o = va - TEXT
        return (self.code[o:o + n] + b'\xcc' * n)[:n] if 0 <= o < len(self.code) else None

    def u32(self, va):
        b = self.read(va, 4)
        return struct.unpack('<I', b)[0] if b else None

    def in_text(self, va):
        return TEXT <= va < TEXT + len(self.code)

    def in_data(self, va):
        return DATA <= va < DATA + 0x1000


def asm_at(blob, off, code):
    blob[off:off + len(code)] = code


blob = bytearray(b'\xcc' * 0x400)
F_ROOT, F_A, F_B, F_SIB, F_STOP, F_BASE, F_TAIL = 0x000, 0x100, 0x140, 0x180, 0x1c0, 0x200, 0x240
# root (NiNode::X): this->vslot 41; child = [[ecx+0xa0] + i*4]->vslot 46; untyped [esi]->vslot 1; store, lock add,
# address-taken global; call the stop; tail jmp
asm_at(blob, F_ROOT, bytes.fromhex(
    '8b01'               # mov eax, [ecx]
    '8b90a4000000'       # mov edx, [eax+0xa4]       slot 41
    '89cf'               # mov edi, ecx
    'ffd2'               # call edx
    '8b97a0000000'       # mov edx, [edi+0xa0]       child array
    '8b349a'             # mov esi, [edx+ebx*4]      child: NiAVObject
    '8b06'               # mov eax, [esi]
    'ff90b8000000'       # call [eax+0xb8]           slot 46 on NiAVObject
    '8b3b'               # mov edi, [ebx]            untyped vptr
    'ff5704'             # call [edi+4]              slot 1, untyped
    'a310001001'         # mov [0x1100010], eax      global write
    'f0ff0514001001'     # lock inc [0x1100014]      atomic
    '6818001001'         # push 0x1100018            address taken
    'e8' + struct.pack('<i', (TEXT + F_STOP) - (TEXT + F_ROOT + 0x38)).hex() +   # call stop
    'e9' + struct.pack('<i', (TEXT + F_TAIL) - (TEXT + F_ROOT + 0x3d)).hex()     # jmp tail
))
for f in (F_A, F_B, F_SIB, F_STOP, F_BASE):
    asm_at(blob, f, b'\xc3')
asm_at(blob, F_TAIL, bytes.fromhex('a120001001c3'))   # mov eax, [0x1100020]; ret
exe = FakeExe(bytes(blob))


class FakeClasses(sd.Classes):
    def __init__(self):
        n = 50
        base = [TEXT + F_BASE] * n
        self.bases = {'NiObject': ['NiObject', 'NiRefObject'], 'NiRefObject': ['NiRefObject'],
                      'NiAVObject': ['NiAVObject', 'NiObjectNET', 'NiObject', 'NiRefObject'],
                      'NiNode': ['NiNode', 'NiAVObject', 'NiObjectNET', 'NiObject', 'NiRefObject'],
                      'NiProperty': ['NiProperty', 'NiObjectNET', 'NiObject', 'NiRefObject']}
        self.vt = {'NiRefObject': base[:2], 'NiObject': base[:35], 'NiAVObject': list(base), 'NiNode': list(base),
                   'NiProperty': list(base)}
        self.vt['NiNode'][41] = TEXT + F_A
        self.vt['NiAVObject'][46] = self.vt['NiNode'][46] = TEXT + F_B   # NiNode inherits it
        self.vt['NiProperty'][41] = TEXT + F_SIB   # a sibling family's slot 41: must not be reached
        self.of_fn = {}
        self._cand = {}


cl = FakeClasses()
w = sd.Walker(exe, cl, {TEXT + F_TAIL}, [(TEXT + F_STOP, 'stop')])
keys, untyped = w.reach([(TEXT + F_ROOT, 'NiNode')])
fns = {k[0] - TEXT for k in keys}
check(F_A in fns, 'typed vcall on this: NiNode slot 41 reached')
check(F_SIB not in fns, 'a sibling family (NiProperty) slot 41 is not reached')
check(F_B in fns and (TEXT + F_B, 'NiAVObject', False) in keys, 'child from the +0xa0 array typed NiAVObject: slot 46 reached')
check(('NiRefObject', 1) in {k: v for k, v in w.base_calls.items()} and F_BASE not in fns,
      'untyped NiRefObject slot 1 is a base-virtual call, not walked')
check(F_TAIL in fns, 'jmp to a known start is a tail call')
g = group = sd.group_map(sd.Walker(exe, cl, {TEXT + F_TAIL}, [(TEXT + F_STOP, 'stop')]), [(TEXT + F_ROOT, 'NiNode')])
gl = {a - DATA: v['flags'] for a, v in g['globals'].items()}
check(gl.get(0x10) == {'w'}, 'mov [g], eax is a write: %s' % sorted(gl.get(0x10, ())))
check({'atomic', 'w'} <= gl.get(0x14, set()), 'lock inc [g] is an atomic write')
check({'addr', 'r', 'w'} <= gl.get(0x18, set()), 'push &g is address taken (read+write)')
check(gl.get(0x20) == {'r'}, 'the tail callee reads its global')
check(g['stopped'] == {'stop': 1}, 'the stop is listed, not walked: %s' % g['stopped'])
print('%d failed' % fails)
sys.exit(1 if fails else 0)
