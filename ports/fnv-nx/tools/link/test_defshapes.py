#!/usr/bin/env python3
"""W15: defshapes.py sub-classifies code-defmissing vtable addresses by exe shape, and thisadj.thunk_layout_ok /
slot_otts gate an adjustor thunk's x86 immediate against the native vtable's offset-to-top. Hand-made bytes and
symbols (no game content).

    python3 tools/link/test_defshapes.py
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import defshapes  # noqa: E402
import test_exevft as tx  # noqa: E402
import thisadj  # noqa: E402
import vtables  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def T(k):
    return tx.BASE + tx.TEXT_RVA + 0x30 * k


def call(at, i, to):
    return b'\xe8' + struct.pack('<i', to - (at + i + 5))


def jmp(at, i, to):
    return b'\xe9' + struct.pack('<i', to - (at + i + 5))


def sdd_global(at, d, dl):
    return (b'\x56\x8b\xf1' + call(at, 3, d) + b'\xf6\x44\x24\x08\x01\x74\x09\x56' + call(at, 13, dl) +
            b'\x83\xc4\x04\x8b\xc6\x5e\xc2\x04\x00')


def sdd_sized(at, d, router):
    return (b'\x56\x8b\xf1' + call(at, 3, d) + b'\xf6\x44\x24\x08\x01\x74\x15' + call(at, 13, router) +
            bytes.fromhex('0fb756048b48108b018b40085256ffd0') + b'\x8b\xc6\x5e\xc2\x04\x00')


code = {
    T(1): b'\x83\xe9\x20' + jmp(T(1), 3, T(2)),             # sub ecx, 0x20; jmp native body
    T(2): b'\x55\x8b\xec\x51\xc3',                           # (native in the fixture)
    T(3): b'\x83\xe9\x2c' + jmp(T(3), 3, T(4)),             # thunk into a sized ??_G
    T(4): sdd_sized(T(4), T(2), T(0)),                       # ??_G, destructor T(2) native
    T(5): b'\x83\xe9\x08' + jmp(T(5), 3, T(6)),             # thunk into a global-delete ??_G
    T(6): sdd_global(T(6), T(2), T(0)),
    T(7): sdd_global(T(7), T(0), T(0)),                     # ??_G whose destructor has no native body
    T(8): b'\x55\x8b\xec\x51\x89\x4d\xfc\x8b\xe5\x5d\xc3',   # a frame function with int3 padding before it
    T(9): b'\x90\x55\x8b\xec\xc3',                          # not a function start (code before it)
    T(10): b'\x83\xe9\x04' + jmp(T(10), 3, T(9)),          # thunk into a non-native non-??_G body
}
code[T(9) - 1] = b'\x90'
tmp = tempfile.mkdtemp(prefix='defshapes-')
exe = os.path.join(tmp, 'Synthetic.exe')
tx.make_pe(exe, lambda rd: None, code)
native = {T(2)}
got = {k: defshapes.classify(exe, T(k), native) for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)}
check(got[1] == ('thunk>native', (0x20, T(2))), 'adjustor thunk into a native body: %s' % (got[1],))
check(got[3] == ('thunk>sdd-sized', (0x2c, T(4), T(2))), 'thunk into a Havok sized-free ??_G: %s' % (got[3],))
check(got[4] == ('sdd-sized', (0, T(4), T(2))), 'the sized ??_G itself: %s' % (got[4],))
check(got[5][0] == 'thunk>sdd-global' and got[6][0] == 'sdd-global', 'global operator delete ??_G and its thunk')
check(got[7][0] == 'sdd-nocallee', 'a ??_G whose destructor is not native is no stand-in: %s' % (got[7],))
check(got[8] == ('function', None), 'a framed function after int3 padding is a real function')
check(got[9] == ('other', None), 'code right before it: not claimed as a function start')
check(got[10][0] == 'other', 'a thunk into something neither native nor a ??_G stays other: %s' % (got[10],))
check(got[2][0] == 'function', 'the native body itself, were it listed, has a plain function shape')

# the layout gate
check(thisadj.thunk_layout_ok(0x6c, {-0x6c}), 'every vtable at offset-to-top -n: the immediate is the native adjustment')
check(not thisadj.thunk_layout_ok(0x6c, {-4}), 'a shell vtable with the subobject at 4: the x86 immediate is wrong')
check(not thisadj.thunk_layout_ok(0x20, {0, -0x20}), 'a primary vtable (offset-to-top 0) naming the thunk disagrees')
check(thisadj.thunk_layout_ok(0x20, set()), 'no vtable names the slot: a call by address keeps the exe\'s this')


class FakeObj:
    defs = {'_ZTV3Foo': 0}

    def __init__(self, path):
        pass

    def ents(self, name):
        E = vtables.Ent
        return [E(0, None, 0), E(0, '_ZTI3Foo', 0), E(0, '_ZN3Foo1fEv', 0), E(0, '_ZN3Foo1gEv', 0),
                E(0xfffffff8, None, 0), E(0, '_ZTI3Foo', 0), E(0, '_ZN3Foo1fEv', 0)]


real = vtables.Obj
vtables.Obj = FakeObj
try:
    thisadj._OTTS.clear()
    check(thisadj.slot_otts('/x/a.o', '_ZN3Foo1fEv') == {0, -8}, 'slot_otts: a slot of the primary and the secondary')
    check(thisadj.slot_otts('/x/a.o', '_ZN3Foo1gEv') == {0}, 'slot_otts: a primary-only slot')
    check(thisadj.slot_otts('/x/a.o', '_ZN3Foo1hEv') == set() and thisadj.slot_otts(None, 'x') == set(),
          'slot_otts: an unnamed symbol / no object gives the empty set')
finally:
    vtables.Obj = real
    thisadj._OTTS.clear()
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
