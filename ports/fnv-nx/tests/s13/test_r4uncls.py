#!/usr/bin/env python3
"""tests/s13/test_r4uncls.py - EQ round 4, the rows F1's unclassified worker left open (difffuzz-20), on hand-written
code and hand-built results (no decomp, no exe bytes).

  rep-movs     the MSVC intrinsics `__movsd` / `__movsb` a Havok unit calls out of line are builtins on the native
               (libcalls.run rep_movsd / rep_movsb, element by element, an overlapping copy repeats the pattern) and no
               call in the trace, as the oracle executes the string instruction
  delete-fold  libcalls.delete_fold: a delete of null (null-delete) and the delete that follows the destructor half
               F(p, 0) of the exe's scalar deleting destructor (split-delete); exeempty.deleting_wrapper is the exe
               evidence for the second, read from the code bytes
  dtor-shape   exeempty.dtor_chain: a function that stores a derived class's vftable and then a base class's
               (RTTI), derived first; a constructor stores them the other way round
  eval-order   runner._perm_align / compare: the same calls in another order are paired by callee (soft eval-order);
               a difference found after that is marked (eval-order-args), not hidden
  bool-chain   classify._bool_chain: several random-background bool bytes are needed, one is not enough

Needs capstone, unicorn (the engines' Result classes).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import arm64emu   # noqa: E402
import causes     # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import exeempty   # noqa: E402
import libcalls   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []


def hint_cause(d):
    try:
        return classify.hint(None, None, None, d, dict(code=b'\xc3', address=0x401000), 0).get('cause')
    except Exception as e:  # noqa: BLE001 - the old head has no flag for it and runs the probes on a missing pair
        return 'error: %s' % type(e).__name__


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class Mem:
    def __init__(self, init=None):
        self.b = dict(init or {})
        self.log = []

    def read(self, a, n):
        return bytes(self.b.get(a + i, 0) for i in range(n))

    def write(self, a, data):
        for i, x in enumerate(data):
            self.b[a + i] = x
        self.log.append((a, bytes(data)))


# ---------------------------------------------------------------------------------------------------- rep-movs
check(libcalls.A64.get('__movsd') == 'rep_movsd' and libcalls.A64.get('__movsb') == 'rep_movsb',
      'rep-movs: __movsd / __movsb are native builtins')
try:
    m = Mem({0x1000 + i: i + 1 for i in range(16)})
    libcalls.run('rep_movsd', m, [0x2000, 0x1000, 3])
    check(m.read(0x2000, 12) == bytes(range(1, 13)) and m.read(0x200c, 1) == b'\0', 'rep-movs: 3 dwords copied')
    m = Mem({0x1000: 0xaa, 0x1001: 0xbb})
    libcalls.run('rep_movsb', m, [0x1001, 0x1000, 4])         # overlapping, forward: the pattern repeats
    check(m.read(0x1000, 5) == bytes([0xaa, 0xaa, 0xaa, 0xaa, 0xaa]), 'rep-movs: overlapping copy repeats the first byte')
    m = Mem({0x3000: 1, 0x3001: 2, 0x3002: 3, 0x3003: 4, 0x3004: 5, 0x3005: 6, 0x3006: 7, 0x3007: 8})
    libcalls.run('rep_movsd', m, [0x3004, 0x3000, 1])
    check(m.read(0x3004, 4) == bytes([1, 2, 3, 4]), 'rep-movs: movsd moves one dword')
except KeyError as e:
    check(False, 'rep-movs: run() has no %s' % e)


# ---------------------------------------------------------------------------------------------------- fake exe
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


def put(mem, a, data):
    for i, x in enumerate(data):
        mem[a + i] = x
    return a + len(data)


def u32(v):
    return struct.pack('<I', v & 0xffffffff)


def rel(frm_end, to):
    return u32(to - frm_end)


def build_exe():
    mem = {}
    # scalar deleting destructor: push ebp; mov ebp, esp; mov eax, [ebp+8]; and eax, 1; je +5; call X; pop ebp; ret 4
    a = 0x401000
    code = bytes.fromhex('55' '8bec' '8b4508' '83e001' '7405') + b'\xe8' + rel(a + 11 + 5, 0x401800) + \
        bytes.fromhex('5d' 'c20400')
    put(mem, a, code)
    # the same without the flag test (a plain destructor body that calls something)
    a = 0x401100
    put(mem, a, bytes.fromhex('55' '8bec') + b'\xe8' + rel(a + 3 + 5, 0x401800) + bytes.fromhex('5d' 'c20400'))
    # test byte ptr [esp+8], 1; je +5; call X; ret 4 (the Havok shape)
    a = 0x401200
    put(mem, a, bytes.fromhex('f6442408' '01' '7405') + b'\xe8' + rel(a + 6 + 5, 0x401800) + bytes.fromhex('c20400'))
    # RTTI: three classes, Base (vftable 0x410010), Mid : Base (0x410110), Leaf : Mid (0x410210)
    def cls(vft, td, chd, names, bases_tds):
        col = vft - 0x40
        put(mem, vft - 4, u32(col))
        put(mem, col, u32(0) + u32(0) + u32(0) + u32(td) + u32(chd))
        put(mem, td, u32(0) + u32(0) + names[0].encode() + b'\0')
        arr = chd + 0x20
        put(mem, chd, u32(0) + u32(0) + u32(len(bases_tds)) + u32(arr))
        for i, t in enumerate(bases_tds):
            bcd = chd + 0x40 + 0x10 * i
            put(mem, arr + 4 * i, u32(bcd))
            put(mem, bcd, u32(t))
    cls(0x410010, 0x411000, 0x412000, ['.?AVBase@@'], [0x411000])
    cls(0x410110, 0x411100, 0x412100, ['.?AVMid@@'], [0x411100, 0x411000])
    cls(0x410210, 0x411200, 0x412200, ['.?AVLeaf@@'], [0x411200, 0x411100, 0x411000])
    # destructor body: [esi] = Leaf vftable, then [esi] = Base vftable (derived first); ret
    put(mem, 0x401300, bytes.fromhex('c706') + u32(0x410210) + bytes.fromhex('c706') + u32(0x410010) + b'\xc3')
    # constructor: Base first, then Leaf
    put(mem, 0x401400, bytes.fromhex('c706') + u32(0x410010) + bytes.fromhex('c706') + u32(0x410210) + b'\xc3')
    # one store only
    put(mem, 0x401500, bytes.fromhex('c706') + u32(0x410210) + b'\xc3')
    # a store of a non-vftable immediate and then a vftable
    put(mem, 0x401600, bytes.fromhex('c706') + u32(0x12345) + bytes.fromhex('c706') + u32(0x410010) + b'\xc3')
    return mem


exeempty._PE[:] = [FakePE(build_exe())]
for _c in ('_WRAP', '_CHAIN'):
    if hasattr(exeempty, _c):
        getattr(exeempty, _c).clear()

# ---------------------------------------------------------------------------------------------------- dtor-shape
try:
    check(exeempty.deleting_wrapper(0x401000), 'dtor-shape: `and eax, 1; je; call .. ret 4` is a deleting wrapper')
    check(not exeempty.deleting_wrapper(0x401100), 'dtor-shape: no flag test, no wrapper')
    check(exeempty.deleting_wrapper(0x401200), 'dtor-shape: test byte ptr [esp+8], 1 is one too')
    check(exeempty.dtor_chain(0x401300), 'dtor-shape: Leaf vftable then Base vftable is a destructor body')
    check(not exeempty.dtor_chain(0x401400), 'dtor-shape: Base vftable then Leaf vftable is a constructor')
    check(not exeempty.dtor_chain(0x401500), 'dtor-shape: one vftable store is no chain')
    check(not exeempty.dtor_chain(0x401600), 'dtor-shape: an immediate that is no vftable is ignored')
except AttributeError as e:
    check(False, 'dtor-shape: %s' % e)

# ---------------------------------------------------------------------------------------------------- delete-fold
try:
    check(libcalls.delete_fold(0, None, None, None) == 'null', 'delete-fold: delete(0) is null')
    check(libcalls.delete_fold(0x3000010, None, None, None) is None, 'delete-fold: delete(p) alone stays a call')
    check(libcalls.delete_fold(0x3000010, 0x401000, 0x3000010, 0) == 'split',
          'delete-fold: delete(p) after F(p, 0), F a deleting wrapper, is split')
    check(libcalls.delete_fold(0x3000010, 0x401000, 0x3000010, 1) is None,
          'delete-fold: after F(p, 1) the delete is an extra free')
    check(libcalls.delete_fold(0x3000020, 0x401000, 0x3000010, 0) is None, 'delete-fold: another pointer stays')
    check(libcalls.delete_fold(0x3000010, 0x401100, 0x3000010, 0) is None,
          'delete-fold: F is no deleting wrapper: stays a call')
except AttributeError as e:
    check(False, 'delete-fold: %s' % e)

# ---------------------------------------------------------------------------------------------------- eval-order
check(hasattr(runner, '_perm_align'), 'eval-order: runner._perm_align exists')
if hasattr(runner, '_perm_align'):
    vc = ({0x00402000, 0x00403000, 0x00404000}, set())
    check(runner._perm_align([1, 2, 3], [1, 2, 3], vc) is None, 'eval-order: equal traces: nothing to align')
    check(runner._perm_align([0x402000, 0x403000, 0x403000], [0x403000, 0x403000, 0x402000], vc) == [2, 0, 1],
          'eval-order: a rotation is paired occurrence by occurrence')
    check(runner._perm_align([0x402000, 0x403000], [0x402000, 0x404000], vc) is None, 'eval-order: other calls: no')
    check(runner._perm_align([0x402000, 0x403000, 0x403000], [0x403000, 0x402000], vc) is None,
          'eval-order: a call missing: no')

cv = conv.Conv('?f@@YAXXZ', 'test')
w = world.World(world.Case(seed=1), {}, {})


def res(tx, ta, writes=None, ret=None):
    a, b = x86emu.Result(), arm64emu.Result()
    a.calls = [x86emu.Call(t, [0, 0, 0], 0) for t in tx]
    b.calls = [arm64emu.Call(t, [0] * 8, [0] * 8, 0) for t in ta]
    a.ret = b.ret = ret
    a.writes, b.writes = dict((writes or ({}, {}))[0]), dict((writes or ({}, {}))[1])
    return a, b


vc = ({0x402000, 0x403000, 0x404000}, set())
rx, ra = res([0x402000, 0x403000, 0x403000], [0x403000, 0x403000, 0x402000])
d = runner.compare(cv, rx, ra, w, vcalls=vc)
check(d is not None and d.get('soft') and d.get('eval_order'),
      'eval-order: the same calls in another order alone: soft eval_order (%s)' % d)
if d:
    check(hint_cause(d) == 'eval-order',
          'eval-order: classify names it')
rx, ra = res([0x402000, 0x403000, 0x403000], [0x403000, 0x403000, 0x402000],
             writes=({0x01000000: 1}, {0x01000000: 2}))
d = runner.compare(cv, rx, ra, w, vcalls=vc)
check(d is not None and d['kind'] == 'mem-write' and not d.get('soft') and d.get('after_perm'),
      'eval-order: then a different store: reported, marked after_perm (%s)' % (d and d['kind']))
if d:
    check(hint_cause(d) == 'eval-order-args',
          'eval-order: ... and named eval-order-args, not decided')
check(causes.policy('eval-order-args')[1:3] == ('S13b', True) and not causes.policy('eval-order')[2] and
      causes.policy('eval-order')[0] == 'by-design', 'eval-order: policy (by-design soft; the args one hard)')

# a delete(0) the native dropped, the oracle's engines left both out: the note is soft
rx, ra = res([0x402000], [0x402000])
rx.null_deletes, ra.null_deletes = 1, 0
d = runner.compare(cv, rx, ra, w, vcalls=vc)
check(d is not None and d.get('soft') and d.get('null_delete'), 'delete-fold: a null delete on one machine: soft note (%s)' % d)
if d:
    check(hint_cause(d) == 'null-delete',
          'delete-fold: classify names null-delete')
rx, ra = res([0x402000], [0x402000])
ra.split_folds = 1
d = runner.compare(cv, rx, ra, w, vcalls=vc)
check(d is not None and d.get('soft') and d.get('split_delete'), 'delete-fold: a folded delete: soft split_delete (%s)' % d)
check(causes.policy('split-delete')[0] == 'by-design' and not causes.policy('split-delete')[2]
      and causes.policy('null-delete')[0] == 'by-design', 'delete-fold: policies are by-design soft')


# ---------------------------------------------------------------------------------------------------- bool-chain
class FakePair:
    """Diverges unless every byte of NEED holds 1 (planted); reports the 1-byte reads of the bytes above 1."""
    NEED = (0x5000, 0x6000, 0x7000)

    def run(self, case, max_steps=0):
        planted = lambda a: any(b <= a < b + len(v) and v[a - b] == 1 for b, v in case.mem.items())  # noqa: E731
        if world.BG_READS is not None:
            for a in self.NEED:
                if not planted(a):
                    world.BG_READS[a] = ({1}, 7)
        if world.BYTE_READS is not None:
            pass
        ok = all(planted(a) for a in self.NEED)
        return None, None, (None if ok else dict(kind='call-target', index=1, x86='00402000', arm='00403000'))


if hasattr(classify, '_bool_chain'):
    h = dict(_runs=0)
    got = classify._bool_chain(FakePair(), None, world.Case(seed=1, bg='rand'),
                               dict(kind='call-target', index=1, x86='00402000', arm='00403000'), None, 1000, h)
    check(sorted(got) == ['00005000', '00006000', '00007000'], 'bool-chain: the three bytes are found (%s)' % got)

    class OnePair(FakePair):
        NEED = (0x5000,)
    h = dict(_runs=0)
    got = classify._bool_chain(OnePair(), None, world.Case(seed=1, bg='rand'),
                               dict(kind='call-target', index=1, x86='00402000', arm='00403000'), None, 1000, h)
    check(got == ['00005000'], 'bool-chain: one byte is the minimal set (%s)' % got)

    class NeverPair(FakePair):
        def run(self, case, max_steps=0):
            r = FakePair.run(self, case, max_steps)
            return None, None, dict(kind='call-target', index=1, x86='00402000', arm='00403000')
    class LaterPair(FakePair):
        def run(self, case, max_steps=0):
            r = FakePair.run(self, case, max_steps)
            if r[2] is None:
                return None, None, dict(kind='call-args', index=5, arg='p0', x86='01', arm='00')
            return r
    real_hint = classify.hint
    for sub, want in ((dict(cause='call-flow', _runs=0), False), (dict(cause='uninit-x86', _runs=0), True),
                      (dict(cause='uninit-arm', _runs=0), False)):
        classify.hint = lambda *a, _s=sub: dict(_s)
        h = dict(_runs=0)
        got = classify._bool_chain(LaterPair(), None, world.Case(seed=1, bg='rand'),
                                   dict(kind='call-target', index=1, x86='00402000', arm='00403000'), None, 1000, h)
        check(bool(got) == want and (sub['cause'] != 'uninit-arm' or h.get('chain_cause') == 'uninit-arm'),
              'bool-chain: bytes planted, then a %s divergence stays: %s (%s)' %
              (sub['cause'], 'bool-repr' if want else 'not bool-repr', got))
    classify.hint = lambda *a: dict(cause='partial-return', _runs=0)
    h = dict(_runs=0)
    got = classify._bool_chain(NeverPair(), None, world.Case(seed=1, bg='rand'),
                               dict(kind='return', x86='07000003', arm='07000000'), None, 1000, h)
    check(not got, 'bool-chain: a return divergence never takes the nested path (test_uncls partial-return): %s' % got)
    classify.hint = real_hint
else:
    check(False, 'bool-chain: classify._bool_chain exists')

if FAIL:
    print('FAILED: %d' % len(FAIL))
    sys.exit(1)
print('ok')
