"""tables.py - the decomp-derived tables (exe address -> identification) are not in this public tree.

They live in the private fnv-nx-build repository (tools/tables.pin.json: repo, branch, commit, tree_sha256 of the
checked-out tree, sha256 of each file). A consumer asks `path(name)` and gets the file of the directory named by
FNVNX_TABLES (the `tables/` directory of that checkout: for a recipe, the external's `at` + `/tables`). There is no
default path and no fallback: the directory unset, the file missing or a sha256 different from the pin ends the process
with exit 2. A test that needs data builds a synthetic table in tests/ and passes it explicitly.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIN = os.path.join(HERE, 'tables.pin.json')


def pin():
    with open(PIN, encoding='utf-8') as f:
        return json.load(f)


def path(name):
    d = os.environ.get('FNVNX_TABLES')
    p = pin()
    if not d:
        sys.exit('tables: FNVNX_TABLES is not set; %s is a decomp-derived table of fnv-nx-build %s (commit %s, '
                 'tree_sha256 %s, dir %s)' % (name, p['ref'], p['commit'], p['tree_sha256'], p['dir']))
    f = os.path.join(d, name)
    want = p['files'].get(name)
    if want is None:
        sys.exit('tables: %s is not a table of the pin' % name)
    try:
        h = hashlib.sha256(open(f, 'rb').read()).hexdigest()
    except OSError as e:
        sys.exit('tables: %s: %s' % (f, e))
    if h != want:
        sys.exit('tables: %s has sha256 %s, the pin (commit %s) says %s' % (f, h, p['commit'], want))
    return f


def pin_id():
    """one string for build keys: the pin's commit and tree hash"""
    p = pin()
    return p['commit'] + ':' + p['tree_sha256']
