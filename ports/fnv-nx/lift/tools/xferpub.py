#!/usr/bin/env python3
"""Publish a liftsrc pack as a pack external and pin it in a copy of lift/pack/build.json (the step before the before/after builds of xferpp.py):
    xferpub.py PACK_DIR --tag NAME --repo REPO --build-repo CLONE --out BUILD_JSON [--no-push]
PACK_DIR   the liftsrc output `pack/` (its files are the root of the published tree, like every pack external)
REPO       the repository checkout (the recipe's repo root): its ports/fnv-nx/verify (cas.py) and ports/fnv-nx/lift/pack/build.json are used
CLONE      a local clone of the artifacts repository (fnv-nx-build, private: exe-derived text never goes to this repository)
Steps: (1) one parentless commit of exactly the files of PACK_DIR on branch l3a-lift/xferpack/NAME of CLONE (a temporary index: the working tree and branches of CLONE are not touched), pushed to its
`origin` (--no-push: not pushed); (2) tree_sha256 = cas.ingest of the commit's checkout, the checkout recipe.fetch_externals makes (read-tree + checkout-index of the commit) and the hash it requires, no second function; (3) BUILD_JSON = a
copy of REPO/lift/pack/build.json whose external `liftpack` is pinned to (ref, commit, tree_sha256); every other byte is the original's. Prints the commit and the tree hash. The commit carries no trailer and no session id."""
import argparse, json, os, shutil, subprocess, sys, tempfile

IDENT = {'GIT_AUTHOR_NAME': 'w13', 'GIT_AUTHOR_EMAIL': 'w13@localhost', 'GIT_COMMITTER_NAME': 'w13', 'GIT_COMMITTER_EMAIL': 'w13@localhost'}


def git(clone, *a, env=None, **kw):
    return subprocess.run(['git', '-C', clone] + list(a), env=dict(os.environ, GIT_TERMINAL_PROMPT='0', **(env or {})), check=True, capture_output=True, text=True, **kw).stdout.strip()


def commit_pack(clone, pack, branch, msg):
    gd = git(clone, 'rev-parse', '--absolute-git-dir')
    with tempfile.TemporaryDirectory() as t:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(t, 'idx'))
        subprocess.run(['git', '--git-dir', gd, '--work-tree', os.path.abspath(pack), 'add', '-A', '-f', '.'], env=env, check=True, capture_output=True, cwd=os.path.abspath(pack))
        tree = git(clone, 'write-tree', env={'GIT_INDEX_FILE': env['GIT_INDEX_FILE']})
        commit = git(clone, 'commit-tree', tree, '-m', msg, env=IDENT)
    git(clone, 'update-ref', 'refs/heads/' + branch, commit)
    return commit


def tree_hash(repo, clone, commit):
    """the tree_sha256 recipe.fetch_externals requires of the checkout of COMMIT (cas.ingest into a scratch store)"""
    sys.path.insert(0, os.path.join(os.path.abspath(repo), 'ports', 'fnv-nx', 'verify'))
    import cas
    with tempfile.TemporaryDirectory() as t:
        co, store = os.path.join(t, 'co'), os.path.join(t, 'store'); os.makedirs(co); os.makedirs(store)
        gd = git(clone, 'rev-parse', '--absolute-git-dir'); env = dict(os.environ, GIT_INDEX_FILE=os.path.join(t, 'idx'))
        subprocess.run(['git', '--git-dir', gd, 'read-tree', commit], env=env, check=True, capture_output=True)
        subprocess.run(['git', '--git-dir', gd, '--work-tree', co, 'checkout-index', '-a', '-f'], env=env, check=True, capture_output=True)
        return cas.ingest(co, store)


def pin(text, ref, commit, tree):
    """BUILD_JSON text with the external `liftpack` pinned; only the three values of that external change"""
    b = json.loads(text); es = [e for e in b['external'] if e['name'] == 'liftpack']
    if len(es) != 1: raise SystemExit('xferpub: build.json must declare exactly one external named liftpack')
    old = (es[0]['ref'], es[0]['commit'], es[0]['tree_sha256'])
    for k, v in (('ref', ref), ('commit', commit), ('tree_sha256', tree)): es[0][k] = v
    out = text
    for o, n in zip(old, (ref, commit, tree)):
        if text.count('"%s"' % o) != 1: raise SystemExit('xferpub: the value %s must occur exactly once in build.json' % o)
        out = out.replace('"%s"' % o, '"%s"' % n)
    if json.loads(out) != b: raise SystemExit('xferpub: internal: the textual pin differs from the structured one')
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('pack'); ap.add_argument('--tag', required=True); ap.add_argument('--repo', required=True)
    ap.add_argument('--build-repo', required=True); ap.add_argument('--out', required=True); ap.add_argument('--no-push', action='store_true')
    a = ap.parse_args(argv)
    if not os.path.isdir(os.path.join(a.pack, 'u')): raise SystemExit('xferpub: %s has no u/ (a liftsrc pack directory)' % a.pack)
    branch = 'l3a-lift/xferpack/' + a.tag
    commit = commit_pack(a.build_repo, a.pack, branch, 'liftsrc pack ' + a.tag)
    if not a.no_push: git(a.build_repo, 'push', 'origin', 'refs/heads/%s:refs/heads/%s' % (branch, branch))
    tree = tree_hash(a.repo, a.build_repo, commit)
    src = os.path.join(a.repo, 'ports', 'fnv-nx', 'lift', 'pack', 'build.json')
    open(a.out, 'w').write(pin(open(src).read(), branch, commit, tree))
    print('%s %s' % (commit, tree)); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
