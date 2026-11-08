#!/usr/bin/env python3
"""tests/s13/test_args.py - EQ args class (difffuzz-20), on hand-written code (no exe bytes).

  regcopy    retsrc.ret_source follows copies through esi / edi / ebx / edx: a constructor that keeps `this` in esi over its
             calls (0x00a75650), a callee that keeps its stack argument in edx over a call to a leaf not writing edx
             (0x00dba6a0); a leaf that does write edx loses it. returned_input names a fastcall free function's first
             parameter (ecx) `this` (the tree declares an unnamed constructor `void *F(void *p)`)
  unread     retsrc.input_reads / unread_labels: a bare `ret 4` reads nothing, a function that never touches ecx has an
             unread `this`, a `[ebp+8]` read is a read, a write to an argument slot makes it a local
  compare    runner.compare: an argument the callee's exe code cannot read is not compared (0x00a89af0, 0x0065e750)
  noreturn   a native shim named `fnvnx_xlength_error` whose original address is std::_Xlen (0x00ec30b0) ends the run
             like the oracle's call does
  destroy    runner._destroys_params: trailing oracle-only calls whose `this` is a by-value parameter's slot
  thunk      engine._indirect_thunk: a virtual-call thunk is no `ret N` function (0x00c91270)
  cpuid      classify.cpuid_dependent: stored bytes that change with the oracle's cpuid answers are cpuid-env (by design);
             a store that does not is not

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13args'))

import a64image   # noqa: E402
import capstone   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import engine     # noqa: E402
import retsrc     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []
BASE = 0x00401000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class FakePE:
    def __init__(self, code):
        self.code = code

    def section_of(self, va):
        return (BASE, len(self.code), 0x20000000, self.code) if BASE <= va < BASE + len(self.code) else None


def exe(blobs):
    """An exe image with each blob at its address (padding int3)."""
    img = bytearray(b'\xcc' * 0x2000)
    for a, b in blobs.items():
        img[a - BASE:a - BASE + len(b)] = b
    retsrc._PE[:] = [FakePE(bytes(img))]
    retsrc._CACHE.clear()
    getattr(retsrc, '_READS', {}).clear()


def rel(at, target):
    return struct.pack('<i', target - (at + 5))


def test_regcopy():
    h = bytes.fromhex
    leaf = 0x00401100
    # push esi; mov esi, ecx; call leaf; mov eax, esi; pop esi; ret
    ctor = h('56' '8bf1' 'e8') + rel(BASE + 3, leaf) + h('8bc6' '5e' 'c3')
    exe({BASE: ctor, leaf: h('c3')})
    check(retsrc.ret_source(BASE) == 'this', 'a constructor keeps this in esi over a call: this (%s)' % (retsrc.ret_source(BASE),))
    # mov edx, [esp+4]; push eax; mov ecx, edx; call leaf2; mov eax, edx; ret 4   (leaf2 writes eax and ecx only)
    f = h('8b542404' '50' '8bca' 'e8') + rel(BASE + 8, leaf) + h('8bc2' 'c20400')
    leaf2 = h('8bc1' '8b4c2404' '8908' 'c20400')
    exe({BASE: f, leaf: leaf2})
    check(retsrc.ret_source(BASE) == ('arg', 0), 'the stack argument kept in edx over a leaf not writing edx (%s)' % (retsrc.ret_source(BASE),))
    exe({BASE: f, leaf: h('33d2' 'c20400')})      # leaf writes edx (xor edx, edx)
    check(retsrc.ret_source(BASE) is None, 'a leaf that writes edx: the argument is lost (%s)' % (retsrc.ret_source(BASE),))
    exe({BASE: f, leaf: h('e8') + rel(leaf, leaf) + h('c20400')})      # not a leaf (calls)
    check(retsrc.ret_source(BASE) is None, 'a callee that is no straight-line leaf: edx is lost')
    # a fastcall free function `void *F(void *p)`: p arrives in ecx
    cv = conv.Conv('?F@@YIPAXPAX@Z', 'test')
    exe({BASE: h('8bc1' 'c3')})
    check(retsrc.returned_input(cv, BASE) == 'this', 'a fastcall first parameter in ecx handed back: this (%s)'
          % (retsrc.returned_input(cv, BASE),))


def test_unread():
    h = bytes.fromhex
    cvm = conv.Conv('?m@Foo@@QAEXH@Z', 'test')          # void Foo::m(int): this ecx, p0 stack
    exe({BASE: h('c20400')})
    check(retsrc.unread_labels(cvm, BASE) == {'this', 'p0'}, 'a bare ret 4 reads nothing (%s)' % retsrc.unread_labels(cvm, BASE))
    exe({BASE: h('8b01' 'c20400')})                    # mov eax, [ecx]; ret 4
    check(retsrc.unread_labels(cvm, BASE) == {'p0'}, 'mov eax,[ecx]: this is read, p0 not (%s)' % retsrc.unread_labels(cvm, BASE))
    exe({BASE: h('55' '8bec' '8b4508' '5d' 'c20400')})  # push ebp; mov ebp,esp; mov eax,[ebp+8]; pop ebp; ret 4
    check(retsrc.unread_labels(cvm, BASE) == {'this'}, 'mov eax,[ebp+8]: p0 is read, this not (%s)' % retsrc.unread_labels(cvm, BASE))
    exe({BASE: h('55' '8bec' 'c7450800000000' '8b4508' '5d' 'c20400')})   # mov dword [ebp+8], 0; mov eax,[ebp+8]
    check(retsrc.unread_labels(cvm, BASE) == {'this', 'p0'}, 'a slot written before it is read is a local (%s)'
          % retsrc.unread_labels(cvm, BASE))
    exe({BASE: h('55' '8bec' '8d4508' '50' 'e8') + rel(BASE + 8, BASE + 0x100) + h('5d' 'c20400')})   # lea eax,[ebp+8]; push eax
    check('p0' not in retsrc.unread_labels(cvm, BASE), 'the address of an argument slot taken: p0 may be read (%s)'
          % retsrc.unread_labels(cvm, BASE))
    exe({BASE: h('ff25') + struct.pack('<I', 0x00fdf1d0)})   # jmp [mem]: a tail jump reads whatever
    check('p0' not in retsrc.unread_labels(cvm, BASE), 'an indirect tail jump: p0 may be read')
    # the callee reads ecx through a call: mov ecx..: `call leaf` with ecx live reaches the leaf, which reads ecx
    exe({BASE: h('e8') + rel(BASE, BASE + 0x100) + h('c20400'), BASE + 0x100: h('8b01' 'c3')})
    check('this' not in retsrc.unread_labels(cvm, BASE), 'ecx handed to a callee that reads it: this is read')
    exe({BASE: h('e8') + rel(BASE, BASE + 0x100) + h('c20400'), BASE + 0x100: h('c3')})
    check('this' in retsrc.unread_labels(cvm, BASE), 'ecx handed to a callee that ignores it: this is unread')
    retsrc._PE[:] = [None]
    getattr(retsrc, '_READS', {}).clear()
    check(retsrc.unread_labels(cvm, BASE) == set(), 'without the exe: nothing is unread')


class Info:
    """callee_info of a Target: definition conv by address."""

    def __init__(self, cvs):
        self.cvs = cvs

    def __call__(self, addr):
        cv = self.cvs.get(addr)
        return (cv.pop, 'int') if cv else None

    def conv(self, addr):
        return self.cvs.get(addr)

    def caller_conv(self, addr):
        return self.cvs.get(addr)


def test_compare():
    callee = BASE + 0x1000
    cvg = conv.Conv('?g@@YAXH@Z', 'test')                          # void __cdecl g(int)
    cvf = conv.Conv('?f@@YAXXZ', 'test')
    oracle = bytes.fromhex('6a05') + b'\xe8' + rel(BASE + 2, callee) + bytes.fromhex('83c404' 'c3')
    arm = '\tstp x29, x30, [sp, #-16]!\n\tmov w0, #7\n\tbl __fnv_%08x\n\tldp x29, x30, [sp], #16\n\tret\n' % callee
    for code, want in ((b'\xc3', 'unread'), (bytes.fromhex('55' '8bec' '8b4508' '5d' 'c3'), 'read')):
        exe({callee: code})
        with tempfile.TemporaryDirectory() as d:
            img = build_image(arm, d, [('__fnv_%08x' % callee, 0x08000000 + callee)])
            p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle}, img, {}, Info({callee: cvg})), {})
            rx, ra, div = p.run(world.Case(seed=3, args={}, mem={}))
        if want == 'unread':
            check(div is None, 'an argument the callee never reads is not compared (%s)' % (div and div['kind']))
        else:
            check(div is not None and div['kind'] == 'call-args' and div.get('arg') == 'p0',
                  'an argument the callee reads is compared (%s)' % ((div and (div['kind'], div.get('arg'))),))


def test_noreturn():
    xlen = 0x00ec30b0
    cvf = conv.Conv('?f@@YAHXZ', 'test')
    oracle = b'\xe8' + rel(BASE, xlen) + bytes.fromhex('b801000000' 'c3')
    arm = '\tstp x29, x30, [sp, #-16]!\n\tbl fnvnx_xlength_error\n\tmov w0, #1\n\tldp x29, x30, [sp], #16\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        a = a64image.NAMED_STUB
        img = build_image(arm, d, [('fnvnx_xlength_error', a)])
        img.stubs = {a: 'fnvnx_xlength_error'}              # a named stub standing for the exe's std::_Xlen
        p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle}, img, {a: xlen}), {})
        rx, ra, div = p.run(world.Case(seed=3, args={}, mem={}))
    check(rx.noreturn and ra.noreturn, 'both machines end at std::_Xlen (%s / %s)' % (rx.noreturn, ra.noreturn))
    check(div is None, 'and agree (%s)' % (div and div['kind']))


def test_destroy():
    class C:
        def __init__(self, target, a0):
            self.target, self.args = target, [a0]
    agg = [(0x06effff0, 0x03001000, 28)]
    check(runner._destroys_params([C(0x45cec0, 0x06effff0)], agg), 'a trailing call on the parameter slot')
    check(runner._destroys_params([C(0x45cec0, 0x06effffa)], agg), 'inside the slot')
    check(not runner._destroys_params([C(0x45cec0, 0x06effff0 + 28)], agg), 'one past the slot: no')
    check(not runner._destroys_params([C(0x45cec0, 0x06effff0), C(0x45cec0, 0x03000000)], agg), 'one call elsewhere: no')
    check(not runner._destroys_params([], agg) and not runner._destroys_params([C(1, 0x06effff0)], None), 'nothing to destroy: no')


def test_thunk():
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    h = bytes.fromhex
    thunk = h('8b495c' '8b01' '8b400c' 'ffe0')                     # mov ecx,[ecx+5c]; mov eax,[ecx]; mov eax,[eax+c]; jmp eax
    check(engine._indirect_thunk(cs, thunk, BASE), 'mov / mov / mov / jmp eax is a thunk')
    check(engine._indirect_thunk(cs, h('8b495c' 'ff6008'), BASE), 'jmp [eax+8] is a thunk')
    check(not engine._indirect_thunk(cs, h('ff25') + struct.pack('<I', 0x00fdf1d0), BASE), 'an import thunk is not (own branch)')
    check(not engine._indirect_thunk(cs, h('8b01' 'c20400'), BASE), 'a function with a ret is not')
    check(not engine._indirect_thunk(cs, h('50' 'ffe0'), BASE), 'a push before the jump is not')
    check(not engine._indirect_thunk(cs, h('e9') + struct.pack('<i', 0x20), BASE), 'a direct jmp is not')


def test_cpuid():
    cvf = conv.Conv('?f@@YAXPAH@Z', 'test')
    # push ebx; push esi; mov esi,[esp+0xc]; mov eax,0x80000000; cpuid; mov [esi],eax; mov dword [esi+8], 6; pop esi;
    # pop ebx; ret   (cpuid at +11)
    oracle = bytes.fromhex('53' '56' '8b74240c' 'b800000080' '0fa2' '8906' 'c7460806000000' '5e' '5b' 'c3')
    # the native stores the cpuid word the host model answers, but 5 where the oracle stores the constant 6: the 6/5
    # store does not depend on cpuid, so the divergence is not cpuid-env
    arm = '\tmovz w1, #0x8000, lsl #16\n\tmovk w1, #8\n\tstr w1, [x0]\n\tmov w1, #5\n\tstr w1, [x0, #8]\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        img = build_image(arm, d)
        p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle}, img, {}), {})
        case = world.Case(seed=3, args={}, mem={world.BUF: bytes(16)})
        rx, ra, div = p.run(case)
        check(div is not None and div['kind'] == 'mem-write', 'the stores differ (%s)' % (div and div['kind']))
        got = classify.cpuid_dependent(p, case, div, 100000)
        check(not got, 'a differing store that does not depend on cpuid: not cpuid-env (%s)' % (got,))
    # now only the stored cpuid word differs (the arm writes another value in the first word)
    oracle2 = bytes.fromhex('53' '56' '8b74240c' 'b800000080' '0fa2' '8906' '5e' '5b' 'c3')
    arm2 = '\tmov w1, #5\n\tstr w1, [x0]\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        img = build_image(arm2, d)
        p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle2}, img, {}), {})
        case = world.Case(seed=3, args={}, mem={world.BUF: bytes(16)})
        rx, ra, div = p.run(case)
        got = classify.cpuid_dependent(p, case, div, 100000)
        check(div is not None and got == [BASE + 11], 'a store of the cpuid result: cpuid-env, the instruction shown (%s)' % (got,))
        fn = dict(code=oracle2, address=BASE)
        h = classify.hint(p, p.target, case, div, fn, 100000)
        check(h.get('cause') == 'cpuid-env', 'classify.hint: cpuid-env (%s)' % (h.get('cause'),))
        check(causes.policy('cpuid-env')[0] == 'by-design' and not causes.policy('cpuid-env')[2], 'cpuid-env is by design, not hard')
    # an oracle without cpuid
    oracle3 = bytes.fromhex('8b542404' 'c70205000000' 'c3')
    arm3 = '\tmov w1, #7\n\tstr w1, [x0]\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        img = build_image(arm3, d)
        p = runner.Pair(runner.Target(cvf, BASE, {BASE: oracle3}, img, {}), {})
        case = world.Case(seed=3, args={}, mem={world.BUF: bytes(16)})
        rx, ra, div = p.run(case)
        check(div is not None and classify.cpuid_dependent(p, case, div, 100000) == [], 'no cpuid in the oracle: not cpuid-env')


def main():
    for t in (test_regcopy, test_unread, test_compare, test_noreturn, test_destroy, test_thunk, test_cpuid):
        try:
            t()
        except Exception as e:  # noqa: BLE001 - a missing mechanism fails the test (the head before the fix)
            check(False, '%s raised %r' % (t.__name__, e))
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
