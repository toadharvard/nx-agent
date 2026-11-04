#!/usr/bin/env python3
"""test_mt1.py - patch class MT1 (gated Switch behaviour changes, docs/PATCHES.md "Patch class MT1") in the .s1b
tooling. Synthetic patches and sources only (no decomp needed):

  1. statement hunks become `if (FNVNX_MT1_ON) { new } else { old }` with the matched old text;
  2. `=== guarded` hunks are kept as written and must test FNVNX_MT1_ON; `=== inert` hunks must only add text;
  3. hunk modes are refused on the byte-parity classes (XINC1, ...);
  4. MT1 sites are exempt from the MSVC byte check (msvc_sites), and equiv classifies MT1 as `platform` (never in
     a neutral MSVC build);
  5. prepare's rule list runs r_mt1 (the Switch build);
  6. the emitted code compiles with the forced fnvnx_compat.h gate, and runs the decomp's path while nothing defines
     fnvnx_mt1_on() and the MT1 path once the platform defines it to return 1 (host g++; skipped if absent).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'rules'))
sys.path.insert(0, os.path.join(PORT, 'tools'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'equiv'))
import s1b_patches as sp  # noqa: E402

fails = []


def check(cond, what):
    if not cond:
        fails.append(what)


SRC = '''// @fnv 0x00401000
int Count(int* p)
{
    int n = 0;
    n = p[0] + 1;
    return n;
}
// @fnv 0x00401100
int Other()
{
    return 3;
}
'''

PATCH = '''@@ MT1 src/mt.cpp 0x00401000
# test: a statement hunk, gated by the tooling
<<<
    n = p[0] + 1;
===
    n = p[0] + 100;
>>>
@@ MT1 src/mt.cpp file
# test: an inert addition (a declaration the gated code could use)
<<<
// @fnv 0x00401100
===
 inert
>>>
'''


def site_set(text):
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, 'mt1_t.s1b'), 'w').write(text)
        return sp.load_sites(d)
    finally:
        shutil.rmtree(d)


def refused(text, what):
    try:
        site_set(text)
    except ValueError:
        return
    fails.append('not refused: ' + what)


# 1/2: parsing and emission ----------------------------------------------------------------------------------------
inert_patch = PATCH.replace('===\n inert\n', '=== inert\nint mt1_extra();\n// @fnv 0x00401100\n')
sites = site_set(inert_patch)
check([s.rule for s in sites] == ['MT1', 'MT1'], 'two MT1 sites parsed')
check(sites[0].modes == [''] and sites[1].modes == ['inert'], 'hunk modes parsed: %r' % [s.modes for s in sites])
saved = (sp.SITES, sp.BY_FILE)
sp.SITES = sites
sp.BY_FILE = {'src/mt.cpp': sites}
from collections import Counter  # noqa: E402
st = Counter()
out = sp.r_mt1('/x/src/mt.cpp', SRC, st)
check(st['MT1'] == 2 and not st['MT1-MISS'], 'both MT1 hunks applied: %r' % dict(st))
check('if (FNVNX_MT1_ON) { /* fnv-nx MT1 */\n    n = p[0] + 100;\n} else {\n    n = p[0] + 1;\n}' in out,
      'statement hunk wrapped in the gate with the old text in the else branch')
check('int mt1_extra();' in out, 'inert hunk kept as written')
st2 = Counter()
check(sp.r_xinc1('/x/src/mt.cpp', SRC, st2) == SRC, 'XINC1 does not apply MT1 sites')
sp.SITES, sp.BY_FILE = saved

refused(PATCH.replace('===\n inert\n', '=== guarded\nint g();\n'), 'guarded hunk without the gate')
refused(PATCH.replace('===\n inert\n', '=== inert\nint g();\n'), 'inert hunk that drops the old text')
refused(PATCH.replace('@@ MT1', '@@ XINC1').replace('===\n inert\n', '=== inert\n// @fnv 0x00401100\n'),
        'hunk mode on a byte-parity class')
refused(PATCH.replace('===\n inert\n', '=== whatever\n// @fnv 0x00401100\n'), 'unknown hunk mode')
g = site_set(PATCH.replace('===\n inert\n', '=== guarded\nif (FNVNX_MT1_ON) mt1_extra();\n// @fnv 0x00401100\n'))
check(g[1].modes == ['guarded'], 'guarded hunk accepted when it tests the gate')

# 4: MSVC exemption and equiv class -----------------------------------------------------------------------------
x = site_set('@@ XINC1 src/a.cpp 0x1\n# e\n<<<\na\n===\nb\n>>>\n')
check([s.rule for s in sp.msvc_sites(sites + x)] == ['XINC1'], 'msvc_sites drops MT1')
check('MT1' not in sp.MSVC_RULES and 'MT1' in sp.GATED, 'MT1 is a gated class, not a byte-parity one')
import equiv  # noqa: E402
check(equiv.CLASSES.get('MT1') == 'platform', 'equiv classifies MT1 as platform: %r' % equiv.CLASSES.get('MT1'))
mc = open(os.path.join(HERE, 'msvc_check.py')).read()
check('msvc_sites(' in mc and 'MSVC_RULES' in mc, 'msvc_check.py checks byte-parity sites only')

# 5: prepare (the Switch build) runs it ---------------------------------------------------------------------------
check(sp.r_mt1 in sp.RULES, 'r_mt1 is in the module RULES that prepare loads')

# 6: compile and run the emitted code -----------------------------------------------------------------------------
cxx = shutil.which('g++')
if cxx:
    d = tempfile.mkdtemp()
    try:
        body = out.replace('int mt1_extra();', '')
        main = body + '\nint main(){ int v = 1; return Count(&v); }\n'
        open(os.path.join(d, 'm.cpp'), 'w').write(main)
        open(os.path.join(d, 'on.cpp'), 'w').write('extern "C" int fnvnx_mt1_on(void) { return 1; }\n')
        compat = os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h')
        gate = open(compat).read()
        i = gate.index('/* Patch class MT1')
        j = gate.index('#define FNVNX_MT1_ON')
        gate = gate[i:gate.index('\n', j) + 1]          # the gate block alone (the rest needs the port's headers)
        open(os.path.join(d, 'gate.h'), 'w').write(gate)
        for name, extra, want in (('off', [], 2), ('on', [os.path.join(d, 'on.cpp')], 101)):
            exe = os.path.join(d, name)
            r = subprocess.run([cxx, '-O2', '-include', os.path.join(d, 'gate.h'), os.path.join(d, 'm.cpp')] + extra +
                               ['-o', exe], capture_output=True, text=True)
            if r.returncode:
                fails.append('compile (%s): %s' % (name, r.stderr[:400]))
                continue
            rc = subprocess.run([exe]).returncode
            check(rc == want, 'gate %s: Count returned %d, want %d' % (name, rc, want))
    finally:
        shutil.rmtree(d)
else:
    print('skip: no host g++ for the compile/run check')

for f in fails:
    print('FAIL', f)
print('test_mt1: %d failures' % len(fails))
sys.exit(1 if fails else 0)
