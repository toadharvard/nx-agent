#!/usr/bin/env python3
"""tests/s13/test_fuzz_tree.py - fuzz_job.sh's choice of the link's prepared tree (J-0145 follow-up, difffuzz-14).

link/c0d6c71516f6 was made on the rolling `tree` (published for 7b3b20231f0d); t/c0d6c71516f6 never existed and the
rolling tree moved on, so every shard waited 30 min on the link commit and failed (fz1, fz25 at 469d73e). The job
now tries the link commit, the commit the link's meta.json says its tree was published for (tree hash checked),
then the link's objects commit, and waits only when none exists. The block runs here with a stub fetch_tree.sh.
"""
import json
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
JOB = os.path.join(HERE, '..', '..', 'tools', 'farm', 'fuzz_job.sh')
FAIL = []

STUB = r'''#!/usr/bin/env bash
# stub fetch_tree.sh <commit> <dir> <wait>: AVAIL="<c12>=<hash> ..." are the published trees
c12=${1:0:12}; echo "$1 $3" >> "$CALLS"
for kv in $AVAIL; do
  if [ "${kv%%=*}" = "$c12" ]; then mkdir -p "$2"; echo "${kv#*=}"; exit 0; fi
done
echo "no prepared tree for $c12" >&2; exit 1
'''


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def block():
    s = open(JOB).read()
    m = re.search(r"\n(# --- the link's tree.*?\n)# --- the player's", s, re.S)
    return m.group(1)


def git(repo, *a):
    return subprocess.run(['git', '-C', repo] + list(a), check=True, capture_output=True, text=True).stdout.strip()


PREP = r'''import os, sys
# stub prepare.py <decomp> <out>: PREP_FAIL=1 fails; else a tree whose content is PREP_TEXT
if os.environ.get('PREP_FAIL'):
    sys.exit(4)
os.makedirs(os.path.join(sys.argv[2], 'build'))
open(os.path.join(sys.argv[2], 'build', 'units.json'), 'w').write(os.environ.get('PREP_TEXT', 'u'))
'''


def tree_hash_of(text):
    """tree_hash.sh of the stub's tree"""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'build'))
    open(os.path.join(d, 'build', 'units.json'), 'w').write(text)
    h = subprocess.run(['bash', os.path.join(HERE, '..', '..', 'tools', 'farm', 'tree_hash.sh'), d],
                       capture_output=True, text=True).stdout.strip()
    subprocess.run(['rm', '-rf', d])
    return h


def run(tmp, commits, meta, avail, local=None):
    """local: None (FUZZ_LOCAL_PREPARE=0, the published trees only) or the stub prepare.py's environment"""
    d = tempfile.mkdtemp(dir=tmp)
    farm, runp, link, here = (os.path.join(d, x) for x in ('farm', 'run', 'link', 'here'))
    for p in (farm, runp, link, here):
        os.makedirs(p)
    open(os.path.join(here, 'fetch_tree.sh'), 'w').write(STUB)
    subprocess.run(['cp', os.path.join(HERE, '..', '..', 'tools', 'farm', 'tree_hash.sh'), here], check=True)
    pp = os.path.join(d, 'p')
    os.makedirs(os.path.join(pp, 'tools'))
    open(os.path.join(pp, 'tools', 'prepare.py'), 'w').write(PREP)
    json.dump(meta, open(os.path.join(link, 'meta.json'), 'w'))
    calls = os.path.join(d, 'calls')
    script = ('set -euo pipefail\nfail_report() { echo "FAILED: $2"; exit "$1"; }\nlog() { :; }\n'
              'L12=${LCOMMIT:0:12}\n' + block() + 'echo "NOTE $NOTE_TREE"\n')
    env = dict(os.environ, FARM_HOME=farm, RUN=runp, LINK=link, HERE=here, REPO=commits['repo'],
               LCOMMIT=commits['L'], ARTIFACTS=d, CALLS=calls, FUZZ_TREE_WAIT='7',
               AVAIL=' '.join('%s=%s' % (commits[k][:12], h) for k, h in avail.items()),
               P=pp, DECOMP=os.path.join(d, 'decomp'), FUZZ_LOCAL_PREPARE='0' if local is None else '1')
    env.update(local or {})
    env.pop('FUZZ_TREE', None)
    r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env)
    c = open(calls).read().split('\n') if os.path.exists(calls) else []
    return r, [x for x in c if x]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, 'repo')
        os.makedirs(repo)
        git(repo, 'init', '-q')
        cs = {}
        for name in ('T', 'O', 'L'):
            git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', name)
            cs[name] = git(repo, 'rev-parse', 'HEAD')
        cs['repo'] = repo
        meta = dict(commit=cs['L'], objects_commit=cs['O'],
                    tree='prepare farm, tree (published for %s), tree hash efb9808e7d542f00' % cs['T'][:12])

        r, calls = run(tmp, cs, meta, {'L': 'efb9808e7d542f00'})
        check(r.returncode == 0 and 'prepare farm tree of %s (tree hash efb9808e7d542f00)' % cs['L'][:12] in r.stdout,
              "the link commit's own tree is taken first")
        check(len(calls) == 1 and calls[0].endswith(' 0'), 'the first try does not wait')

        r, calls = run(tmp, cs, meta, {'T': 'efb9808e7d542f00', 'O': 'efb9808e7d542f00'})
        check(r.returncode == 0 and 'prepare farm tree of %s' % cs['T'][:12] in r.stdout,
              'no t/<L12>: the tree the link records (c0d6c71516f6 -> 7b3b20231f0d)')
        check([c.split()[1] for c in calls] == ['0', '0'], 'found without waiting (%s)' % calls)

        r, calls = run(tmp, cs, meta, {'T': '1111111111111111', 'O': 'efb9808e7d542f00'})
        check(r.returncode == 0 and 'prepare farm tree of %s' % cs['O'][:12] in r.stdout,
              "a recorded tree with another hash is skipped; the objects commit's tree matches")

        meta2 = dict(meta, tree='')
        r, calls = run(tmp, cs, meta2, {'O': '2222222222222222'})
        check(r.returncode == 3 and not any(c.startswith(cs['O']) for c in calls),
              'no recorded tree hash: the objects commit is not taken unchecked (V1 F2), rc %d' % r.returncode)
        r, calls = run(tmp, cs, dict(meta, tree='prepare farm, tree (published for %s), tree hash '
                                     'efb9808e7d542f00' % cs['T'][:12]), {'L': '3333333333333333'})
        check(r.returncode == 3, 'the waited-for t/<L12> with another hash than the recorded one is refused (V1 F1)')

        r, calls = run(tmp, cs, meta, {})
        check(r.returncode == 3 and 'FAILED: no prepared tree' in r.stdout, 'nothing published: fail_report 3')
        check(calls and calls[-1] == '%s 7' % cs['L'], 'only the link commit is waited for (%s)' % calls)

        r, calls = run(tmp, cs, {}, {})
        check(r.returncode == 3 and len(calls) == 2, 'a link without meta.json: L, then the wait on L')
        # T1: nothing published (full1 prepares locally; fz1..fz8 on 97deacd38aec): prepared here
        r, calls = run(tmp, cs, dict(meta, tree=''), {}, local={})
        th = tree_hash_of('u')
        check(r.returncode == 0 and 'prepared here from %s (no published tree;' % cs['L'][:12] in r.stdout and th in r.stdout
              and not any(c.endswith(' 7') for c in calls), 'nothing published: the tree prepared here, no wait (%s)' % r.stdout.strip())
        r, calls = run(tmp, cs, dict(meta, tree='prepare farm, tree hash %s' % th), {}, local={})
        check(r.returncode == 0 and 'prepared here' in r.stdout, 'prepared here, the hash the link records: taken')
        r, calls = run(tmp, cs, dict(meta, tree='prepare farm, tree hash %s' % th), {}, local={'PREP_TEXT': 'other'})
        check(r.returncode == 3, 'prepared here with another hash than the link records: refused, rc %d' % r.returncode)
        r, calls = run(tmp, cs, dict(meta, tree=''), {}, local={'PREP_FAIL': '1'})
        check(r.returncode == 3 and 'none prepared here' in r.stdout and calls and calls[-1] == '%s 7' % cs['L'],
              'prepare.py fails here: the wait for a published tree, then fail_report 3')
        r, calls = run(tmp, cs, meta, {'L': 'efb9808e7d542f00'}, local={})
        check(r.returncode == 0 and 'prepare farm tree of' in r.stdout, 'a published tree is still taken first')
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())
