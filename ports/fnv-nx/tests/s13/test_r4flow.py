#!/usr/bin/env python3
"""tests/s13/test_r4flow.py - the call-flow / oracle-fault / pop residue (EQ round 4, difffuzz-20), synthetic code only.

  dtorstore  causes: the scalar-deleting wrapper shape also takes an `int` flag (`?f@C@@QAEPAXH@Z`, 0x00bd8480), a
             decomp-named `Destructor` is a destructor, and an oracle-only store of 0x80000000 (hkArray's
             DONT_DEALLOCATE_FLAG, 0x00d08f80) is as dead as a zero store (runner marks x86_const_only)
  nrvocopy   runner._nrvo_copy: the copy constructor of a `struct` is `??0X@@QAE@ABU0@@Z` (0x00aaed70), not only `V`
  pusharg    classify.entry_register_reads: `push ecx` is a call argument when the call's `add esp, N` consumes it
             (0x00d409b0: `push eax; push ecx; push esi; call; add esp, 0xc`), padding when it does not
  deletesplit causes: the native frees with its own operator delete after the D1 binding of a scalar deleting
             destructor wrapper the oracle called once (0x009edf40)
  importcanned x86emu: an import's canned eax has the low byte 0/1 as the native's untyped result (0x00d45c50)

Needs capstone.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import capstone   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def _rec(symbol, this=0x03000000):
    return dict(symbol=symbol, source='s', address='00401000', case=dict(args={'this': struct.pack('<I', this).hex()}))


def _div(addrs, **kw):
    n = len(addrs)
    d = dict(kind='mem-write', addr='%08x' % addrs[0], ndiff=n, x86='00' * 8, arm='00' * 8, only_x86=n, only_arm=0,
             addrs=['%08x' % a for a in addrs])
    d.update(kw)
    return d


def test_dtorstore():
    d = _div([0x0300013c], x86_zero_only=True)
    check(causes._rule_mem_write(d, _rec('?FUN_00bd8480@C@@QAEPAXH@Z')) == 'dtor-dead-store',
          'scalar-deleting wrapper with an int flag')
    check(causes._rule_mem_write(d, _rec('?f@C@@QAEPAXF@Z')) == 'x86-only-store', 'another parameter type: no')
    d = _div([0x0300004f], x86_const_only=True)
    check(causes._rule_mem_write(d, _rec('?Destructor@Tgt@@QAEXXZ')) == 'dtor-dead-store',
          'a decomp-named Destructor with a 0x80000000 flag word store')
    check(causes._rule_mem_write(d, _rec('?Reset@Tgt@@QAEXXZ')) == 'x86-only-store', 'a method of another name: no')
    check(causes._rule_mem_write(_div([0x0300004f]), _rec('?Destructor@Tgt@@QAEXXZ')) == 'x86-only-store',
          'without the constant-bytes mark the store stays x86-only-store')


def _compare_marks(wx, wa, bg=0):
    """The x86_zero_only / x86_const_only marks runner.compare puts on a store divergence of these two write maps."""
    class R:
        error = None
        saved_ok = True
        calls = []
        ret = None
        blocks = set()
        noreturn = None

        def __init__(self, w):
            self.writes = w
    cv = conv.Conv('?Destructor@Tgt@@QAEXXZ', 'x.cpp')

    class W:
        def page(self, p):
            return bytes([bg]) * 0x1000
    return runner.compare(cv, R(wx), R(wa), W())


def test_const_marks():
    d = _compare_marks({0x0300004f: 0x80}, {})
    check(d and d.get('x86_const_only') and not d.get('x86_zero_only'), '0x80 in the top byte of a word: const only: %s' % d)
    d = _compare_marks({0x0300004e: 0x80}, {})
    check(d and not d.get('x86_const_only'), '0x80 in a middle byte: not a flag word')
    d = _compare_marks({0x0300004f: 0x81}, {})
    check(d and not d.get('x86_const_only'), 'another top byte: not a flag word')
    d = _compare_marks({0x0300004c: 0x00, 0x0300004d: 0x00}, {}, bg=0xff)
    check(d and d.get('x86_zero_only'), 'zero bytes keep x86_zero_only')
    d = _compare_marks({0x0300004f: 0x80}, {}, bg=0xff)
    check(d and not d.get('x86_const_only'), 'a flag byte over a nonzero background is not the flag word')
    d = _compare_marks({0x0300004c: 0, 0x0300004d: 0, 0x0300004e: 0, 0x0300004f: 0x80}, {}, bg=0xff)
    check(d and d.get('x86_const_only'), 'the four bytes written: the flag word over a nonzero background')


def test_nrvocopy():
    class Call:
        def __init__(self, target, args):
            self.target, self.args = target, args
    local, sret = 0x06effe00, 0x03002000
    xcalls = [Call(0x500000, [0, 0]), Call(0x658f30, [sret, 0, local]), Call(0x449680, [local, 0, 0])]

    def names(a):
        return {0x658f30: conv.Conv('??0Mat@@QAE@ABU0@@Z', 'x.cpp'),
                0x449680: conv.Conv('??1Vec@@QAE@XZ', 'x.cpp')}.get(a)
    got = runner._nrvo_copy(xcalls, [0x500000], sret, (names,))
    check(got == local, 'struct copy constructor + destructor of the local: nrvo-copy (%s)' % got)

    def names_cls(a):
        return {0x658f30: conv.Conv('??0Mat@@QAE@ABV0@@Z', 'x.cpp'),
                0x449680: conv.Conv('??1Vec@@QAE@XZ', 'x.cpp')}.get(a)
    check(runner._nrvo_copy(xcalls, [0x500000], sret, (names_cls,)) == local, 'class copy constructor still')

    def names_plain(a):
        return {0x658f30: conv.Conv('?Copy@Mat@@QAE@ABU1@@Z', 'x.cpp'),
                0x449680: conv.Conv('??1Vec@@QAE@XZ', 'x.cpp')}.get(a)
    check(runner._nrvo_copy(xcalls, [0x500000], sret, (names_plain,)) is None, 'a method that is no constructor: no')


def _fn(code):
    return dict(code=code, address=0x00401000)


def test_pusharg():
    cv = conv.Conv('?f@@YAXPAX0HH@Z', 'x.cpp')       # cdecl: nothing in registers
    call = bytes.fromhex('e8' + ((0x500000 - 0x401009) & 0xffffffff).to_bytes(4, 'little').hex())
    # push eax; push ecx; push esi; call; add esp, 0xc: three pushed arguments
    code = bytes.fromhex('50' '51' '56') + call + bytes.fromhex('8a5602' '83c40c' 'c3')   # ... mov dl, [esi+2]
    got = classify.entry_register_reads(_fn(code), cv)
    check(got == ['eax', 'ecx', 'esi'], 'the cleanup after a plain instruction (0x00d409b0 shape): %s' % got)
    code = bytes.fromhex('50' '51' '56') + call + bytes.fromhex('83c40c' '8a5602' 'c3')
    got = classify.entry_register_reads(_fn(code), cv)
    check(got == ['eax', 'ecx', 'esi'], 'push eax / ecx / esi consumed by add esp, 0xc: %s' % got)
    # push ecx (slot); push eax; call; add esp, 4: only eax is an argument, ecx stays padding
    call2 = bytes.fromhex('e8' + ((0x500000 - 0x401007) & 0xffffffff).to_bytes(4, 'little').hex())
    code = bytes.fromhex('51' '50') + call2 + bytes.fromhex('83c404' 'c3')
    got = classify.entry_register_reads(_fn(code), cv)
    check(got == ['eax'], 'a reservation before a one-argument call stays padding: %s' % got)
    code = bytes.fromhex('51' '8b4424' '08' 'c3')     # push ecx; mov eax, [esp+8]; ret
    check(classify.entry_register_reads(_fn(code), cv) == [], 'a bare reservation stays padding')


def test_deletesplit():
    import retsrc

    class FakePE:
        # 0x00798450: push ebp; mov ebp, esp; mov eax, [ebp+8]; and eax, 1; je +5; call 0x401030; ret 4
        def read(self, va, n):
            body = bytes.fromhex('55' '8bec' '8b4508' '83e001' '7405') + b'\xe8' + \
                ((0x401030 - (va + 16)) & 0xffffffff).to_bytes(4, 'little') + bytes.fromhex('c20400')
            return (body + bytes(n))[:n]
    retsrc._PE[:] = [FakePE()]
    d = dict(kind='call-target', index=2, x86='00483710', arm='00aa1460', x86_next='006708b0', arm_next='00483710',
             x86_trace=['009ee020', '00798450', '00483710'], arm_trace=['009ee020', '00798450', '00aa1460'])
    check(causes._rule_dtor_split(d) == 'dtor-split-delete', 'native frees itself after the wrapper: dtor-split-delete')
    check(causes._rule_calls(d) == 'dtor-split-delete', '... through _rule_calls')
    d2 = dict(d, arm_next='006708b0')
    check(causes._rule_dtor_split(d2) is None, 'the native goes on with another call: no class')
    d3 = dict(d, arm='00500000')
    check(causes._rule_dtor_split(d3) is None, 'not a deallocator: no class')
    d4 = dict(d, x86_trace=['009ee020', '00798440', '00483710'], arm_trace=['009ee020', '00798440', '00aa1460'])
    # 0x00798440 is no wrapper in the fake image either, but the fake returns the same bytes for every address:
    retsrc._PE[:] = [type('P', (), {'read': lambda self, va, n: bytes.fromhex('55' '8bec' '5d' 'c3') + bytes(n)})()]
    check(causes._rule_dtor_split(d4) is None, 'the previous call is no deleting wrapper: no class')
    check(causes.policy('dtor-split-delete')[0] == 'by-design', 'dtor-split-delete is by design')


def test_importcanned():
    import x86emu
    import inspect
    src = inspect.getsource(x86emu.X86Engine._on_import)
    # S1-rules d26-imp superseded R4 flow's normalisation: an import's canned result is the raw word on both machines
    # (the native stub path skips imports: arm64emu._is_import)
    import arm64emu
    check("(v & ~0xff) | (case.ret_bytes(k)[0] & 1)" not in src and hasattr(arm64emu.Arm64Engine, '_is_import'),
          'an import keeps its raw canned word on both machines (d26-imp)')


for t in (test_dtorstore, test_const_marks, test_nrvocopy, test_pusharg, test_deletesplit, test_importcanned):
    t()

print('FAILED: %d' % len(FAIL) if FAIL else 'ALL OK')
sys.exit(1 if FAIL else 0)
