#!/usr/bin/env python3
"""Test of toolchain/ftoi/fnvnx_ftoi.cc (FTOI3: one plugin, class C, two modes) through recipe stages on the guest compiler (plugbuild.py).
  native (a native root: every plugin, the plugin's mode comes from cxx_origin):
    - a conversion outside a helper is MSVC's _ftol (0x80000000 for NaN and out of range);
    - a function carrying __attribute__((fnvnx_f2i)) is left unrewritten (the bare fcvtzs) and is a helper in the report;
    - the report (out=/out/unit.f2i.json, kept by the recipe as the unit's f2i file) counts the sites and lists the helper;
    - a builtin conversion (lrintf) outside a helper is a finding of the report, not rewritten.
  lifted (a lifted-origin root: fnvnx_ftoi is required and its mode is lifted):
    - a conversion inside a helper builds, no rewrite, the report has the helper, the site, no finding;
    - a conversion outside a helper (cast), lround, and an asm with a float operand are each a build error naming the rule;
  the recipe validation: a lifted root without fnvnx_ftoi, with another mode or with a mode argument of a native root is red.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_ftoi')
HELPER = '#define F2I __attribute__((fnvnx_f2i))\nF2I int helper(float f) { return (int)f; }\n'
OK = HELPER + 'int user(float f) { return helper(f) + 1; }\n'
NATIVE = HELPER + 'int plain(float f) { return (int)f; }\nlong lr(float f) { return __builtin_lrintf(f); }\n'
BAD_CAST = HELPER + 'int bad_cast(float f) { return (int)f; }\n'
BAD_BUILTIN = HELPER + 'long bad_lround(float f) { return __builtin_lroundf(f); }\n'
BAD_ASM = HELPER + 'float bad_asm(float f) { asm volatile("" : "+w"(f)); return f; }\n'


def comp(td, name, text, native=False, plugins=()):
    src = os.path.join(td, name + '.cpp')
    with open(src, 'w') as f:
        f.write(text)
    files = {}
    r = pb.compile(src, os.path.join(td, name + '.o'), list(plugins), files=files, native=native)
    return r, os.path.join(td, name + '.o'), (json.load(open(files['unit.f2i.json'])) if 'unit.f2i.json' in files else None)


def main():
    td = tempfile.mkdtemp(prefix='test-ftoi-')
    try:
        so = pb.build('ftoi', td)
        check('the plugin builds as a recipe plugin stage against msvcmodel\'s headers', os.path.basename(so) == 'fnvnx_ftoi.so')
        # --- native
        r, obj, j = comp(td, 'nat', NATIVE, native=True)
        check('native: builds', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            d = pb.objdump(obj)
            plain = '\n'.join(d.get('_Z5plainf', []))
            helper = '\n'.join(d.get('_Z6helperf', []))
            check('native: a conversion outside a helper is _ftol (0x80000000)', '#0x80000000' in plain, plain)
            check('native: an fnvnx_f2i helper is not rewritten (bare fcvtzs)', 'fcvtzs' in helper and '#0x80000000' not in helper, helper)
            check('native: report holds the helper, 3 sites, and the builtin as the only finding',
                  j and j['format'] == 'fnvnx-f2i/1' and j['helpers'] == ['int helper(float)'] and j['sites'] == 3
                  and [(f['function'], f['kind']) for f in j['findings']] == [('long int lr(float)', 'builtin')], j)
        r, _, _ = comp(td, 'wide', 'F2I_X\n__int128 w(double d) { return (__int128)d; }\n'.replace('F2I_X\n', ''), native=True)
        check('native: double -> __int128 (a shape the rewrite does not cover) is a build error naming both types',
              r.returncode != 0 and 'FTOI' in r.stderr and 'double' in r.stderr and '__int128' in r.stderr, r.stderr[-1500:])
        # --- lifted
        r, obj, j = comp(td, 'ok', OK)
        check('lifted: a conversion inside a helper builds', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            helper = '\n'.join(pb.objdump(obj).get('_Z6helperf', []))
            check('lifted: no rewrite (the helper is the bare fcvtzs)', 'fcvtzs' in helper and '#0x80000000' not in helper, helper)
            check('lifted: report holds the helper, one site, no finding',
                  j and j['helpers'] == ['int helper(float)'] and j['sites'] == 1 and j['findings'] == [], j)
        for name, text, kind in (('cast', BAD_CAST, 'conv'), ('lround', BAD_BUILTIN, 'builtin'), ('asm', BAD_ASM, 'asm')):
            r, _, _ = comp(td, 'bad_' + name, text)
            check('lifted: %s outside a helper is a build error' % name,
                  r.returncode != 0 and 'FTOI (lifted)' in r.stderr and '(%s)' % kind in r.stderr, r.stderr[-1500:])
        # --- the recipe validation (the mode is the root's, a lifted root needs the plugin)
        for what, plug in (('without fnvnx_ftoi', []), ('with mode native', [('ftoi', {'mode': 'native'})]), ('with another argument', [('ftoi', {'mode': 'lifted', 'verbose': ''})])):
            try:
                if not plug:
                    pb._recipe(td, [('sdd', {})], {'t.cpp': 'int t();\n'}, pb.PRODUCT_FLAGS, lifted_ftoi=False)
                else:
                    pb._recipe(td, plug, {'t.cpp': 'int t();\n'}, pb.PRODUCT_FLAGS, raw=True)
                check('recipe: a lifted root %s is red' % what, False)
            except pb.recipe.RecipeError as e:
                check('recipe: a lifted root %s is red' % what, any('fnvnx_ftoi' in x for x in e.errors), e.errors)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
