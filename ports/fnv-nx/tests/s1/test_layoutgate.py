"""S1-rules: tools/link/layoutgate.py finds MSVC-vs-native layout differences and names their cause (synthetic unit)."""
import os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', '..', 'tools', 'link', 'layoutgate.py')
if not (shutil.which('clang++') and shutil.which('aarch64-linux-gnu-g++')):
    print('skipped: clang++ / aarch64-linux-gnu-g++ missing'); sys.exit(0)
t = tempfile.mkdtemp(); os.makedirs(os.path.join(t, 'src')); os.makedirs(os.path.join(t, 'include'))
open(os.path.join(t, 'src', 't.cpp'), 'w').write(
    'struct LD { long double x; int y; };\nstruct BF { int a : 3; char b; int c; };\n'
    'struct Ok { int a; short b; char *p; };\nstruct V { virtual void f(); int a; };\nstruct D : V { char b; };\n'
    'LD g1; BF g2; Ok g3; D *g4;\n')
open(os.path.join(t, 'u.txt'), 'w').write('src/t.cpp\n')
out = os.path.join(t, 'out.tsv')
r = subprocess.run([sys.executable, TOOL, '--tree', t, '--units', os.path.join(t, 'u.txt'), '-o', out, '--jobs', '1'],
                   capture_output=True, text=True)
rows = [l.split('\t') for l in open(out).read().splitlines()[2:]] if os.path.exists(out) else []
fails = 0
for what, ok in (('long double: size, align and the next field differ (cause long-double)',
                  {('LD', 'size', 'long-double'), ('LD', 'field y', 'long-double')} <= {(x[1], x[2], x[5]) for x in rows}),
                 ('bitfield: MSVC keeps the int unit, the next char moves (cause bitfield)',
                  ('BF', 'field b', '4', '1', 'bitfield') in {(x[1], x[2], x[3], x[4], x[5]) for x in rows}),
                 ('identical records give no row (Ok, V, D)', not {x[1] for x in rows} & {'Ok', 'V', 'D'}),
                 ('summary written', os.path.exists(out + '.summary'))):
    print('%-80s %s' % (what, 'ok' if ok else 'FAIL')); fails += not ok
if fails: print(r.stderr[-2000:])
print('%d failed' % fails)
sys.exit(1 if fails else 0)
