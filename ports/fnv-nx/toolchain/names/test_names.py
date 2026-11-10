#!/usr/bin/env python3
"""test_names.py - the fnvnx_names plugin (NAMES1) against cl's own symbol table.

    python3 toolchain/names/test_names.py

test_names.cpp is compiled by the cross g++ with the SDD2 and NAMES1 plugins; the MSVC names of its rows
(.fnvnx.sympair, .fnvnx.vtpair) are compared with test_names.expect, the symbol table MSVC 2008 SP1 itself wrote for
the same file (regenerate: cl /nologo /c /EHsc /Od /GR test_names.cpp, then the object's external symbols).

Both directions are checked. What is left out is left out by class, each with its reason, and each class's exact
size is asserted, so a name that starts or stops matching fails the test:
  cl only   ??_8 (vbtables), ??4 (implicit assignment cl emits for an exported class), ??_G / ??_E of a class
            without a virtual destructor (cl's helpers for delete), vtordisp thunks ($4): the exe's own, no GCC twin
  holes     HOLES below: what GCC's trees do not hold (see fnvnx_names.cc)
Skips without the cross compiler.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# name cl gives -> why the plugin cannot: each is a class the gate (tools/link/namegate.py) counts over the program
HOLES = {
    # size_t is `unsigned long` for aarch64 ILP32 GCC (K), `unsigned int` for cl (I)
    '??2@YAPAXI@Z': 'size_t', '??2Mem@@SAPAXI@Z': 'size_t', '??2Mem@@SAPAXIH@Z': 'size_t', '??_UMem@@SAPAXI@Z': 'size_t',
    # a function parameter and an array parameter inside a function TYPE (not a declaration)
    '?f8@@YAXP6AXH@ZP6AHUS@@PAVC@@@ZP6AXH@ZP6AXQAH@Z6Q6AXH@Z@Z': 'function type',
    # a static in a block below the function's outermost one
    '?b@?2??m@LS@@QAEHXZ@4HA': 'scope', '?e@?4??ls1@@YAHH@Z@4HA': 'scope', '?g@?7??ls1@@YAHH@Z@4HA': 'scope',
    '?h@?9??ls1@@YAHH@Z@4HA': 'scope',
}


def cl_only(n):
    return (n.startswith(('??_8', '??4')) or '@@$4' in n
            or n in ('??_GMem@@QAEPAXI@Z', '??_EMem@@QAEPAXI@Z', '??_ELC@@QAEPAXI@Z', '??_GLC@@QAEPAXI@Z'))


def section(obj, name):
    r = subprocess.run(['aarch64-linux-gnu-objcopy', '--dump-section', '%s=/dev/stdout' % name, obj, '/dev/null'],
                       capture_output=True, text=True)
    return [l.split('\t') for l in r.stdout.splitlines()]


def main():
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    expect = {}
    for l in open(os.path.join(HERE, 'test_names.expect')):
        if not l.startswith('#') and l.strip():
            n, k = l.rstrip('\n').split('\t'); expect.setdefault(n, set()).add(k)
    with tempfile.TemporaryDirectory() as td:
        sos = []
        for d in ('sdd', 'names'):
            r = subprocess.run(['bash', os.path.join(HERE, '..', d, 'build.sh'), os.path.join(td, d)], capture_output=True, text=True)
            if r.returncode != 0:
                print('skip: the plugins do not build here'); return 0
            sos.append('-fplugin=' + r.stdout.strip().splitlines()[-1])
        obj = os.path.join(td, 't.o')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O0', '-fkeep-inline-functions', '-fshort-wchar',
                            '-fsigned-char', '-fno-sized-deallocation', '-fno-devirtualize'] + sos +
                           ['-c', '-o', obj, os.path.join(HERE, 'test_names.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[-3000:]); return 1
        rows = section(obj, '.fnvnx.sympair')
        vt = section(obj, '.fnvnx.vtpair')
        nm = subprocess.run(['aarch64-linux-gnu-nm', '-g', '--defined-only', obj], capture_output=True, text=True).stdout
        defined = {l.split()[-1] for l in nm.splitlines()}
    fails = []
    def check(name, cond, detail=''):
        if not cond: fails.append(name); print('FAIL', name, str(detail)[:1500])
    kinds = {}
    for sym, ms, kind, df in rows: kinds.setdefault(kind.split(':')[0], []).append((sym, ms, kind))
    named = {ms for sym, ms, kind, df in rows if ms and kind not in ('thunk',)}
    names_vt = {r[2] for r in vt if r[2] and not r[2].startswith('unnamed')}
    got = named | names_vt
    want = {n for n in expect if not cl_only(n)}
    aps = {'%s.fnvnx_ap%s' % (r[0], r[1]) for r in vt}
    norow = {d for d in defined - {r[0] for r in rows} - aps if not d.startswith('_ZTC')}     # construction tables: no twin
    check('every defined symbol of the object has a row (an address point: a .fnvnx.vtpair row)', not norow, sorted(norow))
    unnamed = sorted(k for s, m, k in kinds.get('unnamed', []))
    check('the only unnamed rows: 4 nested-scope statics, 4 destructor thunks of classes with virtual bases',
          unnamed == ['unnamed:destructor thunk in a class with virtual bases'] * 4 + ['unnamed:local scope number'] * 4, kinds.get('unnamed'))
    missing = want - got
    check('cl names the plugin does not produce are exactly the stated holes', missing == set(HOLES),
          {'unexpected': sorted(missing - set(HOLES)), 'now matching': sorted(set(HOLES) - missing)})
    extra = got - set(expect)
    # the plugin's side of the size_t and function-type holes (4 + ::operator delete[] the SDD function calls + f8)
    check('names of the plugin that cl does not have are the same holes seen from GCC',
          sorted(extra) == sorted(['??2@YAPAXK@Z', '??2Mem@@SAPAXK@Z', '??2Mem@@SAPAXKH@Z', '??_UMem@@SAPAXK@Z', '??_U@YAPAXK@Z',
                                   '?f8@@YAXP6AXH@ZP6AHUS@@PAVC@@@Z0P6AXPAH@Z5Q6AXH@Z@Z',
                                   # GCC's own: D1's ??_G (cl wrote only ??_E here), delete[] called by the ??_E bodies
                                   '??_GD1@@UAEPAXI@Z', '??_V@YAXPAX@Z']), sorted(extra))
    by = {ms: kind for sym, ms, kind, df in rows}
    check('the vector deleting destructor of a primary table is ??_E', by.get('??_ED1@@UAEPAXI@Z') == 'dtor.sdd', by.get('??_ED1@@UAEPAXI@Z'))
    check('... of a secondary table is cl\'s adjustor thunk', by.get('??_ED1@@W7AEPAXI@Z') == 'dtor.sdd.thunk', by.get('??_ED1@@W7AEPAXI@Z'))
    check('a virtual base class: the complete destructor is ??_D', by.get('??_DVD@@QAEXXZ') == 'dtor.d1', by.get('??_DVD@@QAEXXZ'))
    check('a user-written member of a local class is internal for cl',
          sorted(s for s, m, k in kinds.get('internal', [])) == ['_ZZ3ls1iEN1L2lmEv', '_ZZ3ls1iEN1L2lvEv', '_ZZZ3ls1iEN1L2lmEvE1q'],
          kinds.get('internal'))
    vtnames = {(r[0], r[1]): r[2] for r in vt}
    check('address points of a table group are cl\'s vftables', vtnames.get(('_ZTV2D1', '8')) == '??_7D1@@6BB1@@@'
          and '??_7D1@@6BB2@@@' in names_vt and '??_7D1@@6BB3@@@' in names_vt and '??_7V1@@6B0@@' in names_vt
          and '??_7VD@@6BVB@@@' in names_vt, sorted(vtnames.items()))
    check('every ??_7 of cl is an address point', {n for n in expect if n.startswith('??_7')} <= names_vt,
          sorted({n for n in expect if n.startswith('??_7')} - names_vt))
    print('test_names: %s (%d rows, %d names equal to cl\'s, %d stated holes)'
          % ('FAILED %d' % len(fails) if fails else 'ok', len(rows), len(got & set(expect)), len(HOLES)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
