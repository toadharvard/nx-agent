#!/usr/bin/env python3
"""tests/s13/test_r5misc.py - the round-5 residue (EQ, `r5misc`, difffuzz-20): hard divergences of 14 functions that
four rounds left unresolved, on hand-written code (no decomp content). Each test fails on the head before the class
and passes with it.

  pushslot   x86emu.x87_result_use: `call [reg+N]; push ecx; fstp dword [esp]` (and `sub esp, 4; fstp dword [esp]`)
             is a float result stored into a freshly reserved argument slot; the push / sub does not touch ST0, so the
             store after it decides. Before, the oracle read an empty x87 stack (0.0) for an untyped virtual call's
             float result and the native had the canned bytes (0x00886cb0: `kRotate.FUN_004a0c90(FUN_vt2bc(0))`)
  copywin    a memcpy / memmove / strcpy builtin copies data: the native code-pointer scan (arm64emu._norm_bytes)
             looked at every byte offset of the copied bytes, so the planted data 00 00 00 0a read as the code stub
             0x0a000000 at an odd offset and the native recorded other bytes than it stored (0x00c7f900: 127 eight-byte
             memmoves, the 49th chunk). Copied data is scanned at 4-aligned addresses only
  sretbridge a callee behind an `sret-explicit` bridge (tools/link abi2: the reference passes the result buffer as an
             explicit pointer, the definition takes it hidden) is called by the bridge without that parameter, so the
             intercepted native call holds every later argument one slot earlier: conv.Conv.bridged_view and
             runner.decode_a64 (0x00d984b0: p0 = &result never reaches the stub)
  calltarget a call-target that depends on an unwritten stack byte: the all-zero fill (world.UNIFORM) removes it, as it
             does for a call-count (0x006e1670: `len`, the out-parameter of a stubbed callee)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13r5misc'))

import arm64emu   # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import libcalls   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402
from test_uncls import Info  # noqa: E402
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def use(code, fret='int'):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x1000, 0x1000)
    uc.mem_write(0x1000, code + bytes(16))
    return x86emu.x87_result_use(uc, 0x1000, fret)


def test_pushslot():
    check(use(bytes.fromhex('51' 'd91c24')) == 'f32', 'push ecx; fstp dword [esp]: f32')
    check(use(bytes.fromhex('51' 'dd1c24')) == 'f64', 'push ecx; fstp qword [esp]: f64')
    check(use(bytes.fromhex('83ec04' 'd91c24')) == 'f32', 'sub esp, 4; fstp dword [esp]: f32')
    check(use(bytes.fromhex('50' 'd91c24')) == 'f32', 'push eax; fstp dword [esp]: f32')
    # a push of an argument for the next call does not read ST0
    check(use(bytes.fromhex('51' 'e8' '00000000')) == 'int', 'push ecx; call: no float result')
    check(use(bytes.fromhex('50' '8b4d08')) == 'int', 'push eax; mov ecx, [ebp+8]: no float result')


class FakeUc:
    def __init__(self):
        self.m = bytearray(0x10000)

    def mem_read(self, a, n):
        return self.m[a:a + n]

    def mem_write(self, a, b):
        self.m[a:a + len(b)] = b


def fake_engine():
    e = object.__new__(arm64emu.Arm64Engine)
    # a code stub at 0x0a000000 + original address: 0x0a000000 -> 0x00401000 ..
    e._codeptr = lambda w: (w - 0x0a000000 + 0x00400000) if 0x0a000000 <= w < 0x0a100000 else w
    e.vtables = None
    return e


def test_copywin():
    e = fake_engine()
    base = 0x03000180
    data = bytes.fromhex('00000000' '0a081cc3')        # planted data; bytes 1..4 read 0x0a000000 at an odd offset
    got = e._norm_bytes(data, base) if 'base' in e._norm_bytes.__code__.co_varnames else None
    check(got == data, 'copied data: a code stub at an odd offset is no code pointer (%s)' % (got and got.hex(),))
    ptr = struct.pack('<I', 0x0a001000)
    got = e._norm_bytes(b'\0\0\0\0' + ptr, base) if 'base' in e._norm_bytes.__code__.co_varnames else None
    check(got == b'\0\0\0\0' + struct.pack('<I', 0x00401000), 'copied data: a 4-aligned code stub is a code pointer')
    # a stored value is still scanned at every byte offset (tables of initializers are not always word aligned)
    check(e._norm_bytes(data) != data, 'a stored value keeps the every-offset scan')

    # through the builtin: memmove of 8 bytes, the native normalisation bound as the engine's
    uc, writes = FakeUc(), {}
    src, dst = 0x2000, 0x3000
    uc.mem_write(src, data)
    saved = world.COMPARED
    world.COMPARED = [(0, 0x10000)]
    try:
        m = libcalls.Mem(uc, writes, lambda a, n: None, e._norm_bytes)
        libcalls.run('memmove', m, [dst, src, 8])
    finally:
        world.COMPARED = saved
    rec = bytes(writes.get(dst + i, 0xee) for i in range(8))
    check(rec == data, 'memmove records the bytes it copied (%s)' % rec.hex())


def test_sretbridge():
    cv = conv.Conv('?f@@YAXPAXHH@Z', 'test')           # void __cdecl f(void *out, int a, int b)
    check([v.label for v in cv.vals] == ['p0', 'p1', 'p2'], 'labels p0 p1 p2')
    view = cv.bridged_view('p0') if hasattr(cv, 'bridged_view') else None
    check(view is not None and [v.label for v in view.vals] == ['p1', 'p2'], 'the bridged view omits the result pointer')

    class Call:
        pass
    c = Call()
    c.args = [0x11111111, 0x22222222] + [0] * 6        # x0 = a, x1 = b: the bridge consumed `out`
    c.fargs, c.stack, c.x8 = [0] * 8, b'', 0
    if view is not None:
        cv.native_view = view
    got = runner.decode_a64(cv, c)
    check(got.get('p1') == struct.pack('<I', 0x11111111) and got.get('p2') == struct.pack('<I', 0x22222222),
          'the stub sees a in x0 and b in x1 (%s)' % {k: v.hex() for k, v in got.items()})
    check('p0' not in got, 'no p0 at the stub')
    plain = conv.Conv('?f@@YAXPAXHH@Z', 'test')
    got = runner.decode_a64(plain, c)
    check(got.get('p0') == struct.pack('<I', 0x11111111), 'a callee with no bridge keeps the reference layout')


CALLEE_G, CALLEE_H = 0x00402000, 0x00403000


def test_calltarget():
    # void f(void): char loc; if (loc != 0) g(); else h();    -- the native always calls h
    code = bytes.fromhex('55' '89e5' '83ec08' '807dfc00' '7407')
    code += call_rel(0x00401000 + len(code), CALLEE_G) + bytes.fromhex('eb05')
    code += call_rel(0x00401000 + len(code), CALLEE_H) + bytes.fromhex('89ec' '5d' 'c3')
    nat = ('\tstp x29, x30, [sp, #-16]!\n\tbl __fnv_00403000\n\tldp x29, x30, [sp], #16\n\tret\n')
    import tempfile as tf
    for bg in ('zero', 'rand'):
        with tf.TemporaryDirectory() as d:
            cv = conv.Conv('?f@@YAXXZ', 'test')
            t = runner.Target(cv, 0x00401000, {0x00401000: code},
                              build_image(nat, d, [stub(CALLEE_G), stub(CALLEE_H)]), {},
                              Info(conv.Conv('?g@@YAXXZ', 'test')))
            t.oracle_callees = lambda a: {CALLEE_G, CALLEE_H} if a == 0x00401000 else set()
            p = runner.Pair(t, {})
            case = world.Case(seed=7, bg=bg)
            rx, ra, div = p.run(case)
            check(div is not None and div['kind'] == 'call-target',
                  'bg %s: the oracle branches on an unwritten byte, the native does not (%s)' % (bg, div and div['kind']))
            h = classify.hint(p, t, case, div, dict(code=code, address=0x00401000), 200000) if div else {}
            check(h.get('cause') == 'uninit-x86', 'bg %s: uninit-x86 (%s)' % (bg, h.get('cause')))


if __name__ == '__main__':
    for n, f in sorted(globals().items()):
        if n.startswith('test_') and callable(f):
            print('--', n)
            try:
                f()
            except Exception as e:  # noqa: BLE001
                check(False, '%s raised %r' % (n, e))
    print('FAILED: %d' % len(FAIL) if FAIL else 'all ok')
    sys.exit(1 if FAIL else 0)
