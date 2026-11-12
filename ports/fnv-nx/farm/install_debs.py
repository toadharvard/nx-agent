#!/usr/bin/env python3
"""Install the packages of verify/env.json from the pinned deb mirror. Run as `python3 -I install_debs.py [--env F] [--dry]`, as root.
The mirror (env.json "debs.mirror": repo, commit, tree_sha256; the private fnv-nx-build branch tools/verdict-debs) is fetched by its exact
commit; the checkout's CAS tree hash must equal tree_sha256 and every deb must have the sha256 of env.json "debs.packages" (the same
sha256 as the upstream's own index where it has one: see farm/mirror_debs.py). Any mismatch exits 2 before dpkg runs.
Then ONE `dpkg -i` over every pinned deb whose package is not installed at its pinned version (dpkg orders the unpack and configure
steps). A package already installed at the pinned version is not touched: that dpkg's installed files are not derivable back to the
deb's hash, so this is counted as "present", not as "verified". Output: `install_debs: N packages, A installed from the mirror, P
already present at the pinned version`. Nothing is fetched from a package index: the mirror is the only source."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "verify"))
import cas  # noqa: E402
import hashlib  # noqa: E402

def installed(pkg):
    p = subprocess.run(["dpkg-query", "-W", "-f=${Version}\t${Status}", pkg], capture_output=True, text=True)
    v = p.stdout.split("\t")
    return v[0] if p.returncode == 0 and len(v) == 2 and v[1].endswith("installed") else None

def main(a):
    env = a[a.index("--env") + 1] if "--env" in a else os.path.join(os.path.dirname(HERE), "verify", "env.json")
    doc = json.load(open(env))
    m, pk = doc["debs"]["mirror"], doc["debs"]["packages"]
    url = os.environ.get("ARTIFACTS_URL", "https://github.com/toadharvard/fnv-nx-build")
    work = tempfile.mkdtemp(prefix="debs-")
    genv = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    bare = os.path.join(work, "bare")
    os.makedirs(bare)
    dest = os.path.join(work, "co")
    os.makedirs(dest)
    for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", url, m["commit"]]):
        subprocess.run(["git", "-C", bare] + cmd, env=genv, check=True)
    idx = dict(genv, GIT_INDEX_FILE=os.path.join(work, "idx"))
    subprocess.run(["git", "--git-dir", bare, "read-tree", m["commit"]], env=idx, check=True)
    subprocess.run(["git", "--git-dir", bare, "--work-tree", dest, "checkout-index", "-a", "-f"], env=idx, check=True)
    tree = cas.tree_hash(os.path.join(dest, "debs"))
    if tree != m["tree_sha256"]:
        print("install_debs: mirror tree_sha256 is %s, env.json says %s" % (tree, m["tree_sha256"]), file=sys.stderr)
        return 2
    todo = []
    for name, e in sorted(pk.items()):
        f = os.path.join(dest, "debs", e["file"])
        h = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
        if h != e["sha256"]:
            print("install_debs: %s: sha256 is %s, env.json says %s" % (name, h, e["sha256"]), file=sys.stderr)
            return 2
        if installed(name) != doc["packages"][name]:
            todo.append(f)
    if todo and "--dry" not in a:
        subprocess.run(["dpkg", "-i"] + todo, check=True)
    print("install_debs: %d packages, %d installed from the mirror, %d already present at the pinned version" % (len(pk), len(todo), len(pk) - len(todo)))
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
