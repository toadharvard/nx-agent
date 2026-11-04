#!/usr/bin/env python3
"""S11 tests for tools/equiv/explain.py (the gate's unexplained-difference metric), on synthetic shard reports."""
import importlib.util
from pathlib import Path
import sys
import tempfile

PORT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PORT / 'tools/equiv'))
spec = importlib.util.spec_from_file_location('explain', PORT / 'tools/equiv/explain.py')
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)
fails = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        fails.append(what)


REPORT = """# MSVC equivalence gate - 0123456789ab shard 1/2

## Differing functions (unit, address, kind, rule)

```
src/a.cpp  0x00401000  eh  EXC1
src/a.cpp  0x00401010  code  CTOR1
src/b.cpp  0x00402000  code  PTR1
src/b.cpp  0x00402010  retarget  (interaction)
src/s1b.cpp  0x00403000  code  (interaction)
```

## Units the prepared side does not compile (unit, rule, first error)

```
src/c.cpp  DECL1  C2039 'abs' : is not a member of 'std'
src/d.cpp  ENUM1  C2059 syntax error : ';'
src/e.cpp  DECL1  C2143 syntax error : missing ';' before '*'
```
"""
TABLE = '\n'.join(['# comment', '\t'.join(('EH-ONLY', 'fn', 'eh', '*', '*', '*', '*', 'r')),
                   '\t'.join(('NULLCHECK', 'fn', 'code', 'CTOR1|NEW2', r'src/a\.cpp', '0x00401010', '*', 'r')),
                   '\t'.join(('PTR-AT', 'fn', 'code', 'PTR1', r'src/b\.cpp', '0x00402000', '*', 'r')),
                   '\t'.join(('STD-ABS', 'unit', '*', '*', '*', '*', "is not a member of 'std'", 'r')),
                   '\t'.join(('ENUM', 'unit', '*', 'ENUM1', 'src/.*', '*', 'C2059', 'r'))]) + '\n'
PATCHES = """# x

## By-design MSVC differences (S11 gate)

| Rule | Source | Function | Difference |
|---|---|---|---|
| XINC1 | `src/s1b.cpp` | 0x00403000 | ecx reload |

## Next
| XINC1 | `src/b.cpp` | 0x00402010 | not in the section |
"""
with tempfile.TemporaryDirectory() as d:
    Path(d, 't.tsv').write_text(TABLE)
    Path(d, 'p.md').write_text(PATCHES)
    table, bd = ex.load_table(Path(d, 't.tsv')), ex.load_bydesign(Path(d, 'p.md'))
    check(len(table) == 5 and bd == {('src/s1b.cpp', '0x00403000')}, 'table and PATCHES.md by-design section load')
    for wide in (('W', 'fn', 'code', 'STATIC1', '*', '*', '*', 'r'), ('W', 'fn', 'retarget', 'sdk', '*', '*', '*', 'r'),
                 ('W', 'unit', '*', 'DECL1', '*', '*', '*', 'r')):
        Path(d, 'w.tsv').write_text('\t'.join(wide) + '\n')
        try:
            ex.load_table(Path(d, 'w.tsv'))
            check(False, 'a rule-wide %s row is refused (V1 E2)' % wide[2])
        except SystemExit:
            check(True, 'a rule-wide %s row is refused (V1 E2)' % wide[2])
    Path(d, 'bad.tsv').write_text('A\tfn\teh\n')
    try:
        ex.load_table(Path(d, 'bad.tsv'))
        check(False, 'a short row is refused')
    except SystemExit:
        check(True, 'a short row is refused')
r = ex.merge_reports.parse(REPORT, 'r.md')
fns, units = ex.explain([r, r], table, bd)
cls = {(f['unit'], f['addr']): f['cls'] for f in fns}
check(len(fns) == 5 and len(units) == 3, 'duplicates across reports counted once')
check(cls[('src/a.cpp', '0x00401000')] == 'EH-ONLY' and cls[('src/a.cpp', '0x00401010')] == 'NULLCHECK',
      'kind and rule alternatives match')
check(cls[('src/b.cpp', '0x00402000')] == 'PTR-AT' and cls[('src/b.cpp', '0x00402010')] is None,
      'unit and address must both match; an interaction stays unexplained')
check(cls[('src/s1b.cpp', '0x00403000')] == 'S1B-BYDESIGN', 'S1b by-design site, whatever the attributed rule')
ucls = {u['unit']: u['cls'] for u in units}
check(ucls == {'src/c.cpp': 'STD-ABS', 'src/d.cpp': 'ENUM', 'src/e.cpp': None}, 'no-build units by first error')
m = ex.metric(fns, units)
check((m['unexplained_functions'], m['unexplained_units']) == (1, 1), 'metric 1 function, 1 unit')
text = ex.render(fns, units, listing=True)
check('unexplained: 1 functions, 1 no-build units (of 5 functions, 3 units)' in text and '## Explained' in text,
      'report text')
# retargets by their relocation pairs (## Retargets): every pair must be a known wrapper, whatever the rule
RT = (REPORT + """
## Retargets (unit, address, pristine binding>prepared binding of each relocation that moved)

```
src/b.cpp  0x00402010  0x00406d30>_strcpy_s,0x0043b9d0>?strchr@@YAPADPADH@Z
src/x.cpp  0x00405000  0x00406d30>_strcpy_s,0x00999999>_other
```
""").replace('src/s1b.cpp  0x00403000  code  (interaction)', 'src/x.cpp  0x00405000  retarget  DECL1')
real = ex.load_table()
# V1 E2: new differences in a unit no verdict looked at stay unexplained, whatever rule the gate names
NEW = REPORT.split('## Differing')[0] + '## Differing functions (unit, address, kind, rule)\n\n```\n' + '\n'.join([
    'src/zz/new_unit.cpp  0x00abc000  code  STATIC1', 'src/zz/new_unit.cpp  0x00abc100  code  CTOR1',
    'src/zz/new_unit.cpp  0x00abc200  code  NEW4', 'src/zz/new_unit.cpp  0x00abc300  retarget  sdk']) + '\n```\n'
fz, _ = ex.explain([ex.merge_reports.parse(NEW, 'n.md')], real, set())
check(len(fz) == 4 and all(f['cls'] is None for f in fz), 'explained.tsv explains no unread difference of a rule')
# V1 E1: a FAILED or missing shard marks the metric incomplete
two = ex.merge_reports.parse(REPORT.replace('shard 1/2', 'shard 2/2'), 'r2.md')
failed = ex.merge_reports.parse('# equiv 0123456789ab FAILED\n\n```\nstart equiv 2 2 at x\nkilled\n```\n', 'r2.md')
ok2 = ex.metric(*ex.explain([r, two], table, bd), reports=[r, two])
bad2 = ex.metric(*ex.explain([r, failed], table, bd), reports=[r, failed])
alone = ex.metric(*ex.explain([r], table, bd), reports=[r])
check(ok2['complete'] and not bad2['complete'] and not alone['complete'] and 'FAILED' in ' '.join(bad2['shard_problems'])
      and 'shard 2/2 missing' in alone['shard_problems'], 'FAILED / missing shard -> INCOMPLETE')
fh, uh = ex.explain([r, failed], table, bd)
check('INCOMPLETE' in ex.render(fh, uh, reports=[r, failed]), 'the report text says INCOMPLETE')
fns2, _ = ex.explain([ex.merge_reports.parse(RT, 'rt.md')], real, set())
c2 = {(f['unit'], f['addr']): f['cls'] for f in fns2}
check(c2[('src/b.cpp', '0x00402010')] == 'CRT-WRAPPER', 'an interaction retarget whose pairs are all wrappers')
check(c2[('src/x.cpp', '0x00405000')] is None, 'one unknown pair leaves a DECL1 retarget unexplained')
sys.path.insert(0, str(PORT / 'tools/equiv'))
import equiv
check(equiv.literal_bytes(b'\0') == equiv.literal_bytes(b'\0\0\0') == b'\0' and
      equiv.literal_bytes(b'ab\0\0') == b'ab\0', 'literal digest ignores the zero padding before the next symbol')
check(equiv.retarget_pairs([[4, 'a', '0x00406d30', '_strcpy_s', None], [9, 'a', '0x00406d30', '_strcpy_s', None],
                            [12, 'f', '0x0043b9d0', 'g', equiv.hexa_local]]) == '0x00406d30>_strcpy_s,0x0043b9d0>g',
      'retarget pairs: distinct, sorted, a local definition by name')

check(all(x['reason'] and x['cls'].upper() == x['cls'] for x in real), 'explained.tsv: every row has a class and a reason')
print('explain: %d failed' % len(fails))
sys.exit(1 if fails else 0)
