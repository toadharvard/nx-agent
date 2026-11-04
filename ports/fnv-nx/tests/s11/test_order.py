#!/usr/bin/env python3
"""S11 tests for tools/equiv/order_check.py (S12's ORDER-OBJECT / ORDER-OPERATOR check).

    test_order.py <scratch> [--gcc]

Without a compiler: the recorded MSVC results (tests/s11/order/order.msvc) cover every case of order.cpp in every
MSVC configuration, and the verdict logic (same / differs / msvc-varies) works on synthetic GCC results. With --gcc
(aarch64-linux-gnu-g++, the plugin headers, qemu-aarch64): the GCC side builds and every case prints a log.
"""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
spec = importlib.util.spec_from_file_location('order_check', PORT / 'tools/equiv/order_check.py')
oc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oc)
fails = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        fails.append(what)


cases = oc.cases()
names = [c for c, _ in cases]
check(len(cases) >= 25 and len(set(names)) == len(names), 'order.cpp: %d distinct cases' % len(cases))
check({k for _, k in cases} >= {'ORDER-OBJECT', 'ORDER-OPERATOR', 'ORDER-ARGS'}, 'order.cpp covers the S12 classes')
rec = json.loads(oc.RECORD.read_text())
check(rec.get('format') == oc.FORMAT, 'order.msvc format %s' % rec.get('format'))
msvc = rec['msvc']
check(set(msvc) == {'%s-%s' % (c, t) for c, t, _ in oc.MSVC_CONFIGS}, 'order.msvc has every MSVC configuration')
for cfg, v in msvc.items():
    missing = [n for n in names if n not in v['results']]
    check(not missing, 'order.msvc %s: every case has a log (missing %s)' % (cfg, missing[:3]))
# MSVC's x86 argument order is the calibration: right to left, as tests/s12/order/args.expect says
check(all(v['results'].get('args-member') == 'ba:12' for v in msvc.values()), 'MSVC: arguments right to left')
check(oc.parse('a ao:1\r\nb  bad line\nc \n') == {'a': 'ao:1', 'c': ''}, 'parse: one case per line, CR stripped')

# verdicts on synthetic GCC results
same = {'build': dict(results={n: next(iter(msvc.values()))['results'][n] for n in names})}
rows = {r['case']: r for r in oc.compare(cases, msvc, same)}
check(all(r['verdict'] in ('same', 'msvc-varies') for r in rows.values()), 'GCC = MSVC sp1-O2: no case differs')
varies = [n for n, r in rows.items() if r['verdict'] == 'msvc-varies']
check(all(rows[n]['gcc_matches'] for n in varies), 'msvc-varies: GCC equal to one configuration is listed as a match')
bad = dict(same['build']['results'], **{'obj-arrow': 'oa:1'})
r = {x['case']: x for x in oc.compare(cases, msvc, {'build': dict(results=bad)})}
check(r['obj-arrow']['verdict'] == ('differs' if rows['obj-arrow']['verdict'] == 'same' else 'msvc-varies'),
      'a changed order of an agreed case differs')
md = oc.markdown(list(rows.values()), msvc, same)
check('| ORDER-OBJECT |' in md and md.count('\n|') > len(cases), 'markdown table')

# per-variant verdicts and --strict: msvc-varies never fails, an agreed case does, each variant on its own
two = {'build': dict(results=bad), 'cand': same['build']}
rows2 = oc.compare(cases, msvc, two)
agreed = rows['obj-arrow']['verdict'] == 'same'
check(oc.strict_failures(rows2, 'build') == (['obj-arrow'] if agreed else []), 'strict: the build variant fails')
check(oc.strict_failures(rows2, 'cand') == [], 'strict: the candidate variant passes')
check(all(r['verdicts']['build'] == r['verdict'] for r in rows2), 'verdict = the build variant\'s verdict')
check(len(oc.strict_failures(oc.compare(cases, msvc, {'build': dict(results={})}))) ==
      sum(r['verdict'] == 'same' for r in rows.values()), 'strict: a case without a GCC log differs')
md2 = oc.markdown(rows2, msvc, two)
check('| cand | 0 |' in md2, 'markdown: per-variant line')

# V1 C1: the build variant carries every ORDER_* default compile.sh passes to the plugin
args = oc.compile_sh_order_args()
cs = oc.COMPILE_SH.read_text()
import re as _re
want = [k for v, k in oc.ORDER_KEYS if _re.search(r'^%s=' % v, cs, _re.M) and ('fnvnx_order-%s=' % k) in cs]
check(sorted(a.split('-')[-1].split('=')[0] for a in args) == sorted(want) and len(want) >= 4,
      "build variant = compile.sh's plugin arguments (%s)" % ' '.join(args))
fake = 'ORDER_MODE=${ORDER_MODE:-volatile}\nORDER_OBJECT=${ORDER_OBJECT:-msvc}\n-fplugin-arg-fnvnx_order-mode=x ' \
       '-fplugin-arg-fnvnx_order-object=y\n'
check(oc.compile_sh_order_args(fake) == ['-fplugin-arg-fnvnx_order-mode=volatile',
                                         '-fplugin-arg-fnvnx_order-object=msvc'], 'compile.sh defaults parsed')
# V1 T2: no MSVC log, or a log from only some configurations, is not agreement
none = {cfg: dict(v, results={k: x for k, x in v['results'].items() if k != 'obj-arrow'}) for cfg, v in msvc.items()}
part = {cfg: dict(v, results=dict(v['results'])) for cfg, v in msvc.items()}
for cfg in list(part)[1:]:
    part[cfg]['results'].pop('obj-arrow2')
g = {'build': dict(results={n: next(iter(msvc.values()))['results'][n] for n in names})}
r_none = {x['case']: x for x in oc.compare(cases, none, g)}
r_part = {x['case']: x for x in oc.compare(cases, part, g)}
check(r_none['obj-arrow']['verdict'] == 'no-msvc' and 'obj-arrow' in oc.strict_failures(list(r_none.values())),
      'strict: a case no MSVC configuration logged fails')
check(r_part['obj-arrow2']['verdict'] == 'msvc-incomplete' and 'obj-arrow2' in oc.strict_failures(list(r_part.values())),
      'strict: a case logged by 1 of 5 configurations fails')
# V1 T1: --strict without --gcc refuses
import subprocess as _sp
rr = _sp.run([sys.executable, '-I', str(PORT / 'tools/equiv/order_check.py'), sys.argv[1] + '/t1', '--strict'],
             capture_output=True, text=True)
check(rr.returncode == 2, '--strict without --gcc exits 2 (got %d)' % rr.returncode)

# --msvc-stdout: identical outputs give the text, different ones name the line
t1, v1 = oc.agreed_stdout({'x': ('', 'a\nb\n'), 'y': ('', 'a\nb\n')})
check(t1 == 'a\nb\n' and v1 == [], 'agreed_stdout: identical')
t2, v2 = oc.agreed_stdout({'x': ('', 'a\nb\n'), 'y': ('', 'a\nc\nd\n')})
check(t2 is None and [l for l, _ in v2] == [2, 3] and v2[1][1]['x'] is None, 'agreed_stdout: varying lines named')

if '--gcc' in sys.argv:
    scratch = Path(sys.argv[1]) / 'order'
    scratch.mkdir(parents=True, exist_ok=True)
    gcc = oc.run_gcc(scratch, {})
    b = gcc['build']
    check(b['rc'] == 0 and all(n in b['results'] for n in names), 'GCC build variant runs every case (rc %s)' % b['rc'])
    rows = oc.compare(cases, msvc, gcc)
    print(oc.markdown(rows, msvc, gcc).split('\n\n')[-1])

print('order: %d failed' % len(fails))
sys.exit(1 if fails else 0)
