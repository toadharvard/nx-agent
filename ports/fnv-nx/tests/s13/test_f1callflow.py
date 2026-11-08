#!/usr/bin/env python3
"""tests/s13/test_f1callflow.py - F1 (call flow, D24): the oracle's stack after an intercepted call of unknown or
misdeclared convention, on hand-written code (no decomp content).

  derail   an intercepted stdcall call through a planted pointer, with arguments that are planted pointers: with
           0 bytes popped the oracle's `ret` goes to an argument word in the buffers, which the x86 engine
           intercepts as one more wild call (no fault). It used to stay so (extra oracle calls: native-early-exit);
           the pair now learns the callee's `ret 8` from that shape too
  branch   _pushes_before walks over a conditional branch between the pushes and the call
  imports  engine.import_identity: an IAT slot's name and stack bytes from all its spellings (the import library's
           `__imp__X@N` over the decomp's C++ declarations, a `__stdcall` C++ spelling's parameters)
  thunk    engine.UnitCache.oracle_ret_pop: an import thunk `jmp [__imp_X]` pops the import's bytes; a thunk through
           a slot named only as data is unknown (callee_info None), so the pair learns it

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13f1'))

import conv       # noqa: E402
import engine     # noqa: E402
import libcalls   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


# void __stdcall f(void **p): p[0](p, p) as a stdcall call through a pointer the case fills (pops 8), then p[1]()
ORACLE = bytes.fromhex('56'              # push esi
                       '8b742408'        # mov esi, [esp+8]
                       '56' '56'         # push esi; push esi   (planted buffer addresses: a wrong pop returns there)
                       'ff16'            # call [esi]
                       'ff5604'          # call [esi+4]
                       '5e'              # pop esi
                       'c20400')         # ret 4
NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n\tldr w8, [x19]\n'
          '\tmov w1, w19\n\tblr x8\n\tldr w8, [x19, #4]\n\tblr x8\n'
          '\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')


def case(seed=9):
    return world.Case(seed=seed, args={}, mem={world.BUF: struct.pack('<III', 0x41414140, 0x52525250, 0x63636360)})


def test_derail():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YGXPAPAX@Z', 'test')
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(NATIVE, d), {}), {})
        rx, ra, div = p._run(case(), 200000)
        check(not (rx.error or '').startswith('fault:') and p._derailed(rx),
              'without the learned pop the oracle runs on into a buffer word, no fault (%s, %s)' % (
                  rx.error, ['%08x' % (c.target & 0xffffffff) for c in rx.calls]))
        rx, ra, div = p.run(case())
        check(rx.esp_ok and p.pops.get(0x00401009) == 8,
              'learned: the callee returning to 00401009 pops 8 (%s)' % {hex(k): v for k, v in p.pops.items()})
        check(div is None and [c.target for c in rx.calls] == [0x41414140, 0x52525250],
              'then both machines agree (%s)' % (div and div['kind']))


def test_branch():
    # push 1; push 2; test eax, eax; jne +0; call [esi]: two pushes over the branch
    code = bytes.fromhex('6a01' '6a02' '85c0' '7500' 'ff16' 'c3')
    p = runner.Pair(runner.Target(None, 0x00401000, {0x00401000: code}, None, {}), {})
    check(p._pushes_before(0x0040100a) == 2, 'pushes before a call over a jcc: %d' % p._pushes_before(0x0040100a))


def test_imports():
    import collections

    def ident(*sp):
        counts = collections.Counter(libcalls.import_name(s) for s in sp)
        return engine.import_identity(counts, [(s, 'test') for s in sp])
    got = ident('__imp_?IMP_fdf0a8@fnv_004da670@@YGPAXPAX@Z', '__imp_?IMP_fdf0a8@fnv_004da670@@YGPAXPAX@Z',
                '__imp__FreeLibrary@4')
    check(got == ('FreeLibrary', 4), 'the import library spelling names the slot and gives its bytes: %s' % (got,))
    got = ident('__imp_?GetStockObject@@YGHH@Z')
    check(got == ('GetStockObject', 4), 'a __stdcall C++ spelling only: its parameters (%s)' % (got,))
    got = ident('__imp_?GetTickCount@@YGKXZ', '__imp_?GetTickCount@@YGKXZ', '__imp__GetTickCount@0')
    check(got == ('GetTickCount', 0), 'no parameters: 0 (%s)' % (got,))
    got = ident('__imp__ov_clear')
    check(got == ('ov_clear', 0), 'an undecorated C import (cdecl): 0 (%s)' % (got,))


def test_thunk():
    slot, data_slot = 0x00fdf0a8, 0x00fdf420

    class U:
        funcs = {0x00ee6dec: dict(code=b'\xff\x25' + struct.pack('<I', slot), relocs=[(2, 6, slot, '__imp_x')]),
                 0x00ee6df2: dict(code=b'\xff\x25' + struct.pack('<I', data_slot),
                                  relocs=[(2, 6, data_slot, '?g_x@@3P6AXXZA')])}
    uc = engine.UnitCache.__new__(engine.UnitCache)
    uc.def_at = {0x00ee6dec: ('s', '?FUN_00ee6dec@@YAXXZ'), 0x00ee6df2: ('s', '?FUN_00ee6df2@@YAXXZ')}
    uc.x86_unit = lambda src, a: U()
    uc._imports, uc._iat = {0: ('FreeLibrary', 4)}, {slot: libcalls.IMPORT_BASE}
    check(uc.oracle_ret_pop(0x00ee6dec) == 4, 'a thunk through an import slot pops the import\'s bytes')
    check(uc.oracle_ret_pop(0x00ee6df2) is None and 0x00ee6df2 in uc._thunk_unknown,
          'a thunk through a slot named only as data: unknown')


def test_keys():
    import fnkeys
    ch = [c for c in fnkeys.CHANGES if c.tag == 'd24-pop']
    check(len(ch) == 1 and ch[0].selects(fnkeys.Facts('?f@@YAXXZ', oindirect=True)) and
          not ch[0].selects(fnkeys.Facts('?f@@YAXXZ')), 'd24-pop selects functions whose oracle closure calls '
          'indirectly')
    check(ch and ch[0].recheck({'divergence': {'kind': 'call-count'}}) and
          not ch[0].recheck({'divergence': {'kind': 'return'}}), 'd24-pop rechecks cached call-flow records')


if __name__ == '__main__':
    test_derail()
    test_branch()
    test_imports()
    test_thunk()
    test_keys()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
