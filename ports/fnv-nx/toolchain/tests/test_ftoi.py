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
BAD_MD = HELPER + '#include <arm_neon.h>\nint32x4_t bad_md(float32x4_t v) { return vcvtq_s32_f32(v); }\n'
BAD_ASM = HELPER + 'float bad_asm(float f) { asm volatile("" : "+w"(f)); return f; }\n'


def comp(td, name, text, native=False, plugins=()):
    src = os.path.join(td, name + '.cpp')
    with open(src, 'w') as f:
        f.write(text)
    files = {}
    r = pb.compile(src, os.path.join(td, name + '.o'), list(plugins), files=files, native=native)
    j = json.load(open(files['unit.f2i.json'])) if 'unit.f2i.json' in files else None
    if j is not None:   # the format's identity (what gate fpexact reads): every site is rewritten, ranged, in a helper or a finding; a rewrite list is as long as the count
        check('%s: report is fnvnx-f2i/2 and sites == rewritten + ranged + helper + findings, rewrites as long as rewritten' % name,
              j['format'] == 'fnvnx-f2i/2' and j['sites'] == j['rewritten'] + len(j['ranged']) + j['helper'] + len(j['findings']) and len(j['rewrites']) == j['rewritten']
              and all(f['kind'] in ('conv-scalar', 'conv-vector', 'builtin', 'mdbuiltin', 'asm') for f in j['rewrites'] + j['findings']), json.dumps(j)[:600])
    return r, os.path.join(td, name + '.o'), j


NEON = open(os.path.join(HERE, 'ftoi_values', 'fixture_neon.cpp')).read()   # the class's fixture, also the boot machine's value test (ftoi_values/gen_values.py)


def _ld_binary64():
    """the expectation for a long double conversion comes from the declared data model (toolchain/msvcmodel/DATAMODEL.json, row S09), not from a constant"""
    with open(os.path.join(HERE, '..', 'msvcmodel', 'DATAMODEL.json')) as f:
        rows = json.load(f)
    rows = rows if isinstance(rows, list) else rows['rows']
    row = next(r for r in rows if r['id'] == 'S09')
    return 'binary64' in row['property']


LD_BINARY64 = _ld_binary64()


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
            check('native: report holds the helper, 3 sites, the scalar conversion (msvc/ftol) and the builtin (normal/current) rewritten and no finding',
                  j and j['format'] == 'fnvnx-f2i/2' and j['helpers'] == ['int helper(float)'] and j['sites'] == 3 and j['rewritten'] == 2 and j['helper'] == 1 and j['ranged'] == [] and j['findings'] == []
                  and [(f['function'], f['kind'], f['how']) for f in j['rewrites']] == [('int plain(float)', 'conv-scalar', 'msvc/ftol'), ('long int lr(float)', 'builtin', 'normal/current')], j)
        r, _, j = comp(td, 'nat_md', BAD_MD, native=True)
        check('native: a target float->int builtin (vcvtq_s32_f32) is rewritten, kind mdbuiltin, no finding',
              r.returncode == 0 and j and j['findings'] == [] and [f['kind'] for f in j['rewrites']] == ['mdbuiltin'] and j['rewrites'][0]['how'] == 'md/trunc', (r.stderr[-800:], j))
        r, _, _ = comp(td, 'nat_jcvt', 'int j(double d) { return __builtin_aarch64_jcvtzs(d); }\n', native=True)
        check('native: a target builtin that converts with no rounding in GCC\'s table (jcvtzs) is a build error, never passed through',
              r.returncode != 0 and 'FTOI' in r.stderr and 'table' in r.stderr, r.stderr[-800:])
        for name, text, tys in (
                ('mixed', 'typedef float v4sf __attribute__((vector_size(16)));\ntypedef long long v4di __attribute__((vector_size(32)));\n'
                          'v4di cv(v4sf v) { return __builtin_convertvector(v, v4di); }\n', ('v4sf', 'v4di')),
                ('ld', 'long long ld(long double x) { return (long long)x; }\n', ('long double', 'long long')),
                ('i128', '__int128 w(double d) { return (__int128)d; }\n', ('double', '__int128'))):
            r, _, _ = comp(td, 'wide_' + name, text, native=True)
            if name == 'ld' and LD_BINARY64:   # DATAMODEL S09: long double is binary64 (MLIB1), so a long double conversion is a double conversion and the rewrite covers it
                check('native: a long double conversion is a double conversion (DATAMODEL S09: binary64) and builds', r.returncode == 0, r.stderr[-1500:])
                continue
            check('native: a conversion the rewrite does not cover (%s) is a build error naming both types' % name,
                  r.returncode != 0 and 'FTOI' in r.stderr and all(t in r.stderr for t in tys), r.stderr[-1500:])
        # --- every float -> integer conversion C does not fully specify: x86's answer in range, the integer indefinite out of it (RULE in the plugin)
        r, nobj, j = comp(td, 'neon', NEON, native=True)
        check('native: the conversion class builds with no finding and every site is in the report as rewritten',
              r.returncode == 0 and j and j['findings'] == [] and len(j['rewrites']) > 40, (r.stderr[-1500:], j and j['findings']))
        if r.returncode == 0:
            # RULE check on the real object, no run: every fixture function carries the in-range test and the select of the rule (a compare and a select
            # instruction), so no site is left as the bare machine conversion
            d = pb.objdump(nobj)
            bare = [fn for fn, body in sorted(d.items()) if not fn.startswith('_Z') and not fn.startswith('.') and not pb.rule_form(body)]
            check('native: every function of the fixture has a compare and a select (no bare conversion left)', not bare, bare[:10])
            ng = len([1 for l in open(os.path.join(HERE, 'ftoi_values', 'fixture_neon.cpp')) if l.startswith('extern "C"')])
            check('native: the sites of the fixture are all rewritten (rewrites cover every extern "C" fixture function)',
                  len({f['function'] for f in j['rewrites']}) >= ng, (ng, len({f['function'] for f in j['rewrites']})))
            # the value test for the boot machine: the generated drivers compile through the recipe
            for kind in ('neon', 'sse2neon'):
                if kind == 'sse2neon':
                    continue   # needs the compat root (test_ftoi_sse2neon.py)
                drv = os.path.join(td, 'values_%s.cpp' % kind)
                subprocess.run([sys.executable, '-I', os.path.join(HERE, 'ftoi_values', 'gen_values.py'), kind, drv], check=True)
                rr, _, _ = comp(td, 'values_' + kind, open(drv).read(), native=True)
                check('native: the generated value driver (%s) builds' % kind, rr.returncode == 0, rr.stderr[-800:])
        # --- lifted
        r, obj, j = comp(td, 'ok', OK)
        check('lifted: a conversion inside a helper builds', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            helper = '\n'.join(pb.objdump(obj).get('_Z6helperf', []))
            check('lifted: no rewrite (the helper is the bare fcvtzs)', 'fcvtzs' in helper and '#0x80000000' not in helper, helper)
            check('lifted: report holds the helper, one site, no finding',
                  j and j['helpers'] == ['int helper(float)'] and j['sites'] == 1 and j['helper'] == 1 and j['rewritten'] == 0 and j['findings'] == [], j)
        for name, text, kind in (('cast', BAD_CAST, 'conv'), ('lround', BAD_BUILTIN, 'builtin'), ('target builtin', BAD_MD, 'mdbuiltin'), ('asm', BAD_ASM, 'asm')):
            r, _, _ = comp(td, 'bad_' + name, text)
            check('lifted: %s outside a helper is a build error' % name,
                  r.returncode != 0 and 'FTOI (lifted)' in r.stderr and '(%s)' % kind in r.stderr, r.stderr[-1500:])
        # --- the recipe validation (the mode is the root's, a lifted root needs the plugin)
        for what, plug in (('without fnvnx_ftoi', []), ('with mode native', [('ftoi', {'mode': 'native'})]), ('with another argument', [('ftoi', {'mode': 'lifted', 'verbose': ''})])):
            try:
                if not plug:
                    pb._recipe(td, [('sdd', {})], {'t.cpp': 'int t();\n'}, pb.PRODUCT_FLAGS, raw=True)
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
