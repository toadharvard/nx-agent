#!/usr/bin/env python3
"""tests/s13/test_memdiv.py - the store / call-args residue of the stores and signedness classes (EQ stream, D24,
difffuzz-20), on hand-written code (no decomp content).

  calib-float   engine.calib_float_noise / calibrate_custom: a calibration case that differs only by a NaN payload,
                a ulp or a cancellation residue agrees on the register pairing; vector code (0x00dacf10, 0x00d5b440,
                0x00cd6120, 0x00da9210) never reached the 7-of-8 bar, so the declared order stayed
  calib-tie     engine.calibrate_custom: pairings tied at the top score run fresh cases until one is left (0x00d6c3c0)
  local-static  runner.compare: a function-local static (and guard) the oracle stores leaves the diff on its own,
                the rest of the diff stays (0x00c206b0)
  zero-sign     runner.compare / classify.hint: -0.0 against +0.0 stored by both machines is routed to the x87
                probes; an x87 intermediate beyond the float range keeps its sign until the store (x87-range)
  float-store   runner.compare: the float-word check reads the raw stores, so a one-ulp store whose low byte is the
                initial byte is compared as a float (0x006e9830)
  canned-alias  classify.canned_unaliased / _uncls2_probes: a canned result that points into the argument buffers
                is moved to the heap; equal afterwards is `canned-alias` (0x0043a810)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13memdiv'))

import arm64emu   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import engine     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def results(wx, wa):
    rx, ra = x86emu.Result(), arm64emu.Result()
    rx.writes, ra.writes = dict(wx), dict(wa)
    rx.ret = ra.ret = None
    return rx, ra


def word(addr, value):
    return {addr + i: b for i, b in enumerate(struct.pack('<I', value))}


def test_noise():
    mw = {'kind': 'mem-write'}
    cases = (
        ('NaN payload against the default NaN', word(0x03000000, 0xffc00000), word(0x03000000, 0x7fc00000), True),
        ('a NaN with a payload against a NaN', word(0x03000000, 0xffe19cf8), word(0x03000000, 0xffc00000), True),
        ('one ulp', word(0x03000000, 0x3f800000), word(0x03000000, 0x3f7fffff), True),
        ('0.0 against a 1.6e-18 residue', word(0x03000000, 0), word(0x03000000, 0x21 << 24 | 0x6cf3db), True),
        ('an integer: 0 against 5 is a denormal, not noise', word(0x03000000, 0), word(0x03000000, 5), False),
        ('a different float', word(0x03000000, 0x3f800000), word(0x03000000, 0x40000000), False),
        ('a store only the native made', {}, word(0x03000000, 0x3f800000), False),
    )
    for what, wx, wa, want in cases:
        rx, ra = results(wx, wa)
        check(engine.calib_float_noise(rx, ra, dict(mw)) is want, 'calib_float_noise: %s -> %s' % (what, want))
    rx, ra = results(word(0x03000000, 1), word(0x03000000, 2))
    check(engine.calib_float_noise(rx, ra, dict(mw, soft=True)) is True, 'a soft record agrees')
    rx.error = 'fault:r:00000000'
    check(engine.calib_float_noise(rx, ra, dict(mw)) is False, 'a fault does not')


# void f(int *p0, int *p1), eax carries the output pointer; the oracle stores 1.0 there, the native 0.99999994
ORACLE = bytes.fromhex('c700' '0000803f' 'c3')                  # mov dword [eax], 0x3f800000; ret
NATIVE = '\tmov w2, #0xffff\n\tmovk w2, #0x3f7f, lsl #16\n\tstr w2, [x1]\n\tret\n'


def test_calibrate_float():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXPAH0@Z', 'test')
        cv.with_registers(['eax'])
        t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(NATIVE, d), {})
        t.custom_cc, t.source = list(cv.custom), 'test'
        pair = runner.Pair(t, {})
        c = world.Case(seed=3, mem={world.BUF: bytes(64), world.BUF + world.BUF_SIZE: bytes(64)})
        _, _, d0 = pair.run(c)
        check(d0 is not None, 'declared order (eax = p0): the machines store to different buffers (%s)' % (
            d0 and d0['kind']))
        engine.calibrate_custom(pair, t)
        got = dict((lab, sp) for lab, sp, _, _ in t.conv.x86)
        check(got.get('p1') == 'eax', 'calibrated: eax carries p1 though every case differs by one ulp (%s)' % (
            getattr(t, 'custom_calib', None),))


# void f(int p0, int *p1, int *p2), eax = p1 (input) and ecx = p2 (output) on the oracle: *out = 1 always, and
# out[1] = *in when p0 is odd. When p0 is even the pairing (eax = p0, ecx = p2) agrees with the right one
TIE_ORACLE = bytes.fromhex('c701' '01000000' 'f6442404' '01' '7405' '8b10' '895104' 'c3')
TIE_NATIVE = ('\tmov w3, #1\n\tstr w3, [x2]\n\ttbz w0, #0, 1f\n\tldr w3, [x1]\n\tstr w3, [x2, #4]\n1:\tret\n')


def build_at(body, d, addr):
    import subprocess
    import a64image
    s = os.path.join(d, 'f.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_%08x\n\t.type __fnv_%08x, %%function\n__fnv_%08x:\n' % (addr, addr, addr)
                       + body)
    o, elf, ld = os.path.join(d, 'f.o'), os.path.join(d, 'f.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o],
                   check=True, capture_output=True)
    return a64image.Image(elf, {}, {})


def test_tie():
    import fuzz
    # the calibration cases come from fuzz.Gen seeded with the target address: take an address whose first eight
    # cases have p0 even (the pairings with ecx = p2 tie at 8 of 8) and whose later cases do not
    found = None
    for k in range(0x401000, 0x401000 + 0x2000, 0x10):
        cv = conv.Conv('?f@@YAXHPAH0@Z', 'test')
        cv.with_registers(['eax', 'ecx'])
        t = runner.Target(cv, k, {k: TIE_ORACLE}, None, {})
        t.custom_cc = list(cv.custom)
        gen = fuzz.Gen(t, [], k ^ 0x5ca1ab1e)
        bits = [gen.fresh().args.get('p0', b'\0')[0] & 1 for _ in range(32)]
        if not any(bits[:8]) and sum(bits[8:]) >= 6:
            found = k
            break
    check(found is not None, 'an address whose first round cannot decide the pairing, a later one can (%s)' % found)
    if found is None:
        return
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXHPAH0@Z', 'test')
        cv.with_registers(['eax', 'ecx'])
        t = runner.Target(cv, found, {found: TIE_ORACLE}, build_at(TIE_NATIVE, d, found), {})
        t.custom_cc, t.source = list(cv.custom), 'test'
        pair = runner.Pair(t, {})
        engine.calibrate_custom(pair, t)
        got = dict((lab, sp) for lab, sp, _, _ in t.conv.x86)
        check(got.get('p1') == 'eax' and got.get('p2') == 'ecx',
              'the tie is broken by later rounds: eax = p1, ecx = p2 (%s)' % got)


def test_local_static():
    cv = conv.Conv('?f@@YAXXZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})
    st = {0x011d0000, 0x011d0001, 0x011d0002, 0x011d0003}
    wx = {k: 1 for k in st}
    rx, ra = results(wx, {})
    d = runner.compare(cv, rx, ra, w, local_statics=st)
    check(d is not None and d.get('soft') and d['kind'] == 'local-static', 'only the static differs: soft (%s)' % d)
    wx2 = dict(wx)
    wx2[0x03000010] = 7
    rx, ra = results(wx2, {})
    d = runner.compare(cv, rx, ra, w, local_statics=st)
    check(d is not None and not d.get('soft') and d['kind'] == 'mem-write' and d['addr'] == '03000010' and
          d['ndiff'] == 1, 'the static plus another store: the other store is the divergence (%s)' % (d,))


def test_zero_sign_and_float_store():
    cv = conv.Conv('?f@@YAXXZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})
    rx, ra = results(word(0x03000000, 0x80000000), word(0x03000000, 0))
    d = runner.compare(cv, rx, ra, w)
    check(d is not None and d['kind'] == 'mem-write' and d.get('zero_sign') is True,
          '-0.0 against +0.0, both stored: zero_sign (%s)' % (d and d.get('zero_sign')))
    rx, ra = results(word(0x03000000, 0x80000000), {})
    d = runner.compare(cv, rx, ra, w)
    check(d is not None and not d.get('zero_sign'), 'a store only one machine made is not (%s)' % d)
    # 0xc2248200 against 0xc2248201: the x86 low byte is the world's initial 0x00, so the effective map has a hole
    rx, ra = results(word(0x03000008, 0xc2248200), word(0x03000008, 0xc2248201))
    d = runner.compare(cv, rx, ra, w)
    check(d is not None and (d.get('float_word') or {}).get('tolerant') is True,
          'a one-ulp store with an initial low byte is compared as a float (%s)' % (d and d.get('float_word')))


# void f(float a, float b, float c, float *out): *out = a * b + c on the x87 stack, rounded when stored
X87_ORACLE = bytes.fromhex('d9442404' 'd84c2408' 'd844240c' '8b442410' 'd918' 'c3')
X87_NATIVE = '\tfmul s0, s0, s1\n\tfadd s0, s0, s2\n\tstr s0, [x0]\n\tret\n'


def test_zero_sign_range():
    cv = conv.Conv('?f@@YAXMMMPAM@Z', 'test')
    den = struct.pack('<I', 32)                       # 4.5e-44, a float denormal
    nden = struct.pack('<I', 0x80000000 | 32)
    args = {'p0': nden, 'p1': den, 'p2': struct.pack('<f', 0.0)}
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: X87_ORACLE}, build_image(X87_NATIVE, d), {})
        pair = runner.Pair(t, {})
        c = world.Case(seed=3, args=args, mem={world.BUF: bytes(16)})
        rx, ra, div = pair.run(c)
        check(div is not None and div['kind'] == 'mem-write' and div.get('zero_sign') is True,
              'the oracle stores -0.0 (-2e-87 rounded at the store), the native +0.0 (%s)' % (div and div['kind']))
        if div is None:
            return
        h = classify.hint(pair, t, c, div, dict(code=X87_ORACLE, address=0x00401000), 200000)
        check(h.get('cause') == 'x87-range' and not causes.policy('x87-range')[2],
              'the clamp rerun agrees: x87-range, by design (%s)' % h.get('cause'))


def test_canned_alias():
    c = world.Case(seed=1, rets=[(world.BUF + 0x14).to_bytes(4, 'little') + bytes(12),
                                 (5).to_bytes(4, 'little') + bytes(12)])
    t = classify.canned_unaliased(c)
    w0 = int.from_bytes(t.rets[0][:4], 'little')
    check(t is not None and not (world.BUF <= w0 < world.BUF + 0x10000) and w0 >= world.HEAP and
          t.rets[1] == c.ret_bytes(1), 'a result inside the buffers moves to the heap, others stay (%x)' % w0)
    check(classify.canned_unaliased(world.Case(seed=1, rets=[bytes(16)])) is None or
          all(not (world.BUF <= int.from_bytes(classify.canned_unaliased(world.Case(seed=1, rets=[bytes(16)])).ret_bytes(i)[:4],
                                              'little') < world.BUF + 0x10000) for i in range(8)),
          'no result in the buffers: nothing to move')

    class Pair:
        def run(self, case, steps=0):
            rx, ra = x86emu.Result(), arm64emu.Result()
            rx.writes = {}
            ra.writes = {}
            alias = any(world.BUF <= int.from_bytes(case.ret_bytes(i)[:4], 'little') < world.BUF + 0x10000
                        for i in range(8))
            return rx, ra, (dict(kind='mem-write', addr='03000000', ndiff=1, x86='01', arm='02') if alias else None)
    h = {'_runs': 0}
    got = classify._uncls2_probes(Pair(), c, dict(kind='mem-write'), dict(code=b'\xc3', address=0x00401000), set(),
                                  1000, h)
    check(got == 'canned-alias', 'the divergence that needs the aliasing is canned-alias (%s)' % got)
    check(causes.policy('canned-alias')[0] == 'artifact' and not causes.policy('canned-alias')[2],
          'an artifact, not hard')


def main():
    for t in (test_noise, test_calibrate_float, test_tie, test_local_static, test_zero_sign_and_float_store,
              test_zero_sign_range, test_canned_alias):
        try:
            t()
        except Exception as e:  # noqa: BLE001 - a missing function is a failure of this class, not of the rest
            check(False, '%s raised %s: %s' % (t.__name__, type(e).__name__, e))
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
