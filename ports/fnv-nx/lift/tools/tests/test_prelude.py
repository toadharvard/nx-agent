#!/usr/bin/env python3
"""Unit test of tools/prelude.py, the one inliner of the pack's runtime headers (inc/fnvl_prelude.h, inc/imp.h; packstage.py inc), and of its two users:
the recipe (liftrecipe.inc_stages: the inputs of each step are the closure of its source) and the pack copy step (an external that carries an inc/ is rejected).
    python3 -I test_prelude.py"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.dirname(HERE); LIFT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import fnvnx.lift.tools.prelude as prelude; import fnvnx.lift.tools.packcopy as packcopy
bad = 0


def check(name, ok):
    global bad
    print('%-100s %s' % (name, 'ok' if ok else 'FAIL')); bad += 0 if ok else 1


def w(d, name, text):
    os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
    open(os.path.join(d, name), 'w', newline='').write(text)


with tempfile.TemporaryDirectory() as t:
    a, b, g = t + '/a', t + '/b', t + '/g'
    w(a, 'fnvl.h', '#include <stddef.h>\nint x;\n#include "p.h"\n#include "gen.h"\nint y;\r\n')
    w(a, 'p.h', 'int p;\n#include "q.h"\n')
    w(b, 'q.h', 'int q;\n')
    w(b, 'p.h', 'int shadowed;\n')
    w(g, 'gen.h', 'int gen;\n')
    d = [a, b, g]
    check('inline: quote includes are replaced recursively, angle includes stay, the first directory wins, CRLF is kept', prelude.inline(d, 'fnvl.h') == '#include <stddef.h>\nint x;\nint p;\nint q;\nint gen;\nint y;\r\n')
    check('inline: a header in no directory is an error', all(subprocess.run([sys.executable, '-I', '-c', 'import sys;sys.path.insert(0,%r);import prelude;prelude.inline(%r,"nope.h")' % (TOOLS, d)], capture_output=True).returncode for _ in (0,)))
    c = prelude.closure([a, b], 'fnvl.h')
    check('closure: the repo files read, in the directories of the stage; the generated header (not in a, b) is skipped', sorted(os.path.relpath(p, t) for p in c) == ['a/fnvl.h', 'a/p.h', 'b/q.h'])
    check('closure: the file of each include is the first directory that holds it', {os.path.basename(p): v[0] for p, v in c.items()}['p.h'] == a)
    w(a, 'imp.h', '#include "q.h"\nint imp;\n')
    check('text: imp.h is the inlined file without a guard', prelude.text('imp.h', d) == 'int q;\nint imp;\n')
    check('text: fnvl_prelude.h is guarded', prelude.text('fnvl_prelude.h', d).startswith('#ifndef FNVL_PRELUDE_H\n#define FNVL_PRELUDE_H\n') and prelude.text('fnvl_prelude.h', d).endswith('\n#endif\n'))
    out = t + '/o.h'
    r = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'packstage.py'), 'inc', 'imp.h', out] + d, capture_output=True, text=True)
    check('packstage inc: the command line writes the same text', r.returncode == 0 and open(out).read() == prelude.text('imp.h', d))
    pk = t + '/pack'
    os.makedirs(pk + '/u')
    for n in packcopy.names(): w(pk, n[:-4] + '.src', '')
    packcopy.main(pk, t + '/out')
    check('packcopy: a pack without inc/ is copied', os.path.isfile(t + '/out/u/fff.cpp'))
    os.makedirs(pk + '/inc')
    try: packcopy.main(pk, t + '/out2'); ok = False
    except SystemExit as e: ok = 'inc' in str(e)
    check('packcopy: a pack with an inc/ (a carried prelude) is rejected', ok)

# the recipe: the inputs of each inc step are exactly the closure of its source in the real tree, and the build.json says so
import fnvnx.lift.tools.liftrecipe as liftrecipe
rec = json.load(open(os.path.join(LIFT, 'pack', 'build.json')))
steps = {g['outputs'][0]: g for g in rec['generated'] if g['args'][0] == 'inc'}
repo = os.path.join(LIFT, '..', '..', '..')
for name, (src, _) in prelude.INC.items():
    dirs = [os.path.join(repo, x) for x in liftrecipe.INC_DIRS]
    want = sorted(os.path.relpath(os.path.join(dd, n), repo) for dd, n in prelude.closure(dirs, src).values())
    check('recipe: inc/%s has the closure of %s over %s as its inputs (%d files)' % (name, src, liftrecipe.INC_DIRS, len(want)), steps.get('inc/' + name, {}).get('inputs') == want and want)
check('recipe: the pack include directory is its own generated inc/, and no external carries a header', rec['include'] == ['ports/fnv-nx/lift/pack/inc'])
print('preludetest: %d failed' % bad); sys.exit(1 if bad else 0)
