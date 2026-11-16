"""The one reader of the generated headers of the admitted root lift/rt (rcpref.h, cpumodel_tab.inc, fnvl_cpuref.h, ...): its declared output GEN (lift/rt/build.json
stage gen_cpuref and the other generate stages), published in the verdict store by the recipe and read through gate_build.published_output.  There is no committed copy and no second generator (rules 2, 3):
every reader (gate_fpexact, ssefp, fpc_host) calls gen_dir(cache) with the cache it was given."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: qualified imports of another root's modules (contract.X, verify.X)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.normpath(os.path.join(HERE, '..', '..', 'verify'))
if VERIFY not in sys.path:
    sys.path.insert(0, VERIFY)
from verify import gate_build  # noqa: E402

ROOT = 'ports/fnv-nx/lift/rt'
OUTPUT = 'GEN'


def gen_dir(cache, repo='.', root=ROOT):
    """-> absolute path of the published GEN directory of ROOT in the verdict store CACHE (an explicit argument: no environment read, nothing built here), through
    gate_build.published_output; LookupError (with the reason) when ROOT is not published there, does not declare GEN, or the published path is gone"""
    if not cache:
        raise ValueError('genout.gen_dir: cache (the verdict store) is required')
    path, _ = gate_build.published_output(cache, root, OUTPUT, repo)
    return path
