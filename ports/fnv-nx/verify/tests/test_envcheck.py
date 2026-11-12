#!/usr/bin/env python3
"""Tests for verify/envcheck.py and farm/setup_verdict_env.sh: the declared environment of the verdict. Plain script: exit 0 on
pass, one PASS/FAIL line per case. Needs the declared environment itself (a mismatch here IS the failure the file reports)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
SETUP = os.path.join(os.path.dirname(VERIFY), "farm", "setup_verdict_env.sh")
ENVF = os.path.join(VERIFY, "env.json")
TD = tempfile.mkdtemp(prefix="envcheck-test-")
FAILED = []

def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)[:400]))
    if not ok:
        FAILED.append(name)

def ec(*a):
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "envcheck.py")] + list(a), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr

def doc():
    return json.load(open(ENVF))

def write(d, name="env.json"):
    p = os.path.join(TD, name)
    json.dump(d, open(p, "w"))
    return p

rc, out, err = ec("check")
check("check: this machine is the declared environment", rc == 0 and out.strip() == "envcheck: OK", (rc, out, err))
rc, out, err = ec("check", "--json", os.path.join(TD, "r.json"))
j = json.load(open(os.path.join(TD, "r.json")))
check("check --json: env_id and no mismatches", j["mismatches"] == [] and len(j["env_id"]) == 64, j)

d0 = doc()
check("env.json: format, programs owned, non-trivial package list", d0["format"] == "fnvnx-env/1" and d0["uid"] == 0
      and d0["arch"] == "x86_64" and {"strace", "busybox", "aarch64-linux-gnu-g++", "aarch64-linux-gnu-as"} <= set(d0["programs"])
      and {"busybox-static", "gcc-13-plugin-dev-aarch64-linux-gnu", "libc6-dev-arm64-cross", "strace"} <= set(d0["packages"])
      and set(d0["python"]["modules"]) == {"capstone", "gmpy2", "keystone-engine", "numpy", "unicorn", "pyelftools", "z3-solver", "pefile"}, d0["programs"])

def mutate(f):
    d = doc()
    f(d)
    return d

def pkgs(f):
    """A change of the package list with the deb pins kept in step: the case then shows only the package mismatch."""
    d = doc()
    f(d["packages"])
    base = doc()["debs"]["packages"]
    d["debs"]["packages"] = {p: dict(base.get(p, next(iter(base.values()))), version=v) for p, v in d["packages"].items()}
    return d

CASES = [
    ("package version", pkgs(lambda p: p.__setitem__("strace", "0.0")), "env: package strace: want 0.0, have "),
    ("missing package", pkgs(lambda p: p.__setitem__("no-such-package-xyz", "1")),
     "env: package no-such-package-xyz: want 1, have not installed"),
    ("deb pin missing for a package", mutate(lambda d: d["debs"]["packages"].pop("strace")), "env: debs.packages: strace has no deb pin"),
    ("deb pin of another version", mutate(lambda d: d["debs"]["packages"]["strace"].__setitem__("version", "0.0")),
     "env: debs.packages.strace: needs version "),
    ("deb pin sha256 not 64 hex", mutate(lambda d: d["debs"]["packages"]["strace"].__setitem__("sha256", "abc")),
     "env: debs.packages.strace: needs version "),
    ("deb pin of a package not in packages", mutate(lambda d: d["debs"]["packages"].__setitem__("zzz", d["debs"]["packages"]["strace"])),
     "env: debs.packages: zzz is not in packages"),
    ("deb mirror commit not 40 hex", mutate(lambda d: d["debs"]["mirror"].__setitem__("commit", "x")), "env: debs.mirror: needs repo"),
    ("no debs section", mutate(lambda d: d.pop("debs")), "env: debs: missing"),
    ("python module version", mutate(lambda d: d["python"]["modules"].__setitem__("capstone", "0.0")),
     "env: python module capstone: want 0.0, have "),
    ("python module content sha256 (same version, other bytes)", mutate(lambda d: d["python"]["wheels"].__setitem__("z3-solver", "0" * 64)),
     "env: python module z3-solver content sha256: want 000000000000, have "),
    ("python module without a content sha256", mutate(lambda d: d["python"]["wheels"].pop("gmpy2")),
     "env: python module gmpy2: no content sha256 in python.wheels"),
    ("python module without a wheel sha256", mutate(lambda d: d["python"]["wheel_sha256"].pop("numpy")),
     "env: python module numpy: no wheel sha256 in python.wheel_sha256"),
    ("python version", mutate(lambda d: d["python"].__setitem__("version", "2.7.0")), "env: python.version: want 2.7.0, have "),
    ("missing program", mutate(lambda d: d["programs"].append("no-such-program-xyz")), "env: program no-such-program-xyz: want in PATH, have missing"),
    ("program not owned by a listed package", pkgs(lambda p: p.pop("busybox-static")), "env: program busybox: want owned by a listed package"),
    ("os version", mutate(lambda d: d["os"].__setitem__("version_id", "99.04")), "env: os.version_id: want 99.04, have "),
    ("arch", mutate(lambda d: d.__setitem__("arch", "aarch64")), "env: arch: want aarch64, have "),
    ("uid", mutate(lambda d: d.__setitem__("uid", 1000)), "env: uid: want 1000, have "),
    ("ingested tree", mutate(lambda d: d["trees"].__setitem__("/usr/include", "0" * 64)), "env: tree /usr/include: want "),
    ("unknown kernel capability", mutate(lambda d: d["kernel"]["needs"].append("time travel")), "env: kernel time travel: want available, have unknown"),
]
for name, d, want in CASES:
    rc, out, err = ec("check", "--env", write(d))
    lines = [ln for ln in out.splitlines() if ln.startswith("env: ")]
    check("check detects: " + name, rc == 2 and len(lines) == 1 and lines[0].startswith(want) and out.splitlines()[-1] == "envcheck: MISMATCH (1)",
          (rc, out, err))

rc, out, err = ec("id")
sha = hashlib.sha256(json.dumps(d0, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
rc2, out2, _ = ec("id")
shuffled = dict(reversed(list(d0.items())))
rc3, out3, _ = ec("id", "--env", write(shuffled, "shuffled.json"))
rc4, out4, _ = ec("id", "--env", write(CASES[0][1], "other.json"))
check("id: stable, = sha256 of the canonical file, independent of key order, changes with content",
      rc == 0 and out == out2 and out.strip() == sha and out3 == out and out4 != out, (out, out2, out3, out4))

rc, out, err = ec("snapshot", "-o", os.path.join(TD, "snap.json"), "--from-code")
check("snapshot --from-code reproduces the committed env.json on this machine", rc == 0 and open(os.path.join(TD, "snap.json")).read()
      == open(ENVF).read(), (rc, err[-300:]))
check("snapshot needs --from-code and -o", ec("snapshot")[0] != 0)

# the setup script
check("setup script: bash -n", subprocess.run(["bash", "-n", SETUP]).returncode == 0)
p = subprocess.run(["bash", SETUP, "--print"], capture_output=True, text=True)
lines = p.stdout.splitlines()
debs = [ln for ln in lines if "install_mirror.py" in ln]
check("setup --print: install_mirror.py (debs and wheels from the mirror, no apt, no PyPI), then envcheck check",
      p.returncode == 0 and len(debs) == 1 and lines[0] == debs[0] and debs[0].endswith("--env " + ENVF) and "apt" not in p.stdout
      and "pip" not in p.stdout and lines[-1].endswith("envcheck.py check --env " + ENVF) and len(lines) == 2, p.stdout[:300] + p.stderr)
# the run mode (real dpkg, real pip) is not run here: it changes the machine. Every farm runs it for real and ends with envcheck.

# install_mirror.py against a local mirror repo (ARTIFACTS_URL): checks tree hash and every sha256 BEFORE dpkg (--dry: dpkg never runs)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
MR = os.path.join(TD, "mirror")
os.makedirs(os.path.join(MR, "debs"))
os.makedirs(os.path.join(MR, "wheels"))
open(os.path.join(MR, "debs", "x_1_amd64.deb"), "wb").write(b"not a real deb\n")
open(os.path.join(MR, "wheels", "fakemod_xyz-1-py3-none-any.whl"), "wb").write(b"not a real wheel\n")
gi = lambda *a: subprocess.run(["git", "-C", MR] + list(a), capture_output=True, text=True, check=True).stdout.strip()
gi("init", "-q", "-b", "m"); gi("add", "-A"); gi("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "m")
MC, MT, WT = gi("rev-parse", "HEAD"), cas.tree_hash(os.path.join(MR, "debs")), cas.tree_hash(os.path.join(MR, "wheels"))
def mirror_doc(tree=MT, sha=hashlib.sha256(b"not a real deb\n").hexdigest(), wtree=WT, wsha=hashlib.sha256(b"not a real wheel\n").hexdigest()):
    return write({"packages": {"no-such-pkg-x": "1"}, "debs": {"mirror": {"repo": "r", "commit": MC, "tree_sha256": tree, "wheels_tree_sha256": wtree},
                  "packages": {"no-such-pkg-x": {"version": "1", "file": "x_1_amd64.deb", "sha256": sha, "url": "u"}}},
                  "python": {"modules": {"fakemod-xyz": "1"}, "wheel_files": {"fakemod-xyz": "fakemod_xyz-1-py3-none-any.whl"},
                             "wheel_sha256": {"fakemod-xyz": wsha}}}, "m.json")
IT = os.path.join(TD, "inst-tmp")
os.makedirs(IT)
def inst(path):
    p = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(VERIFY), "farm", "install_mirror.py"), "--env", path, "--dry"],
                       capture_output=True, text=True, env=dict(os.environ, ARTIFACTS_URL="file://" + MR, TMPDIR=IT))
    return p.returncode, p.stdout, p.stderr
rc, out, err = inst(mirror_doc())
check("install_mirror: right tree and sha256 -> 1 package from the mirror (dry)", rc == 0 and "1 packages, 1 installed from the mirror, 0 already present" in out and "1 modules, 1 installed from the mirror" in out, (rc, out, err))
rc, out, err = inst(mirror_doc(sha="0" * 64))
check("install_mirror: a deb with another sha256 -> exit 2 naming the package, nothing installed", rc == 2 and "no-such-pkg-x: sha256 is" in err, (rc, out, err))
rc, out, err = inst(mirror_doc(tree="0" * 64))
left = os.listdir(IT)
rc2, out2, err2 = inst(mirror_doc(wsha="0" * 64))
check("install_mirror: a wheel with another sha256 -> exit 2 naming the module, pip never runs", rc2 == 2 and "fakemod-xyz: wheel sha256 is" in err2, (rc2, out2, err2))
rc3, out3, err3 = inst(mirror_doc(wtree="0" * 64))
check("install_mirror: a mirror with another wheels tree hash -> exit 2", rc3 == 2 and "wheels tree_sha256 is" in err3, (rc3, out3, err3))
check("install_mirror: a mirror with another tree hash -> exit 2", rc == 2 and "tree_sha256 is" in err, (rc, out, err))
check("install_mirror: it leaves nothing in its TMPDIR, on success and on every failure", left == [], left)

shutil.rmtree(TD, ignore_errors=True)
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
