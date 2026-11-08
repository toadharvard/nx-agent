#!/usr/bin/env python3
"""tests/s13/test_stores.py - the store divergences (native-only-store / x86-only-store / store-width, D24,
difffuzz-20), on hand-written code (no decomp content).

  latereg   classify.entry_register_reads follows every path of the function, not only the straight line up to the
            first branch: a register argument first read after a branch (0x00db0e80 stores through a second pointer
            in edi after a `jp`) is a custom register of the oracle; one written first on that path is not
  st0       x86emu: the calling unit declares a float result of a callee whose definition says void (the tree's
            declaration): the caller's code reads ST0 and the native reads v0, so the oracle gets the canned value
            in ST0 too (0x007fb530: the missing ST0 read as 0 where the native computed -0.0)
  zeroinit  causes: native-only stores of zero bytes into the object a constructor builds or a fresh allocation are
            native-zero-init (by design); nonzero stores, other functions and other regions stay native-only-store
  deadstore causes: oracle-only stores of zero bytes into the `this` of a destructor or a scalar-deleting wrapper are
            dtor-dead-store (by design); a constructor or nonzero bytes stay x86-only-store
  thunk     causes: a store divergence of a function with its own `_ZThn<d>_` thunk is thunk-binding

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13stores'))

import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def test_latereg():
    cv = conv.Conv('?f@@YAXPAH0@Z', 'test')                # void f(int *, int *), cdecl: nothing in registers
    code = bytes.fromhex('85f6'        # test esi, esi
                         '7402'        # jz +2
                         '8937'        # mov [edi], esi     edi first read after the branch
                         'c3')
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check(got == ['esi', 'edi'], 'a register first read after a branch is a custom register: %s' % got)
    code = bytes.fromhex('85f6'        # test esi, esi
                         '7407'        # jz +7
                         'bf05000000'  # mov edi, 5         written first on that path
                         '8937'        # mov [edi], esi
                         'c3')
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check(got == ['esi'], 'one written before its read is not: %s' % got)
    code = bytes.fromhex('85f6' '7402' '8b3f' 'c3')        # jz over `mov edi, [edi]`: a read, still custom
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check(got == ['esi', 'edi'], 'a read of the register itself counts: %s' % got)
    code = bytes.fromhex('e800000000' '85f6' '7402' '8937' 'c3')   # a call first: eax/ecx/edx are clobbered, edi is not
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check(got == ['esi', 'edi'], 'a call before the branch does not hide a callee-saved register: %s' % got)


class Info:
    """callee_info for the oracle's one callee: the definition says void (pops 0), the caller's unit says float."""

    def __init__(self, definition, declaration):
        self._d, self._c = definition, declaration
        self.conv = lambda addr: self._d
        self.caller_conv = lambda addr: self._c

    def __call__(self, addr):
        return (0, 'f32' if self._d.x86_ret == 'st0' else 'int')


def test_st0():
    cv = conv.Conv('?f@@YAMM@Z', 'test')                   # float f(float), cdecl
    oracle = (bytes.fromhex('d9442404' '51' 'd91c24')      # fld [esp+4]; push ecx; fstp [esp]
              + call_rel(0x00401007, 0x00402000)
              + bytes.fromhex('83c404' 'd8c0' 'c3'))       # add esp, 4; fadd st0, st0; ret
    body = ('\tstp x29, x30, [sp, #-16]!\n\tbl __fnv_00402000\n\tfadd s0, s0, s0\n\tldp x29, x30, [sp], #16\n\tret\n')
    rets = [struct.pack('<f', 1.5) + bytes(4) + struct.pack('<d', 1.5)]
    case = world.Case(seed=2, args={'p0': struct.pack('<f', 0.25)}, rets=rets)
    for decl, what in ((conv.Conv('?g@@YAMM@Z', 'test'), 'the caller declares float'),
                       (conv.Conv('?g@@YAXM@Z', 'test'), 'both declare void')):
        with tempfile.TemporaryDirectory() as d:
            img = build_image(body, d, [stub(0x00402000)])
            t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {},
                              Info(conv.Conv('?g@@YAXM@Z', 'test'), decl))
            rx, ra, div = runner.Pair(t, {}).run(case)
            if decl.x86_ret == 'st0':
                check(div is None and rx.ret is not None and struct.unpack('<f', rx.ret[:4])[0] == 3.0,
                      '%s: the oracle gets the canned value in ST0 (%s, %s)' % (
                          what, rx.ret and struct.unpack('<f', rx.ret[:4])[0], div and div['kind']))
            else:
                check(div is not None, '%s: no ST0 load, the machines differ as before (%s)' % (what, div))


def _rec(symbol, this=0x03000000, source='s', address='00401000'):
    return dict(symbol=symbol, source=source, address=address, case=dict(args={'this': struct.pack('<I', this).hex()}))


def _div(addrs, x86=False, arm=False, **kw):
    n = len(addrs)
    d = dict(kind='mem-write', addr='%08x' % addrs[0], ndiff=n, x86='00' * 8, arm='00' * 8,
             only_x86=n if x86 else 0, only_arm=n if arm else 0, addrs=['%08x' % a for a in addrs])
    d.update(kw)
    return d


def test_zeroinit():
    blk = causes.ALLOC_BASE + 0x1000
    d = _div([blk + 4, blk + 5, blk + 6], arm=True, arm_zero_only=True)
    check(causes._rule_mem_write(d, _rec('?FUN_00c610a0@@YAPAXXZ')) == 'native-zero-init',
          'zeroes in a fresh allocation block: native-zero-init')
    d = _div([0x03000038, 0x03000039], arm=True, arm_zero_only=True)
    check(causes._rule_mem_write(d, _rec('??0Foo@@QAE@XZ')) == 'native-zero-init',
          'zeroes in the this of a constructor: native-zero-init')
    check(causes._rule_mem_write(d, _rec('?Reset@Foo@@QAEXXZ')) == 'native-only-store',
          'the same bytes in another method stay native-only-store')
    d2 = _div([0x03000038], arm=True)
    check(causes._rule_mem_write(d2, _rec('??0Foo@@QAE@XZ')) == 'native-only-store',
          'a nonzero native store in a constructor stays native-only-store')
    d3 = _div([0x03000038], arm=True, arm_zero_only=True)
    check(causes._rule_mem_write(d3, _rec('??0Foo@@QAE@XZ', this=0x03001000)) == 'native-only-store',
          'zeroes outside the constructed object stay native-only-store')
    check(not causes.policy('native-zero-init')[2], 'native-zero-init is by design')


def test_deadstore():
    d = _div([0x03000070], x86=True, x86_zero_only=True)
    for sym in ('??1Foo@@UAE@XZ', '??_GFoo@@UAEPAXI@Z', '?DestroyIfFlagged@S@@QAEPAXI@Z', '?f@C@@QAEPAX_N@Z'):
        check(causes._rule_mem_write(d, _rec(sym)) == 'dtor-dead-store', 'dead zero store in %s' % sym)
    check(causes._rule_mem_write(d, _rec('??0Foo@@QAE@XZ')) == 'x86-only-store',
          'a constructor keeps x86-only-store')
    check(causes._rule_mem_write(_div([0x03000070], x86=True), _rec('??1Foo@@UAE@XZ')) == 'x86-only-store',
          'a nonzero oracle store in a destructor keeps x86-only-store')
    check(causes._rule_mem_write(_div([0x03000070], x86=True, x86_zero_only=True), _rec('??1Foo@@UAE@XZ', this=0x03001000))
          == 'x86-only-store', 'a store outside the destroyed object keeps x86-only-store')
    r = _rec('??1Foo@@UAE@XZ')
    r['case']['args'].clear()          # a minimised case drops the `this` it shares with the default buffer
    check(causes._rule_mem_write(d, r) == 'dtor-dead-store', 'no `this` in the case: the first argument buffer')
    check(not causes.policy('dtor-dead-store')[2], 'dtor-dead-store is by design')


def test_thunk():
    orig = causes._object_facts
    causes._object_facts = lambda src: ({'__fnv_00d089f0': {28}}, set())
    try:
        d = _div([0x03000069], arm=True)
        check(causes._rule_mem_write(d, _rec('?f@T@@UAEXPAX@Z', address='00d089f0')) == 'thunk-binding',
              'a function with its own _ZThn thunk: thunk-binding')
        check(causes._rule_mem_write(d, _rec('?f@T@@UAEXPAX@Z', address='00d08a00')) == 'native-only-store',
              'another function: unchanged')
    finally:
        causes._object_facts = orig


if __name__ == '__main__':
    test_latereg()
    test_st0()
    test_zeroinit()
    test_deadstore()
    test_thunk()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
