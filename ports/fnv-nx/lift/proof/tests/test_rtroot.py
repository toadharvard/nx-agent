"""lift/rt/build.json is the build of the lift runtime, and its flags are the flags the lifted packs are compiled with.
The one definition of the lifted-code flags is test/instest.py FLAGS through tools/liftgen.py pack_flags (what `liftgen.py --recipe` writes into
lift/pack/build.json). Checked, as lists: flags.asm equal; flags.cxx = the pack's list without -w (fnvnx_names refuses it) followed by the semantics flags that verify/recipe.py requires of
native C++ (SEMANTICS_FLAGS) and the pack's list does not hold; flags.c = the pack's c list followed by the freestanding rt C flags (the build lines of rt.c,
rt/rcptab.c, rt/x87.c in the header comments and in test/instest.py). Also: the plugins are exactly recipe.py's PLUGIN_SOURCES in load order with PLUGIN_ARGS,
the compiler is the pin of verify/guest_compiler.json, and no tracked .c under lift/rt is C++ (a unit's language is its extension)."""
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
VERIFY = os.path.normpath(os.path.join(LIFT, '..', 'verify'))
sys.path.insert(0, os.path.join(LIFT, 'tools'))
sys.path.insert(0, VERIFY)
import liftgen  # noqa: E402
import recipe as R  # noqa: E402

RT_C_EXTRA = ['-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-builtin']
NATIVE_DROP = ('-w',)     # plugin fnvnx_names (NAMES1) refuses -w: it hides the class-key reports the names are computed from
bad = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else ': ' + str(detail)))
    if not ok:
        bad.append(name)


def instest_flags():
    t = ast.parse(open(os.path.join(LIFT, 'test', 'instest.py')).read())
    n = [x for x in t.body if isinstance(x, ast.Assign) and isinstance(x.targets[0], ast.Name) and x.targets[0].id == 'FLAGS'][0]
    return eval(compile(ast.Expression(n.value), 'FLAGS', 'eval'))


pack = liftgen.pack_flags(instest_flags())
rec = json.load(open(os.path.join(LIFT, 'rt', 'build.json')))
fl = rec['flags']
check('flags.asm == pack asm', fl['asm'] == pack['asm'], (fl['asm'], pack['asm']))
want_cxx = [f for f in pack['cxx'] if f not in NATIVE_DROP] + [f for f in R.SEMANTICS_FLAGS if f not in pack['cxx']]
check('flags.cxx == pack cxx + SEMANTICS_FLAGS missing from it', fl['cxx'] == want_cxx, (fl['cxx'], want_cxx))
check('flags.c == pack c + rt C flags', fl['c'] == pack['c'] + RT_C_EXTRA, (fl['c'], pack['c'] + RT_C_EXTRA))
pin = json.load(open(os.path.join(VERIFY, 'guest_compiler.json')))
check('compiler is the pinned guest compiler', rec['compiler'] == {'external': pin['external']['name'], 'cc': pin['cc'], 'cxx': pin['cxx']}, rec['compiler'])
check('external is the pin', rec['external'] == [pin['external']], rec['external'])
check('plugins are PLUGIN_SOURCES in load order with PLUGIN_ARGS',
      [(p['name'], p['source'], p['args'], p.get('inputs', [])) for p in rec['plugins']] ==
      [(n, s, R.PLUGIN_ARGS[n], R.PLUGIN_INPUTS.get(n, [])) for n, s in R.PLUGIN_SOURCES.items()])
check('units are all', rec['units'] == 'all')
root = os.path.join(LIFT, 'rt')
files = subprocess.check_output(['git', 'ls-files', '--', root], text=True).split('\n')
cc = [os.path.basename(f) for f in files if f.endswith('.c')]
cxx_like = [f for f in cc if re.search(r'^\s*(#include\s*<(?:new|cstdint)>|namespace\b|extern\s+"C"|template\s*<|static_assert\b)', open(os.path.join(os.path.dirname(root), 'rt', f)).read(), re.M)]
check('no .c unit under lift/rt uses C++-only syntax', not cxx_like, cxx_like)
sys.exit(1 if bad else 0)
