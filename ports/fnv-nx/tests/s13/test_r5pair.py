#!/usr/bin/env python3
"""tests/s13/test_r5pair.py - EQ r5 pairing / stack-byte class (difffuzz-20), on hand-written code (no exe bytes).

  calib-touch  engine.calibrate_custom: when every calibration case ends in a fault on both machines the declared
               pairing agrees with any other (0x00dbcdd0: object in edi, count in eax). A fault that differs in kind
               or address is no agreement, and the buffers the oracle touches under a pairing are compared with the
               native's (calib_touch_pick)
  calib-vptr   engine.calib_vptr_noise: a byte copied out of a planted vptr word differs by representation only
               (0x00d409b0)
  stub-flow    the oracle's STUB_OUT zeroing keeps the arguments and the initialised locals around the buffer, and a
               pointer into a dead incoming-argument slot zeroes that one word (0x00a3d370, 0x006a0c50); a call flow
               that depends on such a word is stub-outparam (classify.hint)
  adapt-state  runner.Pair.run: a call divergence found after the pair followed a callee is run again on the oracle
               code as it was; agreeing there is the result (0x00a24280)
  byte-arg     retsrc.byte_only_args / compare: a stack word the callee reads as one byte (directly or by handing the
               word to a callee that does) is compared by its low byte (0x00b6d170)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13r5pair'))

import classify   # noqa: E402
import conv       # noqa: E402
import engine     # noqa: E402
import retsrc     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_args import BASE, Info, rel   # noqa: E402
from test_args import exe as _exe   # noqa: E402
from test_d14 import build_image, call_rel, stub   # noqa: E402
from test_memdiv import build_at   # noqa: E402

FAIL = []
h = bytes.fromhex


def exe(blobs):
    _exe(blobs)
    getattr(retsrc, '_BYTES', {}).clear()


def dv(div):
    return div and (div['kind'], div.get('arg'), div.get('x86'), div.get('arm'))


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


# void f(void *obj, int count): the oracle takes obj in edi and count in eax; `(*(void (**)())(*obj + 0x20))()`.
# Both machines fault whatever the buffers hold (the vptr is 0 or a random word, the slot read is background)
CALIB_ORACLE = h('8b0f' '8b5120' 'ffd2' 'c3')              # mov ecx,[edi]; mov edx,[ecx+0x20]; call edx; ret
CALIB_NATIVE = '\tldr w2, [x0]\n\tldr w3, [x2, #0x20]\n\tblr x3\n\tret\n'


def test_calib_touch():
    found = 0x00401000
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXPAXH@Z', 'test')
        cv.with_registers(['eax', 'edi'])
        t = runner.Target(cv, found, {found: CALIB_ORACLE}, build_at(CALIB_NATIVE, d, found), {})
        t.custom_cc, t.source = list(cv.custom), 'test'
        pair = runner.Pair(t, {})
        engine.calibrate_custom(pair, t)
        got = dict((lab, sp) for lab, sp, _, _ in t.conv.x86)
        check(got.get('p0') == 'edi' and got.get('p1') == 'eax',
              'object in edi, count in eax though the declared order faults like the right one (%s %s)' % (
                  got, getattr(t, 'custom_calib', None)))


class FakePair:
    """run() answers with a mem-write divergence unless world.VPTR_NATIVE is set."""
    def __init__(self, still):
        self.still = still
        self.seen = []

    def run(self, case, max_steps=200000):
        self.seen.append(world.VPTR_NATIVE)
        return None, None, (None if world.VPTR_NATIVE and not self.still else
                            dict(kind='mem-write', x86='00', arm='80'))


def test_calib_vptr():
    d = dict(kind='mem-write', x86='0000', arm='00c0')
    withv = world.Case(seed=1, vptrs={0x03000000: 0x010c6fb0})
    check(engine.calib_vptr_noise(FakePair(False), withv, d) is True, 'agrees once the vptr word is the same on both')
    check(world.VPTR_NATIVE is False, 'the probe switch is restored')
    check(engine.calib_vptr_noise(FakePair(True), withv, d) is False, 'a difference that stays is none of its business')
    check(engine.calib_vptr_noise(FakePair(False), world.Case(seed=1), d) is False, 'no planted vptr: not looked at')
    check(engine.calib_vptr_noise(FakePair(False), withv, dict(d, kind='return')) is False, 'only a store diff')


G, H, K = 0x00402000, 0x00402100, 0x00402200
STUBS = [stub(G), stub(H), stub(K)]


def stub_run(cv, oracle, body, case, stub_out, info=None):
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image(body, d, STUBS), {}, info)
        p = runner.Pair(t, {})
        world.STUB_OUT = stub_out
        try:
            rx, ra, div = p.run(case)
            hint = classify.hint(p, t, case, div, dict(code=oracle, address=0x00401000), 200000) \
                if div and not stub_out else {}
        finally:
            world.STUB_OUT = False
        return rx, ra, div, hint


# void f(int p0, int p1): MSVC reads G's out-parameter into the dead slot of p0; the key count decides a call (H);
# p1 is live and goes to K after
SLOT_ORACLE = (h('8d442404' '50') + call_rel(0x00401005, G) + h('83c404' '837c240400' '7405') +
               call_rel(0x00401013, H) + h('8b442408' '50') + call_rel(0x0040101a, K) + h('83c404' 'c3'))
# the native: int n = 0; G(&n); if (n) H(); K(p1);
SLOT_NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tstr x19, [sp, #16]\n\tmov w19, w1\n\tstr wzr, [sp, #28]\n'
               '\tadd x0, sp, #28\n\tbl __fnv_%08x\n\tldr w2, [sp, #28]\n\tcbz w2, 1f\n\tbl __fnv_%08x\n'
               '1:\tmov w0, w19\n\tbl __fnv_%08x\n\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n' % (G, H, K))
# void f(): int a[2]; a[1] = 0x1234; G(&a[0]); K(a[1]);   (the pointer designates a[0]; a[1] is initialised)
LOCAL_ORACLE = (h('83ec08' 'c744240434120000' '8bc4' '50') + call_rel(0x00401010, G) + h('83c404' '8b442404' '50') +
                call_rel(0x0040101b, K) + h('83c404' '83c408' 'c3'))
LOCAL_NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tmov w1, #0x1234\n\tstr w1, [sp, #20]\n\tadd x0, sp, #16\n'
                '\tbl __fnv_%08x\n\tldr w0, [sp, #20]\n\tbl __fnv_%08x\n\tldp x29, x30, [sp], #32\n\tret\n' % (G, K))


def test_stub_flow():
    retsrc._PE[:] = [None]
    cvk = conv.Conv('?k@@YAXH@Z', 'test')
    info = Info({K: cvk})
    cv = conv.Conv('?f@@YAXHH@Z', 'test')
    case = world.Case(seed=3, args={'p0': (0x40).to_bytes(4, 'little'), 'p1': (0x77).to_bytes(4, 'little')})
    rx, ra, div, hint = stub_run(cv, SLOT_ORACLE, SLOT_NATIVE, case, False, info)
    check(div is not None and div['kind'] == 'call-count',
          'the oracle reads the dead argument slot nobody filled: a call the native does not make (%s)' % (div and div['kind'],))
    check(hint.get('cause') == 'stub-outparam',
          'a call flow that depends on a word the stubbed callee should have filled: stub-outparam (%s)' % hint)
    rx, ra, div, _ = stub_run(cv, SLOT_ORACLE, SLOT_NATIVE, case, True, info)
    check(div is None, 'STUB_OUT: both machines see 0 in the slot and the live argument p1 reaches K intact (%s)'
          % (dv(div),))
    cv0 = conv.Conv('?f@@YAXXZ', 'test')
    rx, ra, div, _ = stub_run(cv0, LOCAL_ORACLE, LOCAL_NATIVE, world.Case(seed=3), True, info)
    check(div is None, 'STUB_OUT: a local initialised beside the buffer stays (%s)'
          % (dv(div),))


# void f(): S(); F2();   S (real code) leaves 0x55 on the stack; F2 (real code) has `int a; G(&a); K(a);`, a at the same
# address. The native has no S frame: its `a` holds the planted fill
SCRUB_S, SCRUB_F2 = 0x00401100, 0x00401200
SCRUB_F = call_rel(0x00401000, SCRUB_S) + call_rel(0x00401005, SCRUB_F2) + h('c3')
SCRUB_SBODY = h('6a55' '6a55' '83c408' 'c3')
SCRUB_F2BODY = (h('83ec04' '8d0424' '50') + call_rel(SCRUB_F2 + 7, G) + h('83c404' '8b0424' '50') +
                call_rel(SCRUB_F2 + 19, K) + h('83c404' '83c404' 'c3'))
SCRUB_NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tadd x0, sp, #28\n\tbl __fnv_%08x\n\tldr w0, [sp, #28]\n'
                '\tbl __fnv_%08x\n\tldp x29, x30, [sp], #32\n\tret\n' % (G, K))


def test_scrub():
    retsrc._PE[:] = [None]
    info = Info({K: conv.Conv('?k@@YAXH@Z', 'test')})
    cv = conv.Conv('?f@@YAXXZ', 'test')
    code = {0x00401000: SCRUB_F, SCRUB_S: SCRUB_SBODY, SCRUB_F2: SCRUB_F2BODY}
    for case in (world.Case(seed=3), world.Case(seed=11)):
        with tempfile.TemporaryDirectory() as d:
            t = runner.Target(cv, 0x00401000, code, build_image(SCRUB_NATIVE, d, STUBS), {}, info)
            p = runner.Pair(t, {})
            world.STUB_OUT = True
            try:
                rx, ra, div = p.run(case)
            finally:
                world.STUB_OUT = False
        check(div is None, 'STUB_OUT, seed %d: the local the stub should have filled is not the residue of an earlier frame'
              ' (%s)' % (case.seed, dv(div)))


# void f(int a): if (a) { X(); return; } X();     the native inlined X (an empty body) at the first site only
X = 0x00403000
ADAPT_ORACLE = h('837c240400' '7406') + call_rel(0x00401007, X) + h('c3') + call_rel(0x0040100d, X) + h('c3')
ADAPT_NATIVE = ('\tcbnz w0, 2f\n\tstp x29, x30, [sp, #-16]!\n\tbl __fnv_%08x\n\tldp x29, x30, [sp], #16\n2:\tret\n' % X)


def test_adapt_state():
    cv = conv.Conv('?f@@YAXH@Z', 'test')
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: ADAPT_ORACLE}, build_image(ADAPT_NATIVE, d, [stub(X)]), {})
        p = runner.Pair(t, {})
        # what an earlier case learned: the native inlined X, so the oracle runs X's body for real from now on
        t.x86_code[X] = b'\xc3'
        p.adapted = 1
        p.xe = None
        rx, ra, div = p.run(world.Case(seed=2, args={'p0': (0).to_bytes(4, 'little')}))
        check(div is None, 'the native calls X on this path: the unadapted oracle agrees, so does the case (%s)'
              % (dv(div),))
        check(X in t.x86_code and p.adapted == 1, 'and the learned follow is kept for the next case')
        # a divergence that stays is still reported: the oracle also calls H, which the native never does, in both states
        h2 = h('e8') + struct.pack('<i', H - (0x00401000 + 5)) + h('e8') + struct.pack('<i', H - (0x00401000 + 10)) + h('c3')
        t2 = runner.Target(cv, 0x00401000, {0x00401000: h2}, build_image('\tret\n', d, [stub(X), stub(H)]), {})
        p2 = runner.Pair(t2, {})
        t2.x86_code[X] = b'\xc3'
        p2.adapted = 1
        rx, ra, div = p2.run(world.Case(seed=2, args={'p0': (0).to_bytes(4, 'little')}))
        check(div is not None and div['kind'] == 'call-count', 'a call the native never makes is still a divergence (%s)'
              % (div and div['kind'],))


def test_byte_arg():
    callee, fwd = BASE + 0x1000, BASE + 0x1100
    byte_only = h('807c240400' 'c3')                                   # cmp byte [esp+4], 0; ret
    whole = h('8b442404' 'c3')                                         # mov eax, [esp+4]; ret
    cmp_dw = h('837c240400' 'c3')                                      # cmp dword [esp+4], 0; ret
    fwd_code = h('8b442404' '50') + b'\xe8' + rel(fwd + 5, callee) + h('83c404' 'c3')   # push [esp+4] -> callee
    exe({callee: byte_only})
    check(retsrc.byte_only_args(callee) == frozenset({0}), 'cmp byte [esp+4], 0: word 0 is byte-only (%s)'
          % (retsrc.byte_only_args(callee),))
    exe({callee: whole})
    check(retsrc.byte_only_args(callee) == frozenset(), 'a whole-word load is no byte read')
    exe({callee: cmp_dw})
    check(retsrc.byte_only_args(callee) == frozenset(), 'a dword compare is no byte read')
    exe({callee: byte_only, fwd: fwd_code})
    check(retsrc.byte_only_args(fwd) == frozenset({0}), 'a word handed on to a callee that reads one byte: byte-only (%s)'
          % (retsrc.byte_only_args(fwd),))
    exe({callee: whole, fwd: fwd_code})
    check(retsrc.byte_only_args(fwd) == frozenset(), 'handed on to a callee that reads it whole: not byte-only')
    exe({callee: h('8d442404' 'c3')})                                  # lea eax,[esp+4]: the address of the slot
    check(retsrc.byte_only_args(callee) is None, 'the address of the slot taken: unknown')
    retsrc._PE[:] = [None]
    check(retsrc.byte_only_args(callee + 1) is None, 'without the exe: unknown')


def test_byte_arg_compare():
    callee = BASE + 0x1000
    byte_only = h('807c240400' 'c3')
    whole = h('8b442404' 'c3')
    # the oracle pushes 0xffffff01 (a byte store into a dead slot), the native passes 1
    cvg = conv.Conv('?g@@YAXH@Z', 'test')
    cvf = conv.Conv('?f@@YAXXZ', 'test')
    oracle = h('68' '01ffffff') + b'\xe8' + rel(BASE + 5, callee) + h('83c404' 'c3')
    arm = '\tstp x29, x30, [sp, #-16]!\n\tmov w0, #1\n\tbl __fnv_%08x\n\tldp x29, x30, [sp], #16\n\tret\n' % callee
    for code, want_div in ((byte_only, False), (whole, True)):
        exe({callee: code})
        with tempfile.TemporaryDirectory() as d:
            img = build_image(arm, d, [('__fnv_%08x' % callee, 0x08000000 + callee)])
            p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle}, img, {}, Info({callee: cvg})), {})
            rx, ra, div = p.run(world.Case(seed=3, args={}, mem={}))
        check((div is not None) == want_div, 'callee reads %s: %s (%s)' % (
            'the whole word' if want_div else 'one byte', 'the upper bytes differ' if want_div else 'equal',
            dv(div)))
    retsrc._PE[:] = [None]


def main():
    for t in (test_calib_touch, test_calib_vptr, test_stub_flow, test_scrub, test_adapt_state, test_byte_arg, test_byte_arg_compare):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            check(False, '%s raised %r' % (t.__name__, e))
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
