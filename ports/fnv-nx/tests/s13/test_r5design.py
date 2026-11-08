#!/usr/bin/env python3
"""tests/s13/test_r5design.py - divergences earlier workers called "by design" without proof (EQ, r5 design,
difffuzz-20), on hand-written code (no decomp content). Each probe names its cause only when the exe's own code shows
the mechanism (a FakePE here) and the rerun without it agrees; each test fails on the head before the class.

  deadres   an int result no caller in the exe reads (every call is followed by a write of eax): dead-result
            (0x00cde920, 0x00e5c420); a caller that stores eax keeps the divergence
  deadinit  a constructor call whose stores the caller all overwrites before any read: dead-init-call
            (0x00b134d0); a caller that overwrites half of the object keeps it
  index     a variable index into a local array that leaves the frame (writes the caller's argument slot):
            local-index-range (0x00db1d90, 0x00e552f0); a function that is wrong inside the frame keeps it
  ptrdiff   (end - begin) / 52 with end - begin not a multiple of 52: ptrdiff-unaligned (0x00abb700)
  x87args   a float argument that overflowed on the native only: x87-range for call-args (0x00a62be0)
  nullderef a wild call's canned 0 dereferenced at once: canned-null-deref (0x005c0bf0, first fault)
  dtorvptr  the oracle may leave a destructor's vptr alone only when the exe shows a base destructor sets it:
            dtor_defers_vptr and runner.dtor_vptr_bytes (0x006e5890)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13r5design'))

import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import exeempty   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402
from test_uncls import Info  # noqa: E402

try:
    import r5design   # noqa: E402
except ImportError:           # the head before the class
    r5design = None

FAIL = []
BASE = 0x00401000
SIZE = 0x14000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class FakePE:
    def __init__(self, mem):
        self.data = bytes(mem.get(BASE + i, 0) for i in range(SIZE))
        self.sections = [(BASE, SIZE, 0x60000020, self.data, '.text')]

    def section_of(self, va):
        return self.sections[0] if BASE <= va < BASE + SIZE else None

    def read(self, a, n):
        return self.data[a - BASE:a - BASE + n]


def put(mem, a, data):
    for i, x in enumerate(data):
        mem[a + i] = x
    return a + len(data)


def u32(v):
    return struct.pack('<I', v & 0xffffffff)


def install(mem):
    exeempty._PE[:] = [FakePE(mem)]
    for c in ('_WRAP', '_CHAIN', '_DEFER', '_CACHE'):
        getattr(exeempty, c, {}).clear()
    if r5design is not None:
        r5design._DEAD.clear()


def run_pair(name, oracle, body, case, defsyms=(), info=None, address=0x00401000):
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv(name, 'test')
        t = runner.Target(cv, address, {address: oracle}, build_image(body, d, defsyms), {}, info)
        p = runner.Pair(t, {})
        rx, ra, div = p.run(case)
        fn = dict(code=oracle, address=address)
        h = {}
        if div:
            try:
                h = classify.hint(p, t, case, div, fn, 200000)
            except Exception as e:  # noqa: BLE001 - the old head has no flag / module for it
                h = {'cause': 'error: %s' % type(e).__name__}
        return div, h


CALLEE, CALLEE2 = 0x00402000, 0x00402100


# ------------------------------------------------------------------------------------------------------- deadres
def test_deadres():
    oracle = bytes.fromhex('8b442404' '8b00' 'c3')                       # int f(int *p): return *p
    nat = '\tmov w0, #0\n\tret\n'
    case = world.Case(seed=3, args={'p0': u32(world.BUF)}, mem={world.BUF: u32(5)})
    for what, tail, want in (('eax written before any read', bytes.fromhex('b801000000' 'c3'), 'dead-result'),
                             ('caller stores eax', bytes.fromhex('8945fc' 'c3'), None)):
        mem = {}
        put(mem, BASE, oracle)
        put(mem, 0x401800, call_rel(0x401800, BASE) + tail)
        install(mem)
        div, h = run_pair('?f@@YAHPAH@Z', oracle, nat, case)
        check(div is not None and div['kind'] == 'return', '%s: the result differs (%s)' % (what, div and div['kind']))
        check((h.get('cause') == 'dead-result') == (want == 'dead-result'), '%s: cause %s (%s)' % (what, want, h.get('cause')))
    # a pass-through wrapper: `call f; ret` whose own caller writes eax; and the address taken somewhere
    mem = {}
    put(mem, BASE, oracle)
    put(mem, 0x401800, b'\xcc' + call_rel(0x401801, BASE) + b'\xc3' + b'\xcc')
    put(mem, 0x401900, call_rel(0x401900, 0x401801) + bytes.fromhex('31c0' 'c3'))
    install(mem)
    check(r5design is not None and r5design.result_dead(BASE), 'a bare pass-through wrapper whose caller does xor eax, eax: dead')
    put(mem, 0x401a00, b'\xb8' + u32(BASE))                              # mov eax, f: the address is taken
    install(mem)
    check(r5design is not None and not r5design.result_dead(BASE), 'a function whose address is stored is not dead')


# ------------------------------------------------------------------------------------------------------ deadinit
def test_deadinit():
    ctor = bytes.fromhex('55' '89e5' '51' '894dfc' '8b45fc' 'c70000000000' 'c7400400000000' '89ec' '5d' 'c3')

    def f(second_store):
        code = bytes.fromhex('55' '89e5' '83ec10' '8d4df8')
        code += call_rel(BASE + len(code), CALLEE)
        code += bytes.fromhex('c745f801000000')
        if second_store:
            code += bytes.fromhex('c745fc02000000')
        code += bytes.fromhex('8d4df0')
        code += call_rel(BASE + len(code), CALLEE)
        return code + bytes.fromhex('89ec' '5d' 'c3')
    nat = ('\tstp x29, x30, [sp, #-16]!\n\tmov w0, wsp\n\tbl __fnv_00402000\n\tldp x29, x30, [sp], #16\n\tret\n')
    for what, second, want in (('every byte overwritten', True, 'dead-init-call'), ('half overwritten', False, None)):
        mem = {}
        put(mem, CALLEE, ctor)
        install(mem)
        oracle = f(second)
        div, h = run_pair('?f@@YAXXZ', oracle, nat, world.Case(seed=3), defsyms=[stub(CALLEE)],
                          info=Info(conv.Conv('?C@@QAEXXZ', 'test')))
        check(div is not None and div['kind'] == 'call-count' and div['x86'] == 2,
              '%s: the oracle makes the extra constructor call (%s)' % (what, div))
        check(h.get('cause') == want if want else h.get('cause') != 'dead-init-call',
              '%s: cause %s (%s)' % (what, want, h.get('cause')))


# ---------------------------------------------------------------------------------------------------------- index
def test_index():
    # void f(int idx, int b, int c): float loc[4]; loc[idx] = 1.0f (bits); g(c)
    #   x86: mov [ebp + eax*4 - 0x10], 0x3f800000 writes the caller's slot of c when idx = 8
    def code(_add):
        c = bytes.fromhex('55' '89e5' '83ec10' '8b4508' 'c74485f0' '0000803f' '8b4d10' '51')
        c += call_rel(BASE + len(c), CALLEE)
        return c + bytes.fromhex('83c404' '89ec' '5d' 'c3')

    def nat(add):
        return ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tmovz w3, #0x3f80, lsl #16\n\tadd x4, sp, #16\n'
                '\tstr w3, [x4, w0, sxtw #2]\n\tadd w0, w2, #%d\n\tbl __fnv_00402000\n'
                '\tldp x29, x30, [sp], #32\n\tret\n' % (1 if add else 0))
    for what, add, want in (('no bug inside the frame', False, 'local-index-range'),
                            ('a bug inside the frame', True, None)):
        install({})
        div, h = run_pair('?f@@YAXHHH@Z', code(add), nat(add),
                          world.Case(seed=3, args={'p0': u32(8), 'p1': u32(0), 'p2': u32(7)}),
                          defsyms=[stub(CALLEE)], info=Info(conv.Conv('?g@@YAXH@Z', 'test')))
        check(div is not None and div['kind'] == 'call-args', '%s: the argument differs (%s)' % (what, div and div['kind']))
        check(h.get('cause') == want if want else h.get('cause') != 'local-index-range',
              '%s: cause %s (%s)' % (what, want, h.get('cause')))


# -------------------------------------------------------------------------------------------------------- ptrdiff
def test_ptrdiff():
    # int f(int *r): return (r[1] - r[0]) / 52   (MSVC idiv; GCC: exact division, (d >> 2) * inverse(13))
    oracle = bytes.fromhex('8b4c2404' '8b4104' '2b01' '99' 'b934000000' 'f7f9' 'c3')
    nat = ('\tldp w1, w2, [x0]\n\tsub w0, w2, w1\n\tasr w0, w0, #2\n\tmovz w3, #0x4ec5\n\tmovk w3, #0xc4ec, lsl #16\n'
           '\tmul w0, w0, w3\n\tret\n')
    wrong = nat.replace('\tmul w0, w0, w3\n', '\tmul w0, w0, w3\n\tadd w0, w0, #1\n')
    for what, body, want in (('exact division', nat, 'ptrdiff-unaligned'), ('wrong for multiples too', wrong, None)):
        install({})
        case = world.Case(seed=3, args={'p0': u32(world.BUF)}, mem={world.BUF: u32(0x1000) + u32(0x1000 + 5 * 52 + 10)})
        div, h = run_pair('?f@@YAHPAH@Z', oracle, body, case)
        check(div is not None and div['kind'] == 'return', '%s: the quotients differ (%s)' % (what, div and div['kind']))
        check(h.get('cause') == want if want else h.get('cause') != 'ptrdiff-unaligned',
              '%s: cause %s (%s)' % (what, want, h.get('cause')))
    install({})
    case = world.Case(seed=3, args={'p0': u32(world.BUF)}, mem={world.BUF: u32(0x1000) + u32(0x1000 + 5 * 52)})
    div, h = run_pair('?f@@YAHPAH@Z', oracle, nat, case)
    check(div is None, 'a multiple of 52: the machines agree (%s)' % (div,))


# --------------------------------------------------------------------------------------------------------- x87args
def test_x87args():
    # void f(float a, b, c, d): g(a * b - c * d)   -- x87 keeps 1e60 - 1e60 = 0; float arithmetic gives inf - inf
    oracle = bytes.fromhex('d9442404' 'd84c2408' 'd944240c' 'd84c2410' 'dee9' '83ec04' 'd91c24')
    oracle += call_rel(BASE + len(oracle), CALLEE) + bytes.fromhex('83c404' 'c3')
    nat = ('\tstp x29, x30, [sp, #-16]!\n\tfmul s0, s0, s1\n\tfmul s2, s2, s3\n\tfsub s0, s0, s2\n\tbl __fnv_00402000\n'
           '\tldp x29, x30, [sp], #16\n\tret\n')
    big = struct.pack('<f', 1e30)
    install({})
    case = world.Case(seed=3, args={'p0': big, 'p1': big, 'p2': big, 'p3': big})
    div, h = run_pair('?f@@YAXMMMM@Z', oracle, nat, case, defsyms=[stub(CALLEE)],
                      info=Info(conv.Conv('?g@@YAXM@Z', 'test')))
    check(div is not None and div['kind'] == 'call-args' and div.get('cls') == 'F32',
          'the float argument differs (%s)' % (div,))
    check(h.get('cause') == 'x87-range', 'x87-range for a call-args divergence (%s)' % (h.get('cause'),))
    check((h.get('x87_range') or {}).get('range') == 'f32', 'the record keeps the range (%s)' % (h.get('x87_range'),))
    small = struct.pack('<f', 3.0)
    div, h = run_pair('?f@@YAXMMMM@Z', oracle, nat, world.Case(seed=3, args={'p0': small, 'p1': small, 'p2': small,
                                                                           'p3': small}),
                      defsyms=[stub(CALLEE)], info=Info(conv.Conv('?g@@YAXM@Z', 'test')))
    check(div is None, 'in range: the machines agree (%s)' % (div,))


# ------------------------------------------------------------------------------------------------------ nullderef
def test_nullderef():
    # int f(): v = wild(); return *(int *)(v + 4)   -- the native never looks at the wild call's result
    oracle = bytes.fromhex('b88df0d70b' 'ffd0' '8b4004' 'c3')
    nat = ('\tstp x29, x30, [sp, #-16]!\n\tmovz w16, #0xf08d\n\tmovk w16, #0x0bd7, lsl #16\n\tblr x16\n\tmov w0, #0\n'
           '\tldp x29, x30, [sp], #16\n\tret\n')
    install({})
    case = world.Case(seed=3, rets=[bytes(16)])
    div, h = run_pair('?f@@YAHXZ', oracle, nat, case)
    check(div is not None and div['kind'] == 'error' and div['x86'] == 'fault:r:00000004' and div['arm'] is None,
          'the oracle faults on its canned 0, the native does not (%s)' % (div,))
    check(h.get('cause') == 'canned-null-deref', 'canned-null-deref (%s)' % (h.get('cause'),))
    # the result is not dereferenced at once: the evidence is missing
    if r5design is not None:
        code = bytes.fromhex('b88df0d70b' 'ffd0' '31c0' '8b4004' 'c3')
        check(not r5design.deref_after_call(code, BASE, BASE + 7, 4), 'eax overwritten before the load: no evidence')
        check(not r5design.deref_after_call(bytes.fromhex('e800000000' '8b4004' 'c3'), BASE, BASE + 5, 4),
              'a direct call is a declared callee, not a wild one')
        check(r5design.deref_after_call(oracle, BASE, BASE + 7, 4), 'indirect call, then [eax + 4]: evidence')
        check(not r5design.deref_after_call(oracle, BASE, BASE + 7, 8), 'another displacement than the fault address')


# ------------------------------------------------------------------------------------------------------- dtorvptr
def test_dtorvptr():
    mem = {}
    vft = 0x410010

    def cls(vft, td, chd, name):
        col = vft - 0x40
        put(mem, vft - 4, u32(col))
        put(mem, col, u32(0) + u32(0) + u32(0) + u32(td) + u32(chd))
        put(mem, td, u32(0) + u32(0) + name.encode() + b'\0')
        arr = chd + 0x20
        put(mem, chd, u32(0) + u32(0) + u32(1) + u32(arr))
        put(mem, arr, u32(chd + 0x40))
        put(mem, chd + 0x40, u32(td))
    cls(vft, 0x411000, 0x412000, '.?AVBase@@')
    # base destructor: mov [ecx], Base::vftable; ret
    put(mem, 0x401300, bytes.fromhex('c701') + u32(vft) + b'\xc3')
    # derived destructor: mov eax, 1; call base; ret   (no vftable store of its own)
    put(mem, 0x401400, bytes.fromhex('b801000000') + call_rel(0x401405, 0x401300) + b'\xc3')
    # a destructor that stores a vftable itself, then calls the base
    put(mem, 0x401500, bytes.fromhex('c701') + u32(vft) + call_rel(0x401506, 0x401300) + b'\xc3')
    # a destructor whose last call is not a vftable-storing base
    put(mem, 0x401600, bytes.fromhex('b801000000') + call_rel(0x401605, 0x401700) + b'\xc3')
    put(mem, 0x401700, bytes.fromhex('31c0' 'c3'))
    install(mem)
    check(getattr(exeempty, 'dtor_defers_vptr', None) is not None and exeempty.dtor_defers_vptr(0x401400),
          'the body calls a base destructor that stores its vftable: the vptr is left to it')
    check(getattr(exeempty, 'dtor_defers_vptr', None) is not None and not exeempty.dtor_defers_vptr(0x401500),
          'a body that stores a vftable itself defers nothing')
    check(getattr(exeempty, 'dtor_defers_vptr', None) is not None and not exeempty.dtor_defers_vptr(0x401600),
          'the last callee stores no vftable: not a base destructor')
    # the final-memory rule: the oracle left the word alone, the native stored its address point
    w = 0x03000000
    wa = {w + i: b for i, b in enumerate(u32(0x0bc00008))}
    is_vft, is_nat = (lambda v: v == vft), (lambda v: 0x0bc00000 <= v < 0x0c000000)
    try:
        got_def = runner.dtor_vptr_bytes([w + i for i in range(4)], {}, wa, is_vft, is_nat, True)
        got_not = runner.dtor_vptr_bytes([w + i for i in range(4)], {}, wa, is_vft, is_nat, False)
    except TypeError:
        got_def = got_not = None
    check(got_def == {w + i for i in range(4)}, 'the exe defers the vptr: the unstored oracle word is no divergence (%s)' % (got_def,))
    check(got_not == set(), 'no such evidence: the word stays a divergence (%s)' % (got_not,))
    wx = {w + i: b for i, b in enumerate(u32(vft))}
    got_both = runner.dtor_vptr_bytes([w + i for i in range(4)], wx, wa, is_vft, is_nat, False)
    check(got_both == {w + i for i in range(4)}, 'both stored a vtable word: as before')


def test_policy():
    for c, cls in (('dead-result', 'by-design'), ('dead-init-call', 'by-design'), ('local-index-range', 'by-design'),
                   ('canned-null-deref', 'artifact')):
        p = causes.policy(c)
        check(p[0] == cls and not p[2], '%s: %s, not hard' % (c, cls))


def main():
    test_deadres()
    test_deadinit()
    test_index()
    test_ptrdiff()
    test_x87args()
    test_nullderef()
    test_dtorvptr()
    test_policy()
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
