#!/usr/bin/env python3
"""The compiler as a stage output (recipe.stage_external, is_stage_ext, Job.compiler_key, unitstage FNVNX_COMPILER_KEY). Pure plumbing on a toy producer
(Job.generate/run_all replaced inside this test by writing the output and recording stage keys): no sandbox stage runs.
  - a stage external {name, root, output, at} is valid in a recipe; a fetched-commit compiler in a guest root is a check error;
  - stage_external ingests the producer's directory output, mounts it at `at`, and returns stage_key = sha256 of the producer's stage keys;
  - the same output with other producer keys gives another stage_key (a plugin-stage change), the same keys the same one;
  - the stage key is in the env of every PP and CC stage doc, so in their keys; an empty key adds nothing;
  - (export/import of the cache entry is tests/test_storeexport.py.)
Run: python3 -I <this file>."""
import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import cas  # noqa: E402
from fnvnx.verify import recipe  # noqa: E402
from fnvnx.verify import unitstage  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


ext = {"name": "gcc", "root": "tool", "output": "COMPILER", "at": "/ext/gcc"}
check("is_stage_ext: the four keys only", recipe.is_stage_ext(ext) and not recipe.is_stage_ext(dict(ext, commit="0" * 40)))
check("a fetched-commit external is not a stage external", not recipe.is_stage_ext({"name": "x", "repo": "fnv-nx-build", "ref": "r", "commit": "0" * 40, "tree_sha256": "0" * 64, "at": "/ext/x"}))

with tempfile.TemporaryDirectory() as td:
    store, cache, repo = os.path.join(td, "store"), os.path.join(td, "ext"), os.path.join(td, "repo")
    os.makedirs(os.path.join(repo, "tool"))
    out = {"keys": {"k1", "k2"}, "payload": "bin"}

    class Prod(recipe.Job):
        files = ["tool/x"]
        red = []
        run = hit = 0
        rec = {"outputs": {"COMPILER": "gcc"}}

        def __init__(self, *a):
            self.keys, self.ext = set(), {}
            self.red = []

        def externals(self, ws):
            pass

        def generate(self, ws):
            d = os.path.join(ws, "root", "gcc", "bin")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "g++"), "w").write(out["payload"])
            self.keys = set(out["keys"])

        def run_all(self, ws):
            pass

    recipe_job = recipe.Job
    recipe.Job = Prod
    recipe._copy = lambda *a, **k: None
    try:
        user = object.__new__(recipe_job)
        user.repo, user.store, user.jobs, user.extcache, user.keys, user.run, user.hit = repo, store, 1, cache, set(), 0, 0
        user.ext, user.rec = {}, {"external": [ext], "compiler": {"external": "gcc", "cc": "bin/gcc", "cxx": "bin/g++"}}
        ws = tempfile.mkdtemp(dir=td)
        user.externals(ws)
        e = user.ext["gcc"]
        check("stage_external: mounted at `at`, tree ingested, host materialised", e["at"] == "/ext/gcc" and e["tree"] == cas.ingest(e["host"], store)
              and open(os.path.join(e["host"], "bin", "g++")).read() == "bin", e)
        k1 = user.compiler_key()
        check("the compiler key is the producer's key digest", len(k1) == 64 and e["stage_key"] == k1)
        user.externals(tempfile.mkdtemp(dir=td))
        check("the same producer keys give the same key", user.compiler_key() == k1)
        out["keys"] = {"k1", "k3"}
        user.externals(tempfile.mkdtemp(dir=td))
        k2 = user.compiler_key()
        check("another producer key (a plugin stage changed) gives another compiler key, same output tree", k2 != k1 and user.ext["gcc"]["tree"] == e["tree"])
        out["payload"] = "bin2"
        user.externals(tempfile.mkdtemp(dir=td))
        check("another output content gives another tree", user.ext["gcc"]["tree"] != e["tree"])
        host_only = object.__new__(recipe_job)
        host_only.rec, host_only.ext = {"compiler": {"cc": "gcc-13", "cxx": "g++-13"}}, {}
        check("a distribution compiler has no compiler key", host_only.compiler_key() == "")
    finally:
        recipe.Job = recipe_job

# the key is in the stage env
b = object.__new__(unitstage.Build)
b.cxx, b.iargs, b.flags, b.cc_flags, b.cc_drop, b.cc_inputs, b.roots = "/ext/gcc/bin/g++", [], [], [], set(), [], [("/src", "t")]
b.rt, b.variant, b.compiler_key = {"inputs": [{"at": "/ext/gcc", "tree": "t"}]}, "", ""
d0 = b.doc("pp", "u.cc", [], [], "/src/u.cc")
b.compiler_key = "ab" * 32
d1 = b.doc("pp", "u.cc", [], [], "/src/u.cc")
d2 = b.doc("cc", "u.cc", [], [], "/src/u.cc")
check("an empty compiler key adds nothing to the env", "FNVNX_COMPILER_KEY" not in d0["env"])
check("the compiler key is in the env of PP and CC stages", d1["env"].get("FNVNX_COMPILER_KEY") == "ab" * 32 and d2["env"].get("FNVNX_COMPILER_KEY") == "ab" * 32)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
