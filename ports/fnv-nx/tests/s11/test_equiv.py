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
# a stream module's POST hook edits the prepared tree too: the gate must know which rule it is (a hook it does not
# know runs always and fails `equiv.py prepare`'s allow-list check, as BIND1 did on every farm)
hooks = {h.__name__ for h in eq.load_prepare().STREAM_POST}
check(hooks <= set(eq.POST_RULES), 'POST hooks without a rule in POST_RULES (tools/equiv/equiv.py): %s'
      % sorted(hooks - set(eq.POST_RULES)))
check(len({r[1] for r in table}) == len(table), 'duplicate rule function names (FNVNX_PREPARE_SKIP selects by name)')
check(not set(eq.CLASSES) - set(ids), 'classified ids that no rule has: %s' % sorted(set(eq.CLASSES) - set(ids)))
doc = (EQ.parent.parent.parent / 'docs' / 'EQUIVALENCE.md').read_text()
check(all('`%s`' % i in doc for i in ids), 'rules missing from docs/EQUIVALENCE.md: %s'
      % [i for i in ids if '`%s`' % i not in doc])

# 2. allow-list specs
neutral = eq.resolve_spec('neutral')
check('EXC1' in neutral and 'TAIL1' not in neutral and 'SSE1' not in neutral, 'neutral class')
check(eq.resolve_spec('neutral,-EXC1') == neutral - {'EXC1'}, 'spec subtraction')
check(eq.resolve_spec('TAIL1,SDK1') == {'TAIL1', 'SDK1'}, 'spec ids')
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

# 6. merge_reports: two shard reports and a FAILED one sum up, per rule and per kind
spec = importlib.util.spec_from_file_location('merge_reports', EQ.parent / 'merge_reports.py')
mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
shard = '''# MSVC equivalence gate - abc123 shard %d/3

- units: %d compared; %d identical, %d differ, %d prepared side does not compile, 0 pristine does not compile
- functions: 50 compared, 3 differ (EH-only 2, retarget 0, code 1)

| rule | eh | retarget | code | no-build |
|---|---:|---:|---:|---:|
| EXC1 | 2 | 0 | 0 | 0 |
| ENUM1 | 0 | 0 | 1 | %d |

## Differing functions (unit, address, kind, rule)

```
src/s%d.cpp  0x00401000  eh  EXC1
```

## Units the prepared side does not compile (unit, rule, first error)

```
%s```
'''
r1 = mr.parse(shard % (1, 4, 2, 1, 1, 1, 1, 'src/x.cpp  ENUM1  C2059 syntax error\n'), 'eq1')
r2 = mr.parse(shard % (2, 3, 2, 1, 0, 0, 2, ''), 'eq2')
r3 = mr.parse('# equiv abc123 FAILED (rc 1)\n\n```\nT start equiv 3 3 at abc123\nFileNotFoundError: x\n```\n', 'eq3')
check(r1['shard'] == 1 and r1['units']['no-build'] == 1 and r1['table']['ENUM1']['no-build'] == 1
      and len(r1['nobuild']) == 1 and r3['failed'] and r3['shard'] == 3, 'merge_reports parse: %s %s' % (r1, r3))
m = mr.merge([r1, r2, r3])
check('(2 of 3 shards)' in m and '- units: 7 compared; 4 identical, 2 differ, 1 prepared' in m
      and '| EXC1 | 4 | 0 | 0 | 0 | 4 |' in m and '| ENUM1 | 0 | 0 | 2 | 1 | 3 |' in m
      and 'shards without a report: 3' in m and 'eq3: FAILED - `FileNotFoundError: x`' in m, 'merge_reports: %s' % m)

# 7. DECL1's fnvnx_cmath/ headers reach MSVC (prepared trees get /I cmath_dir()): the include root holds exactly the
#    shim's headers, byte for byte, and nothing else of shim/include; each has the MSVC declaration
os.environ['XDG_CACHE_HOME'] = str(OUT / 'cache')
shim = eq.CMATH
names = sorted(f.name for f in shim.glob('*.h'))
check(len(names) > 20, 'shim/include/fnvnx_cmath: %d headers' % len(names))
d = eq.cmath_dir()
(d / 'fnvnx_cmath' / 'stale.h').write_text('x')
(d / 'fnvnx_cmath' / names[0]).write_text('old')
d = eq.cmath_dir()
check([p.name for p in d.iterdir()] == ['fnvnx_cmath'], 'cmath root holds only fnvnx_cmath/: %s' % list(d.iterdir()))
check(sorted(f.name for f in (d / 'fnvnx_cmath').iterdir()) == names
      and all((d / 'fnvnx_cmath' / n).read_bytes() == (shim / n).read_bytes() for n in names), 'cmath copy in sync')
for n in names:
    t = (shim / n).read_text()
    if not ('defined(_MSC_VER)' in t and '__cdecl %s(' % n[:-2] in t):
        check(False, 'fnvnx_cmath/%s: no MSVC (_MSC_VER, __cdecl) declaration' % n)

print('test_equiv: %d failures' % len(fails))
sys.exit(1 if fails else 0)
