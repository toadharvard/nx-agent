#!/usr/bin/env python3
"""test_sdd.py - synthetic test of the fnvnx_sdd plugin (SDD2: one vtable slot per virtual destructor).

    python3 toolchain/sdd/test_sdd.py

Compiles test_sdd.cpp (plain C++) with the plugin and checks the assembly: table contents in exe order, the slot each
virtual call loads, destructor flags, pointers to virtual members, address-point symbols. The game's flag set (-fno-devirtualize)
stays on. Skips without the cross
compiler and its plugin headers.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, cond, detail=''):
    if not cond:
        FAILS.append(name)
        print('FAIL', name, detail)


def body(asm, sym):
    m = re.search(r'^%s:\n(.*?)\.cfi_endproc' % re.escape(sym), asm, re.S | re.M)
    return m.group(1) if m else ''


def slot_loads(asm, sym):
    """byte offsets of the loads `ldr wN, [xM, off]` / `[xM]` that follow a vptr load, i.e. table slots"""
    return [int(o or 0) for o in re.findall(r'ldr\s+w\d+, \[x\d+(?:, (\d+))?\]\n\s+mov\s+x16', body(asm, sym))]


def words(asm, sym):
    m = re.search(r'^%s:\n((?:\s+\.\w+\s+\S+\n)+)' % re.escape(sym), asm, re.M)
    return re.findall(r'\.word\s+(\S+)', m.group(1)) if m else []


def main():
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(['bash', os.path.join(HERE, 'build.sh'), os.path.join(td, 'so')], capture_output=True, text=True)
        if r.returncode != 0:
            print('skip: the plugin does not build here:', r.stderr.strip().splitlines()[-1:]); return 0
        so = r.stdout.strip().splitlines()[-1]
        out = os.path.join(td, 't.s')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-fno-sized-deallocation', '-fno-devirtualize',
                            '-fplugin=' + so, '-S', '-o', out, os.path.join(HERE, 'test_sdd.cpp')], capture_output=True, text=True)
        check('compiles', r.returncode == 0, r.stderr[:3000])
        if r.returncode != 0: return 1
        s = open(out).read()
    # struct A { virtual int f(); virtual ~A(); virtual int g(); }: slots f 0, ??_G 1, g 2
    check('table M: exe order, one destructor slot, the secondary table too',
          words(s, '_ZTV1M') == ['0', '_ZTI1M', '_ZN1A1fEv', '_ZN1MD1Ev.fnvnx_sdd', '_ZN1A1gEv', '_ZN1M1kEv', '_ZN1M4lastEv',
                                 '-8', '_ZTI1M', '_ZThn8_N1MD1Ev.fnvnx_sdd', '_ZThn8_N1M1kEv'], str(words(s, '_ZTV1M')))
    check('delete p: slot 1, flags 1', slot_loads(s, '_Z5del_aP1A') == [4] and re.search(r'mov\s+w1, 1\n', body(s, '_Z5del_aP1A')))
    check('p->~A(): slot 1, flags 0', slot_loads(s, '_Z9destroy_aP1A') == [4] and re.search(r'mov\s+w1, 0\n', body(s, '_Z9destroy_aP1A')))
    check('delete through the second base: its slot 0', slot_loads(s, '_Z6del_s2P2S2') == [0])
    check('g(): slot 2', slot_loads(s, '_Z6call_gP1A') == [8], str(slot_loads(s, '_Z6call_gP1A')))
    check('k() through S2: slot 1', slot_loads(s, '_Z6call_kP2S2') == [4])
    check('M::last(): slot 4', slot_loads(s, '_Z9call_lastP1M') == [16])
    check('pointer to virtual member: constant', words(s, 'table') == ['8', '1', '0', '1'], str(words(s, 'table')))
    check('pointer to virtual member: comparison', re.search(r'cmp\s+w0, 8\n', body(s, '_Z2eqM1AFivE')))
    check('pointer to virtual member: template argument', slot_loads(s, '_Z5use_tP1A') == [8])
    check('instantiated class template', slot_loads(s, '_Z6use_ttP2TTIiE') == [4] and slot_loads(s, '_Z6del_ttP2TTIiE') == [0])
    uj = re.findall(r'ldr\s+w\d+, \[x\d+, (\d+)\]', body(s, '_Z5use_jP1J'))
    check('inline member bodies: g() slot 2, J::w() slot 3', uj == ['8', '12'], str(uj))
    sets = dict(re.findall(r'\.set\s+(\S+), (\S+)\n', s))
    check('address points: own symbols', sets.get('_ZTV1M.fnvnx_ap8') == '_ZTV1M+8' and sets.get('_ZTV1M.fnvnx_ap36') == '_ZTV1M+36', str(sets))
    ctor = body(s, '_ZN1MC2Ev')
    check('constructor: one relocation per address point, no immediate',
          len(re.findall(r':got_lo12:_ZTV1M\.fnvnx_ap(?:8|36)\]', ctor)) == 2 and ':got:_ZTV1M\n' not in ctor, ctor)
    th = body(s, '_ZN1MD1Ev.fnvnx_sdd')
    flat = lambda x: re.sub(r'\s+', ' ', re.sub(r'^\s*\.cfi.*$', '', x, flags=re.M))
    th = flat(body(s, '_ZN1MD1Ev.fnvnx_sdd'))
    check('??_E flags 0: the destructor; flags 1: the deleting destructor; returns this',
          'tst w1, #3 bne 1f bl _ZN1MD1Ev b 9f 1: mov w20, w0 tbnz w21, #1, 2f bl _ZN1MD0Ev b 9f' in th
          and '9: mov x0, x19' in th, th)
    check('??_E flags 2: n = the word before the array, elements last first, then the global operator delete on bit 0 (cl: ??3)',
          '2: ldr w22, [x20, -4] 3: cbz w22, 4f sub w22, w22, #1 ldr w9, =16 madd w0, w22, w9, w20 bl _ZN1MD1Ev b 3b '
          '4: tbz w21, #0, 9f sub w0, w20, #4 bl _ZdlPv' in th, th)
    th2 = flat(body(s, '_ZThn8_N1MD1Ev.fnvnx_sdd'))
    check('??_E of the second base: the thunk for flags 0, the adjusted object otherwise',
          'bl _ZThn8_N1MD1Ev b 9f 1: ldr w9, =-8 add w0, w0, w9 mov w20, w0 tbnz w21, #1, 2f bl _ZN1MD0Ev' in th2, th2)
    th3 = flat(body(s, '_ZN2VBD1Ev.fnvnx_sdd'))
    check("??_E frees with the class's own operator delete[]", 'ldr w9, =12 madd' in th3 and 'bl _ZN2VBdaEPv' in th3, th3)
    th4 = flat(body(s, '_ZN2HKD1Ev.fnvnx_sdd'))
    check('placement delete[] only (and a scalar operator delete of the class): the global operator delete, as cl',
          'tbz w21, #0, 9f sub w0, w20, #4 bl _ZdlPv' in th4 and '_ZN2HKdlEPv' not in th4.split('2: ldr w22')[1], th4)
    th5 = flat(body(s, '_ZN2SZD1Ev.fnvnx_sdd'))
    check('sized delete[]: the member, with the size of one element (cl passes sizeof(the class))',
          re.search(r'tbz w21, #0, 9f ldr w1, =12 sub w0, w20, #4 bl _ZN2SZdaEPv[jm]', th5), th5)
    na = flat(body(s, '_Z6new_a8i'))
    check('COOKIE1: the array of a class aligned to 8 starts 4 bytes into its block',
          re.search(r'add w\d+, w\d+, #?4\b', na) and not re.search(r'add w\d+, w\d+, #?8\b', na), na)
    check('no deleting destructor in any table', not re.search(r'\.word\s+\S*D0Ev', s))
    check('deleting destructors are written out', '_ZN1MD0Ev:' in s and '_ZN1BD0Ev:' in s)
    vm = re.findall(r'\.ascii\s+"(_ZTV1M[^"]*)"', s)
    check('table map: one line per address point with its base path',
          vm == ['_ZTV1M\\t8\\t0\\t1M\\t1M\\n', '_ZTV1M\\t36\\t8\\t1M\\t1M;2S2\\n'], str(vm))
    print('test_sdd: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
