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
    'LD g1; BF g2; Ok g3; D *g4;\n'
    'struct PB { virtual void f(); };\n#pragma pack(push, 1)\nstruct P : PB { FNVNX_MSVC_BASE_END(PB) short a; };\n#pragma pack(pop)\n'
    'struct H { char c; P p; };\nH g5;\n'
    'struct __declspec(align(16)) A16 { float v[4]; };\n'
    'struct VP { virtual void f(); unsigned m10; unsigned m14; A16 m20; };\n'
    'struct VS { virtual void f(); unsigned unk04; unsigned unk08; A16 m10; };\n'
    'struct Q { int x; }; typedef void (Q::*Cb)(int);\nstruct Tbl { Cb v00; Cb v04; int n; };\nVP g6; VS g7; Tbl g8;\n')
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
                 ('pack: a class packed to 1 with the shim\'s FNVNX_MSVC_BASE_END filler (MSVC align 1, GCC 4)',
                  ('P', 'align', '1', '4') in {(x[1], x[2], x[3], x[4]) for x in rows}),
                 ('member check: that class after a char is a pack-member row (and H differs at p)',
                  any(x[1] == 'H' and x[2] == 'member p' and x[5] == 'pack-member' for x in rows)),
                 ('vfpad: an own vfptr in a 16-aligned record, member names agree with MSVC (m10 at 16)',
                  ('VP', 'field m10', '16', '4', 'vfpad') in {(x[1], x[2], x[3], x[4], x[5]) for x in rows}),
                 ('vfpad-src: the same with member names that agree with the native layout (unk04 at 4)',
                  ('VS', 'field unk04', '16', '4', 'vfpad-src') in {(x[1], x[2], x[3], x[4], x[5]) for x in rows}),
                 ('memfnptr: pointers to member functions, 4 bytes under MSVC, 8 natively',
                  ('Tbl', 'field v04', '4', '8', 'memfnptr') in {(x[1], x[2], x[3], x[4], x[5]) for x in rows}),
                 ('identical records give no row (Ok, V, D)', not {x[1] for x in rows} & {'Ok', 'V', 'D'}),
                 ('summary written', os.path.exists(out + '.summary'))):
    print('%-80s %s' % (what, 'ok' if ok else 'FAIL')); fails += not ok
if fails: print(r.stderr[-2000:]); print('\n'.join('\t'.join(x) for x in rows))
print('%d failed' % fails)
sys.exit(1 if fails else 0)
