#!/usr/bin/env python3
"""tests/s13/test_flow2.py - the call-flow family on a fresh sample (flow2, difffuzz-20): the oracle's stack after an
intercepted call whose pop is not declared, on hand-written code (no decomp content).

  ilt      engine.UnitCache.oracle_ret_pop: an incremental-link thunk `jmp rel32` pops what the function it jumps
           to pops (the decomp defines the thunk as a `void F(void)` placeholder whose declaration said 0; 0x00aa0ad0
           -> 0x00a81fe0 -> a `ret 4` function); a chain that ends nowhere known is unknown, so the pair learns it
  prologue _pushes_before does not count the entry prologue's `push ebx/esi` as the arguments of a call
  delayed  _delayed_cleanup: MSVC's merged `add esp, N` after several cdecl calls marks the earlier sites as cdecl
  tail     a clean run whose last call is a tail `jmp` to a known function returns into the epilogue stub: that is
           not a derail (it was, and _learn_pop then discarded the true pops of the earlier planted calls)

Needs unicorn, capstone.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13flow2'))

import conv       # noqa: E402
import engine     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def jmp(src, dst):
    return b'\xe9' + struct.pack('<i', dst - (src + 5))


def test_ilt():
    t1, t2, real, odd = 0x00aa0ad0, 0x00a81fe0, 0x00a5d2f0, 0x00a5d300

    class U:
        funcs = {t1: dict(code=jmp(t1, t2), relocs=[(1, 0x14, t2, '_t2')]),
                 t2: dict(code=jmp(t2, real), relocs=[(1, 0x14, real, '_real')]),
                 real: dict(code=bytes.fromhex('8b442404c20400'), relocs=[]),      # mov eax,[esp+4]; ret 4
                 0x00aa0b00: dict(code=jmp(0x00aa0b00, odd), relocs=[(1, 0x14, odd, '_odd')]),
                 odd: dict(code=bytes.fromhex('c20400' 'c3'), relocs=[])}           # ret 4 and ret: disagree
    uc = engine.UnitCache.__new__(engine.UnitCache)
    uc.def_at = {a: ('s', '?F@@YAXXZ') for a in U.funcs}
    uc.x86_unit = lambda src, a: U()
    check(uc.oracle_ret_pop(t1) == 4, 'a thunk chain pops what the real function pops: %s' % uc.oracle_ret_pop(t1))
    check(uc.oracle_ret_pop(0x00aa0b00) is None and 0x00aa0b00 in uc._thunk_unknown,
          'a chain ending at a function whose returns disagree: unknown, learned from the run')


def pair_for(code, base=0x00401000):
    return runner.Pair(runner.Target(None, base, {base: code}, None, {}), {})


def test_prologue():
    # push ebx; push esi; mov esi, ecx; push 0; call [esi]  (one argument; the two prologue pushes are not)
    code = bytes.fromhex('53' '56' '89ce' '6a00' 'ff16' 'c3')
    p = pair_for(code)
    n = p._pushes_before(0x00401000 + 8)
    check(n == 1, 'the entry prologue pushes are not arguments: %d' % n)
    check(p._pushes_before(0x00401000 + 8, strict=True) == 1, 'strict count the same')


def test_delayed():
    # push 1; call [esi]; push 2; call [esi+4]; add esp, 0xc; ret : 3 words freed, 1 pushed since the first call
    code = bytes.fromhex('6a01' 'ff16' '6a02' 'ff5604' '83c40c' 'c3')
    p = pair_for(code)
    check(p._delayed_cleanup(0x00401000 + 4), 'the merged add esp after two cdecl calls marks the first')
    # push 1; call [esi]; pop edi; ret : the epilogue ends the scan
    code = bytes.fromhex('6a01' 'ff16' '5f' 'c3')
    check(not pair_for(code)._delayed_cleanup(0x00401000 + 4), 'a `pop` ends the scan: no cleanup to find')
    # push 1; call [esi]; push 2; call [esi+4]; add esp, 4 : frees only the second call's word
    code = bytes.fromhex('6a01' 'ff16' '6a02' 'ff5604' '83c404' 'c3')
    check(not pair_for(code)._delayed_cleanup(0x00401000 + 4), 'a cleanup of just the later pushes is not evidence')


def test_tail():
    import types
    import x86emu
    base, g = 0x00401000, 0x00402000
    p = pair_for(b'\xc3', base)
    p.xe = types.SimpleNamespace(_in_loaded=lambda a: base <= a < base + 0x100)

    def call(target, ret_to, unknown):
        return types.SimpleNamespace(target=target, ret_to=ret_to, pop_unknown=unknown)
    wild = call(0x41414140, base + 8, True)                   # a planted call of unknown pop, returning into the code
    tail = call(g, x86emu.STUBS + 0x20, False)                # g, jumped to: returns into the epilogue stub
    rx = types.SimpleNamespace(calls=[wild, tail], esp_ok=True)
    check(p._returns_into_code(tail), 'a known callee returning into the epilogue stub is a tail call')
    check(not p._derailed(rx), 'a clean run ending in a tail call is not derailed')
    stray = call(0x2dca94da, x86emu.STUBS + 0x20, True)       # a derail: the wild call that the garbage return makes
    check(p._derailed(types.SimpleNamespace(calls=[wild, stray], esp_ok=True)),
          'the wild call a derailed return makes (pop unknown) still reads as a derail')


if __name__ == '__main__':
    test_ilt()
    test_prologue()
    test_delayed()
    test_tail()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
