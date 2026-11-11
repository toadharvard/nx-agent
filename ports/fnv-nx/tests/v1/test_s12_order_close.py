#!/usr/bin/env python3
"""test_s12_order_close.py - V1 (D14) against S12-ordering 1634ca7 (W-UNINIT: uninit=zero), 29a1bde (ORDER-OBJECT:
compile.sh object=last), a2ced7f (ORDER-OPERATOR: compile.sh operator=rtl) and S11 ff320bf / e713657 (order_check.py),
5468b6b (explain.py + explained.tsv).

Every case is our own synthetic code or synthetic report text; the MSVC side is S11's recorded tests/s11/order/order.msvc.
  U1  aggregate local read unwritten on one path (`S s; if (c) s.a = 5; return s.a == 5;`): ORDERING.md says GCC does
      not fold a memory local as undefined; SRA scalarizes it after the pass, so it may fold to `return 1`
  U2  address-taken local (`int *p = &x; if (c) *p = 5; return x == 5;`): promoted to a register after the pass
  U3  one-element array, same shape
  U4  control: the scalar fold of tests/s12/order/uninit.cpp is gone (claim)
  U5  loop-body local written only in iteration 0 (MSVC: the stack slot keeps 7): result under qemu
  C1  order_check.py's `build` variant ("the plugin as compile.sh loads it") carries compile.sh's object= / operator=
  C2  compile.sh's own order (its ORDER_OBJECT / ORDER_OPERATOR defaults) vs recorded MSVC on order.cpp: no agreed
      case may differ (a2ced7f: "S11's MSVC run of evalorder.cpp confirms both")
  C3  short-circuit && || ?: and the comma operator keep their sequenced order under compile.sh's switches (qemu)
  C4  compile.sh rebuilds an object when ORDER_OBJECT / ORDER_OPERATOR change (no flags hash: only mtimes count)
  T1  order_check.py --strict without --gcc: exit 0 with no GCC evidence
  T2  a case with no MSVC record (or recorded by one configuration of five): strict passes / verdict `same`
  E1  explain.py: a FAILED or missing shard report adds no functions and no warning (the metric drops)
  E2  explain.py: a new, unrelated code difference in a unit attributed to STATIC1 / CTOR1 / NEW4 is "explained"
      by a rule-wide row of explained.tsv (no unit, no address)
  python3 -I tests/v1/test_s12_order_close.py   -> one line per case, exit 1 on any DEFECT (skip: no toolchain)
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
WORK = tempfile.mkdtemp(prefix='v1-s12close-')   # raises when it cannot
BUILD = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char',
         '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks', '-ffp-contract=off', '-fPIC',
         '-fpermissive', '-Wno-narrowing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector',
         '-fno-stack-clash-protection', '-mno-outline-atomics']
fails = 0


def out(case, defect, text):
    global fails
    fails += bool(defect)
    print('%-7s %-3s %s' % ('DEFECT' if defect else 'ok', case, text))


def note(case, text):
    print('%-7s %-3s %s' % ('note', case, text))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    spec.loader.exec_module(m)
    return m


def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def compile_sh_defaults():
    t = open(os.path.join(PORT, 'tools', 'link', 'compile.sh')).read()
    d = {}
    for k in ('ORDER_ARGS', 'ORDER_OBJECT', 'ORDER_OPERATOR'):
        m = re.search(r'^%s=\$\{%s:-([^}]*)\}' % (k, k), t, re.M)
        d[k] = m.group(1) if m else None
    return d


CS = compile_sh_defaults()
CS_PLUG = ['-fplugin-arg-fnvnx_order-args=%s' % CS['ORDER_ARGS'],
           '-fplugin-arg-fnvnx_order-object=%s' % CS['ORDER_OBJECT'],
           '-fplugin-arg-fnvnx_order-operator=%s' % CS['ORDER_OPERATOR']]
oc = load('order_check', os.path.join(PORT, 'tools', 'equiv', 'order_check.py'))
ex = load('explain', os.path.join(PORT, 'tools', 'equiv', 'explain.py'))
MSVC = json.load(open(oc.RECORD))['msvc']

# ---- T1 / T2: order_check.py logic, no compiler
r = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'tools', 'equiv', 'order_check.py'), os.path.join(WORK, 't1'),
                    '--strict'], capture_output=True, text=True)
out('T1', r.returncode == 0, '--strict without --gcc: exit %d, %s' % (
    r.returncode, 'no GCC evidence, still passes' if r.returncode == 0 else 'refused'))
cases = oc.cases()
one = {cfg: dict(v, results={k: x for k, x in v['results'].items() if k != 'obj-arrow'}) for cfg, v in MSVC.items()}
gcc_bad = {'build': dict(results={n: 'zz' for n, _ in cases})}
rows = {x['case']: x for x in oc.compare(cases, one, gcc_bad)}
partial = {cfg: dict(v, results=dict(v['results'])) for cfg, v in MSVC.items()}
for cfg in list(partial)[1:]:
    partial[cfg]['results'].pop('obj-arrow2', None)
first = next(iter(MSVC))
gcc_eq = {'build': dict(results={'obj-arrow2': MSVC[first]['results']['obj-arrow2']})}
rows2 = {x['case']: x for x in oc.compare([('obj-arrow2', 'ORDER-OBJECT')], partial, gcc_eq)}
out('T2', rows['obj-arrow']['verdict'] != 'differs' and 'obj-arrow' not in oc.strict_failures(list(rows.values())),
    'a case with no MSVC log and a wrong GCC log: verdict %s, strict %s; recorded by 1 of %d configurations: %s'
    % (rows['obj-arrow']['verdict'], 'passes' if 'obj-arrow' not in oc.strict_failures(list(rows.values())) else 'fails',
       len(MSVC), rows2['obj-arrow2']['verdict']))

# ---- E1 / E2: explain.py on synthetic reports
REP = """# MSVC equivalence gate - 0123456789ab shard %d/2

## Differing functions (unit, address, kind, rule)

```
%s
```
"""
r1 = ex.merge_reports.parse(REP % (1, 'src/a.cpp  0x00401000  code  PTR1'), 'r1.md')
failed = ex.merge_reports.parse('# equiv 0123456789ab FAILED\n\n```\nstart equiv 2 2 at x\nkilled\n```\n', 'r2.md')
table = ex.load_table()
f_all, u_all = ex.explain([r1, ex.merge_reports.parse(REP % (2, 'src/b.cpp  0x00402000  code  PTR1'), 'r2.md')], table, set())
f_half, _ = ex.explain([r1, failed], table, set())
text = ex.render(f_half, [])
warn = re.search(r'FAILED|missing|shard', text)
out('E1', ex.metric(f_half, [])['unexplained_functions'] < ex.metric(f_all, u_all)['unexplained_functions'] and not warn,
    'shard 2 FAILED: unexplained %d (both shards: %d); report names the failed shard: %s'
    % (ex.metric(f_half, [])['unexplained_functions'], ex.metric(f_all, u_all)['unexplained_functions'], bool(warn)))
new = ex.merge_reports.parse(REP % (1, '\n'.join([
    'src/zz/new_unit.cpp  0x00abc000  code  STATIC1', 'src/zz/new_unit.cpp  0x00abc100  code  CTOR1',
    'src/zz/new_unit.cpp  0x00abc200  code  NEW4', 'src/zz/new_unit.cpp  0x00abc300  retarget  sdk'])), 'n.md')
fn, _ = ex.explain([new], table, set())
got = {f['addr']: f['cls'] for f in fn}
out('E2', any(got.values()), 'four new differences in a unit no verdict looked at: %s' % got)

# ---- toolchain cases
if not shutil.which(CXX) or not shutil.which('g++') or not shutil.which('qemu-aarch64'):
    print('skip: toolchain cases need %s, g++, qemu-aarch64' % CXX)
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(1 if fails else 0)
r = subprocess.run(['bash', os.path.join(PORT, 'toolchain', 'order', 'build.sh'), os.path.join(WORK, 'plug')],
                   capture_output=True, text=True, env=dict(os.environ, CXX=CXX))
if r.returncode:
    print('skip: plugin does not build: ' + r.stderr[-200:])
    sys.exit(1 if fails else 0)
SO = r.stdout.strip()


def asm_of(src, extra=()):
    write(os.path.join(WORK, 'u.cpp'), src)
    r = subprocess.run([CXX] + BUILD + ['-fplugin=' + SO] + CS_PLUG + list(extra) +
                       ['-w', '-S', '-o', os.path.join(WORK, 'u.s'), os.path.join(WORK, 'u.cpp')],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-500:])
    funcs, cur = {}, None
    for ln in open(os.path.join(WORK, 'u.s')):
        m = re.match(r'^(_Z\w+):', ln)
        if m:
            cur = m.group(1)
            funcs[cur] = ''
        elif cur:
            funcs[cur] += ln
    return funcs


U = r'''
struct S { int a; int b; };
int agg(int c) { S s; if (c) s.a = 5; return s.a == 5; }
int addr(int c) { int x; int *p = &x; if (c) *p = 5; return x == 5; }
int arr(int c) { int a[1]; if (c) a[0] = 5; return a[0] == 5; }
int scal(int c) { int x; if (c) x = 5; return x == 5; }
'''
fs = asm_of(U)
folded = lambda b: bool(re.search(r'mov\tw0, 1\n', b)) and 'cmp' not in b and 'cset' not in b
for case, fn, why in (('U1', '_Z3aggi', 'aggregate (SRA after the pass)'), ('U2', '_Z4addri', 'address-taken, promoted'),
                      ('U3', '_Z3arri', 'one-element array')):
    b = fs.get(fn, '')
    out(case, folded(b), '%s: %s' % (why, 'folds to return 1 (undefined value used)' if folded(b) else 'compare kept'))
out('U4', folded(fs.get('_Z4scali', '')), 'scalar control: %s' % ('folded' if folded(fs.get('_Z4scali', '')) else 'compare kept'))

sys.path.insert(0, os.path.join(PORT, 'tools', 'verify', 'v1_link'))
import qemu_ilp32 as q  # noqa: E402


def qrun(src, name, flags):
    p = os.path.join(WORK, name + '.cpp')
    write(p, src)
    base = list(q.CXXFLAGS)
    q.CXXFLAGS = base + oc.GCC_SEM + ['-fplugin=' + SO] + flags
    try:
        exe = q.build([p], os.path.join(WORK, name))
    finally:
        q.CXXFLAGS = base
    return q.run(exe)


LOOP = r'''
extern "C" void v1_puts(const char *);
__attribute__((noinline)) int g(int n) { int r = 0; for (int i = 0; i < n; i++) { int x; if (i == 0) x = 7; r += x; } return r; }
extern "C" int main() { volatile int n = 3; return g(n); }
'''
rc, _ = qrun(LOOP, 'loop', CS_PLUG)
out('U5', rc != 21, 'loop-body local written in iteration 0 only, 3 iterations: %d (MSVC slot reuse: 21)' % rc)

# ---- C1 / C2: order_check against compile.sh's order
src_build = open(os.path.join(PORT, 'tools', 'equiv', 'order_check.py')).read()
m = re.search(r"build = \[([^\]]*)\]", src_build)
has = m and 'object=' in m.group(1) and 'operator=' in m.group(1)
out('C1', not has, "order_check's build variant: %s; compile.sh: object=%s operator=%s"
    % (m.group(1).replace("'", '').replace('\n', ' ') if m else '?', CS['ORDER_OBJECT'], CS['ORDER_OPERATOR']))
gcc = oc.run_gcc(__import__('pathlib').Path(WORK, 'oc'), {'compilesh': ['-fplugin-arg-fnvnx_order-object=' + CS['ORDER_OBJECT'],
                                                                        '-fplugin-arg-fnvnx_order-operator=' + CS['ORDER_OPERATOR']]})
rows = oc.compare(oc.cases(), MSVC, gcc)
bad = oc.strict_failures(rows, 'compilesh')
detail = ', '.join('%s MSVC %s GCC %s' % (x['case'], sorted(set(x['msvc'].values()))[0], x['gcc']['compilesh'])
                   for x in rows if x['case'] in bad)
out('C2', bool(bad), "compile.sh's order vs recorded MSVC: %d agreed cases differ%s" % (len(bad), (': ' + detail) if bad else ''))
regress = [x['case'] for x in rows if x['verdicts'].get('build') == 'same' and x['verdicts'].get('compilesh') == 'differs']
note('C2', 'same under the old defaults, differs under compile.sh now: %s' % (regress or 'none'))

SEQ = r'''
extern "C" void v1_puts(const char *);
static char g_log[64]; static int g_n;
static void mark(char c) { g_log[g_n++] = c; g_log[g_n] = 0; }
static void out(const char *s) { v1_puts(s); v1_puts(" "); v1_puts(g_log); v1_puts("\n"); g_n = 0; g_log[0] = 0; }
#define NI __attribute__((noinline))
NI int a() { mark('a'); return 1; } NI int b() { mark('b'); return 2; } NI int z() { mark('z'); return 0; }
struct X { int v; NI int m(int p) { mark(':'); return p; } NI int m2(int p, int q) { mark(':'); return p + q; }
           NI X &operator<<(int p) { mark(':'); return *this; } NI int operator[](int p) { mark(':'); return p; } };
X gx; NI X *o() { mark('o'); return &gx; } NI X &r() { mark('o'); return gx; }
extern "C" int main() {
  (void)(o()->m(a()) && o()->m(b())); out("and");
  (void)(o()->m(z()) || o()->m(b())); out("or");
  (void)(o()->m(a()) ? o()->m(b()) : o()->m(z())); out("cond");
  (void)(o()->m(a()), o()->m(b())); out("comma");
  (void)o()->m2((a(), b()), z() && a()); out("comma-arg");
  (void)(r()[a()] && r()[b()]); out("and-index");
  return 0;
}
'''
want = {'and': 'ao:bo:', 'or': 'zo:bo:', 'cond': 'ao:bo:', 'comma': 'ao:bo:'}
rc, txt = qrun(SEQ, 'seq', CS_PLUG)
got = dict(l.split(' ', 1) for l in txt.splitlines() if ' ' in l)
wrong = {k: got.get(k) for k, v in want.items() if got.get(k) != v}
# inside each operand the object comes after its argument (object=last); the operands themselves stay left to right
seqok = all(got.get(k, '').index(':') < got.get(k, '').rindex(':') for k in want if ':' in got.get(k, ''))
ca = got.get('comma-arg', '')
out('C3', bool(wrong) or rc != 0 or not seqok,
    'rc %d; && || ?: comma %s; comma-arg %s; and-index %s' % (rc, wrong or 'left to right', ca, got.get('and-index')))

# ---- C4: compile.sh and a change of ORDER_OBJECT
s2n = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
if not os.path.isfile(os.path.join(s2n, 'sse2neon.h')) or not shutil.which('jq'):
    note('C4', 'skipped: needs jq and SSE2NEON')
else:
    tree, obj = os.path.join(WORK, 'tree'), os.path.join(WORK, 'obj')
    os.makedirs(os.path.join(tree, 'build'))
    os.makedirs(os.path.join(tree, 'include'))
    write(os.path.join(tree, 'c4.cpp'), 'struct X { int v; __attribute__((noinline)) int m(int p) { return v + p; } };\n'
          'X *get(); int arg(); int use() { return get()->m(arg()); }\n')
    json.dump({'units': [{'source': 'c4.cpp'}]}, open(os.path.join(tree, 'build', 'units.json'), 'w'))
    cs = os.path.join(PORT, 'tools', 'link', 'compile.sh')
    env = dict(os.environ, SSE2NEON=s2n, CXX=CXX, ORDER_OBJECT='first', ORDER_OPERATOR='cpp17')
    r1 = subprocess.run(['bash', cs, tree, obj, '', '1'], capture_output=True, text=True, env=env)
    o = os.path.join(obj, 'c4.cpp.o')
    b1 = open(o, 'rb').read() if os.path.exists(o) else None
    time.sleep(1.1)
    env2 = dict(env)
    env2.pop('ORDER_OBJECT')
    env2.pop('ORDER_OPERATOR')
    r2 = subprocess.run(['bash', cs, tree, os.path.join(WORK, 'obj_fresh'), '', '1'], capture_output=True, text=True, env=env2)
    fresh = open(os.path.join(WORK, 'obj_fresh', 'c4.cpp.o'), 'rb').read() if r2.returncode == 0 else None
    r3 = subprocess.run(['bash', cs, tree, obj, '', '1'], capture_output=True, text=True, env=env2)
    b3 = open(o, 'rb').read() if os.path.exists(o) else None
    if b1 is None or fresh is None or b1 == fresh:
        note('C4', 'inconclusive: first=%s fresh=%s same=%s %s' % (b1 is not None, fresh is not None, b1 == fresh,
                                                                    (r1.stdout + r1.stderr + r2.stderr)[-300:]))
    else:
        out('C4', b3 == b1, 'obj dir built with ORDER_OBJECT=first, rerun with the defaults (object=%s): object %s'
            % (CS['ORDER_OBJECT'], 'kept stale (object=first code)' if b3 == b1 else 'rebuilt'))

shutil.rmtree(WORK, ignore_errors=True)
print('%d defects' % fails)
sys.exit(1 if fails else 0)
