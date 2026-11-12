#!/usr/bin/env python3
"""tests/s10/test_tools.py - dyninit_rewrite.py + mkxctab.py, end to end on the synthetic demo tree.

    python3 -I tests/s10/test_tools.py [--out build/s10/tools]

1. rewrite the demo tree (copy) and check the output forms, the variant copy, the report, idempotence and
   that the prepare.py rule entry gives the same text;
2. compile the units (host C++): rewritten objects define __fnvnx_dyninit_/__fnvnx_dynexit_ and carry no
   .init_array, the original unit does;
3. mkxctab.py: table from the synthetic address map + objects, coverage report against units.json;
4. link demo objects + xctab.c + startup/guest + tests/s10/demo/e2e_main.c (ASan/UBSan) and run it.
No game content: everything is synthetic.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, 'startup', 'tools'))
import dyninit_rewrite  # noqa: E402
import mkxctab  # noqa: E402

FAILS = []
CHECKS = 0


def check(cond, what):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(what)
        print('FAIL:', what)


def run(cmd, **kw):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)
    if r.returncode != 0:
        print(' '.join(cmd))
        print(r.stdout)
        raise SystemExit('command failed (%d)' % r.returncode)
    return r.stdout


def make_tree(dst):
    """The synthetic decomp tree: sources from demo/tree, build/units.json from demo/units.json."""
    shutil.copytree(os.path.join(HERE, 'demo', 'tree'), dst)
    os.makedirs(os.path.join(dst, 'build'))
    shutil.copyfile(os.path.join(HERE, 'demo', 'units.json'), os.path.join(dst, 'build', 'units.json'))


def test_split_decl():
    sd = dyninit_rewrite.split_decl
    check(sd('Logger g(1);') == ('Logger', 'g', '1'), 'split: T x(a)')
    check(sd('SettingT<GameSettingCollection> s(k, d);') == ('SettingT<GameSettingCollection>', 's', 'k, d'),
          'split: template type')
    check(sd('Agg6 g(&d, FUN_1(), (const void*)8);') == ('Agg6', 'g', '&d, FUN_1(), (const void*)8'),
          'split: nested parens')
    check(sd('FNVNX_KEEP Plain g;') == ('Plain', 'g', None), 'split: KEEP1 prefix, T x;')
    check(sd('ns::T g;') == ('ns::T', 'g', None), 'split: qualified type')
    for bad in ('T g = h;', 'T g{1};', 'T g[2];', 'static T g;', 'T g();', 'T g(1)', 'g;', 'void (*fp)(int);'):
        check(sd(bad) is None, 'split rejects %r' % bad)


def test_rewrite(tree):
    index = dyninit_rewrite.Index.load(os.path.join(tree, 'build', 'units.json'))
    report = []
    run([sys.executable, '-I', os.path.join(ROOT, 'startup', 'tools', 'dyninit_rewrite.py'), '--units',
         os.path.join(tree, 'build', 'units.json'), '--tree', tree, '--report', os.path.join(tree, 'rewrite.tsv')])
    a = open(os.path.join(tree, 'src/generated/demo_a.cpp')).read()
    b = open(os.path.join(tree, 'src/generated/demo_b.cpp')).read()
    v = open(os.path.join(tree, 'src/generated/demo_a_variant.cpp')).read()
    check(a.startswith(dyninit_rewrite.MARKER), 'prologue marker')
    check('extern Logger g_a1;' in a and 'Logger g_a1(1);' not in a, 'object becomes extern')
    check('extern "C" void __fnvnx_dyninit_00f40010(void) { ::new ((fnvnx_dyn_tag *)(void *)__builtin_addressof(g_a1)) '
          'Logger(1); atexit(__fnvnx_dynexit_00fc8010); }' in a, 'dyninit with exit registration')
    check('extern "C" void __fnvnx_dynexit_00fc8010(void) { fnvnx_dyn_destroy(g_a1); }' in a, 'dynexit')
    check('__builtin_addressof(g_a2)) Plain; }' in a and 'dynexit_' not in a.split('g_a2')[1].split('\n')[2],
          'T x; form, no exit for a trivial destructor')
    check('// @fnv 0x00fc8030\nextern "C" void __fnvnx_dynexit_00fc8030' in a and
          '// @fnv 0x00f40050\nextern "C" void __fnvnx_dyninit_00f40050' in a, 'tags precede their functions')
    check('void InitializeRuntimeDemo1()' in b and '__fnvnx_dyninit_00f40020' in b, 'plain initializer untouched')
    check('extern Logger g_a1;' in v and '__fnvnx_dyninit' not in v and '@fnv 0x00f40010' not in v and
          '@fnv 0x00500000' in v, 'variant copy: extern only, foreign tags neutralized, own tag kept')
    check(v.count('\n') == open(os.path.join(HERE, 'demo/tree/src/generated/demo_a_variant.cpp')).read().count('\n')
          + dyninit_rewrite.PROLOGUE.count('\n'), 'variant keeps its line count')
    rows = [l.split('\t') for l in open(os.path.join(tree, 'rewrite.tsv')) if not l.startswith('#')]
    actions = Counter(r[5].strip().split(' (')[0] for r in rows)
    check(actions == Counter({'init + exit': 3, 'init': 1, 'extern': 1}), 'report actions %s' % dict(actions))
    # idempotent
    again = dyninit_rewrite.rewrite('src/generated/demo_a.cpp', a, index, Counter(), report)
    check(again == a, 'idempotent')
    # the prepare.py rule entry finds build/units.json above the source and gives the same text
    pristine_tree = os.path.join(os.path.dirname(tree), 'pristine')
    make_tree(pristine_tree)
    pristine = os.path.join(pristine_tree, 'src', 'generated', 'demo_a.cpp')
    stats = Counter()
    via_rule = dyninit_rewrite.r_dyninit(pristine, open(pristine).read(), stats)
    check(via_rule == a and stats['XC1'] == 3, 'prepare rule == CLI (XC1 %d)' % stats['XC1'])


def compile_units(tree, out, cxx):
    objs = {}
    for name in ('demo_a', 'demo_b', 'demo_a_variant'):
        o = os.path.join(out, 'src_generated_%s.cpp.o' % name)
        run([cxx, '-std=gnu++17', '-O2', '-c', '-fno-exceptions', '-fno-rtti', '-Wall', '-Werror', '-I',
             os.path.join(tree, 'include'), os.path.join(tree, 'src', 'generated', name + '.cpp'), '-o', o])
        objs[name] = o
    orig = os.path.join(out, 'orig_demo_a.o')
    run([cxx, '-std=gnu++17', '-O2', '-c', '-I', os.path.join(HERE, 'demo', 'tree', 'include'),
         os.path.join(HERE, 'demo', 'tree', 'src', 'generated', 'demo_a.cpp'), '-o', orig])
    secs, _ = mkxctab.elf_sections_and_symbols(orig)
    check(any(s.startswith('.init_array') for s in secs), 'the unrewritten unit has .init_array (GCC per-unit init)')
    for name, o in objs.items():
        secs, syms = mkxctab.elf_sections_and_symbols(o)
        check(not any(s.startswith('.init_array') for s in secs), '%s: no .init_array after the rewrite' % name)
        g = mkxctab.defined_globals(syms)
        want = {'demo_a': {'__fnvnx_dyninit_00f40010', '__fnvnx_dyninit_00f40030', '__fnvnx_dyninit_00f40050',
                           '__fnvnx_dynexit_00fc8010', '__fnvnx_dynexit_00fc8030'},
                'demo_b': {'__fnvnx_dyninit_00f40020', '__fnvnx_dynexit_00fc8020'},
                'demo_a_variant': set()}[name]
        got = {n for n in g if n.startswith('__fnvnx_dyn')}
        check(got == want, '%s defines %s' % (name, sorted(got)))
        check(not ({'g_a1', 'g_a2', 'g_a3', 'g_b1'} & set(g)), '%s defines no object storage' % name)
    return objs


def test_mkxctab(out, objs):
    tsv = os.path.join(out, 'objects.tsv')
    with open(tsv, 'w') as f:
        for name, o in objs.items():
            f.write('src/generated/%s.cpp\t%s\tok\n' % (name, o))
        f.write('src/generated/demo_c.cpp\t%s\tfail\n' % os.path.join(out, 'missing.o'))
    xctab = os.path.join(out, 'xctab.c')
    log = run([sys.executable, '-I', os.path.join(ROOT, 'startup', 'tools', 'mkxctab.py'), '--out', xctab,
               '--tsv', os.path.join(out, 'xctab.tsv'), '--addrmap', os.path.join(HERE, 'demo', 'addrmap.tsv'),
               '--objects', tsv, '--units', os.path.join(HERE, 'demo', 'units.json'),
               '--group', '0x00f40000:0x00f40080', '--strict'])
    print(log, end='')
    check('5 natives (1 plain from the address map, 4 dyninit from 2 objects; 0 in both)' in log, 'mkxctab counts')
    check('decomp defines 6 functions in the group; 5 have a native, 1 do not' in log, 'coverage')
    check('1 without native  src/generated/demo_c.cpp' in log, 'missing unit named')
    check('2 byte ranges of the group defined by no unit (32 bytes)' in log, 'holes')
    text = open(xctab).read()
    order = [l.split()[1] for l in text.split('\n') if l.startswith('    { 0x')]
    check(order == ['0x00f40010u,', '0x00f40020u,', '0x00f40030u,', '0x00f40040u,', '0x00f40050u,'],
          'table sorted by address: %s' % order)
    check('{ 0x00f40040u, __fnv_00f40040 }' in text and 'fnvnx_xc_group_hi = 0x00f40080u' in text, 'table text')
    # duplicate natives are an error with --strict
    dup = os.path.join(out, 'dup.o')
    shutil.copyfile(objs['demo_b'], dup)
    r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'startup', 'tools', 'mkxctab.py'), '--out',
                        os.path.join(out, 'dup.c'), '--strict', objs['demo_b'], dup], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    check(r.returncode == 1 and 'defined by' in r.stdout, 'duplicate dyninit detected')
    return xctab


def test_e2e(out, objs, xctab, cc, cxx):
    san = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-omit-frame-pointer', '-g']
    inc = ['-I', os.path.join(ROOT, 'startup', 'guest')]
    cobjs = []
    for src in (xctab, os.path.join(ROOT, 'startup/guest/xc.c'),
                os.path.join(HERE, 'demo/storage.c'), os.path.join(HERE, 'demo/e2e_main.c')):
        o = os.path.join(out, os.path.basename(src) + '.e2e.o')
        run([cc, '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror'] + san + inc + ['-c', src, '-o', o])
        cobjs.append(o)
    exe = os.path.join(out, 'e2e')
    run([cxx] + san + cobjs + list(objs.values()) + ['-o', exe])
    res = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(res.stdout, end='')
    check(res.returncode == 0 and 's10 e2e: PASS' in res.stdout, 'e2e run')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'build', 's10', 'tools'))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    tree = os.path.join(out, 'tree')
    make_tree(tree)
    cc, cxx = os.environ.get('CC', 'cc'), os.environ.get('CXX', 'c++')
    test_split_decl()
    test_rewrite(tree)
    objs = compile_units(tree, out, cxx)
    xctab = test_mkxctab(out, objs)
    test_e2e(out, objs, xctab, cc, cxx)
    print('test_tools: %d checks, %d failed' % (CHECKS, len(FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
