#!/usr/bin/env python3
"""prepare_profile.py <decomp> <out tree> <tools dir> <report> - one cold prepare (tools/prepare.py of <tools dir>)
with every rule wrapped in a timer: the total, the top 40 rules by wall time (rule module and name), and the
prepared tree's content hash (tools/farm/tree_hash.sh's value, for a byte-identity check of a speed fix). T1: the
prepare regression 16 -> 52 min on bl1 since ef092d51. Timings only: nothing of the decomp in the report.
FNVNX_RECON (tools/farm/recon.sh fetch_recon) as for any prepare."""
import collections, functools, os, subprocess, sys, time
decomp, out, tools, report = sys.argv[1:5]
sys.path.insert(0, tools)
import prepare
T, N = collections.Counter(), collections.Counter()


def wrap(r):
    k = (os.path.basename(r.__code__.co_filename), r.__name__)

    @functools.wraps(r)
    def w(path, text, stats):
        t = time.perf_counter()
        try:
            return r(path, text, stats)
        finally:
            T[k] += time.perf_counter() - t
            N[k] += 1
    return w


prepare.RULES[:] = [wrap(r) for r in prepare.RULES]
orig = prepare.load_stream_rules
prepare.load_stream_rules = lambda: [wrap(r) for r in orig()]
sys.argv = ['prepare.py', decomp, out]
t0 = time.time()
err = None
try:
    prepare.main()
except BaseException as ex:   # a POST hook's failure is reported, the timings are kept
    err = repr(ex)
total = time.time() - t0
th = subprocess.run(['bash', os.path.join(tools, 'farm', 'tree_hash.sh'), out], capture_output=True, text=True).stdout.strip()
with open(report, 'w') as f:
    f.write('total %.1f s, rules %.1f s, tree hash %s%s\n' % (total, sum(T.values()), th or '?',
                                                          '; prepare raised: ' + err if err else ''))
    for k, v in T.most_common(40):
        f.write('%8.1f s  %6d calls  %s %s\n' % (v, N[k], *k))
print(open(report).read())
