#!/usr/bin/env python3
"""tools/xferpub.py on a tiny pack: the published commit is fetched by recipe.fetch_externals (the recipe's own external check) from the artifacts repository, and the pinned copy of lift/pack/build.json
is accepted; a wrong tree_sha256 is refused; only the three values of the external `liftpack` differ from the original; the working tree and the branches of the clone are untouched.
    test_xferpub.py"""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE)); REPO = os.path.dirname(os.path.dirname(os.path.dirname(LIFT)))
sys.path.insert(0, os.path.join(LIFT, 'tools')); sys.path.insert(0, os.path.join(REPO, 'ports', 'fnv-nx', 'verify'))
import xferpub, recipe
FAILS = []


def check(n, ok):
    print('%-78s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def sh(*a, **k): return subprocess.run(a, check=True, capture_output=True, text=True, **k).stdout.strip()


t = tempfile.mkdtemp(prefix='xferpub-')
bare, clone, pack = t + '/origin.git', t + '/clone', t + '/pack'
sh('git', 'init', '-q', '--bare', bare); sh('git', 'init', '-q', clone); sh('git', '-C', clone, 'remote', 'add', 'origin', bare)
os.makedirs(pack + '/u'); open(pack + '/u/000.src', 'w').write('#include "fnvl_prelude.h"\nextern "C" void L_00401000(void)\n{ FNVL_RETP(t) }\n'); open(pack + '/entries.tsv', 'w').write('00401000\tL_00401000\tcode\n')
os.symlink('u/000.src', pack + '/link')
out = t + '/build.json'
r = subprocess.run([sys.executable, os.path.join(LIFT, 'tools', 'xferpub.py'), pack, '--tag', 'tiny', '--repo', REPO, '--build-repo', clone, '--out', out], capture_output=True, text=True)
check('publish ran: %s' % (r.stderr[-200:] or r.stdout.strip()), r.returncode == 0)
commit, tree = r.stdout.split()
check('the branch is in the artifacts repository at the commit', sh('git', '-C', bare, 'rev-parse', 'refs/heads/l3a-lift/xferpack/tiny') == commit)
check('the commit is parentless and its message is exactly the one line', sh('git', '-C', bare, 'log', '-1', '--format=%P', commit) == '' and sh('git', '-C', bare, 'log', '-1', '--format=%B', commit) == 'liftsrc pack tiny')
check('the clone has no working-tree change and only the published branch', sh('git', '-C', clone, 'status', '--short') == '' and sh('git', '-C', clone, 'for-each-ref', '--format=%(refname)') == 'refs/heads/l3a-lift/xferpack/tiny')
orig = open(os.path.join(REPO, 'ports', 'fnv-nx', 'lift', 'pack', 'build.json')).read(); new = open(out).read()
bo, bn = json.loads(orig), json.loads(new)
e = [x for x in bn['external'] if x['name'] == 'liftpack'][0]
check('the copy pins liftpack to (branch, commit, tree)', (e['ref'], e['commit'], e['tree_sha256']) == ('l3a-lift/xferpack/tiny', commit, tree))
for x in bo['external']:
    if x['name'] == 'liftpack': x.update(ref=e['ref'], commit=commit, tree_sha256=tree)
check('every other value of the recipe is the original\'s', bo == bn)
# the recipe's own external check
store, ext, ws = t + '/store', t + '/ext', t + '/ws'
os.makedirs(store); os.makedirs(ext); recipe.set_artifacts(ext, bare)
got = recipe.fetch_externals([dict(e)], store, ext, ws)
check('recipe.fetch_externals accepts the pinned entry', got['liftpack']['tree'] == tree and os.path.isfile(got['liftpack']['host'] + '/u/000.src'))
store2, ext2 = t + '/store2', t + '/ext2'; os.makedirs(store2); os.makedirs(ext2); recipe.set_artifacts(ext2, bare)
bad = dict(e, tree_sha256='0' * 64)
try:
    recipe.fetch_externals([bad], store2, ext2, ws); ok = False
except recipe.BuildRed: ok = True
check('a wrong tree_sha256 is refused', ok)
sys.exit(1 if FAILS else 0)
