#!/usr/bin/env python3
"""tests/s13/test_f1b.py - F1b (oracle-fault / native-early-exit / unclassified, D24, difffuzz-20): harness
artifacts behind oracle-only faults and early exits, on hand-written code (no decomp content).

  idioms     classify.entry_register_reads: MSVC alignment padding (`lea ecx, [ecx]`, `mov edi, edi`) and writes
             that ignore the old value (`or esi, -1`, `sbb edx, edx`) are not register reads; they made a custom
             register argument of a register the function only overwrites (0x00e05de0, 0x00e192c0)
  calibrate  engine.calibrate_custom: a custom register carries the parameter its pairing agrees on, not the
             declared order (0x00d58d40 `shl eax, 4; add eax, [esp+4]`: base on the stack, the index in eax)
  smash      x86emu: a store into the oracle's own return-address slot marks the divergence x86_frame_smash, and an
             oracle-only fault after it is oracle-frame-smash (by design: a local array overrun by an out-of-range
             fuzzed count, 0x00b3c750 / 0x00b3c840)
  follow     runner.Pair.run: the adaptive follow does not load an oracle callee the native function calls out of
             line itself (it skipped the call, it did not inline it): the record stays a call-count, not an oracle
             fault inside the followed body (0x009d6c00, 0x00997aa0, 0x00e99260: bool bytes)
  loop       runner.Pair.run: a case that follows a sixth oracle callee is run again after the fifth load (it
             returned the run from before the load: 0x00c4b600 inlines five header functions)
  bgbool     x86emu world.BG_READS: the bytes the oracle reads from the random background, so the bool-repr probe
             can plant 1 there (0x00adb9f0, 0x0087b7c0, 0x00703180); classify._benign: a trial that leaves only a
             NaN sign / payload store counts as agreeing (0x00c207a0)
  nrvo       runner._nrvo_copy / compare_args: the oracle's trailing copy constructor into the result buffer from
             a stack local and that local's destructor are MSVC without NRVO (/Od); the local is the native's
             result buffer in the calls before (0x0071dfa0)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13f1b'))

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


INDEX = bytes.fromhex('c1e004'            # shl eax, 4         (the index, in eax)
                      '03442404'          # add eax, [esp+4]   (the base, on the stack)
                      'c3')


def test_idioms():
    cv = conv.Conv('?f@@YAPAHPAHH@Z', 'test')          # int *f(int *, int), cdecl
    code = bytes.fromhex('8b442404'       # mov eax, [esp+4]
                         '8d4900'         # lea ecx, [ecx]       padding
                         '8bff'           # mov edi, edi         padding
                         '83ceff'         # or esi, -1
                         '1bd2'           # sbb edx, edx
                         '8b0e'           # mov ecx, [esi]       esi written above
                         'c3')
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check(got == [], 'padding and value-free writes read no register: %s' % got)
    got = classify.entry_register_reads(dict(code=INDEX, address=0x00401000), cv)
    check(got == ['eax'], 'a real custom register still counts: %s' % got)


def test_calibrate():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAPAHPAHH@Z', 'test')
        cv.with_registers(['eax'])
        img = build_image('\tadd w0, w0, w1, lsl #4\n\tret\n', d)
        t = runner.Target(cv, 0x00401000, {0x00401000: INDEX}, img, {})
        t.custom_cc, t.source = list(cv.custom), 'test'
        pair = runner.Pair(t, {})
        c = world.Case(seed=3, args={'p1': struct.pack('<I', 2)}, mem={world.BUF: bytes(64)})
        _, _, d0 = pair.run(c)
        check(d0 is not None and d0['kind'] == 'return', 'declared order (eax = p0): the results differ (%s)' % (
            d0 and d0['kind']))
        engine.calibrate_custom(pair, t)
        check('eax=p1' in (getattr(t, 'custom_calib', None) or {}) and dict(
            (lab, sp) for lab, sp, _, _ in t.conv.x86).get('p1') == 'eax',
              'calibrated: eax carries p1 (%s)' % getattr(t, 'custom_calib', None))
        _, _, d1 = pair.run(c)
        check(d1 is None, 'then the machines agree (%s)' % (d1 and d1['kind']))


def test_smash():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXXZ', 'test')
        oracle = bytes.fromhex('c7042410000000'   # mov dword [esp], 0x10   (its own return slot)
                               'a110000000'       # mov eax, [0x10]
                               'c3')
        img = build_image('\tret\n', d)
        _, _, div = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {}), {}).run(
            world.Case(seed=1))
        check(div is not None and div.get('x86_frame_smash') is True, 'the return-slot store is recorded (%s)' % div)
        check(causes._rule_error(div or {}) == 'oracle-frame-smash' and not causes.policy('oracle-frame-smash')[2],
              'an oracle-only fault after it is oracle-frame-smash, not hard')
        x = x86emu.Result()
        check(x.ret_smashed is False, 'a fresh result is not smashed')


def test_follow():
    callee = 0x00402000
    oracle = b'\xe8' + struct.pack('<i', callee - (0x00401000 + 5)) + b'\xc3'
    faulting = bytes.fromhex('a100000000' 'c3')        # mov eax, [0]: the oracle's body faults on null
    saved = causes.native_callees, causes.link_symbol
    try:
        for names, want in (({'__fnv_00402000'}, 'call-count'), (set(), 'error')):
            causes.native_callees = lambda src, a, names=names: names if a == 0x00401000 else set()
            causes.link_symbol = lambda a: None
            with tempfile.TemporaryDirectory() as d:
                cv = conv.Conv('?f@@YAXXZ', 'test')
                img = build_image('\tret\n', d)
                t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {})
                t.source = 'test'
                t.oracle_code = lambda a: (faulting, {}) if a == callee else None
                _, _, div = runner.Pair(t, {}).run(world.Case(seed=1))
                check(div is not None and div['kind'] == want,
                      'native names %s: %s (%s)' % (sorted(names) or 'nothing', want, div and div['kind']))
    finally:
        causes.native_callees, causes.link_symbol = saved


def test_loop():
    callees = [0x00402000 + 0x10 * k for k in range(6)]
    oracle = b''
    for k, c in enumerate(callees):
        oracle += b'\xe8' + struct.pack('<i', c - (0x00401000 + 5 * k + 5))
    oracle += b'\xc3'
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXXZ', 'test')
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image('\tret\n', d), {})
        t.source = 'test'
        t.oracle_code = lambda a: (b'\xc3', {}) if a in callees else None
        p = runner.Pair(t, {})
        _, _, div = p.run(world.Case(seed=1))
        check(div is None and p.adapted == 6, 'six followed callees, then the machines agree (%s, %d loads)' % (
            div and div['kind'], p.adapted))


def test_bgbool():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXXZ', 'test')
        oracle = bytes.fromhex('a000000005'        # mov al, [0x05000000]   a byte of the random background
                               'c3')
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image('\tret\n', d), {}), {})
        c = world.Case(seed=5, bg='rand')
        bg = {}
        world.BG_READS = bg
        try:
            p.run(c)
        finally:
            world.BG_READS = None
        e = bg.get(0x05000000)
        check(e is not None and e[0] == {1} and isinstance(e[1], int), 'the background byte read is recorded (%s)' % e)
    check(classify._benign(None) and classify._benign(dict(kind='mem-write', x86='0000c07f', arm='0000c0ff')) and
          not classify._benign(dict(kind='mem-write', x86='0000803f', arm='00000040')),
          'a NaN sign / payload remainder is benign, another value is not')


class _Call:
    def __init__(self, target, args):
        self.target, self.args = target, args


class _Conv:
    def __init__(self, name):
        self.name = name


def test_nrvo():
    sret, local = 0x03001000, world.SP0 - 0x2c
    names = {0x004047f0: '??0S@@QAE@ABV0@@Z', 0x004037d0: '??1S@@QAE@XZ', 0x00438390: '?g@@YAXXZ'}
    cf = lambda a: _Conv(names.get(a, ''))      # noqa: E731
    xcalls = [_Call(0x004047f0, [local, 0, 0x03000000]), _Call(0x00438390, [0, 0]),
              _Call(0x004047f0, [sret, 0, local]), _Call(0x004037d0, [local, 0])]
    ta = [0x004047f0, 0x00438390]
    check(runner._nrvo_copy(xcalls, ta, sret, (cf,)) == local, 'copy into the result buffer + dtor of the local')
    check(runner._nrvo_copy(xcalls, ta, sret + 4, (cf,)) is None, 'not when the copy goes elsewhere')
    bad = xcalls[:3] + [_Call(0x004037d0, [local + 8, 0])]
    check(runner._nrvo_copy(bad, ta, sret, (cf,)) is None, 'not when another object is destroyed')
    names2 = dict(names); names2[0x004047f0] = '??0S@@QAE@H@Z'
    check(runner._nrvo_copy(xcalls, ta, sret, (lambda a: _Conv(names2.get(a, '')),)) is None,
          'not for a constructor that is no copy constructor')
    cv = conv.Conv('?h@@YAXPAH@Z', 'test')
    cx = _Call(0x004047f0, [0, 0, local])
    ca = type('A', (), {'args': [sret] + [0] * 7, 'fargs': [0] * 8})()
    check(runner.compare_args(cv, cx, ca) is not None, 'without the alias the local and the buffer differ')
    check(runner.compare_args(cv, cx, ca, alias=(local, sret)) is None, 'with it they are the same object')


if __name__ == '__main__':
    test_idioms()
    test_calibrate()
    test_smash()
    test_follow()
    test_loop()
    test_bgbool()
    test_nrvo()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
