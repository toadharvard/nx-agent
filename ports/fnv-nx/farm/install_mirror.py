#!/usr/bin/env python3
"""Install the packages and python modules of verify/env.json from the pinned mirror. Run as `python3 -I install_mirror.py [--env F] [--dry]`, as root.
The mirror (env.json "debs.mirror": repo, commit, tree_sha256; the private fnv-nx-build branch tools/verdict-debs) is fetched by its exact
commit; the checkout's CAS tree hash of debs/ must equal tree_sha256 and of wheels/ wheels_tree_sha256; every deb must have the sha256 of env.json "debs.packages" (the same
sha256 as the upstream's own index where it has one: see farm/mirror_debs.py). Any mismatch exits 2 before dpkg runs.
Then ONE `dpkg -i` over every pinned deb whose package is not installed at its pinned version (dpkg orders the unpack and configure
steps). A package already installed at the pinned version is not touched: that dpkg's installed files are not derivable back to the
deb's hash, so this is counted as "present", not as "verified". Output: `install_mirror: N packages, A installed from the mirror, P
already present at the pinned version`. Then the python modules of python.modules, from wheels/<python.wheel_files>: one
`pip install --no-index --no-deps --require-hashes --break-system-packages -r FILE` over the modules not installed at the pinned
version, FILE holding `<wheel path> --hash=sha256:<python.wheel_sha256>`; output `install_mirror: M modules, W installed from the
mirror`. Nothing is fetched from a package index (apt, PyPI): the mirror is the only source."""
import json
import os
import shutil
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

def fetch(m, work):
    """Check out the mirror commit M["commit"] of the artifacts repo (shallow, exact commit) into WORK/co; returns the checkout."""
    url = os.environ.get("ARTIFACTS_URL", "https://github.com/toadharvard/fnv-nx-build")
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
    return dest

def main(a):
    work = tempfile.mkdtemp(prefix="debs-")
    try:
        return run(a, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)   # on every outcome

def run(a, work):
    env = a[a.index("--env") + 1] if "--env" in a else os.path.join(os.path.dirname(HERE), "verify", "env.json")
    doc = json.load(open(env))
    m, pk = doc["debs"]["mirror"], doc["debs"]["packages"]
    dest = fetch(m, work)
    tree = cas.tree_hash(os.path.join(dest, "debs"))
    if tree != m["tree_sha256"]:
        print("install_mirror: mirror tree_sha256 is %s, env.json says %s" % (tree, m["tree_sha256"]), file=sys.stderr)
        return 2
    todo = []
    for name, e in sorted(pk.items()):
        f = os.path.join(dest, "debs", e["file"])
        h = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
        if h != e["sha256"]:
            print("install_mirror: %s: sha256 is %s, env.json says %s" % (name, h, e["sha256"]), file=sys.stderr)
            return 2
        if installed(name) != doc["packages"][name]:
            todo.append(f)
    if todo and "--dry" not in a:
        subprocess.run(["dpkg", "-i"] + todo, check=True)
    wt = cas.tree_hash(os.path.join(dest, "wheels"))
    if wt != m["wheels_tree_sha256"]:
        print("install_mirror: mirror wheels tree_sha256 is %s, env.json says %s" % (wt, m["wheels_tree_sha256"]), file=sys.stderr)
        return 2
    from importlib import metadata
    py, wl = doc["python"], []
    for mod, ver in sorted(py["modules"].items()):
        f = os.path.join(dest, "wheels", py["wheel_files"][mod])
        h = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
        if h != py["wheel_sha256"][mod]:
            print("install_mirror: %s: wheel sha256 is %s, env.json says %s" % (mod, h, py["wheel_sha256"][mod]), file=sys.stderr)
            return 2
        try:
            have = metadata.version(mod)
        except metadata.PackageNotFoundError:
            have = None
        if have != ver:
            wl.append("%s --hash=sha256:%s" % (f, h))
    if wl and "--dry" not in a:
        rf = os.path.join(work, "wheels.txt")
        open(rf, "w").write("\n".join(wl) + "\n")
        subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages", "--no-index", "--no-deps", "--require-hashes", "-r", rf], check=True)
    print("install_mirror: %d modules, %d installed from the mirror" % (len(py["modules"]), len(wl)))
    print("install_mirror: %d packages, %d installed from the mirror, %d already present at the pinned version" % (len(pk), len(todo), len(pk) - len(todo)))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
