#!/usr/bin/env python3
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
put(repo, "r/d/t1.tsv", "1\n")
put(repo, "r/d/t2.tsv", "2\n")
put(repo, "r/d/untracked.tsv", "u\n")
os.makedirs(os.path.join(repo, "r/empty"))
put(repo, "r/empty/x", "x\n")
os.symlink("/etc/passwd", os.path.join(repo, "r/link"))
git(repo, "add", "r/in.tsv", "r/link", "r/d/t1.tsv", "r/d/t2.tsv")
R = lambda ref, rc=rec, **kw: recipe.resolve_input("r", ref, repo, cache, rc)   # noqa: E731

p, ident, files = R("in.tsv")
check("bare string resolves to the tracked file of the root", p == os.path.join(repo, "r/in.tsv") and ident == "r/in.tsv", (p, ident))
p, ident, files = R({"external": "specs", "path": "specs"})
check("external resolves to the mounted tree, ident carries tree_sha256 + path",
      os.path.isfile(os.path.join(p, "a.spec")) and ident == thash + ":specs", (p, ident))
p, ident, files = R("d")
check("tracked directory: only its tracked files are listed (the untracked one is not seen)", files == ["r/d/t1.tsv", "r/d/t2.tsv"], files)
check("red: a directory with no tracked file", "not tracked" in (red(lambda: R("empty")) or ""))
check("file and external path carry no list", R("in.tsv")[2] is None and R({"external": "specs", "path": "specs"})[2] is None)
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

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
