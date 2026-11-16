#!/usr/bin/env python3
"""Builds one host-target recipe root through verify/recipe.py (gate_build.ensure) in the cache given as an argument and prints the path of its linked output.
usage: hostbuild.py CACHE ROOT [OUTPUT]     (OUTPUT: a declared output NAME of the root; its file is printed instead of the linked output; CACHE: the verdict's store, passed explicitly; ROOT: an admitted root such as ports/fnv-nx/lift/rcpgen)
Exit 1 with the recipe's findings when the build is not green. The one way a lift tool or farm job obtains a host program: no compiler call of its own."""
import os, sys
H = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.normpath(os.path.join(H, '..', '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'ports'))
from fnvnx.verify import gate_build, recipe  # noqa: E402


def host_exe(cache, root, output=None):
    try:
        res, _ = gate_build.ensure(root, os.path.abspath(cache), REPO)
    except recipe.RecipeError as e:
        raise SystemExit('hostbuild: %s: %s' % (root, e))
    if res.get('status') == 'green':
        got = res['outputs'].get(output) if output else res.get('elf')
        if got: return got['path']
        raise SystemExit('hostbuild: %s has no %s' % (root, output or 'linked output'))
    raise SystemExit('hostbuild: %s is not green: %s' % (root, res.get('status')))


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4): raise SystemExit(__doc__)
    print(host_exe(*sys.argv[1:]))
