#!/usr/bin/env python3
"""S11 host tests for tools/equiv/equiv.py (no compiler needed).  test_equiv.py <equiv.py> <scratch dir>"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

EQ, OUT = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('equiv', EQ)
eq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eq)
fails = []


def check(cond, what):
    if not cond:
        fails.append(what)
        print('FAIL', what)


# 1. every prepare rule (core, stream modules, NATIVE1) has an id and a class; ids and function names are unique
table = eq.rule_table()
ids = [r[0] for r in table]
check(all(r[3] in ('neutral', 'layout', 'platform') for r in table),
      'unclassified rules: %s (add them to CLASSES in tools/equiv/equiv.py and docs/EQUIVALENCE.md)'
      % [r[0] for r in table if r[3] not in ('neutral', 'layout', 'platform')])
check(len(set(ids)) == len(ids), 'duplicate rule ids')
check(len({r[1] for r in table}) == len(table), 'duplicate rule function names (FNVNX_PREPARE_SKIP selects by name)')
check(not set(eq.CLASSES) - set(ids), 'classified ids that no rule has: %s' % sorted(set(eq.CLASSES) - set(ids)))
doc = (EQ.parent.parent.parent / 'docs' / 'EQUIVALENCE.md').read_text()
check(all('`%s`' % i in doc for i in ids), 'rules missing from docs/EQUIVALENCE.md: %s'
      % [i for i in ids if '`%s`' % i not in doc])

# 2. allow-list specs
neutral = eq.resolve_spec('neutral')
check('EXC1' in neutral and 'TAIL1' not in neutral and 'SSE1' not in neutral, 'neutral class')
check(eq.resolve_spec('neutral,-EXC1') == neutral - {'EXC1'}, 'spec subtraction')
check(eq.resolve_spec('TAIL1,CS28') == {'TAIL1', 'CS28'}, 'spec ids')
check(eq.resolve_spec('all') == set(ids), 'spec all')

# 3. linkage-equivalent names of global variables (LINK1)
check(eq.var_key('_DAT_00fdf000') == 'DAT_00fdf000', 'C name')
check(eq.var_key('?DAT_00fdf000@@3HA') == 'DAT_00fdf000', 'C++ global variable')
check(eq.var_key('?f@@YAXXZ') is None, 'C++ function is not a variable')
check(eq.var_key('?m@A@@2HA') is None, 'static member is not a global variable')
names = {'_DAT_00fdf000': 0xfdf000, '?g@@3PAXA': 0x1000, '__imp__CreateFileA@28': 0xfdf010}
r = eq.Resolver([], names)
check(r.by_key['DAT_00fdf000'] == {0xfdf000} and r.by_key['g'] == {0x1000}, 'resolver index: variables')
check(r.by_key[('imp', 'CreateFileA')] == {0xfdf010}, 'resolver index: imports')
check(eq.import_key('__imp_?CreateFileA@@YGPAXPBDKKPAXKK0@Z') == ('imp', 'CreateFileA'), 'C++ import decoration')
check(eq.key_free('?f@@YAHPAUS@@@Z') == eq.key_free('?f@@YAHPAVS@@@Z'), 'class key U/V')
check(eq.SG_LITERAL.match('$SG12345') and not eq.SG_LITERAL.match('??_C@_03ABC@x?$AA@'), 'numbered literals')

# 4. /d1reportAllClassLayout parsing
text = ('class A\tsize(8):\n\t+---\n 0\t| {vfptr}\n 4\t| x\n\t+---\n\n'
        'A::$vftable@:\n\t| &A_meta\n\n'
        'class B\tsize(12):\n\t+---\n\t| +--- (base class A)\n 0\t| | {vfptr}\n 4\t| | x\n\t| +---\n 8\t| y\n\t+---\n\n')
lay = eq.parse_layouts(text)
check(sorted(lay) == ['A', 'B'] and lay['A'][0] == 8 and lay['B'][0] == 12, 'layout parse: %s' % lay)

# 5. prepare with an allow-list on a synthetic tree: only EXC1 may edit
src = OUT / 'mini'
(src / 'src').mkdir(parents=True)
(src / 'include').mkdir()
(src / 'build').mkdir()
(src / 'build' / 'units.json').write_text(json.dumps(dict(units=[])))
(src / 'src' / 'a.cpp').write_text('void f() throw();\nextern "C" int DAT_00fdf000;\n// @fnv 0x00401000\nint g() { return 1; }\n')
r = subprocess.run([sys.executable, str(EQ), 'prepare', str(src), str(OUT / 'mini_out'), '--rules', 'EXC1'],
                   capture_output=True, text=True)
check(r.returncode == 0, 'prepare --rules EXC1: %s' % r.stderr[-500:])
if r.returncode == 0:
    a = (OUT / 'mini_out' / 'src' / 'a.cpp').read_text()
    check('throw()' not in a and 'extern "C" int DAT' in a and 'FNVNX_KEEP' not in a, 'only EXC1 applied: %r' % a)
    check((OUT / 'mini_out' / 'equiv_rules.txt').read_text().strip() == 'EXC1', 'equiv_rules.txt')

print('test_equiv: %d failures' % len(fails))
sys.exit(1 if fails else 0)
