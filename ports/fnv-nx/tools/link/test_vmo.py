#!/usr/bin/env python3
"""Unit tests of D26 (c), fnvlink.minus_one_copies: the minus-one native copy of an image vftable that stays at its
image address. Hand-made address-map entries and exe slots; no exe needed. With aarch64-linux-gnu-as on PATH the
emitted copy is also assembled and its words checked.

    python3 tools/link/test_vmo.py
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elfsyms   # noqa: E402
import exevft    # noqa: E402
import fnvlink   # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def sym(name):
    return elfsyms.Sym(name, 0, 0, 'func', 'global', 1, '.text', False)


def nat(name, msvc=None, variants=None):
    s = sym(name)
    return dict(unit='u.cpp', sym=s, variants=variants or {}, tier='def', msvc=msvc)


def w19(native, out):
    """W19: every RTTI exe vftable no V row stands for gets a copy (referenced as data or not); a secondary vftable's
    adjustor-thunk slots keep the exe's this-adjustment only where it is the COL offset."""
    exev = {
        0x01010000: exevft.Vft('.?AVA@@', 0, [], [0x00401000]),     # primary, unreferenced
        0x01010500: exevft.Vft('.?AVB@@', 8, [], [0x00409000, 0x00409010, 0x00409020, 0x00409030, 0x00409040]),
        0x01010600: exevft.Vft('.?AVC@@', 0, [], [0x00401300, 0x00409050]),   # a primary: no adjusting thunk
        0x01010700: exevft.Vft('.?AVD@@', 8, [], [0x00401300]),                 # already has a V row
    }
    used = {0x01010600}
    old = fnvlink.vmo_candidates(exev, {0x01010700: ('x', 8, 'o', 'primary')}, {}, used, every=False)
    new = fnvlink.vmo_candidates(exev, {0x01010700: ('x', 8, 'o', 'primary')}, {}, used, every=True)
    check(set(old[0]) == {0x01010600}, 'head: only the vftable referenced as data is a candidate')
    check(new[0] == {0x01010000: 0, 0x01010500: -8, 0x01010600: 0},
          'W19: every RTTI vftable without a V row is a candidate, offset-to-top from its COL (a paired one is not)')
    check(new[1]['vmo-rtti-unreferenced'] == 2 and new[1]['vmo-rtti-referenced'] == 1, 'the unreferenced are counted')
    thunks = {0x00409000: (8, 0x00401000), 0x00409010: (8, 0x00401100), 0x00409020: (4, 0x00401300),
              0x00409030: (8, 0x0040ffff), 0x00409040: (4, 0x00409041), 0x00409041: (2, 0x00401100),
              0x00409050: (8, 0x00401100)}
    saved = exevft.thunk, exevft.leaf_constant, exevft.deleting_dtor
    exevft.thunk = lambda p, a: thunks.get(a)
    exevft.leaf_constant = lambda p, a: None
    exevft.deleting_dtor = lambda p, a: False
    try:
        bridges, traps = {}, {}
        got, st = fnvlink.minus_one_copies(new[0], exev, native, {}, 'exe', lambda n: None, bridges, traps, out)
    finally:
        exevft.thunk, exevft.leaf_constant, exevft.deleting_dtor = saved
    check(set(got) == set(new[0]), 'W19: a copy for the unreferenced primary and the secondary')
    b = [l.split('.word ')[1] for l in bridges['zzvmo_01010500'].split('\n') if '.word ' in l]
    check(b[0] == '0xfffffff8', 'the secondary copy carries the COL offset as offset-to-top')
    check(b[2:4] == ['__fnvnx_vadjd_00409000_0', '__fnvnx_vadjd_00409000_1'],
          'a destructor slot behind `sub ecx, 8; jmp ??_G`: adjusting D1/D0 stubs')
    check('sub w0, w0, #8' in bridges['__fnvnx_vadjd_00409000_1'] and 'b __fnv_00401000\n' in
          bridges['__fnvnx_vadjd_00409000_1'], 'the D0 stub subtracts the offset, then enters the deleting destructor')
    check('b __fnv_00401000_D1' in bridges['__fnvnx_vadjd_00409000_0'], 'and the D1 stub enters the complete destructor')
    check(b[4] == '__fnvnx_vadj_00409010' and 'sub w0, w0, #8' in bridges['__fnvnx_vadj_00409010'] and
          'b __fnv_00401100' in bridges['__fnvnx_vadj_00409010'], 'a plain member behind an adjustor thunk by the COL offset')
    check(b[5] in traps and b[5].startswith('__fnvnx_vtrap_'), 'a thunk by another immediate than the offset is a trap')
    check(b[6] in traps, 'a thunk into a function no native build defines is a trap')
    check(b[7] in traps, 'a chain whose sum is not the offset is a trap')
    c = [l.split('.word ')[1] for l in bridges['zzvmo_01010600'].split('\n') if '.word ' in l]
    check(c[4] in traps, 'a primary vftable never adjusts: an adjusting thunk there is a trap')
    check(st['slots-adjustor-thunk'] == 1 and st['slot0-adjustor-dtor'] == 1, 'the adjusted slots are counted')


def main():
    d0, d1 = sym('_ZN1AD0Ev'), sym('_ZN1AD1Ev')
    native = {
        0x00401000: dict(unit='u.cpp', sym=d0, variants={'D0': d0, 'D1': d1}, tier='def', msvc='??_GA@@UAEPAXI@Z'),
        0x00401100: nat('_ZN1A1fEv'),
        0x00401200: nat('_ZN1B12FUN_00401200Ej', msvc='??_GB@@UAEPAXI@Z'),
        0x00401300: nat('_ZN1C1gEv'),
    }
    exev = {
        0x01010000: exevft.Vft('.?AVA@@', 0, [], [0x00401000, 0x00401100, 0x00409990]),   # G pair, f, missing
        0x01010100: exevft.Vft('.?AVB@@', 4, [], [0x00401200, 0x00401100]),               # (this, flags) ??_G
        0x01010200: exevft.Vft(None, None, [], [0x00401300]),                              # slot 0 not a dtor
    }
    cands = {0x01010000: 0, 0x01010100: -4, 0x01010200: 0, 0x01010300: 0}
    bridges, traps = {}, {}
    out = tempfile.mkdtemp()
    got, st = fnvlink.minus_one_copies(cands, exev, native, {}, None, lambda n: None, bridges, traps, out)
    check(set(got) == {0x01010000, 0x01010100, 0x01010200}, 'a copy for every candidate with exe slots')
    check(st['no-exe-slots'] == 1, 'a candidate without exe slots is counted, not copied')
    a = bridges['zzvmo_01010000'].split('\n')
    words = [l.split('.word ')[1] for l in a if '.word ' in l]
    check(words[:2] == ['0x0', '0'], 'offset-to-top and typeinfo words')
    check(words[2:4] == ['__fnv_00401000_D1', '__fnv_00401000'], 'D1/D0 from the address map G pair at slots 0/1')
    check(words[4] == '__fnv_00401100', 'exe slot 1 at native slot 2 (minus-one)')
    check(words[5].startswith('__fnvnx_vtrap_') and words[5] in traps, 'a missing exe slot is a trap stub')
    check(traps[words[5]][1] == ('__fnvnx_vmo_01010000', 5), 'the trap names the copy and its word index')
    b = [l.split('.word ')[1] for l in bridges['zzvmo_01010100'].split('\n') if '.word ' in l]
    check(b[0] == '0xfffffffc', 'a secondary vftable carries its offset-to-top')
    check(b[2:4] == ['__fnvnx_vflag_00401200_0', '__fnvnx_vflag_00401200_1'],
          'a (this, flags) ??_G definition: flags stubs 0 (D1) and 1 (D0)')
    check('mov w1, #1' in bridges['__fnvnx_vflag_00401200_1'], 'the D0 stub passes flags 1')
    c = [l.split('.word ')[1] for l in bridges['zzvmo_01010200'].split('\n') if '.word ' in l]
    check(c[2] in traps and c[3] == '__fnv_00401300', 'slot 0 not a destructor: D1 traps, exe slot 0 at slot 1')
    rows = open(os.path.join(out, 'vmo.tsv')).read().splitlines()
    check(len(rows) == 4 and rows[1].split('\t')[3] == '1', 'vmo.tsv: one row per copy with its trap slots')
    # V rows (W14): each copy of an exe vftable no native vtable stands for gets an address-map V row at word 2
    vm = fnvlink.vmo_vrows(got, {0x01010100: ('_ZTV1B', 8, 'b.o', 'primary')}, exev)
    check(set(vm) == {0x01010000, 0x01010200}, 'a V row for each copy; a vftable the map already pairs keeps its row')
    check(vm[0x01010000][:3] == ('__fnvnx_vmo_01010000', 8, fnvlink.VTEXT_OBJ), 'the row names the copy, point 8, bridges')
    check(vm[0x01010000][3].startswith('primary') and "RTTI class A" in vm[0x01010000][3],
          'the note says primary and names the RTTI class')
    sec = {**exev, 0x01010000: exev[0x01010000]._replace(off=8)}
    check(fnvlink.vmo_vrows(got, {}, sec)[0x01010000][3].startswith('secondary'), 'a secondary vftable is noted so')
    amd = tempfile.mkdtemp()
    kinds, _ = fnvlink.write_addrmap(amd, {}, {}, {}, vm, None, None)
    tsv = [l.split('\t') for l in open(os.path.join(amd, 'addrmap.tsv')) if not l.startswith('#')]
    check([(r[1], r[2], r[6]) for r in tsv] == [('V', '__fnvnx_vmo_01010000', '8'), ('V', '__fnvnx_vmo_01010200', '8')],
          'addrmap.tsv carries the V rows')
    check('__fnvnx_vmo_01010000 + 8' in open(os.path.join(amd, 'addrmap.c')).read(), 'addrmap.c points at the address point')
    shutil.rmtree(amd)
    w19(native, out)
    asm = shutil.which('aarch64-linux-gnu-as')
    if asm:
        src = os.path.join(out, 'b.S')
        text = ''.join(bridges[k] for k in sorted(bridges) if isinstance(bridges[k], str))
        text += fnvlink.emit_traps(traps)
        open(src, 'w').write('    .text\n' + text)
        r = subprocess.run([asm, '-mabi=ilp32', src, '-o', os.path.join(out, 'b.o')], capture_output=True, text=True)
        check(r.returncode == 0, 'the copies and stubs assemble for ILP32 (%s)' % r.stderr.strip()[:200])
    else:
        print('skip assembling: no aarch64-linux-gnu-as')
    shutil.rmtree(out)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
