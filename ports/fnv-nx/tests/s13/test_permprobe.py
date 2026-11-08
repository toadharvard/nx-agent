#!/usr/bin/env python3
"""tests/s13/test_permprobe.py - EQ farm-baseline round (difffuzz-20): two getters whose results are the arguments of one
call, evaluated in the other order by the native (0x0084e700: `f(a->GetFormID(), a->GetFormType(mask), mask)`),
hand-written code and hand-built results (no decomp, no exe bytes).

compare() pairs the swapped calls by callee (_perm_align) and reorders the native's call list in place, then the
canned results (numbered by call index) still feed the consumer the other getter's value: a hard call-args
divergence marked after_perm. Pair._order_probe is the run that renumbers them, and it used to look at the already
reordered list (equal traces: no region, no probe). It must start from the order the native made the calls in.

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


mem = {}
G, V, CL = 0x401f00, 0x401f10, 0x401f80        # two getters and the consumer (never run)
at, rets = 0x401100, []


def emit(hexs):
    global at
    b = bytes.fromhex(hexs.replace(' ', ''))
    for i, x in enumerate(b):
        mem[at + i] = x
    at += len(b)


def call(to):
    emit('e8' + struct.pack('<i', to - (at + 5)).hex())
    rets.append(at)


# push eax; call V ... : the exe evaluates G() then V(), pushes both results, calls the consumer (cdecl)
call(G)
emit('50')
call(V)
emit('50')
call(CL)
emit('83c408 c3')
exeempty._PE[:] = [FakePE(mem)]
getattr(exeempty, '_EXPR', {}).clear()

cv = conv.Conv('?f@@YAXXZ', 'test')
ccv = conv.Conv('?c@@YAXHH@Z', 'test')                 # the consumer: void __cdecl c(int, int)
base = world.Case(seed=11)
word = lambda c, k: struct.unpack('<I', c.ret_bytes(k)[:4])[0]      # noqa: E731

rx = x86emu.Result()
# the oracle: G (canned 0), V (canned 1), the consumer called with the pair (V's result, G's result)
rx.calls = [x86emu.Call(G, [0, 0, 0], rets[0]), x86emu.Call(V, [0, 0, 0], rets[1]),
            x86emu.Call(CL, [0, 0, word(base, 1), word(base, 0)], None)]


def ares(ta, a0):
    r = arm64emu.Result()
    r.calls = [arm64emu.Call(t, [a0[i], 0] + [0] * 6, [0] * 8, 0) for i, t in enumerate(ta)]
    return r


class FakeArm:
    """The native: V first (canned 0), then G (canned 1); the consumer gets (V's result, G's result)."""
    def __init__(self):
        self.case = None

    def reset(self, w):
        pass

    def run(self, cvv, case, placement, orig, max_steps=0):
        self.case = case
        r = ares([V, G, CL], [0, 0, 0])
        r.calls[2].args[0], r.calls[2].args[1] = word(case, 0), word(case, 1)
        return r


pair = runner.Pair.__new__(runner.Pair)
pair.target = types.SimpleNamespace(x86_code={}, conv=cv, orig_addr=0x401100, vt_ap={})
pair.pinned = {}
pair.ae = FakeArm()
pair._vc, pair._vc_key = ({G, V, CL}, set()), 0
w = world.World(base, {}, {})


def cmp_(ra_, w_):
    return runner.compare(cv, rx, ra_, w_, callee_conv=lambda t: ccv if t == CL else None, vcalls=pair._vc)


ra = pair.ae.run(cv, base, {}, 0)
d0 = cmp_(ra, w)
check(d0 is not None and not d0.get('soft') and d0.get('after_perm'),
      'plain comparison: a hard divergence after pairing the swapped getters by callee (%s)' % ((d0 and (d0.get('kind'), d0.get('after_perm'), d0.get('soft'))),))
got = pair._order_probe(base, rx, ra, w, {}, 1000, cmp_)
check(got is not None, 'probe: the exe shows one argument list: the native is run with its results renumbered')
if got:
    ra2, d, w2 = got
    check(d is None or (d.get('soft') and d.get('expr_order')), 'probe: ... and nothing hard is left (%s)' % (d,))
    check(isinstance(pair.ae.case, runner._RenumCase), 'probe: ... the second run is numbered by the oracle\'s calls')

if FAIL:
    print('FAILED: %d' % len(FAIL))
    sys.exit(1)
print('ok')
