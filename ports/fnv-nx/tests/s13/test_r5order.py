#!/usr/bin/env python3
"""tests/s13/test_r5order.py - EQ round 5, call order (difffuzz-20): the oracle and the native make the same calls in
a different order, hand-written code and hand-built results (no decomp, no exe bytes).

  expr-deps    exeempty.expr_deps: the calls are the operands / arguments of ONE expression, read from the code: every
               result reaches the end of the expression (pushed, moved to a register, spilled to a compiler
               temporary, loaded as `this`, combined by `cmp`), nothing is stored to memory but dead `[ebp-N]`
               temporaries, no branch in between; the answer is the data dependence between the calls. Statements
               (a result dropped, kept in a variable that is read again, stored through a pointer) are not.
  pairs        runner._order_pairs: the oracle's calls paired with the native's, dependences kept, a wild target
               paired with a wild one by position
  probe        Pair._order_probe: the native run again with its canned results numbered by the oracle's counterpart
               call, accepted only if that run makes the oracle's calls (a wild call on the same `this`)
  fault        both runs fault inside one expression: compared up to it (which operand faults first is open)

Needs capstone, unicorn.
"""
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import arm64emu   # noqa: E402
import conv       # noqa: E402
import exeempty   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


BASE = 0x00401000
SIZE = 0x40000


class FakePE:
    def __init__(self, mem):
        self.mem = mem

    def section_of(self, va):
        if BASE <= va < BASE + SIZE:
            return (BASE, SIZE, 0x60000020, bytes(self.mem.get(BASE + i, 0) for i in range(SIZE)))
        return None

    def read(self, a, n):
        return bytes(self.mem.get(a + i, 0) for i in range(n))


class Asm:
    """A tiny hand assembler: bytes appended at an address, call sites remembered by their return addresses."""

    def __init__(self, mem, at):
        self.mem, self.at = mem, at
        self.rets = []

    def raw(self, hexs):
        for i, x in enumerate(bytes.fromhex(hexs.replace(' ', ''))):
            self.mem[self.at + i] = x
        self.at += len(bytes.fromhex(hexs.replace(' ', '')))
        return self

    def call(self, to):
        self.raw('e8' + struct.pack('<i', to - (self.at + 5)).hex())
        self.rets.append(self.at)
        return self


mem = {}
G, V, H, CL = 0x401f00, 0x401f10, 0x401f20, 0x401f80        # callees (never run)

# 1. two getters first, then two virtual calls compared (MSVC /Od: `A()->f() == B()->g()`)
a1 = Asm(mem, 0x401100)
a1.call(G).raw('8945d4')                                    # G1: mov [ebp-0x2c], eax (before the region)
a1.call(G).raw('8945d0')                                    # G2: mov [ebp-0x30], eax
a1.raw('8b45d0 8b10 8b4dd0 8b82c8000000 ffd0')              # mov eax,[slot]; mov edx,[eax]; mov ecx,[slot]; call [edx+0xc8]
a1.rets.append(a1.at)
a1.raw('8bf0')                                              # mov esi, eax
a1.raw('8b4dd4 8b11 8b82cc000000 ffd0')                     # the other receiver's virtual call
a1.rets.append(a1.at)
a1.raw('3bc6 7500 c3')                                      # cmp eax, esi; jne; ret
EXPR1 = a1.rets[1:]                                         # G2, vcall 1, vcall 2 (G1 is outside the region)
EXPR1_ALL = a1.rets

# 2. pushed results (an argument list with a float, `push ecx; fstp [esp]`), a chain through ecx, the consumer last
a2 = Asm(mem, 0x401200)
a2.call(G).raw('51 d91c24')                                 # arg 1: push ecx; fstp dword [esp]
a2.raw('8b4dec')                                            # mov ecx,[ebp-0x14]
a2.call(G).raw('8bc8')                                      # mov ecx, eax
a2.call(V).raw('50')                                        # push eax   (arg 2 = G()->V())
a2.call(G).raw('50')                                        # push eax   (arg 3)
a2.call(CL).raw('83c40c c3')                                # the consumer: add esp, 0xc; ret
ARGS = a2.rets[:4]

# 3. statements: the results are dropped
a3 = Asm(mem, 0x401300)
a3.call(G).call(G).call(CL).raw('c3')
DROP = a3.rets[:2]

# 4. a statement: the result is kept in a variable that is read again afterwards
a4 = Asm(mem, 0x401400)
a4.call(G).raw('8945fc')                                    # mov [ebp-4], eax
a4.raw('8b4dfc')                                            # mov ecx,[ebp-4]
a4.call(V).raw('50')                                        # push eax
a4.call(CL).raw('83c404')
a4.raw('8b55fc c3')                                         # mov edx,[ebp-4] after the expression: a variable
VAR = a4.rets[:2]
# the same without the later read: a compiler temporary
a4b = Asm(mem, 0x401500)
a4b.call(G).raw('8945fc 8b4dfc')
a4b.call(V).raw('50')
a4b.call(CL).raw('83c404 c3')
TEMP = a4b.rets[:2]

# 5. a result stored through a pointer
a5 = Asm(mem, 0x401600)
a5.call(G).raw('8903')                                      # mov [ebx], eax
a5.call(V).raw('50')
a5.call(CL).raw('83c404 c3')
PTR = a5.rets[:2]

# 6. a branch before the results are consumed (`a && b`)
a6 = Asm(mem, 0x401700)
a6.call(G).raw('85c0 7403')                                 # test eax,eax; je +3
a6.raw('90 90 90')
a6.call(V).raw('50')
a6.call(CL).raw('83c404 c3')
BR = a6.rets[:2]

exeempty._PE[:] = [FakePE(mem)]
getattr(exeempty, "_EXPR", {}).clear()

# ---------------------------------------------------------------------------------------------------- expr-deps
try:
    d = exeempty.expr_deps(EXPR1)
    check(d == {0: frozenset(), 1: frozenset({0}), 2: frozenset()},
          'expr-deps: getter, its virtual call, the other virtual call, joined by cmp: %s' % (d,))
    d = exeempty.expr_deps(EXPR1_ALL)
    check(d == {0: frozenset(), 1: frozenset(), 2: frozenset({1}), 3: frozenset({0})},
          'expr-deps: with the first getter: its virtual call depends on it: %s' % (d,))
    d = exeempty.expr_deps(ARGS[:3])
    check(d == {0: frozenset(), 1: frozenset(), 2: frozenset({1})},
          'expr-deps: pushed float, a chain through ecx, a pushed eax: %s' % (d,))
    d = exeempty.expr_deps(ARGS[:1] + ARGS[1:3] + ARGS[3:4])
    check(d is not None and d[3] == frozenset(), 'expr-deps: the last argument is independent: %s' % (d,))
    check(exeempty.expr_deps(DROP) is None, 'expr-deps: results dropped (statements): not an expression')
    check(exeempty.expr_deps(VAR) is None, 'expr-deps: a result kept in a variable read again: not an expression')
    check(exeempty.expr_deps(TEMP) == {0: frozenset(), 1: frozenset({0})},
          'expr-deps: the same slot never read again is a compiler temporary')
    check(exeempty.expr_deps(PTR) is None, 'expr-deps: a result stored through a pointer: not an expression')
    check(exeempty.expr_deps(BR) is None, 'expr-deps: a branch before the results are used: not an expression')
    check(exeempty.expr_deps([EXPR1[0], 0x409999]) is None, 'expr-deps: return addresses the code does not show')
    check(exeempty.expr_deps([EXPR1[1], EXPR1[0]]) is None, 'expr-deps: the calls in another order than the code')
except AttributeError as e:
    check(False, 'expr-deps: %s' % e)

# ---------------------------------------------------------------------------------------------------- pairs
if hasattr(runner, '_order_pairs'):
    WL, WN = 0x7000001, 0x7000002                           # two wild words (not called directly, not planted)
    vc = ({G, V, H, CL}, set())
    wild = runner._is_wild(vc)
    tx = [G, G, WL, WN]                                     # oracle: both getters, then the two virtual calls
    ta = [G, WN, G, WL]                                     # native: getter, call, getter, call
    deps = {0: frozenset(), 1: frozenset({0}), 2: frozenset()}      # (region starts at the second getter)
    sols = runner._order_pairs(tx, ta, 1, 4, deps, wild)
    check(sols == [{1: 2, 2: 3, 3: 1}], 'pairs: the wild call of the getter pairs after it: %s' % sols)
    sols = runner._order_pairs(tx, ta, 1, 4, {0: frozenset(), 1: frozenset(), 2: frozenset()}, wild)
    check(len(sols) == 2, 'pairs: without the dependence a wild call can pair with either: %d' % len(sols))
    check(runner._order_pairs([G, V], [V, G], 0, 2, {0: frozenset(), 1: frozenset({0})}, wild) == [],
          'pairs: a call that depends on another is never paired before it')
    check(runner._order_pairs([G, V], [V, V], 0, 2, {0: frozenset(), 1: frozenset()}, wild) == [],
          'pairs: different callees do not pair')
else:
    check(False, 'pairs: runner._order_pairs exists')

# ---------------------------------------------------------------------------------------------------- probe
cv = conv.Conv('?f@@YAXXZ', 'test')


def xres(tx, args0, rets):
    r = x86emu.Result()
    r.calls = [x86emu.Call(t, [args0[i], 0, 0], rets[i]) for i, t in enumerate(tx)]
    return r


def ares(ta, args0):
    r = arm64emu.Result()
    r.calls = [arm64emu.Call(t, [args0[i]] + [0] * 7, [0] * 8, 0) for i, t in enumerate(ta)]
    return r


if hasattr(runner, '_RenumCase') and hasattr(runner.Pair, '_order_probe'):
    base = world.Case(seed=7)
    word = lambda c, k: struct.unpack('<I', c.ret_bytes(k)[:4])[0]    # noqa: E731
    WL, WN = 0x7000001, 0x7000002
    # the oracle: G1 (call 0), G2 (1), vcall on G2's object (2), vcall on G1's object (3), the exe's addresses
    rets = [0, EXPR1[0], EXPR1[1], EXPR1[2]]
    tx = [G, G, WL, WN]
    rx = xres(tx, [0, 0, word(base, 1), word(base, 0)], rets)

    class FakeArm:
        """The native: getter, vcall, getter, vcall; each canned result by call index, a vcall's `this` is the
        result of its getter."""
        def __init__(self, tw=(WN, WL)):
            self.tw = tw
            self.case = None

        def reset(self, w):
            pass

        def run(self, cvv, case, placement, orig, max_steps=0):
            self.case = case
            return ares([G, self.tw[0], G, self.tw[1]], [0, word(case, 0), 0, word(case, 2)])

    pair = runner.Pair.__new__(runner.Pair)
    pair.target = types.SimpleNamespace(x86_code={}, conv=cv, orig_addr=0x401100, vt_ap={})
    pair.pinned = {}
    pair.ae = FakeArm()
    pair._vc, pair._vc_key = ({G, V, H, CL}, set()), 0
    getattr(exeempty, "_EXPR", {}).clear()
    ra = ares([G, WN, G, WL], [0, word(base, 0), 0, word(base, 2)])      # the first run: canned by index
    w = world.World(base, {}, {})
    vcs = pair._vc

    def cmp_(ra_, w_):
        return runner.compare(cv, rx, ra_, w_, vcalls=vcs)
    d0 = cmp_(ares([G, WN, G, WL], [0, word(base, 0), 0, word(base, 2)]), w)
    check(d0 is not None and d0['kind'] == 'call-target' and not d0.get('soft'),
          'probe: the plain comparison is a hard call-target divergence (%s)' % (d0 and d0.get('kind')))
    got = pair._order_probe(base, rx, ra, w, {}, 1000, cmp_)
    check(got is not None, 'probe: the exe shows one expression: the native is run with its results renumbered')
    if got:
        ra2, d, w2 = got
        check(d is not None and d.get('soft') and d.get('expr_order') and d.get('order_probe'),
              'probe: ... and the divergence is soft call-order (%s)' % (d,))
        check(isinstance(pair.ae.case, runner._RenumCase) and pair.ae.case.ret_bytes(2) == base.ret_bytes(1),
              'probe: ... the native\'s second getter got the oracle\'s second getter\'s result')
    # a native whose virtual call is on another object stays: the `this` words differ after the renumbering
    pair.ae = types.SimpleNamespace(reset=lambda w: None,
                                    run=lambda cvv, case, placement, orig, max_steps=0:
                                    ares([G, WN, G, WL], [0, word(case, 3), 0, word(case, 2)]))
    check(pair._order_probe(base, rx, ra, w, {}, 1000, cmp_) is None,
          'probe: a wild call on another object is no pair: stays')
    # reordered statements (the code does not show an expression): no probe
    rx2 = xres([G, G, WL, WN], [0, 0, word(base, 1), word(base, 0)], [0] + DROP + [DROP[0]])
    check(pair._order_probe(base, rx2, ra, w, {}, 1000, cmp_) is None, 'probe: statements are not probed')
else:
    check(False, 'probe: runner._RenumCase / Pair._order_probe exist')

# ---------------------------------------------------------------------------------------------------- fault
# the oracle faults on the argument's virtual call, the native on the receiver's: both inside one expression
if hasattr(runner, '_fault_cut'):
    vc = ({G, V, H, CL}, set())
    HH = EXPR1_ALL
    rx = xres([G, V, G, V], [0] * 4, [0, HH[1], HH[2], HH[3]])
    rx.error = 'fault:r:00000024'
    ra = ares([G, V, V], [0] * 3)
    ra.error = 'fault:r:00000058'
    rx = xres([G, G, G, V], [0] * 4, [0, EXPR1_ALL[0], EXPR1_ALL[1], EXPR1_ALL[2]])
    rx.error = 'fault:r:00000024'
    ra = ares([G, G, V], [0] * 3)
    ra.error = 'fault:r:00000058'
    check(runner._fault_cut(rx, ra, vc) == 2, 'fault: both fault inside the expression: cut at the first difference')
    d = runner.compare(cv, rx, ra, world.World(world.Case(seed=1), {}, {}), vcalls=vc)
    check(d is not None and d.get('soft') and d.get('fault_in_expr'), 'fault: compare: soft call-order (%s)' % (d,))
    rx = xres([G, G, G, V], [0] * 4, [0, DROP[0], DROP[1], 0x409999])
    rx.error = 'fault:r:00000024'
    ra = ares([G, G, V], [0] * 3)
    ra.error = 'fault:r:00000058'
    d = runner.compare(cv, rx, ra, world.World(world.Case(seed=1), {}, {}), vcalls=vc)
    check(d is not None and d['kind'] == 'call-target' and not d.get('soft'),
          'fault: calls the code does not show to be one expression stay a divergence (%s)' % (d and d['kind']))
    rx = xres([G, G, G, V], [0] * 4, [0, EXPR1_ALL[0], EXPR1_ALL[1], EXPR1_ALL[2]])
    rx.error = 'fault:r:00000024'
    ra = ares([G, G, H], [0] * 3)
    ra.error = 'fault:r:00000058'
    check(runner._fault_cut(rx, ra, vc) is None, 'fault: a native call the oracle did not make: stays')
    ra = ares([G, G, G, V], [0] * 4)
    d = runner.compare(cv, rx, ra, world.World(world.Case(seed=1), {}, {}), vcalls=vc)
    check(d is not None and d['kind'] == 'error', 'fault: only one side faulting is still an error (%s)' % (d and d['kind']))
else:
    check(False, 'fault: runner._fault_cut exists')

if FAIL:
    print('FAILED: %d' % len(FAIL))
    sys.exit(1)
print('ok')
