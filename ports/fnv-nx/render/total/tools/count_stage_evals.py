"""MEASUREMENT TOOL, not run by any gate: Counts how often each run step of the sm_link roots is evaluated when the sm_link gate reads its summary through recipe.resolve_input.
The sandbox is stubbed: Job.run_one records the step, resolves the step's stage-ref inputs exactly as the real run_one does (recipe.resolve_input),
and writes its declared outputs as empty files. Nothing else of recipe.py is changed."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import collections, os, sys, tempfile
repo = sys.argv[1]
sys.path.insert(0, os.path.join(repo, "ports/fnv-nx/verify"))
import fnvnx.verify.recipe as recipe
calls = collections.Counter()
def run_one(self, ws, i, g):
    calls[(self.root.split("/")[-1], g["name"])] += 1
    for ref in g["inputs"]:
        if isinstance(ref, dict) and "stage" in ref and ref["stage"].partition(":")[0] != self.root:
            recipe.resolve_input(self.root, ref, self.repo, self.cache_for_stub)
    for o in g["outputs"]:
        p = os.path.join(ws, "root", o)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("")
recipe.Job.run_one = run_one
orig_init = recipe.Job.__init__
def init(self, repo_, root, store, jobs, extc):
    orig_init(self, repo_, root, store, jobs, extc)
    self.cache_for_stub = os.path.dirname(store)
recipe.Job.__init__ = init
recipe.Job.externals = lambda self, ws: None
cache = tempfile.mkdtemp(prefix="cnt-")
os.makedirs(os.path.join(cache, "store"))
R = "ports/fnv-nx/render/shaders/sm_link_sum"
recipe.resolve_input(R, {"stage": R + ":run:summary", "path": "summary.json"}, repo, cache)
tot = collections.Counter()
for (root, name), n in sorted(calls.items()):
    tot[root] += n
    print("%-14s %-9s evaluated %d times" % (root, name, n))
print("per root:", dict(tot), "all run steps:", sum(tot.values()))
