#!/usr/bin/env python3
"""A tool step reads ONE file of an external ({"external": NAME, "path": FILE}, RECIPE.md "Single file of an external"): the external is pinned by {commit, tree_sha256}
over its whole checkout, the step mounts only that file at /in/N.
  - the step is green and writes what the file holds; the recipe check accepts the form;
  - the key holds the file's content: changing another file of the external (a new commit, a new tree_sha256) leaves the step key, changing the file re-keys it;
  - a path that is not in the external's tree is red (the step does not run), as is a path that leaves the tree, an undeclared external, and a wrong tree_sha256.
The debs are a toy static busybox through recipe.deb_pins and Job._deb_tree (as test_toolstep.py); the sandbox, the externals and the key are the real ones.
Run: python3 -I <this file>. Needs root, a static busybox, the declared environment."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import recipe  # noqa: E402

if not shutil.which("busybox"):
    sys.exit("test_toolext: needs a static busybox on PATH (as test_toolstep.py)")
fails = 0
T = "ports/fnv-nx/"
R = T + "tools"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def toy_tree(self, tmp, name, pin):
    d = os.path.join(tmp, "deb-" + name)
    os.makedirs(os.path.join(d, "usr/bin"))
    if name == "base-files":
        os.makedirs(os.path.join(d, "etc"))
        return None
    shutil.copy(shutil.which("busybox"), os.path.join(d, "usr/bin/busybox"))
    for a in ("sh", "cat"):
        os.symlink("busybox", os.path.join(d, "usr/bin", a))
    os.symlink("usr/bin", os.path.join(d, "bin"))
    return None


recipe.deb_pins = lambda: {"toybb": {"version": "1", "sha256": "0" * 64}, "base-files": {"version": "1", "sha256": "1" * 64}}
recipe.Job._deb_tree = toy_tree
recipe.env_blocked = lambda repo: None


def publish(td, name, files):
    """A commit of FILES {path: text} pushed to branch NAME of the bare artifacts repo (recipe.ARTIFACTS, a stand-in for fnv-nx-build) -> (commit, tree_sha256 of the checkout)."""
    bare = os.path.join(td, "artifacts.git")
    if not os.path.isdir(bare):
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    d = os.path.join(td, "pub-" + name)
    os.makedirs(d)
    for p, t in files.items():
        w(d, p, t)
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", name)
    git(d, "push", "-q", bare, "HEAD:refs/heads/" + name)
    subprocess.run(["git", "clone", "-q", bare, d + "-co", "-b", name], check=True, capture_output=True)
    return git(d, "rev-parse", "HEAD").strip(), cas.ingest(d + "-co", os.path.join(td, "hs-" + name), [".git"])


def make(td, name, ext, path="doc.txt", declared=True):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    w(repo, R + "/s.sh", "cat /in/1 > /out/o.txt\n")
    step = {"name": "one", "exec": "/bin/sh", "args": ["/in/2"], "inputs": [{"external": "sdm", "path": path}, R + "/s.sh"], "debs": ["toybb"], "outputs": ["o.txt"]}
    rec = {"format": "fnvnx-recipe/1", "run": [step], "outputs": {"o": "o.txt"}}
    if declared:
        rec["external"] = [ext]
    w(repo, R + "/build.json", json.dumps(rec, indent=1))
    w(repo, "ports/fnv-nx/verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
    w(repo, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "verify", "class": "tool"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def produced(repo, td, tag):
    j = recipe.Job(repo, R, os.path.join(td, "store"), 2, recipe.ext_of_store(os.path.join(td, "store")))
    ws = tempfile.mkdtemp(prefix="te-" + tag, dir=td)
    recipe._copy(repo, [f for f in j.files if recipe.under(f, R)], R, os.path.join(ws, "root"))
    try:
        j.externals(ws)
        j.run_all(ws)
    except recipe.BuildRed as e:
        j.red = j.red or [e.args[0]]
    p = os.path.join(ws, "root", "o.txt")
    return j, (open(p).read() if os.path.exists(p) else None)


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    recipe.ARTIFACTS = os.path.join(td, "artifacts.git")
    c1, t1 = publish(td, "sdm1", {"doc.txt": "the document\n", "other.txt": "one\n", "sub/x.txt": "x\n"})
    ex1 = {"name": "sdm", "repo": "fnv-nx-build", "ref": "sdm1", "commit": c1, "tree_sha256": t1, "at": "/ext/sdm"}
    repo = make(td, "r1", ex1)
    errs = recipe.check(json.load(open(os.path.join(repo, R, "build.json"))), R, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    check("the recipe with a single-file external input is valid", not errs, errs)
    j1, out1 = produced(repo, td, "1")
    check("the step is green and writes what the one file holds", not j1.red and out1 == "the document\n", (j1.red, out1))
    # another commit that changes only other.txt: a new tree_sha256, the same file
    c2, t2 = publish(td, "sdm2", {"doc.txt": "the document\n", "other.txt": "two\n", "sub/x.txt": "x\n"})
    ex2 = dict(ex1, ref="sdm2", commit=c2, tree_sha256=t2)
    j2, out2 = produced(make(td, "r2", ex2), td, "2")
    check("a change of another file of the external (new commit and tree hash) leaves the step key: only the file's content is in it", not j2.red and out2 == out1 and j2.keys == j1.keys, (j2.red, len(j2.keys ^ j1.keys)))
    c3, t3 = publish(td, "sdm3", {"doc.txt": "a new document\n", "other.txt": "one\n", "sub/x.txt": "x\n"})
    ex3 = dict(ex1, ref="sdm3", commit=c3, tree_sha256=t3)
    j3, out3 = produced(make(td, "r3", ex3), td, "3")
    check("a change of the file re-keys the step and its output", not j3.red and out3 == "a new document\n" and j3.keys != j1.keys, (j3.red, out3))
    # red cases
    j4, out4 = produced(make(td, "r4", ex1, path="missing.txt"), td, "4")
    check("a file that is not in the external's tree is red and the step does not run", bool(j4.red) and out4 is None, (j4.red, out4))
    j5, out5 = produced(make(td, "r5", ex1, path="../doc.txt"), td, "5")
    check("a path that leaves the tree is red", bool(j5.red) and out5 is None, (j5.red, out5))
    j6, out6 = produced(make(td, "r6", dict(ex1, tree_sha256="0" * 64)), td, "6")
    check("a tree_sha256 that is not the checkout's is red", bool(j6.red) and out6 is None, (j6.red, out6))
    r7 = make(td, "r7", ex1, declared=False)
    errs7 = recipe.check(json.load(open(os.path.join(r7, R, "build.json"))), R, recipe.tracked(r7), recipe.load_admit(r7), None, r7)
    check("an external that the recipe does not declare is red in check", any("external" in e and "sdm" in e for e in errs7), errs7)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
