#!/usr/bin/env python3
# V6n: scenes with programmable VS + PS. tests/v6n/vs/*.scene.in reference shaders as "@name"; this assembles
# tests/v6n/vs/<name>.vsa / .psa with S6b's s6b_asm, translates them with sdp2dksh --glsl, writes the scenes with
# the real paths to <work> and runs pixdiff on them (unmutated must MATCH; with --mutant M they must MISMATCH).
#   python3 -I render/verify/shaders/vsscenes.py [--tools DIR] [--work DIR] [--mutant NAME] [--target gl|s6ngl]
import os, sys, glob, subprocess, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
a = sys.argv[1:]
tools = os.environ.get('FNVNX_SHADERS_BUILD', os.path.join(ROOT, 'render/shaders/build/host'))
work = os.path.join(ROOT, 'build/v6n/vs'); mutant = None; target = 'gl'; src_sub = ''
i = 0
while i < len(a):
    if a[i] == '--tools': tools = a[i + 1]; i += 2
    elif a[i] == '--work': work = a[i + 1]; i += 2
    elif a[i] == '--mutant': mutant = a[i + 1]; i += 2
    elif a[i] == '--target': target = a[i + 1]; i += 2
    elif a[i] == '--findings': src_sub = 'findings'; i += 1   # tests/v6n/vs/findings: S6b findings, expected to differ
    else: sys.exit('unknown ' + a[i])
src = os.path.join(ROOT, 'tests/v6n/vs', src_sub)
os.makedirs(work, exist_ok=True)
paths = {}
for f in sorted(glob.glob(os.path.join(src, '*.vsa')) + glob.glob(os.path.join(src, '*.psa'))):
    n = os.path.basename(f)[:-4]; b = os.path.join(work, n + '.bin')
    subprocess.run([os.path.join(tools, 's6b_asm'), f, b], check=True)
    sdp = os.path.join(work, n + '.sdp'); ext = '.vso' if f.endswith('.vsa') else '.pso'
    subprocess.run([os.path.join(tools, 'sdp2dksh'), '--pack', sdp, n + ext + '=' + b], check=True, capture_output=True)
    od = os.path.join(work, n + '_t'); shutil.rmtree(od, ignore_errors=True); os.makedirs(od)
    subprocess.run([os.path.join(tools, 'sdp2dksh'), '-o', od, '--glsl', '--no-dksh', sdp], check=True, capture_output=True)
    g = glob.glob(os.path.join(od, '*.glsl'))[0]
    paths[n] = (b, g)
scenes = []
for f in sorted(glob.glob(os.path.join(src, '*.scene.in'))):
    t = open(f).read()
    for n, (b, g) in paths.items():
        t = t.replace('@' + n, '%s %s' % (b, g))
    o = os.path.join(work, os.path.basename(f)[:-3]); open(o, 'w').write(t); scenes.append(o)
cmd = [sys.executable, '-I', os.path.join(ROOT, 'render/verify/pixdiff.py')] + (['--mutant', mutant] if mutant else []) + ['--target', target] + scenes
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout.strip())
ok = (r.returncode == 0) if not mutant else ('0 match' in r.stdout)
if src_sub: ok = True   # findings are reported, not required
sys.exit(0 if ok else 1)
