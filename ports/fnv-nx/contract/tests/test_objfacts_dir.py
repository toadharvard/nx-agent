#!/usr/bin/env python3
"""Synthetic check of objfacts_dir.py: the object name of an entry is its path under the directory (equal basenames of two roots never collide),
the output is deterministic and has store kind `obj`."""
import os, shutil, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import objfacts_dir, store

CC = shutil.which('aarch64-linux-gnu-gcc-13') or shutil.which('aarch64-linux-gnu-gcc')
fails = []
if not CC:
    print('objfacts_dir: SKIP (no aarch64 cross compiler in this environment)')
    sys.exit(0)
with tempfile.TemporaryDirectory() as d:
    for r in ('rootA', 'rootB/sub'):
        os.makedirs(os.path.join(d, 'objs', r))
        open(d + '/f.c', 'w').write('int f(int x) { return x + 1; }\n')
        subprocess.run([CC, '-c', '-O1', '-o', os.path.join(d, 'objs', r, 'x.c.o'), d + '/f.c'], check=True)
    out = objfacts_dir.collect(d + '/objs')
    names = sorted({k[0] for k in out})
    if names != ['rootA/x.c.o', 'rootB/sub/x.c.o']:
        fails.append('object names %r' % names)
    if not all((n, 'sympair') in out and (n, 'ztv') in out for n in names):
        fails.append('section entries missing: %r' % sorted(out))
    outs = []
    for i in (1, 2):
        r = subprocess.run([sys.executable, '-I', C + '/objfacts_dir.py', '--objects', d + '/objs', '--out', '%s/o%d.json' % (d, i)], capture_output=True, text=True)
        if r.returncode:
            fails.append('cli: ' + r.stderr[-200:])
        outs.append(open('%s/o%d.json' % (d, i), 'rb').read())
    if outs[0] != outs[1]:
        fails.append('not deterministic')
    store.load(d + '/o1.json', 'obj')
if fails:
    print('\n'.join('FAIL ' + f for f in fails))
    sys.exit(1)
print('objfacts_dir: ok')
