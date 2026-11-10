#!/usr/bin/env python3
"""test_sdd.py - synthetic test of the fnvnx_sdd plugin (SDD2: one vtable slot per virtual destructor).

    python3 toolchain/sdd/test_sdd.py

Compiles test_sdd.cpp (plain C++) with the plugin and checks the assembly: table contents in exe order, the slot each
virtual call loads, destructor flags, pointers to virtual members, address-point symbols. Skips without the cross
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
        r2 = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-fno-sized-deallocation', '-fplugin=' + so,
                             '-S', '-o', os.devnull, os.path.join(HERE, 'test_sdd.cpp')], capture_output=True, text=True)
        check('devirtualization on is refused', r2.returncode != 0 and 'fno-devirtualize' in r2.stderr, r2.stderr[:300])
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
    check('address points: own symbols at their place in the shrunk table',
          sets.get('_ZTV1M.fnvnx_ap8') == '_ZTV1M+8' and sets.get('_ZTV1M.fnvnx_ap40') == '_ZTV1M+36', str(sets))
    ctor = body(s, '_ZN1MC2Ev')
    check('constructor: one relocation per address point, no immediate',
          len(re.findall(r':got_lo12:_ZTV1M\.fnvnx_ap(?:8|40)\]', ctor)) == 2 and ':got:_ZTV1M\n' not in ctor, ctor)
    th = body(s, '_ZN1MD1Ev.fnvnx_sdd')
    check('??_G: flags bit 0 picks the deleting destructor, returns this',
          re.search(r'tbnz\s+w1, #0, 1f\n\s+bl\s+_ZN1MD1Ev\n\s+b\s+2f\n1:\s+bl\s+_ZN1MD0Ev\n2:\s+mov\s+x0, x19', th), th)
    print('test_sdd: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
