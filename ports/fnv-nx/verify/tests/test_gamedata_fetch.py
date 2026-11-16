#!/usr/bin/env python3
"""farm/gamedata_fetch.sh reads only what the admitted recipes declare, by commit, tree-checked (`recipe.py externals`):
  - the declared gamedata/exe and gamedata/redist are installed from a toy artifacts repository, by commit, and a second run is "up to date";
  - ARTIFACTS_URL only changes the location: a repository that holds a different tree under the pinned commit's name cannot be installed (red);
  - a data item that no recipe declares is red, naming the remedy;
  - `recipe.py externals` lists the same ref pinned two ways as an error.
Real git, the real script and the real recipe.py in a toy checkout. Run: python3 -I <this file>. Needs git, sha256sum."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NX = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(NX, "verify"))
import cas  # noqa: E402

fails = 0
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_TERMINAL_PROMPT="0")


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd] + list(a), check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def artifacts_repo(td, name, exe_text):
    """a repository with two commits' worth of content: returns (path, {ref: (commit, tree)}) - each ref's content committed as the root of its own commit"""
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    pins = {}
    for ref, files in (("gamedata/exe", {"FalloutNV.exe": exe_text, "FalloutPrefs.ini": "[Display]\n"}), ("gamedata/redist", {"d3dx9_31.dll": "dll\n"})):
        for f in os.listdir(repo):
            if f != ".git":
                shutil.rmtree(os.path.join(repo, f)) if os.path.isdir(os.path.join(repo, f)) else os.remove(os.path.join(repo, f))
        for f, t in files.items():
            w(repo, f, t)
        if ref.endswith("redist"):
            sums = subprocess.run("sha256sum d3dx9_31.dll", shell=True, cwd=repo, capture_output=True, text=True).stdout
            w(repo, "SHA256SUMS", sums)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", ref)
        c = git(repo, "rev-parse", "HEAD")
        co = os.path.join(td, "co-" + name + ref.replace("/", "_"))
        os.makedirs(co)
        git(repo, "--work-tree", co, "checkout", "-f", "HEAD", "--", ".")
        pins[ref] = (c, cas.ingest(co, os.path.join(td, "probe-store")))
    return repo, pins


def toy_checkout(td, pins, extra=()):
    chk = os.path.join(td, "chk")
    os.makedirs(chk)
    git(chk, "init", "-q")
    for d in ("verify", "link", "contract"):
        shutil.copytree(os.path.join(NX, d), os.path.join(chk, "ports/fnv-nx", d), ignore=shutil.ignore_patterns("__pycache__", "tests"))
    os.makedirs(os.path.join(chk, "ports/fnv-nx/farm"))
    os.makedirs(os.path.join(chk, "ports/fnv-nx/toolchain/msvcmodel"))
    shutil.copy(os.path.join(NX, "toolchain/msvcmodel/DATAMODEL.json"), os.path.join(chk, "ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json"))   # recipe.py reads the data model when it is imported
    shutil.copy(os.path.join(NX, "farm/gamedata_fetch.sh"), os.path.join(chk, "ports/fnv-nx/farm/gamedata_fetch.sh"))
    ext = [{"name": r.replace("/", "_"), "repo": "x", "ref": r, "commit": c, "tree_sha256": t, "at": "/ext/" + r.replace("/", "_")} for r, (c, t) in pins.items()] + list(extra)
    w(chk, "ports/fnv-nx/toyroot/build.json", json.dumps({"format": "fnvnx-recipe/1", "external": ext}))
    w(chk, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": "ports/fnv-nx/toyroot", "class": "product", "target": "host", "recipe": "ports/fnv-nx/toyroot/build.json"}]}))
    git(chk, "add", "-A")
    git(chk, "commit", "-q", "-m", "toy")
    return chk


def run(chk, out, url, *args, cache):
    return subprocess.run(["bash", os.path.join(chk, "ports/fnv-nx/farm/gamedata_fetch.sh"), out] + list(args), capture_output=True, text=True,
                          env=dict(ENV, ARTIFACTS_URL=url, GAMEDATA_CACHE=cache, GAMEDATA_TRIES="1"))


with tempfile.TemporaryDirectory() as td:
    good, pins = artifacts_repo(td, "good", "exe v1\n")
    evil, _ = artifacts_repo(td, "evil", "exe v2 (not the pinned tree)\n")
    chk = toy_checkout(td, pins)
    cache, out = os.path.join(td, "cache"), os.path.join(td, "game")
    r = run(chk, out, good, "--no-data", cache=cache)
    check("the declared exe and redist are installed by commit", r.returncode == 0 and open(os.path.join(out, "FalloutNV.exe")).read() == "exe v1\n" and os.path.isfile(os.path.join(out, "d3dx9_31.dll")), (r.returncode, r.stderr[-300:], r.stdout[-300:]))
    r = run(chk, out, good, "--no-data", cache=cache)
    check("a second run with unchanged pins is up to date", r.returncode == 0 and "up to date" in r.stdout, r.stdout + r.stderr)
    out2 = os.path.join(td, "game2")
    cache2 = os.path.join(td, "cache2")
    r = run(chk, out2, evil, "--no-data", cache=cache2)
    check("another location serving other bytes cannot be installed (the pinned commit is not there)", r.returncode == 1 and not os.path.exists(os.path.join(out2, "FalloutNV.exe")), (r.returncode, r.stderr[-300:]))
    r = run(chk, os.path.join(td, "game3"), good, "someitem", cache=cache)
    check("a data item that no recipe declares is red and names the remedy", r.returncode == 1 and "declared by no recipe" in r.stderr, (r.returncode, r.stderr[-300:]))
    lst = subprocess.run([sys.executable, "-I", "ports/fnv-nx/verify/recipe.py", "externals", "--ref-prefix", "gamedata/"], cwd=chk, capture_output=True, text=True)
    check("`recipe.py externals` lists ref, commit, tree", lst.returncode == 0 and sorted(l.split("\t")[0] for l in lst.stdout.split("\n") if l) == ["gamedata/exe", "gamedata/redist"], lst.stderr[-300:])
    chk2 = toy_checkout(os.path.join(td, "x") if os.makedirs(os.path.join(td, "x")) is None else "", pins, extra=[{"name": "dup", "repo": "x", "ref": "gamedata/exe", "commit": "0" * 40, "tree_sha256": "1" * 64, "at": "/ext/dup"}])
    lst = subprocess.run([sys.executable, "-I", "ports/fnv-nx/verify/recipe.py", "externals", "--ref-prefix", "gamedata/"], cwd=chk2, capture_output=True, text=True)
    check("a ref pinned two ways is an error", lst.returncode != 0 and "pinned two ways" in lst.stderr, (lst.returncode, lst.stderr[-300:]))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
