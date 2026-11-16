#!/usr/bin/env python3
"""The input form {"external_of": ROOT, "name": EXTERNAL, "path": SUB} (RECIPE.md "An external of another root"): a path inside a pinned external that ANOTHER admitted root declares.
One validator (recipe.external_of_errors) for check, key and mount; one reader (recipe.resolve_input) on the owner's recipe and pin.
  - declared (the owner is `uses`d or an admit.json import of the consuming root): green, in a run step input, an exec and a generated input;
  - an owner that is not declared, an unknown external name, a root's built output (stage external), an own-root owner, an absolute or escaping path: red;
  - the pin is the owner's: the stage input is the external's tree, so a different owner pin is a different tree (the key changes);
  - the reader refuses an undeclared owner and an unknown name.
Run: python3 -I <this file> from anywhere (git needed)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from verify import recipe  # noqa: E402
from verify import cas  # noqa: E402

fails = 0
T = "ports/fnv-nx/"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:500]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout.strip()


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def toy_ext(td, name, text):
    """a git repo with one commit: -> (url, commit, tree_sha256 of its checkout)"""
    r = os.path.join(td, name)
    os.makedirs(r)
    git(r, "init", "-q")
    w(r, "d/f.txt", text)
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "x")
    co = os.path.join(td, name + "-co")
    subprocess.run(["git", "-C", r, "checkout-checkout".replace("checkout-checkout", "worktree"), "add", "-q", "--detach", co], check=True, capture_output=True)
    shutil_rm(os.path.join(co, ".git"))
    return r, git(r, "rev-parse", "HEAD"), cas.ingest(co, os.path.join(td, "tmpstore"))


def shutil_rm(p):
    import shutil
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.exists(p):
        os.remove(p)


def make(td, ext, uses=True, imports=False, stage_ext=False):
    repo = os.path.join(td, "repo")
    os.makedirs(repo, exist_ok=True)
    git(repo, "init", "-q")
    own = {"format": "fnvnx-recipe/1", "external": [{"name": "pack", "repo": "x", "ref": "r", "commit": ext[1], "tree_sha256": ext[2], "at": "/ext/pack"}],
           "outputs": {"o": "o.txt"}, "generated": [{"tool": T + "own/g.py", "args": [], "inputs": [], "outputs": ["o.txt"]}]}
    if stage_ext:
        own["external"].append({"name": "cc", "root": T + "own", "output": "o", "at": "/ext/cc"})
    w(repo, T + "own/build.json", json.dumps(own))
    w(repo, T + "own/g.py", "open('/out/o.txt','w').write('x')\n")
    ref = {"external_of": T + "own", "name": "pack", "path": "d/f.txt"}
    con = {"format": "fnvnx-recipe/1", "outputs": {"c": "c.txt"}, "uses": [{"root": T + "own", "output": "o"}] if uses else [],
           "generated": [{"tool": T + "con/g.py", "args": [], "inputs": [ref], "outputs": ["c.txt"]}]}
    w(repo, T + "con/build.json", json.dumps(con))
    w(repo, T + "con/g.py", "open('/out/c.txt','w').write('x')\n")
    adm = {"format": "fnvnx-admit/1", "roots": [
        {"path": T + "own", "class": "product", "target": "host", "recipe": T + "own/build.json"},
        dict({"path": T + "con", "class": "product", "target": "host", "recipe": T + "con/build.json"}, **({"imports": [T + "own"]} if imports else {}))]}
    w(repo, T + "verify/admit.json", json.dumps(adm))
    w(repo, T + "verify/env.json", open(os.path.join(HERE, "..", "env.json")).read())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo, ref


def errs(repo, ref, root="con"):
    roots = recipe.load_admit(repo)
    rec = recipe.load_recipe(repo, roots, T + root)
    return recipe.external_of_errors("t", ref, T + root, rec, roots, repo)


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    e1 = toy_ext(td, "e1", "one\n")
    repo, ref = make(os.path.join(td, "a"), e1)
    check("declared through `uses`: green", errs(repo, ref) == [], errs(repo, ref))
    repo2, ref2 = make(os.path.join(td, "b"), e1, uses=False, imports=True)
    check("declared through admit.json `imports`: green", errs(repo2, ref2) == [], errs(repo2, ref2))
    repo3, ref3 = make(os.path.join(td, "c"), e1, uses=False)
    check("an owner that is neither used nor imported is red", any("declare the dependency" in e for e in errs(repo3, ref3)), errs(repo3, ref3))
    check("an unknown external name is red", any("declares no external" in e for e in errs(repo, dict(ref, name="nope"))), errs(repo, dict(ref, name="nope")))
    check("an owner that is no admitted root is red", any("not an admitted root" in e for e in errs(repo, dict(ref, external_of=T + "ghost"))))
    check("the root itself as owner is red (that is {external, path})", any("itself" in e for e in errs(repo, dict(ref, external_of=T + "con"))))
    for bad in ("/etc/passwd", "../x", "a/../../x", ""):
        check("path %r is red" % bad, any("absolute or leaves" in e for e in errs(repo, dict(ref, path=bad))), errs(repo, dict(ref, path=bad)))
    check("a malformed ref is red", any("is not {external_of" in e for e in errs(repo, {"external_of": T + "own", "name": "pack"})))
    repo4, ref4 = make(os.path.join(td, "d"), e1, stage_ext=True)
    check("a root's built output (stage external) is red: take it through `uses`", any("built output" in e for e in errs(repo4, dict(ref4, name="cc"))), errs(repo4, dict(ref4, name="cc")))
    # the whole recipe check sees the form in a generated input (and refuses it for an undeclared owner)
    roots = recipe.load_admit(repo)
    rc = recipe.load_recipe(repo, roots, T + "con")
    recipe.env_blocked = lambda r: None
    e_ok = recipe.check(rc, T + "con", recipe.tracked(repo), roots, None, repo)
    check("recipe.check of the consuming root holds no external_of error", not [e for e in e_ok if "external_of" in e], e_ok)
    roots3 = recipe.load_admit(repo3)
    e_bad = recipe.check(recipe.load_recipe(repo3, roots3, T + "con"), T + "con", recipe.tracked(repo3), roots3, None, repo3)
    check("recipe.check of a root with an undeclared owner is red", any("external_of" in e and "declare the dependency" in e for e in e_bad), e_bad)
    # the reader: mount by the owner's pin; a different owner pin is a different tree (a different key)
    cache = os.path.join(td, "cache")
    os.makedirs(os.path.join(cache, "store"), exist_ok=True)
    ext = recipe.ext_dir(cache)
    os.makedirs(ext, exist_ok=True)
    recipe.set_artifacts(ext, e1[0])
    o1 = recipe.load_recipe(repo, roots, T + "own")["external"][0]
    recipe.fetch_externals([o1], os.path.join(cache, "store"), ext, os.path.join(td, "ws1"))
    h1, t1 = recipe.resolve_input(T + "con", ref, repo, cache, rc)
    check("the reader mounts the owner's external at the owner's pin (tree = tree_sha256)", t1 == "%s:d/f.txt" % e1[2] and open(h1).read() == "one\n", t1)
    e2 = toy_ext(td, "e2", "two\n")
    repo5, ref5 = make(os.path.join(td, "e"), e2)
    recipe.set_artifacts(ext, e2[0])
    roots5 = recipe.load_admit(repo5)
    o2 = recipe.load_recipe(repo5, roots5, T + "own")["external"][0]
    recipe.fetch_externals([o2], os.path.join(cache, "store"), ext, os.path.join(td, "ws2"))
    h2, t2 = recipe.resolve_input(T + "con", ref5, repo5, cache, recipe.load_recipe(repo5, roots5, T + "con"))
    check("a different owner pin is a different tree: the key changes", t1 != t2 and t2 == "%s:d/f.txt" % o2["tree_sha256"], (t1, t2))
    try:
        recipe.resolve_input(T + "con", ref3, repo3, cache, None)
        check("the reader refuses an undeclared owner", False)
    except ValueError as x:
        check("the reader refuses an undeclared owner", "declare the dependency" in str(x), x)
    try:
        recipe.resolve_input(T + "con", dict(ref, name="nope"), repo, cache, rc)
        check("the reader refuses an unknown name", False)
    except ValueError as x:
        check("the reader refuses an unknown name", "declares no external" in str(x), x)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
