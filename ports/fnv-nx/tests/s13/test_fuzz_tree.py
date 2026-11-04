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


def run(tmp, commits, meta, avail):
    d = tempfile.mkdtemp(dir=tmp)
    farm, runp, link, here = (os.path.join(d, x) for x in ('farm', 'run', 'link', 'here'))
    for p in (farm, runp, link, here):
        os.makedirs(p)
    open(os.path.join(here, 'fetch_tree.sh'), 'w').write(STUB)
    json.dump(meta, open(os.path.join(link, 'meta.json'), 'w'))
    calls = os.path.join(d, 'calls')
    script = ('set -euo pipefail\nfail_report() { echo "FAILED: $2"; exit "$1"; }\n'
              'L12=${LCOMMIT:0:12}\n' + block() + 'echo "NOTE $NOTE_TREE"\n')
    env = dict(os.environ, FARM_HOME=farm, RUN=runp, LINK=link, HERE=here, REPO=commits['repo'],
               LCOMMIT=commits['L'], ARTIFACTS=d, CALLS=calls, FUZZ_TREE_WAIT='7',
               AVAIL=' '.join('%s=%s' % (commits[k][:12], h) for k, h in avail.items()))
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
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())
