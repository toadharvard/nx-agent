"""lift/rt/build.json is the build of the lift runtime, and the recipe is the one definition of its flags: verify/recipe.py loads it through its own loader
(load_admit, load_recipe) and judges it with its own guest_errors (the pinned compiler and external, only per-root flag classes, no flag that downgrades
an error). The semantic flags of the lifted code are the pin's flags.semantics (verify/guest_compiler.json), appended by the recipe to every guest compile stage;
the build.json holds none of them, so the test reads no second copy. Checked: the recipe accepts the root, its effective flags are the pin's semantics followed by
the root's own, the plugins are exactly recipe.py's PLUGIN_SOURCES in load order with PLUGIN_ARGS, units are all, and no tracked .c under lift/rt is C++
(a unit's language is its extension)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports/fnv-nx: qualified imports of another root's modules (contract.X, verify.X)
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
VERIFY = os.path.normpath(os.path.join(LIFT, '..', 'verify'))
sys.path.insert(0, os.path.join(LIFT, 'tools'))
sys.path.insert(0, VERIFY)
from verify import recipe as R  # noqa: E402

bad = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else ': ' + str(detail)))
    if not ok:
        bad.append(name)


REPO = os.path.normpath(os.path.join(LIFT, '..', '..', '..'))
ROOT = 'ports/fnv-nx/lift/rt'
roots = R.load_admit(REPO)
check('lift/rt is an admitted product root', ROOT in roots, sorted(roots))
rec = R.load_recipe(REPO, roots, ROOT)
pin = R.load_pin(REPO)
check('the recipe accepts the root (guest_errors)', R.guest_errors(rec, ROOT, roots, pin) == [], R.guest_errors(rec, ROOT, roots, pin))
fl = rec['flags']
sem = pin['flags']['semantics']   # {"common": [...], "lifted_extra": [...]}: the one source; the recipe reads it, the test reads it again
origin = roots[ROOT].get('cxx_origin')
from verify import targets as T  # noqa: E402
prefix = list(T.semantic_prefix(T.load()))      # ABI, architecture, tuning, code model and the DATAMODEL rows: generated from the declared targets (verify/targets.py), the leading run of the set
want = prefix + list(sem['common']) + (list(sem['lifted_extra']) if R.origin_class(origin) == 'lifted' else [])
check('effective semantic flags of the root are the pin semantics (the prefix of the targets, the common set of the pin, plus lifted_extra for a lifted origin)', list(R.semantics_set(origin)) == want, (origin, list(R.semantics_set(origin)), want))
check('compiler is the pinned guest compiler', rec['compiler'] == dict(zip(('cc', 'cxx'), R.pin_drivers()), external=pin['external']['name']), rec['compiler'])
check('external is the pin', rec['external'] == [pin['external']], rec['external'])
check('plugins are the names of PLUGIN_SOURCES in load order (source and arguments are derived by the recipe, plugin_decls / plugin_args, never listed in the root)',
      rec['plugins'] == [{'name': n} for n in R.PLUGIN_SOURCES] and all(n in R.plugin_args(origin) for n in R.PLUGIN_SOURCES), rec['plugins'])
check('units are all', rec['units'] == 'all')
root = os.path.join(LIFT, 'rt')
files = subprocess.check_output(['git', 'ls-files', '--', root], text=True).split('\n')
cc = [os.path.basename(f) for f in files if f.endswith('.c')]
cxx_like = [f for f in cc if re.search(r'^\s*(#include\s*<(?:new|cstdint)>|namespace\b|extern\s+"C"|template\s*<|static_assert\b)', open(os.path.join(os.path.dirname(root), 'rt', f)).read(), re.M)]
check('no .c unit under lift/rt uses C++-only syntax', not cxx_like, cxx_like)


class T(unittest.TestCase):
    def test_checks(self): self.assertEqual(bad, [])      # the checks above run at import and print their lines


if __name__ == '__main__':
    sys.exit(1 if bad else 0)
