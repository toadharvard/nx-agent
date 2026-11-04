#!/usr/bin/env python3
"""ab_compare.py - two full builds of the same head, side by side (the GAME_CXX A/B: standin vs tc).

    ab_compare.py <artifacts clone> <commit12> [variant]      (default variant: tc)

Reads meta.json of branch link/<commit12> (the default build) and link/<commit12>-<variant> (full_job.sh with
FARM_VARIANT=<variant>, e.g. FARM_MAKE_ARGS="GAME_CXX=tc") in the private artifacts repository and prints:
- the compiler;
- census units, failing and errors;
- compile ok and failed;
- the link counts;
- guest undefined;
- the boot (WinMain, stop, milestone);
- nx1 open;
- provenance.
Each numeric row says which side is better. Verdict: B is not worse when no row is worse than A's, both builds'
provenance is PASS, and both are at that commit. Exit 0 = B not worse, 1 = B worse, 2 = a side missing.
"""
import json
import subprocess
import sys

LOWER = {'census.failing', 'census.errors', 'compile.failed', 'link.undefined', 'link.multidef', 'guest_undefined',
         'nx1_open'}
HIGHER = {'census.clean', 'compile.ok', 'link.resolved', 'boot_winmain', 'boot_stub_winmain'}


def meta(art, branch):
    if subprocess.run(['git', '-C', art, 'fetch', '-q', 'origin', '+refs/heads/%s:refs/remotes/origin/%s' % (
            branch, branch)], capture_output=True).returncode:
        return None
    r = subprocess.run(['git', '-C', art, 'show', 'refs/remotes/origin/%s:meta.json' % branch], capture_output=True,
                       text=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def flat(m):
    out = {}
    for k, v in m.items():
        if isinstance(v, dict) and k in ('census', 'compile', 'link'):
            for k2, v2 in v.items():
                out['%s.%s' % (k, k2)] = v2
        elif not isinstance(v, (dict, list)):
            out[k] = v
    return out


def compare(a, b):
    """-> (rows [(key, a, b, mark)], worse keys)"""
    fa, fb = flat(a), flat(b)
    rows, worse = [], []
    for k in sorted(set(fa) | set(fb)):
        va, vb = fa.get(k), fb.get(k)
        mark = ''
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va != vb:
            better = vb < va if k in LOWER else vb > va if k in HIGHER else None
            if better is not None:
                mark = 'B better' if better else 'B WORSE'
                if not better:
                    worse.append(k)
        rows.append((k, va, vb, mark))
    return rows, worse


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    art, c12 = argv[0], argv[1][:12]
    var = argv[2] if len(argv) > 2 else 'tc'
    a, b = meta(art, 'link/' + c12), meta(art, 'link/%s-%s' % (c12, var))
    if a is None or b is None:
        print('ab_compare: missing %s' % ', '.join(n for n, m in (('link/' + c12, a), ('link/%s-%s' % (c12, var), b))
                                                  if m is None))
        return 2
    rows, worse = compare(a, b)
    keys = ('game_cxx', 'commit', 'provenance_ok', 'census.', 'compile.', 'link.', 'guest_undefined', 'boot_', 'nx1_open')
    print('| | A link/%s | B link/%s-%s | |\n|---|---|---|---|' % (c12, c12, var))
    for k, va, vb, mark in rows:
        if k.startswith(keys):
            print('| %s | %s | %s | %s |' % (k, str(va)[:60], str(vb)[:60], mark))
    same_commit = a.get('commit') == b.get('commit') and str(a.get('commit', '')).startswith(c12)
    prov = a.get('provenance_ok') is True and b.get('provenance_ok') is True
    ok = not worse and same_commit and prov
    print('\nverdict: B %s (worse rows: %s; same commit: %s; provenance both PASS: %s)' % (
        'NOT worse' if ok else 'WORSE or not comparable', ', '.join(worse) or 'none', same_commit, prov))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
