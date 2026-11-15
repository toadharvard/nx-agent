"""The one reader of the generated headers of the admitted root lift/rt (rcpref.h, cpumodel_tab.inc, fnvl_cpuref.h, ...): its declared output GEN (lift/rt/build.json
stage gen_cpuref and the other generate stages), published once in GATE_CACHE by gate_build.ensure.  There is no committed copy and no second generator (rules 2, 3):
every reader (gate_fpexact, ssefp, fpc_host) calls gen_dir()."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.normpath(os.path.join(HERE, '..', '..', 'verify'))
if VERIFY not in sys.path:
    sys.path.insert(0, VERIFY)
import gate_build  # noqa: E402
import recipe  # noqa: E402

ROOT = 'ports/fnv-nx/lift/rt'
OUTPUT = 'GEN'


def gen_dir(cache=None, repo='.', root=ROOT):
    """-> absolute path of the published GEN directory of ROOT (built or taken from the store CACHE, default env GATE_CACHE, by gate_build.ensure); RuntimeError
    when no store is given or the root does not build or does not declare GEN"""
    cache = cache or os.environ.get('GATE_CACHE')
    if not cache:
        raise RuntimeError('GATE_CACHE (the verdict store) is not set: the generated headers of %s are its published output %s' % (root, OUTPUT))
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        raise RuntimeError('%s does not build: %s' % (root, '; '.join(e.errors)[:300]))
    if res.get('status') == 'red':
        raise RuntimeError('%s does not build (stage %s)' % (root, res['failure']['stage']))
    try:
        return res['outputs'][OUTPUT]['path']
    except KeyError:
        raise RuntimeError('%s publishes no output %s' % (root, OUTPUT))
