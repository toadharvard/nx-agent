#!/usr/bin/env python3
"""The recipe of the product root lift/pack: write lift/pack/build.json for the published pack tree (fnv-nx-build branch, commit, cas.py input tree sha).
This tool writes recipes; it runs on the developer's host and is no stage of any recipe (verify/recipe.py imports are fine here). The shard and finish stages are tools/liftgen.py,
which imports nothing of verify/.
    liftrecipe.py REF COMMIT TREE_SHA256"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: qualified imports of another root's modules (contract.X, verify.X)
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, LIFT)
import liftgen
# The flags of lifted C++: the recipe's (verify/guest_compiler.json flags.semantics + lift/pack/build.json), resolved by resolved_flags() for every test that compiles pack code. No flag that turns an error into a warning or silences one.
PACK_ROOT_FLAGS = {'c': ['-Werror=attributes'], 'cxx': ['-std=gnu++17', '-O2', '-Werror=attributes'], 'asm': []}   # the per-root classes of the pack (recipe.PER_ROOT); the semantic flags are the pin's, never listed here


def resolved_flags(driver='aarch64-linux-gnu-g++', kind='cxx'):
    """The flags the recipe passes to a unit of the product root lift/pack: the pin's flags.semantics as DRIVER applies them to KIND (recipe.semantics_for), then the root's own
    flags of lift/pack/build.json. The one list: every test that compiles pack code takes it from here, and nothing else carries a flag list of the lifted code."""
    sys.path.insert(0, os.path.join(LIFT, '..', 'verify'))
    from verify import recipe as R
    rec = json.load(open(os.path.join(LIFT, 'pack', 'build.json')))
    return R.semantics_for(driver, kind, origin='lifted') + list(rec['flags'][kind])
GEN_USE = {'root': 'ports/fnv-nx/lift/rt', 'output': 'GEN'}      # the generated rt headers (fnvl_cpuref.h, fnvl_faults.h, fnvl_sys.h): included, not copied


INC_DIRS = ['ports/fnv-nx/lift/rt', 'ports/fnv-nx/platform/include']      # where the runtime's headers are, in `#include "x"` search order (the rt root's GEN output is searched last)


def inc_stages():
    """the `generated` steps of the pack's own header directory inc/ (fnvl_prelude.h, imp.h): the runtime's headers of the CURRENT tree, inlined by tools/prelude.py. The
    inputs of each step are the files of the closure of its source over INC_DIRS (prelude.closure: a pure function of the headers' include lines), so a change of
    one of those headers re-keys the step and, through the include directory, every unit; any other file of rt/ does not."""
    import prelude
    repo = os.path.join(LIFT, '..', '..', '..')
    dirs = [os.path.join(repo, d) for d in INC_DIRS]
    out = []
    for name, (src, _) in sorted(prelude.INC.items()):
        files = sorted(os.path.relpath(os.path.join(d, n), repo) for d, n in prelude.closure(dirs, src).values())
        out.append({'tool': 'ports/fnv-nx/lift/tools/packstage.py', 'args': ['inc', name, '/out/inc/' + name] + ['/in/' + d for d in INC_DIRS] + ['/uses/ports/fnv-nx/lift/rt/GEN'],
                    'inputs': files, 'outputs': ['inc/' + name]})
    return out


def recipe(ref, commit, tree, dest=None):
    """lift/pack/build.json. The recipe is the one definition of the semantic flags (verify/guest_compiler.json flags.semantics, appended to every guest compile stage), so
    the build.json holds only the per-root classes (PACK_ROOT_FLAGS; recipe.PER_ROOT: -O, -W, -D, -std ...), and the plugins are the full set of recipe.PLUGIN_SOURCES in
    load order with recipe.plugin_args(origin) of the pack's declared lifted origin (fnvnx_ftoi mode lifted); the units are the fixed names."""
    sys.path.insert(0, os.path.join(LIFT, '..', 'verify'))
    from verify import recipe as R
    keep = lambda fl: [f for f in fl if R.PER_ROOT.fullmatch(f)]
    rec = {'format': 'fnvnx-recipe/1', 'profile': 'guest',      # the game's compiler, its runtime and the full plugin set come from the profile (verify/guest_compiler.json, recipe.py): a root names only its own flags
           'flags': {k: keep(v) for k, v in PACK_ROOT_FLAGS.items()},
           'include': ['ports/fnv-nx/lift/pack/inc'], 'units': 'all',
           'uses': [{'root': 'ports/fnv-nx/bridgetable', 'output': 'BRIDGE'}, {'root': 'ports/fnv-nx/loader', 'output': 'IMPORTED_TSV'}, {'root': 'ports/fnv-nx/loader', 'output': 'MODULES_TSV'}, GEN_USE],
           'include_uses': [GEN_USE],
           'generated': [{'tool': 'ports/fnv-nx/lift/tools/packstage.py', 'args': ['copy', '/ext/liftpack', '/out'], 'inputs': [], 'outputs': liftgen.unit_names()},
                         {'tool': 'ports/fnv-nx/lift/tools/packstage.py', 'args': ['imp', '--bridge', '/uses/ports/fnv-nx/bridgetable/BRIDGE', '--imported', '/uses/ports/fnv-nx/loader/IMPORTED_TSV', '--prelude', 'fnvl_prelude.h', '--out', '/out'],
                          'inputs': [], 'outputs': ['imp.cpp', 'imp_entries.tsv', 'imp_blocked.tsv']},
                         {'tool': 'ports/fnv-nx/lift/tools/packstage.py', 'args': ['entries', '/ext/liftpack/entries.tsv', '/stage/imp_entries.tsv', '/uses/ports/fnv-nx/bridgetable/BRIDGE', '/uses/ports/fnv-nx/loader/MODULES_TSV', '/out/entries.tsv'],
                          'inputs': [{'stage': 'ports/fnv-nx/lift/pack:gen:1', 'path': 'imp_entries.tsv'}], 'outputs': ['entries.tsv']},
                         {'tool': 'ports/fnv-nx/lift/tools/packstage.py', 'args': ['disp', '--rows', '/out/disp_rows.tsv', '/stage/entries.tsv', '/out/disp.cc'],
                          'inputs': [{'stage': 'ports/fnv-nx/lift/pack:gen:2', 'path': 'entries.tsv'}], 'outputs': ['disp.cc', 'disp_rows.tsv']}] + inc_stages(),
           'outputs': {'ENTRIES': 'entries.tsv', 'DISP_ROWS': 'disp_rows.tsv'},
           'link': None,
           'external': [{'name': 'liftpack', 'repo': 'fnv-nx-build', 'ref': ref, 'commit': commit, 'tree_sha256': tree, 'at': '/ext/liftpack'}]}
    dest = dest or os.path.join(LIFT, 'pack', 'build.json')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'w') as f:
        json.dump(rec, f, indent=1); f.write('\n')



if __name__ == '__main__':
    if len(sys.argv) != 4: sys.exit('usage: liftrecipe.py REF COMMIT TREE_SHA256')
    recipe(*sys.argv[1:])
