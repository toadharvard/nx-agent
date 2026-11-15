#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/link ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/verify
"""Tests for recipe.resolve_input (the one reader of a path named by a gate input file): a bare path resolves to the tracked file of the root,
an external to its mounted, checked tree; red for an undeclared external, a path that leaves the tree, a missing or untracked path, a
tree_sha256 mismatch. Plain script: exit 0 on pass. Needs only git."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402
import recipe  # noqa: E402
import scratch  # noqa: E402

scratch.install()
BASE = tempfile.mkdtemp(prefix="resolve-input-")
FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:300]))
    if not ok:
        FAILED.append(name)


def git(d, *a):
    return subprocess.run(["git", "-C", d] + list(a), check=True, capture_output=True, text=True).stdout.strip()


def put(d, rel, text):
    os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
    with open(os.path.join(d, rel), "w") as f:
        f.write(text)


def red(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None


# the external: a one-commit tree, fetched as a bare repo at CACHE/external/<commit> (what the build does)
ext = os.path.join(BASE, "ext")
os.makedirs(ext)
git(ext, "init", "-q")
put(ext, "specs/a.spec", "1 stdcall f()\n")
put(ext, "names.tsv", "x\ty\n")
git(ext, "add", "-A")
git(ext, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "e")
commit = git(ext, "rev-parse", "HEAD")
cache = os.path.join(BASE, "cache")
bare = os.path.join(cache, "external", commit)
os.makedirs(bare)
subprocess.run(["git", "clone", "-q", "--bare", ext, bare], check=True, capture_output=True)
co = os.path.join(BASE, "co")
subprocess.run(["git", "clone", "-q", ext, co], check=True, capture_output=True)
thash = cas.ingest(co, os.path.join(BASE, "hs"), [".git"])
rec = {"external": [{"name": "specs", "repo": "x", "ref": "r", "commit": commit, "tree_sha256": thash, "at": "/ext/specs"}]}

repo = os.path.join(BASE, "repo")
os.makedirs(repo)
git(repo, "init", "-q")
put(repo, "r/in.tsv", "a\n")
put(repo, "r/untracked.tsv", "u\n")
os.symlink("/etc/passwd", os.path.join(repo, "r/link"))
git(repo, "add", "r/in.tsv", "r/link")
R = lambda ref, rc=rec, **kw: recipe.resolve_input("r", ref, repo, cache, rc)   # noqa: E731

p, ident = R("in.tsv")
check("bare string resolves to the tracked file of the root", p == os.path.join(repo, "r/in.tsv") and ident == "r/in.tsv", (p, ident))
p, ident = R({"external": "specs", "path": "specs"})
check("external resolves to the mounted tree, ident carries tree_sha256 + path",
      os.path.isfile(os.path.join(p, "a.spec")) and ident == thash + ":specs", (p, ident))
check("red: undeclared external", "does not declare" in (red(lambda: R({"external": "nope", "path": "names.tsv"})) or ""))
check("red: .. escape (bare)", red(lambda: R("../x")) is not None)
check("red: .. escape (external)", red(lambda: R({"external": "specs", "path": "../x"})) is not None)
check("red: absolute path", red(lambda: R("/etc/passwd")) is not None)
check("red: symlink leaving the tree", "symlink" in (red(lambda: R("link")) or ""))
check("red: missing file", "does not exist" in (red(lambda: R({"external": "specs", "path": "nope.tsv"})) or ""))
check("red: untracked file", "not tracked" in (red(lambda: R("untracked.tsv")) or ""))
check("red: malformed ref", red(lambda: R({"external": "specs"})) is not None)
bad = {"external": [dict(rec["external"][0], tree_sha256="0" * 64)]}
recipe._EXT_CHECKED.clear()
check("red: tree_sha256 mismatch", "tree_sha256" in (red(lambda: R({"external": "specs", "path": "names.tsv"}, bad)) or ""))

# a tracked directory: a copy holding exactly its tracked files; an untracked file inside is not visible; editing a tracked file changes the ident
put(repo, "r/d/t.txt", "t1\n")
put(repo, "r/d/sub/u.txt", "u1\n")
put(repo, "r/d/untracked.txt", "x\n")
git(repo, "add", "r/d/t.txt", "r/d/sub/u.txt")
pd, idd = R("d")
check("tracked directory: the copy holds the tracked files", open(os.path.join(pd, "t.txt")).read() == "t1\n" and open(os.path.join(pd, "sub/u.txt")).read() == "u1\n", pd)
check("tracked directory: an untracked file inside is not visible", not os.path.exists(os.path.join(pd, "untracked.txt")), os.listdir(pd))
check("tracked directory: the copy is not the working tree", not pd.startswith(repo), pd)
put(repo, "r/d/t.txt", "t2\n")
git(repo, "add", "r/d/t.txt")
pd2, idd2 = R("d")
check("tracked directory: editing a tracked file inside changes the ident", idd2 != idd and open(os.path.join(pd2, "t.txt")).read() == "t2\n", (idd, idd2))
check("tracked directory: same content, same ident", R("d")[1] == idd2)
check("tracked directory: needs the cache", red(lambda: recipe.resolve_input("r", "d", repo, None, rec)) is not None)


print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
