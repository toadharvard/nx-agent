#!/usr/bin/env python3
"""test_pi.py - synthetic test of the fnvnx_pi plugin (N3 verdict, inline tree, touched types).

    python3 toolchain/pi/test_pi.py

Compiles test_pi.cpp with the plugin and checks the lines of section .fnvnx.n3. Skips without the cross compiler
and its plugin headers.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    cxx = shutil.which(os.environ.get('CXX') or 'aarch64-linux-gnu-g++')   # the compiler the plugins are built for
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(['bash', os.path.join(HERE, 'build.sh'), os.path.join(td, 'so')], capture_output=True, text=True)
        if r.returncode != 0:
            print('skip: the plugin does not build here'); return 0
        out = os.path.join(td, 't.s')
        # the header as a whole drags in sse2neon (its inline bodies are rows too): take its declarations of the lifted forms
        hdr = open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h')).read().splitlines()
        part = [l for l in hdr if (l.endswith(';') and 'fnvnx_lifted' in l) or re.match(r'#define (__try|__except|__finally|__leave|_exception_|GetException)', l)]
        decl = [l for l in part if not l.startswith('#define')]
        open(os.path.join(td, 'fnvnx_seh_part.h'), 'w').write('extern "C" {\n' + '\n'.join(decl) + '\n}\n' + '\n'.join(l for l in part if l.startswith('#define')) + '\n')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-I' + td, '-O2', '-fPIC', '-fplugin=' + r.stdout.strip().splitlines()[-1],
                            '-S', '-o', out, os.path.join(HERE, 'test_pi.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[:2000]); return 1
        s = open(out).read()
        subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-c', '-o', out.replace('t.s', 't.o'), out], capture_output=True)
        nm = subprocess.run(['nm', '-u', out.replace('t.s', 't.o')], capture_output=True, text=True).stdout
    s2 = s[s.index('.fnvnx.argorder'):]
    s2 = s2[:s2.index('.popsection')]
    sec = s[s.index('.fnvnx.n3'):]
    rows = {}
    for l in re.findall(r'\.ascii\s+"(.*)\\n"', sec[:sec.index('.popsection')]):
        f = l.split('\\t')
        rows[(f[0], f[3])] = f
    want = {
        ('_Z3topP1R', ''): ['ok', '_ZL3midP1R,_ZL4leafP1R', '1R'],     # the inline tree, transitively
        ('_ZL3midP1R', 'inline'): ['ok', '', '1R'],                    # a body seen but not emitted on its own
        ('_ZL4leafP1R', 'inline'): ['ok', '', '1R'],
        ('_Z2dvii', ''): ['div', '', ''],
        ('_Z2dci', ''): ['ok', '', ''],                                # division by a constant is inside
        ('_Z2shjj', ''): ['shift', '', ''],
        ('_Z2vaiz', ''): ['varargs', '', ''],
        ('_Z3volPVi', ''): ['volatile', '', ''],
        ('_Z2uni', ''): ['uninit', '', ''],                            # read early: the optimizer deletes the use
        ('_Z2asi', ''): ['asm', '', ''],
        ('_ZL2cpii', 'constprop.0'): ['ok', '', ''],                   # a clone, under the function's own name
        ('_Z5usecpi', ''): ['ok', '', ''],
        ('_Z2m1v', ''): ['ok', '', '1Q'],
        ('_Z2m2v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m3v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m4i', ''): ['uninit-mem', '_Z2uni', '1Q'],        # ICF made its body one with un's: GCC inlined un (the scope blocks never showed it)
        ('_Z2m5v', ''): ['ok', '', '1Q'],
        ('_Z2o1v', ''): ['ok', '', ''],
        ('_Z2o2v', ''): ['argorder', '', ''],
        ('_Z2o3i', ''): ['argorder', '', ''],
        ('_Z2o4v', ''): ['ok', '', ''],
        ('_Z2o5R1W', ''): ['ok', '', '1W'],
        ('_Z3fo1i', ''): ['falloff', '', ''],                          # FALL2
        ('_Z3fo2i', ''): ['ok', '_Z2uni', ''],                         # the same: ICF
        ('_Z3fo3i', ''): ['ok', '', ''],
        ('_Z3fo4i', ''): ['ok', '', ''],
        ('_Z3fo5i', ''): ['falloff', '', ''],
        ('_ZN1KC2Ev', ''): ['ok', '', '1K'],
        ('_ZN2KDD2Ev', ''): ['ok', '', '2KD'],
        ('_ZN2KDD1Ev', 'alias'): ['ok', '', '2KD'],
        ('_ZN2KED2Ev', ''): ['argorder', '', '2KE'],
        ('_ZN2KED1Ev', 'alias'): ['argorder', '', '2KE'],
        ('_ZN1KD2Ev', ''): ['ok', '', '1K'],
        ('_Z3sx1i', ''): ['seh', '', ''],                              # SEH-PI: the callee's attribute, not its name
        ('_Z3sx2i', ''): ['seh', '', ''],
        ('_Z3sx3i', ''): ['ok', '', ''],
        ('_ZL3sh1i', 'inline'): ['seh', '', ''],
        ('_Z3sx4i', ''): ['ok', '_ZL3sh1i', ''],
        ('_Z2dbi', ''): ['int3', '', ''],
        ('_Z3ao1i', ''): ['argorder', '', '2S1'],
        ('_ZN1KC1Ev', 'alias'): ['ok', '', '1K'],                      # the same body under its other symbol
        ('_ZN1KD1Ev', 'alias'): ['ok', '', '1K'],
        # INL2: the inlined set from GCC's inlining bookkeeping (removal hook, inline-clone edges), not only the scope blocks
        ('_Z2f2i', ''): ['ok', '_ZL2h2i', ''],
        ('_Z3f2bi', ''): ['ok', '_ZL2i2i', ''],
        ('_Z2f3i', ''): ['ok', '_ZL2g3i,_ZL2h3i', ''],
        ('_Z2f4i', ''): ['ok', '_ZL2g4i,_ZL2h4i', ''],
        ('_Z2e5i', ''): ['ok', '_ZL2h5i', ''],
        ('_Z2f5i', ''): ['ok', '_ZL2h5i', ''],
        ('_Z2f6i', ''): ['ok', '', ''],
        ('_Z2f7i', ''): ['ok', '_ZL2r7i', ''],
        ('_Z3f7bi', ''): ['ok', '_ZL2r7i', ''],
        ('_Z2f8i', ''): ['ok', '_ZL2z8i', ''],
        ('_Z2f9i', ''): ['ok', '_ZL2t9i,_ZL3ip9PFiiEi', ''],
        ('_ZL2h5i', ''): ['ok', '', ''],
        ('_Z3f10i', ''): ['ok', '_ZL3g10i,_ZL3h10i', ''],         # INL3
        ('_ZL3g10i', 'inline'): ['ok', '', ''],
        ('_Z3f11i', ''): ['uninit-mem', '_ZL3i11i,_ZN1ZC4Ei', '1Z'],           # INL3: the clone's own inlining is in the caller's list
        ('_ZL3i11i', 'inline'): ['ok', '', ''],
        ('_ZN1ZC2Ei', 'inline'): ['ok', '', '1Z'],
        ('_ZL3h10i', 'inline'): ['ok', '', ''],
        ('_ZL3cp6ii', 'constprop.0'): ['ok', '_ZL2g6i', ''],     # into a clone: the line is the origin's
        ('_ZL2g3i', 'inline'): ['ok', '', ''],
        ('_ZL2g4i', 'inline'): ['ok', '', ''],
        ('_ZL2g6i', 'inline'): ['ok', '', ''],
        ('_ZL2h2i', 'inline'): ['ok', '', ''],
        ('_ZL2h3i', 'inline'): ['ok', '', ''],
        ('_ZL2h4i', 'inline'): ['ok', '', ''],
        ('_ZL2i2i', 'inline'): ['ok', '', ''],
        ('_ZL2t9i', 'inline'): ['ok', '', ''],
        ('_ZL2z8i', 'inline'): ['ok', '', ''],
        ('_ZL3ip9PFiiEi', 'inline'): ['ok', '', ''],
        ('_ZL2r7i', 'inline'): ['ok', '', ''],
        # INL4: records under bodies inside removed code are consumed by the callgraph's own removal facts
        ('_Z4inl4P2Ta', ''): ['uninit-mem', '_ZN2MaC4Ev,_ZN2RaC4Ev,_ZN2TaC4Ev,_ZN2VaC4Ev', '2Ta,2Va'],
        ('_ZN2TaC2Ev', 'inline'): ['ok', '', '2Ta'],
        ('_ZN2MaC2Ev', 'inline'): ['ok', '', '2Ma'],
        ('_ZN2RaC2Ev', 'inline'): ['ok', '', '2Ra'],
        ('_ZN2VaC2Ev', 'inline'): ['ok', '', '2Va'],
    }
    fails = 0
    for k, w in want.items():
        got = rows.get(k)
        if not got or [got[1], got[2], got[4]] != w:
            fails += 1
            print('FAIL', k, 'want', w, 'got', got)
    if set(rows) != set(want):
        fails += 1
        print('FAIL rows', sorted(set(rows) ^ set(want)))
    ao = re.findall(r'\.ascii\s+"(.*)\\n"', s2)
    # the last field: the call's number among the function's calls of that callee / how many there are
    wantao = ['_Z2o1v\\t_Z3twoii\\t_Z2gav|_Z2gbv\\t0/1', '_Z2o5R1W\\t_ZN1W1wEii\\t_ZN1W4selfEv|_Z2gav|\\t0/1',
              '_ZN2KDD2Ev\\t_Z3twoii\\t_Z2gav|_Z2gbv\\t0/1']
    if ao != wantao:
        fails += 1
        print('FAIL argorder rows', ao)
    for w in ('fnvnx_seh_try', 'fnvnx_seh_except', 'fnvnx_seh_finally', '__debugbreak'):
        if not re.search(r'\sU ' + w + r'$', nm, re.M):
            fails += 1
            print('FAIL undefined reference to', w)
    print('test_pi: %s' % ('FAILED %d' % fails if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
