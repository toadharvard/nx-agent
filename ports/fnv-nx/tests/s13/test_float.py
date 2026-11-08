#!/usr/bin/env python3
"""tests/s13/test_float.py - the float class (EQ stream, D24, difffuzz-20): hard `float` divergences that were
harness artifacts or the compilers' evaluation methods, on hand-written code (no decomp content).

  consumers  x86emu.x87_result_use: the code a call returns to reads ST0 through a `jmp` to the shared epilogue
             (`return virtual_call()`, 0x0086a050) or through fidiv / fistp / fcomip / fucomp / ftst / fsqrt (0x00893530
             `fidiv dword [ebp-4]`); before, the oracle read an empty x87 stack (0.0) where the native has the canned bytes
  overlap    coffobj.exe_literals: a pinned literal that differs from the exe at its address (units.json maps
             several $SG strings of an object to one address, so a 64-byte wrong string covered the __real@ constants
             behind it, 0x00476b20) is replaced by the exe's bytes, the union of overlapping ones too; no exe, no change
  boolfloat  classify.hint: a float result that depends on a bool byte other than 0/1 is bool-repr, not float
             (0x008786b0: GCC tests bit 0, MSVC the byte); before, the float branch returned before the probe
  precision  classify.x87_probes: the oracle rerun with the x87 control word at 24-bit precision agrees with the
             native: x87-precision, however many ulps the cancellation shows (0x004b3ab0, 0x00647f00)
  range      classify.x87_probes: the oracle held an intermediate beyond the float range and the native result is
             inf / NaN: x87-range (0x00a6dc30, 0x00c5d2b0); a difference with neither mechanism stays float

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13float'))

import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402
from test_d14 import build_image  # noqa: E402
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def f32(x):
    return struct.pack('<f', x).hex()


def use(code, fret='int'):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x1000, 0x1000)
    uc.mem_write(0x1000, code + bytes(16))
    return x86emu.x87_result_use(uc, 0x1000, fret)


def test_consumers():
    for what, code, want in (
            ('jmp short to an epilogue that stores ST0', bytes.fromhex('eb02' '9090' 'd95dfc'), 'f32'),
            ('jmp near to fstp qword', bytes.fromhex('e902000000' '9090' 'dd5df8'), 'f64'),
            ('jmp short to ret takes the function result kind', bytes.fromhex('eb00' 'c3'), 'f32'),
            ('fidiv dword [ebp-4]', bytes.fromhex('da75fc'), 'f32'),
            ('fiadd dword [esp]', bytes.fromhex('da0424'), 'f32'),
            ('fistp dword [ebp-4]', bytes.fromhex('db5dfc'), 'f32'),
            ('fistp qword [ebp-8]', bytes.fromhex('df7df8'), 'f32'),
            ('fcomip st, st(1)', bytes.fromhex('dff1'), 'f32'),
            ('fucomp st(1)', bytes.fromhex('dde9'), 'f32'),
            ('ftst', bytes.fromhex('d9e4'), 'f32'),
            ('fsqrt', bytes.fromhex('d9fa'), 'f32'),
            ('fld1 reads nothing', bytes.fromhex('d9e8'), 'int'),
            ('fldz reads nothing', bytes.fromhex('d9ee'), 'int'),
            ('fild dword reads nothing', bytes.fromhex('db45fc'), 'int'),
            ('an integer use of eax', bytes.fromhex('8945fc'), 'int')):
        got = use(code, 'f32' if 'takes the function' in what else 'int')
        check(got == want, '%s: %s (want %s)' % (what, got, want))


class FakeExe:
    def __init__(self, mem):
        self.mem = mem

    def read(self, va, n):
        return bytes(self.mem[va + k] for k in range(n))     # KeyError outside the image


def test_overlap():
    import coffobj
    base = 0x01012058
    exe = FakeExe({base + k: v for k, v in enumerate(b'%s %s\0\0\0' + struct.pack('<d', 0.0) + bytes(8) +
                                                       struct.pack('<d', 1.0) + bytes(64))})
    wrong = b'Interface\\Icons\\Message Icons\\glow_message_vaultboy_neutral.dds\0'
    lit = {base: wrong, base + 8: struct.pack('<d', 0.0), base + 24: struct.pack('<d', 1.0), 0x02f00000: b'pool'}
    stats = {}
    got = coffobj.exe_literals(lit, exe, stats)
    check(got[base] == exe.read(base, len(got[base])) and got[base][24:32] == struct.pack('<d', 1.0), 'overlapping literals: the exe bytes over their union')
    check(got[0x02f00000] == b'pool' and stats['overlap'] == 1 and stats['differ'] == 1,
          'the pool literal stays, one group, one literal differs (%s)' % stats)
    lit2 = {0x01012000: b'good', 0x01012010: b'bad!'}
    exe2 = FakeExe({0x01012000 + k: v for k, v in enumerate(b'good' + bytes(12) + b'real')})
    got = coffobj.exe_literals(lit2, exe2, {})
    st2 = {}
    got = coffobj.exe_literals(lit2, exe2, st2)
    check(got == lit2 and st2['differ'] == 1, 'a lone literal that differs from the exe is counted, not replaced')
    got = coffobj.exe_literals({0x01012000: b'good', 0x01013000: b'x'}, exe2, {})
    check(got[0x01013000] == b'x', 'an address the exe does not map keeps the literal')
    saved, saved_tried = coffobj.EXE, coffobj._EXE_TRIED
    coffobj.EXE, coffobj._EXE_TRIED = None, True
    try:
        check(coffobj.exe_literals(lit) is lit, 'without the exe image nothing changes')
    finally:
        coffobj.EXE, coffobj._EXE_TRIED = saved, saved_tried


def run_hint(sym, oracle, body, args, mem=None):
    cv = conv.Conv(sym, 'test')
    fn = dict(code=oracle, address=0x00401000)
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image(body, d), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=3, args=args, mem=mem or {})
        rx, ra, div = p.run(c)
        return div, (classify.hint(p, t, c, div, fn, 200000) if div else {})


MEM = 0x03000000


def test_boolfloat():
    # float f(bool *p): MSVC `test al, al`, GCC `tbz w0, #0`; the byte is 0x40
    oracle = bytes.fromhex('8b442404' '8a00' '84c0' '7404' 'd9e8' 'c3' 'd9ee' 'c3')
    body = '\tldrb w0, [x0]\n\ttbz w0, #0, 1f\n\tfmov s0, #1.0\n\tret\n1:\tmovi d0, #0\n\tret\n'
    div, h = run_hint('?f@@YAMPA_N@Z', oracle, body, {'p0': MEM.to_bytes(4, 'little')}, {MEM: bytes([0x40, 0, 0, 0])})
    check(div is not None and div['kind'] == 'return' and 'float' in div, 'float result differs (%s)' % (div and div['kind']))
    check(h.get('cause') == 'bool-repr', 'a float result behind a bool byte 0x40: bool-repr (%s)' % h.get('cause'))
    check(not causes.policy(h.get('cause'))[2], 'and not hard')


# float f(float a, float b): (a + b) - a, x87 53-bit intermediates; the native rounds a + b to a float
CANCEL_X86 = bytes.fromhex('d9442404' 'd8442408' 'd8642404' 'c3')
CANCEL_A64 = '\tfadd s1, s0, s1\n\tfsub s0, s1, s0\n\tret\n'


def test_precision():
    args = {'p0': bytes.fromhex(f32(1.0)), 'p1': bytes.fromhex(f32(1e-10))}
    div, h = run_hint('?f@@YAMMM@Z', CANCEL_X86, CANCEL_A64, args)
    fl = (div or {}).get('float') or {}
    check(div is not None and fl.get('tolerant') is False and fl.get('rel') == 1.0,
          'the cancellation is 100 %% off, beyond the tolerance (%s)' % fl)
    check(h.get('cause') == 'x87-precision' and h.get('x87_pc24') is True,
          'the 24-bit control word rerun agrees with the native: x87-precision (%s)' % h.get('cause'))
    # a native that computes something else is no precision difference
    div, h = run_hint('?f@@YAMMM@Z', CANCEL_X86, '\tfadd s0, s1, s1\n\tret\n', args)
    check(div is not None and h.get('cause') == 'float', 'another value stays float (%s)' % h.get('cause'))
    check(world.X87_CW == 0x027f, 'the control word is restored')


# float f(float a): a**4 / a**2 with the x87 stack; the native overflows the float a**4
RANGE_X86 = bytes.fromhex('d9442404' 'd8c8' 'd8c8' 'd9442404' 'd8c8' 'def9' 'c3')
RANGE_A64 = '\tfmul s1, s0, s0\n\tfmul s2, s1, s1\n\tfdiv s0, s2, s1\n\tret\n'


def test_range():
    args = {'p0': bytes.fromhex(f32(1e15))}
    div, h = run_hint('?f@@YAMM@Z', RANGE_X86, RANGE_A64, args)
    fl = (div or {}).get('float') or {}
    check(div is not None and fl.get('arm') == 'inf' and fl.get('x86') not in ('inf', 'nan'),
          'the native overflowed to inf, the oracle is finite (%s)' % fl)
    check(h.get('cause') == 'x87-range' and h.get('x87_range', {}).get('range') == 'f32',
          'x87-range with the float clamp (%s)' % h.get('x87_range'))
    check(not causes.policy('x87-range')[2], 'by design, not hard')
    args = {'p0': bytes.fromhex(f32(3.0))}
    div, h = run_hint('?f@@YAMM@Z', RANGE_X86, RANGE_A64.replace('fdiv s0, s2, s1', 'fmul s0, s2, s1'), args)
    check(div is not None and h.get('cause') == 'float', 'a plain difference at a small argument stays float (%s)'
          % h.get('cause'))


# float f(float a): a**16 + a**16 * 0 in double on the native; the x87 stack holds a**16 = 1e480 (beyond a double)
BIG_X86 = bytes.fromhex('d9442404' 'd8c8' 'd8c8' 'd8c8' 'd8c8' 'd9c0' 'd9ee' 'dec9' 'dec1' 'c3')
BIG_A64 = ('\tfcvt d0, s0\n\tfmul d0, d0, d0\n\tfmul d0, d0, d0\n\tfmul d0, d0, d0\n\tfmul d0, d0, d0\n'
           '\tfmov d1, xzr\n\tfmul d1, d0, d1\n\tfadd d0, d0, d1\n\tfcvt s0, d0\n\tret\n')


def test_range_double():
    div, h = run_hint('?f@@YAMM@Z', BIG_X86, BIG_A64, {'p0': bytes.fromhex(f32(1e30))})
    fl = (div or {}).get('float') or {}
    check(div is not None and fl.get('x86') == 'inf' and fl.get('arm') == 'nan', 'inf against NaN (%s)' % fl)
    check(h.get('cause') == 'x87-range' and h.get('x87_range', {}).get('clamped', 0) > 0,
          'an intermediate beyond the double range: x87-range, double clamp (%s)' % h.get('x87_range'))


# float f(): the first stack slot below the frame (the oracle's garbage; the native returns 0)
UNINIT_X86 = bytes.fromhex('83ec04' 'd90424' '83c404' 'c3')


def test_garbage_rand():
    cv = conv.Conv('?f@@YAMXZ', 'test')
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: UNINIT_X86}, build_image('\tmovi d0, #0\n\tret\n', d), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=9, bg='rand')
        _, _, div = p.run(c)
        h = classify.hint(p, t, c, div, dict(code=UNINIT_X86, address=0x00401000), 200000) if div else {}
    check(div is not None and 'float' in div and not div['float'].get('tolerant'), 'a float result from garbage differs')
    check(h.get('cause') == 'uninit-x86', 'random background: the garbage probe finds uninit-x86 (%s)' % h.get('cause'))
    check(world.GARBAGE_XOR == 0, 'the garbage seed is restored')


def test_width():
    # float f(): narrows the result of an untyped callee with `fstp dword`; the native converts it from d0
    callee = 0x00402000
    oracle = bytes.fromhex('83ec04') + b'\xe8' + struct.pack('<i', callee - (0x00401000 + 3 + 5)) + \
        bytes.fromhex('d91c24' 'd90424' '83c404' 'c3')
    body = '\tstp x29, x30, [sp, #-16]!\n\tbl __fnv_00402000\n\tfcvt s0, d0\n\tldp x29, x30, [sp], #16\n\tret\n'
    cv = conv.Conv('?f@@YAMXZ', 'test')
    with tempfile.TemporaryDirectory() as d:
        img = build_image(body, d, [('__fnv_00402000', 0x08000000 + callee)])
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {})
        p = runner.Pair(t, {})
        c = world.Case(seed=21, rets=[bytes.fromhex('0000000000004940')])         # 50.0 as a double
        _, _, div = p.run(c)
        h = classify.hint(p, t, c, div, dict(code=oracle, address=0x00401000), 200000) if div else {}
    check(div is not None and 'float' in div and not div['float'].get('tolerant'),
          'the oracle read the canned bytes as a float, the native as a double (%s)' % (div or {}).get('float'))
    check(h.get('cause') == 'callee-float-width' and causes.policy('callee-float-width')[0] == 'artifact',
          'the other width agrees: callee-float-width, an artifact (%s)' % h.get('cause'))
    check(world.X87_FLIP_WIDTH is False, 'the width guess is restored')


# int f(float a, float b, float c): fistp((a + b - a) * c); the native rounds a + b to a float first
ITOI_X86 = bytes.fromhex('d9442404' 'd8442408' 'd8642404' 'd84c240c' '83ec04' 'db1c24' '8b0424' '83c404' 'c3')
ITOI_A64 = '\tfadd s1, s0, s1\n\tfsub s0, s1, s0\n\tfmul s0, s0, s2\n\tfcvtzs w0, s0\n\tret\n'


def test_float_to_int():
    args = {'p0': bytes.fromhex(f32(1.0)), 'p1': bytes.fromhex(f32(1e-10)), 'p2': bytes.fromhex(f32(1e10))}
    div, h = run_hint('?f@@YAHMMM@Z', ITOI_X86, ITOI_A64, args)
    check(div is not None and div['kind'] == 'return' and 'float' not in div, 'the integer results differ (%s)' % (div or {}).get('kind'))
    check(h.get('cause') == 'x87-precision' and h.get('x87_pc24') is True, 'float -> fistp: x87-precision (%s)' % h.get('cause'))
    div, h = run_hint('?f@@YAHMMM@Z', ITOI_X86, ITOI_A64.replace('fmul s0, s0, s2', 'fadd s0, s0, s2'), args)
    check(div is not None and h.get('cause') != 'x87-precision', 'another value is not (%s)' % h.get('cause'))


if __name__ == '__main__':
    test_float_to_int()
    test_consumers()
    test_overlap()
    test_boolfloat()
    test_precision()
    test_range()
    test_range_double()
    test_garbage_rand()
    test_width()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
