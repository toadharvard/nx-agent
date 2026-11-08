#!/usr/bin/env python3
"""W22: native stand-ins for the Havok sized-free scalar deleting destructors (??_G whose exe code calls the router
0x00c85750 and frees through the heap's blockFree slot) and the adjustor thunks into them. Hand-made bytes, symbols
and address-map entries (no game content); with aarch64-linux-gnu-as the stub is assembled for ILP32.

    python3 tools/link/test_hksdd.py
"""
import collections
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import defshapes  # noqa: E402
import elfsyms  # noqa: E402
import exevft  # noqa: E402
import fnvlink  # noqa: E402
import test_exevft as tx  # noqa: E402
import thisadj  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def T(k):
    return tx.BASE + tx.TEXT_RVA + 0x30 * k


def call(at, i, to):
    return b'\xe8' + struct.pack('<i', to - (at + i + 5))


def jmp(at, i, to):
    return b'\xe9' + struct.pack('<i', to - (at + i + 5))


SIZED = bytes.fromhex('0fb756048b48108b018b40085256ffd0')


def sdd_sized(at, d, router=defshapes.HK_ROUTER, tail=b'\x8b\xc6\x5e\xc2\x04\x00'):
    return (b'\x56\x8b\xf1' + call(at, 3, d) + b'\xf6\x44\x24\x08\x01\x74\x15' + call(at, 15, router) + SIZED + tail)


def sym(name):
    return elfsyms.Sym(name, 0, 0, 'func', 'global', 1, '.text', False)


def nat(name, variants=None):
    return dict(unit='u.cpp', sym=sym(name), variants=variants or {}, tier='def', msvc=None)


G, G2, G3, G4 = T(1), T(2), T(3), T(4)      # ??_G: exact shape / wrong router / wrong tail / destructor not native
D, DD0 = T(5), T(6)                            # plain destroy-only body / an Itanium D0 with a D1
TH, TH2 = T(7), T(8)                           # thunks: sub ecx, 0x28; jmp G / ... G2
code = {
    G: sdd_sized(G, D),
    G2: sdd_sized(G2, D, router=0x00c85760),
    G3: sdd_sized(G3, D, tail=b'\x8b\xc6\x5e\xc3'),
    G4: sdd_sized(G4, T(0)),
    D: b'\x55\x8b\xec\x5d\xc3',
    TH: b'\x83\xe9\x28' + jmp(TH, 3, G),
    TH2: b'\x83\xe9\x28' + jmp(TH2, 3, G2),
}
tmp = tempfile.mkdtemp(prefix='hksdd-')
exe = os.path.join(tmp, 'Synthetic.exe')
tx.make_pe(exe, lambda rd: None, code)

# 1. the shape match, from the exe bytes
check(defshapes.sdd_sized_exact(exe, G) == D, 'the exact Havok sized-free ??_G gives its destructor')
check(defshapes.sdd_sized_exact(exe, G2) is None, 'a call to another router is no match')
check(defshapes.sdd_sized_exact(exe, G3) is None, 'a different tail (no `ret 4`) is no match')
check(defshapes.sdd_sized_exact(exe, D) is None and defshapes.sdd_sized_exact(exe, TH) is None,
      'a plain body and a thunk are no match')
check(defshapes.classify(exe, G, {D})[0] == 'sdd-sized', 'defshapes calls it sdd-sized before the stand-in')

# 2. the rows and the gate
dsym = sym('_ZN3HkAD1Ev')
d0sym = sym('_ZN3HkBD0Ev')
native = {D: nat('FUN_x'), DD0: dict(unit='u.cpp', sym=d0sym, variants={'D0': d0sym, 'D1': sym('_ZN3HkBD1Ev')},
                                     tier='def', msvc=None)}
del dsym
def_at = {a: ('u', n) for a, n in ((G, '??_G'), (G2, '??_G'), (G3, '??_G'), (G4, '??_G'), (TH, '?t'), (D, '?d'))}
def_at = {a: v for a, v in def_at.items() if a not in native}
st = collections.Counter()
rows = fnvlink.hk_sdd_rows(def_at, native, exe, {fnvlink.HK_FREE}, {}, st)
check(rows == {G: '__fnv_%08x' % D}, 'with the free function defined: one row, the destructor\'s native symbol: %s' % rows)
check(st['hk_sdd_no_destructor'] == 1 and st['hk_sdd_no_free'] == 0, 'a ??_G whose destructor is not native is counted')
st = collections.Counter()
check(fnvlink.hk_sdd_rows(def_at, native, exe, set(), {}, st) == {} and st['hk_sdd_no_free'] == 1,
      'without fnvnx_hk_heap_block_free: nothing emitted, hk_sdd_no_free counted')
check(fnvlink.hk_sdd_rows(def_at, native, None, {fnvlink.HK_FREE}, {}, collections.Counter()) == {},
      'no exe: nothing')
check(fnvlink.hk_d_sym(native, DD0) == '__fnv_%08x_D1' % DD0, 'a D0 at the destructor address: its D1 is called')

# 3. the stub
stub = fnvlink.emit_hk_sdd_stub('__fnv_%08x' % G, '__fnv_%08x' % D, fnvlink.HK_FREE)
check('bl __fnv_%08x' % D in stub and 'ldrh w1, [x19, #4]' in stub and 'bl fnvnx_hk_heap_block_free' in stub and
      'tbz w20, #0, 1f' in stub, 'stub: D(this); if (flags & 1) free(this, *(u16 *)(this + 4)); return this')
asm = shutil.which('aarch64-linux-gnu-as')
if asm:
    d = tempfile.mkdtemp()
    open(os.path.join(d, 'a.S'), 'w').write('    .text\n' + stub)
    r = subprocess.run([asm, '-mabi=ilp32', os.path.join(d, 'a.S'), '-o', os.path.join(d, 'a.o')],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'the stub assembles for ILP32 %s' % r.stderr[:120])
    shutil.rmtree(d)
else:
    print('skip the assembly check (no aarch64-linux-gnu-as)')

# 4. a thunk binds once the stand-in exists
fnvlink.HK_SDD.clear()
check(fnvlink.adjustor_thunks({TH: ('u', '?t@@')}, native, exe) == {}, 'before the stand-in: the thunk stays a trap')
fnvlink.HK_SDD.update(rows)
adj = fnvlink.adjustor_thunks({TH: ('u', '?t@@'), TH2: ('u', '?t2@@')}, native, exe)
check(adj == {TH: (0x28, G)}, 'after it: the thunk binds (this -= 0x28, then the stand-in); a thunk into a non-row not: %s' % adj)
check(fnvlink.adj_target(native, G) == '__fnv_%08x' % G, 'the stub branches to the stand-in\'s address symbol')
check('sub w0, w0, #40' in fnvlink.emit_adj_stub('__fnv_x', fnvlink.adj_target(native, G), 0x28), 'sub w0, w0, #40')
check(thisadj.thunk_layout_ok(0x28, {-0x28}) and not thisadj.thunk_layout_ok(0x28, {-4}),
      'the W15 offset-to-top gate still decides (a shell layout stays a trap)')

# 5. D26 (c): the copies and V rows
exev = {
    0x01010000: exevft.Vft('.?AVHkA@@', 0, [], [G, D]),            # slot 0 the stand-in
    0x01010100: exevft.Vft('.?AVHkA@@', 0x28, [], [TH]),                              # secondary: slot 0 behind a thunk
    0x01010200: exevft.Vft('.?AVHkB@@', 0x28, [], [G, D, D, TH]),                     # slot 3 the thunk
}
cands = {0x01010000: 0, 0x01010100: -0x28, 0x01010200: -0x28}
bridges, traps = {}, {}
out = tempfile.mkdtemp()
got, st = fnvlink.minus_one_copies(cands, exev, native, {}, exe, lambda n: None, bridges, traps, out)
w = {v: [l.split('.word ')[1] for l in bridges['zzvmo_%08x' % v].split('\n') if '.word ' in l] for v in cands}
check(w[0x01010000][2:4] == ['__fnvnx_vflag_%08x_0' % G, '__fnvnx_vflag_%08x_1' % G],
      'primary vftable slot 0: the flags stubs (D1 = 0, D0 = 1) into the stand-in')
check('b __fnv_%08x' % G in bridges['__fnvnx_vflag_%08x_1' % G] and 'mov w1, #1' in bridges['__fnvnx_vflag_%08x_1' % G],
      'the D0 form passes flags 1')
check(w[0x01010100][2:4] == ['__fnvnx_vadjd_%08x_0' % TH, '__fnvnx_vadjd_%08x_1' % TH] and
      'sub w0, w0, #40' in bridges['__fnvnx_vadjd_%08x_1' % TH], 'a secondary slot 0 behind a thunk: adjusting D1/D0 stubs')
slot3 = w[0x01010200][2 + 1 + 3]
check(slot3 == '__fnvnx_vadj_%08x' % TH and 'b __fnv_%08x' % G in bridges[slot3],
      'a thunk to the stand-in in slot 3 (the 0x00d68730 shape): the plain adjusting stub, no special case')
check(st['slot0-hk-sdd'] == 3 and not traps, 'counted, and no trap in the copies: %s' % sorted(traps))
vm = fnvlink.vmo_vrows(got, {}, exev)
check(set(vm) == set(cands) and vm[0x01010000][0] == '__fnvnx_vmo_01010000' and vm[0x01010000][1] == 8,
      'V rows at the address point (word 2) for the copies')
amd = tempfile.mkdtemp()
fnvlink.write_addrmap(amd, {}, {}, {}, vm, None, None)
check(('V\t__fnvnx_vmo_01010000') in open(os.path.join(amd, 'addrmap.tsv')).read(), 'addrmap.tsv carries the V row')
fnvlink.HK_SDD.clear()
shutil.rmtree(amd)
shutil.rmtree(tmp)
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
