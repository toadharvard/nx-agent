#!/usr/bin/env python3
"""lift/pack/build.json is a pure function of liftgen.recipe: regenerate it from the external it names (ref, commit, tree_sha256) into a scratch file and diff
it against the tracked file. A hand edit (a duplicate key, a stale plugin_cxx, a plugin entry with source/args) is red."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import liftgen

TRACKED = os.path.join(LIFT, 'pack', 'build.json')


def main():
    raw = open(TRACKED).read()
    seen = []
    doc = json.loads(raw, object_pairs_hook=lambda ps: seen.append([k for k, _ in ps]) or dict(ps))
    dup = [k for ks in seen for k in set(ks) if ks.count(k) > 1]
    bad = []
    if dup:
        bad.append('duplicate keys in the tracked build.json: %s' % sorted(set(dup)))
    e = doc['external'][0]
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, 'build.json')
        liftgen.recipe(e['ref'], e['commit'], e['tree_sha256'], out)
        if open(out).read() != raw:
            bad.append('lift/pack/build.json differs from liftgen.recipe(%s, %s, %s): regenerate it with `liftgen.py --recipe REF COMMIT TREE`' % (e['ref'], e['commit'][:12], e['tree_sha256'][:12]))
    for b in bad:
        print('FAIL', b)
    print('FAIL' if bad else 'PASS', 'pack build.json == liftgen.recipe')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
