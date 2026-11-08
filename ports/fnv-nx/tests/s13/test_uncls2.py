#!/usr/bin/env python3
"""tests/s13/test_uncls2.py - the unclassified residue (EQ, `uncls2`, difffuzz-20), on hand-written code (no decomp
content). Each test fails on the head before the class and passes with it.

  uniform    a bool test of an unwritten stack byte is 1 for 255 of 256 fills, so two random fills agree and the
             garbage probe saw nothing; the all-zero fill (world.UNIFORM) moves the outcome: uninit-x86 (0x00c32840,
             0x00a82d90)
  corder     two calls of one callee whose results the machines use in swapped roles: equal results in either
             value make them agree: call-order (0x00456aa0); a probe that moves a pointer is no evidence (0x00480000)
  shift      _allshl with a count >= 64 gives 0, an AArch64 lsl takes the count modulo 64: shift-count (0x0062a3b0)
  trig       fsincos of an infinity: unicorn leaves the infinity, the x87 gives the indefinite NaN: x87-trig-inf
             (0x00524ac0)
  vptrbytes  a byte of a planted vptr word read as data: the exe vftable address on the oracle, the native address
             point natively: vptr-bytes (0x0043c470)
  ptrdiff    (end - begin) / 4 * 4 against end - begin, for pointers that are not 4 apart: ptrdiff-unaligned
             (0x00b24960)
  pool       the oracle stores the pool address of its object's unnamed vftable section, the native the exe address
             whose first word is the pool literal's: vptr-pool (0x006ad660)
  execonst   a data symbol the object holds in a writable section that the exe keeps read-only: its exe bytes are
             pinned (0x00a3c6d0, _INVALID_FLOAT)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13uncls2'))

import classify   # noqa: E402
import coffobj    # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402
from test_uncls import Info  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def run_pair(name, oracle, body, case, defsyms=(), info=None, pinned=None, address=0x00401000):
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv(name, 'test')
        t = runner.Target(cv, address, {address: oracle}, build_image(body, d, defsyms), {}, info)
        p = runner.Pair(t, pinned or {})
        rx, ra, div = p.run(case)
        fn = dict(code=oracle, address=address)
        return t, p, div, (classify.hint(p, t, case, div, fn, 200000) if div else {})


def test_uniform():
    # void f(char *p): char loc; *p = (loc != 0);   -- loc is an unwritten stack byte; the native writes false
    oracle = bytes.fromhex('55' '89e5' '83ec08' '807dfc00' '0f95c0' '8b4d08' '8801' '89ec' '5d' 'c3')
    nat = '\tstrb wzr, [x0]\n\tret\n'
    for bg in ('zero', 'rand'):
        t, p, div, h = run_pair('?f@@YAXPAD@Z', oracle, nat, world.Case(seed=7, bg=bg, mem={world.BUF: b'\x55' * 8}))
        check(div is not None and div['kind'] == 'mem-write',
              'bg %s: the oracle stores a test of a stack byte, the native false (%s)' % (bg, div and div['kind']))
        check(h.get('cause') == 'uninit-x86', 'bg %s: uninit-x86 (%s)' % (bg, h.get('cause')))
    # a byte the oracle wrote itself is no garbage: the divergence stays
    wrote = oracle.replace(bytes.fromhex('83ec08' '807dfc00'), bytes.fromhex('83ec08' 'c645fc01'))   # mov byte [ebp-4], 1
    wrote = wrote.replace(bytes.fromhex('0f95c0'), bytes.fromhex('807dfc00' '0f95c0'), 1)
    t, p, div, h = run_pair('?f@@YAXPAD@Z', wrote, nat, world.Case(seed=7, mem={world.BUF: b'\x55' * 8}))
    check(div is not None and h.get('cause') != 'uninit-x86',
          'a byte the oracle stored: not uninit-x86 (%s)' % (h.get('cause'),))


CALLEE = 0x00402000


def test_corder():
    # void f(int *p): int *a = g(); int *b = g(); *a = (*b == 0)   (MSVC), *b = (*a == 0) (GCC)
    #   oracle: call g; mov esi, eax; call g; cmp dword [eax], 0; sete cl; movzx ecx, cl; mov [esi], ecx
    code = b'\x56' + call_rel(0x00401001, CALLEE) + b'\x89\xc6'
    code += call_rel(0x00401000 + len(code), CALLEE) + bytes.fromhex('833800' '0f94c1' '0fb6c9' '890e' '5e' 'c3')
    nat = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tbl __fnv_00402000\n\tmov w19, w0\n\tbl __fnv_00402000\n'
           '\tldr w1, [x19]\n\tcmp w1, #0\n\tcset w1, eq\n\tstr w1, [x0]\n\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n'
           '\tret\n')
    r0, r1 = world.BUF + 0x100, world.BUF + 0x200
    case = world.Case(seed=3, mem={r0: struct.pack('<I', 5), r1: struct.pack('<I', 0)},
                      rets=[struct.pack('<I', r0), struct.pack('<I', r1)])
    t, p, div, h = run_pair('?f@@YAXPAH@Z', code, nat, case, defsyms=[stub(CALLEE)],
                            info=Info(conv.Conv('?g@@YAPAHXZ', 'test')))
    check(div is not None and div['kind'] == 'mem-write', 'the results are used in swapped roles (%s)' % (div,))
    # (r4 uncls) equal results agree, but these are two statements (no consuming call takes both results: the exe's
    # code does not show an argument list), so the order is not proven by-design: call-order-unproven, hard
    check(h.get('cause') == 'call-order-unproven',
          'equal results in either value agree, two statements: call-order-unproven (%s)' % (h.get('cause'),))


def test_shift():
    # unsigned long long f(int n): 1ULL << n, through _allshl; the native shifts by n modulo 64
    code = bytes.fromhex('8b4c2404' 'b801000000' '31d2') + call_rel(0x00401000 + 11, 0x00ec7810) + b'\xc3'
    nat = '\tmov x1, #1\n\tsxtw x2, w0\n\tlsl x0, x1, x2\n\tret\n'
    t, p, div, h = run_pair('?f@@YA_KH@Z', code, nat, world.Case(seed=1, args={'p0': struct.pack('<I', 78)}))
    check(div is not None and div['kind'] == 'return', 'count 78: 0 against 1 << 14 (%s)' % (div and div['kind']))
    check(h.get('cause') == 'shift-count', 'shift-count (%s)' % (h.get('cause'),))
    t, p, div, h = run_pair('?f@@YA_KH@Z', code, nat, world.Case(seed=1, args={'p0': struct.pack('<I', 5)}))
    check(div is None, 'count 5: the machines agree (%s)' % (div,))


def test_trig():
    # float f(float x): fsincos, return sin: the native returns the NaN sin(inf) gives
    code = bytes.fromhex('d9442404' 'd9fb' 'dd d8'.replace(' ', '') + 'c3')       # fld [esp+4]; fsincos; fstp st(0); ret
    nat = '\tmov w0, #0x7fc00000\n\tfmov s0, w0\n\tret\n'
    inf = struct.pack('<f', float('-inf'))
    t, p, div, h = run_pair('?f@@YAMM@Z', code, nat, world.Case(seed=1, args={'p0': inf}))
    check(div is not None and div['kind'] == 'return' and h.get('cause') == 'x87-trig-inf',
          'fsincos(-inf), float result: x87-trig-inf (%s, %s)' % (div and div['kind'], h.get('cause')))


def test_vptr():
    vft, ap = 0x010c1fc4, 0x0bc00008
    # int f(char *o): o[3]  -- the top byte of the object's vptr word
    code = bytes.fromhex('8b442404' '0fb64003' 'c3')
    nat = '\tldrb w0, [x0, #3]\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(conv.Conv('?f@@YAHPAD@Z', 'test'), 0x00401000, {0x00401000: code}, build_image(nat, d), {})
        t.vt_ap = {vft: ap}
        p = runner.Pair(t, {})
        c = world.Case(seed=1, mem={world.BUF: bytes(8)}, vptrs={world.BUF: vft})
        rx, ra, div = p.run(c)
        h = classify.hint(p, t, c, div, dict(code=code, address=0x00401000), 200000) if div else {}
        check(div is not None and div['kind'] == 'return', 'o[3]: 0x01 against 0x0b (%s)' % (div,))
        check(h.get('cause') == 'vptr-bytes', 'vptr-bytes (%s)' % (h.get('cause'),))


def test_ptrdiff():
    # int f(int b, int e, int out): out + (e - b)   -- MSVC: sar 2 then lea; GCC: end - begin as bytes
    code = bytes.fromhex('8b442408' '2b442404' 'c1f802' '8b4c240c' '8d0481' 'c3')
    nat = '\tsub w1, w1, w0\n\tadd w0, w2, w1\n\tret\n'
    odd = world.Case(seed=1, args={'p0': struct.pack('<I', 0x1000100), 'p1': struct.pack('<I', 0x100010d), 'p2': struct.pack('<I', 0x2000000)})
    t, p, div, h = run_pair('?f@@YAHHHH@Z', code, nat, odd)
    check(div is not None and div['kind'] == 'return', 'a difference of 13 bytes: the result differs by 1 (%s)' % (div,))
    check(h.get('cause') == 'ptrdiff-unaligned', 'ptrdiff-unaligned (%s)' % (h.get('cause'),))
    ok = world.Case(seed=1, args={'p0': struct.pack('<I', 0x1000100), 'p1': struct.pack('<I', 0x100010c), 'p2': struct.pack('<I', 0x2000000)})
    t, p, div, h = run_pair('?f@@YAHHHH@Z', code, nat, ok)
    check(div is None, 'a difference of 12 bytes: the machines agree (%s)' % (div,))


def test_pool():
    class Exe:
        def read(self, va, n):
            return {0x0106ba58: struct.pack('<I', 0x006b3970)}[va].ljust(n, b'\0')[:n]
    saved = coffobj.EXE, coffobj._EXE_TRIED
    coffobj.EXE, coffobj._EXE_TRIED = Exe(), True
    try:
        pool = coffobj.LITERAL_POOL
        old = coffobj._POOL_NEXT
        coffobj._POOL_NEXT = pool + 0x10
        rx = types.SimpleNamespace(writes=dict(enumerate(struct.pack('<I', pool), 0x03000000)))
        ra = types.SimpleNamespace(writes=dict(enumerate(struct.pack('<I', 0x0106ba58), 0x03000000)))
        pinned = {pool: struct.pack('<I', 0x006b3970)}
        check(classify.pool_vftable_stores(rx, ra, pinned), 'pool literal {0x006b3970} against the exe table that starts so')
        pinned2 = {pool: struct.pack('<I', 0x006b3971)}
        check(not classify.pool_vftable_stores(rx, ra, pinned2), 'a literal that differs from the exe word: no')
        ra2 = types.SimpleNamespace(writes=dict(enumerate(struct.pack('<I', 0x01234567), 0x03000000)))
        check(not classify.pool_vftable_stores(rx, ra2, pinned), 'another native word (not readable in the exe): no')
        coffobj._POOL_NEXT = old
    finally:
        coffobj.EXE, coffobj._EXE_TRIED = saved


def test_execonst():
    # a data symbol in a writable section of the object (.data), read-only in the exe: its exe bytes are pinned
    class Sec:
        pass
    class Exe:
        def section_of(self, va):
            return (0x00fdf000, 0x1000000, 0x40000040, b'')     # initialised read-only data

        def read(self, va, n):
            return b'\xff\xff\x7f\xff'[:n].ljust(n, b'\0')
    sym = dict(section=5, value=0, name='_INVALID_FLOAT')
    fake = types.SimpleNamespace(
        funcs={0x00a3c6d0: dict(relocs=[(0x28, coffobj.DIR32, 0x0109a918, '_INVALID_FLOAT')])},
        mappings={'_INVALID_FLOAT': 0x0109a918},
        coff=types.SimpleNamespace(by_name={'_INVALID_FLOAT': [sym]},
                                   sections=[None] * 5 + [dict(flags=coffobj.IMAGE_SCN_MEM_WRITE, data=b'\0\0\0\0')],
                                   symbols={'a': sym}))
    saved = coffobj.EXE, coffobj._EXE_TRIED
    coffobj.EXE, coffobj._EXE_TRIED = Exe(), True
    try:
        out = {}
        coffobj.UnitImage._exe_constants(fake, out)
        check(out == {0x0109a918: b'\xff\xff\x7f\xff'}, 'the exe bytes of _INVALID_FLOAT are pinned (%s)' % (out,))
        out2 = {0x0109a918: b'\0\0\0\0'}
        coffobj.UnitImage._exe_constants(fake, out2)
        check(out2 == {0x0109a918: b'\0\0\0\0'}, 'a symbol the unit already pinned is left alone')
        coffobj.EXE = None
        coffobj._EXE_TRIED = True
        out3 = {}
        coffobj.UnitImage._exe_constants(fake, out3)
        check(out3 == {}, 'with no exe image nothing changes')
    finally:
        coffobj.EXE, coffobj._EXE_TRIED = saved


if __name__ == '__main__':
    for n, f in sorted(globals().items()):
        if n.startswith('test_') and callable(f):
            print('--', n)
            try:
                f()
            except Exception as e:  # noqa: BLE001
                check(False, '%s raised %r' % (n, e))
    print('FAILED: %d' % len(FAIL) if FAIL else 'all ok')
    sys.exit(1 if FAIL else 0)
