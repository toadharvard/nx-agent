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
sys.path.insert(0, VERIFY)
import envcheck  # noqa: E402
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
    pins = dict(base)   # the closure's pins stay; a package of the list gets its own pin at its version
    for p, v in d["packages"].items():
        pins[p] = dict(base.get(p, next(iter(base.values()))), version=v)
    keep = set(d["packages"]) | set(envcheck.dep_closure(d["packages"]))
    d["debs"]["packages"] = {p: v for p, v in pins.items() if p in keep}
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
     "env: debs.packages: zzz is not in the packages or their dependency closure"),
    ("deb mirror commit not 40 hex", mutate(lambda d: d["debs"]["mirror"].__setitem__("commit", "x")), "env: debs.mirror: needs repo"),
    ("no debs section", mutate(lambda d: d.pop("debs")), "env: debs: missing"),
    ("python module version", mutate(lambda d: d["python"]["modules"].__setitem__("capstone", "0.0")),
     "env: python module capstone: want 0.0, have "),
    ("python module content sha256 (same version, other bytes)", mutate(lambda d: d["python"]["installed_files_sha256"].__setitem__("z3-solver", "0" * 64)),
     "env: python module z3-solver installed files sha256: want 000000000000, have "),
    ("python module without a content sha256", mutate(lambda d: d["python"]["installed_files_sha256"].pop("gmpy2")),
     "env: python module gmpy2: no installed-files sha256 in python.installed_files_sha256"),
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

# the plugin headers of the distro compilers of admitted roots (objstub builds its terminator plugin against them)
sys.path.insert(0, VERIFY)
import envcheck  # noqa: E402
# foreign files: owned only by packages outside the pins -> listed, not in the digest; unowned and pinned-owner files count
ft = os.path.join(TD, "ftree")
os.makedirs(ft)
for n in ("own.h", "foreign.h", "shared.h", "unowned.h"):
    open(os.path.join(ft, n), "w").write(n)
own_map = {os.path.join(ft, "own.h"): {"pinned"}, os.path.join(ft, "foreign.h"): {"outsider"},
           os.path.join(ft, "shared.h"): {"outsider", "pinned"}}
cnt, frn = envcheck.tree_files(ft, {"pinned"}, own_map)
check("tree: a file owned only by an unpinned package is foreign, the rest counts",
      [os.path.basename(p) for _, p, _ in frn] == ["foreign.h"] and len(cnt) == 3, (cnt, frn))
d0 = envcheck.tree_digest(ft, {"pinned"}, own_map)
open(os.path.join(ft, "foreign.h"), "w").write("changed foreign file of another size")
check("tree digest ignores foreign files and sees counted ones",
      envcheck.tree_digest(ft, {"pinned"}, own_map) == d0 and envcheck.tree_digest(ft) != d0)

fr = os.path.join(TD, "fakerepo")
os.makedirs(os.path.join(fr, "ports/fnv-nx/verify"))
os.makedirs(os.path.join(fr, "ports/fnv-nx/hroot"))
json.dump({"roots": [{"path": "ports/fnv-nx/hroot", "class": "product", "target": "host", "recipe": "ports/fnv-nx/hroot/build.json"},
                     {"path": "ports/fnv-nx/gext", "class": "product", "target": "guest", "recipe": "ports/fnv-nx/gext/build.json"}]},
          open(os.path.join(fr, "ports/fnv-nx/verify/admit.json"), "w"))
json.dump({"compiler": {"cc": "x86_64-linux-gnu-gcc-13", "cxx": "x86_64-linux-gnu-g++-13"}}, open(os.path.join(fr, "ports/fnv-nx/hroot/build.json"), "w"))
os.makedirs(os.path.join(fr, "ports/fnv-nx/gext"))
json.dump({"compiler": {"external": "x", "cc": "bin/cc", "cxx": "bin/c++"}}, open(os.path.join(fr, "ports/fnv-nx/gext/build.json"), "w"))
check("root compilers: the distro drivers of admitted roots, an external compiler is none",
      envcheck._root_compilers(fr) == ["x86_64-linux-gnu-g++-13", "x86_64-linux-gnu-gcc-13"], envcheck._root_compilers(fr))
good = [l for l in envcheck.check(doc(), fr) if "plugin headers" in l]
d1 = doc()
del d1["packages"]["gcc-13-plugin-dev"]
bad = [l for l in envcheck.check(d1, fr) if "plugin headers" in l]
check("a root's distro compiler whose plugin headers are in no listed package is a mismatch; the committed env has none",
      good == [] and len(bad) == 2 and all("gcc-13-plugin-dev" in l for l in bad), (good, bad))

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
def closure_doc(top_ver="1", pin_ver="1"):
    d = json.load(open(mirror_doc()))
    d["debs"]["packages"]["closure-only-pkg"] = dict(d["debs"]["packages"]["no-such-pkg-x"])   # a Depends-closure deb: pinned, not declared
    d["debs"]["packages"]["no-such-pkg-x"]["version"] = pin_ver
    d["packages"]["no-such-pkg-x"] = top_ver
    return write(d, "c.json")
IT = os.path.join(TD, "inst-tmp")
os.makedirs(IT)
def inst(path):
    p = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(VERIFY), "farm", "install_mirror.py"), "--env", path, "--dry"],
                       capture_output=True, text=True, env=dict(os.environ, ARTIFACTS_URL="file://" + MR, TMPDIR=IT))
    return p.returncode, p.stdout, p.stderr
rc, out, err = inst(mirror_doc())
check("install_mirror: right tree and sha256 -> 1 package from the mirror (dry)", rc == 0 and "1 packages, 1 installed from the mirror, 0 already present" in out and "1 modules, 1 installed from the mirror" in out, (rc, out, err))
rc, out, err = inst(closure_doc())
check("install_mirror: a closure-only deb (pinned, not declared) is checked against debs.packages -> 2 packages, no KeyError", rc == 0 and "2 packages, 2 installed from the mirror" in out and "Traceback" not in err, (rc, out, err))
rc, out, err = inst(closure_doc(top_ver="2"))
check("install_mirror: a declared version that disagrees with debs.packages -> exit 2 naming the package", rc == 2 and "packages.no-such-pkg-x is 2, debs.packages says 1" in err, (rc, out, err))
d = json.load(open(mirror_doc())); d["packages"]["undeclared-pin"] = "1"
rc, out, err = inst(write(d, "u.json"))
check("install_mirror: a declared package with no deb pin -> exit 2", rc == 2 and "packages.undeclared-pin is 1, debs.packages says no pin" in err, (rc, out, err))
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

# ---- build profiles (env.json profiles.toolchain-build): structure is checked everywhere, the package set only in its own chroot
PN = "toolchain-build"
d0 = doc()
check("profile %s: 162-package whole set with deb pins, programs, mirror tools/toolchain-debs" % PN,
      len(d0["profiles"][PN]["packages"]) == len(d0["profiles"][PN]["debs"]["packages"]) >= 100 and d0["profiles"][PN]["programs"]
      and d0["profiles"][PN]["debs"]["mirror"]["branch"] == "tools/toolchain-debs", None)
def prof(f):
    d = doc()
    f(d["profiles"][PN])
    return write(d, "prof.json")
for name, f, msg in (
        ("deb pin missing", lambda p: p["debs"]["packages"].pop("make"), "env: profile %s: debs.packages: make has no deb pin" % PN),
        ("deb pin sha256 not 64 hex", lambda p: p["debs"]["packages"]["make"].__setitem__("sha256", "abc"), "env: profile %s: debs.packages.make: needs version" % PN),
        ("mirror commit not 40 hex", lambda p: p["debs"]["mirror"].__setitem__("commit", "x"), "env: profile %s: debs.mirror needs repo" % PN),
        ("no programs", lambda p: p.__setitem__("programs", []), "env: profile %s: programs: the programs the build invokes" % PN)):
    rc, out, err = ec("check", "--env", prof(f))
    check("verdict check refuses a broken profile: " + name, rc == 2 and msg in out, (rc, out[-300:]))
rc, out, err = ec("check", "--profile", PN)
check("check --profile on the verdict root is a mismatch (a profile is installed only in its own chroot)",
      rc == 2 and "env: profile %s: package texinfo: want " % PN in out and "program makeinfo: want in PATH" in out, (rc, out[-200:]))
rc, out, err = ec("check", "--profile", "no-such-profile")
check("check --profile of an unknown profile", rc == 2 and "env: no profile no-such-profile" in out, (rc, out))
rc, out, err = ec("id", "--profile", PN)
check("id --profile: sha256 of the profile alone", rc == 0 and len(out.strip()) == 64 and out.strip() != ec("id")[1].strip(), out)
MK = os.path.join(os.path.dirname(VERIFY), "farm", "make_chroot.py")
rc = subprocess.run([sys.executable, "-I", MK, "--profile", PN, "--chroot", "/"], capture_output=True, text=True)
check("make_chroot refuses the root /", rc.returncode == 2 and "not an empty directory" in rc.stderr, rc)
ne = os.path.join(TD, "notempty")
os.makedirs(ne)
open(os.path.join(ne, "x"), "w").close()
rc = subprocess.run([sys.executable, "-I", MK, "--profile", PN, "--chroot", ne], capture_output=True, text=True)
check("make_chroot refuses a directory that is not empty", rc.returncode == 2 and "not an empty directory" in rc.stderr, rc)
rc = subprocess.run(["bash", SETUP, "--profile", PN], capture_output=True, text=True)
check("setup --profile needs NAME and CHROOT", rc.returncode == 1 and "usage" in rc.stderr, rc)

# script_programs: the programs a build script invokes, from a real parse (shfmt)
SP = os.path.join(os.path.dirname(VERIFY), "farm", "script_programs.py")
sh = os.path.join(TD, "b.sh")
open(sh, "w").write("""#!/bin/bash
f() { :; }
CONF=(--target=x --prefix=y)
FOO=1 make -j4 && tar -xf a.tar.xz   # a comment: rm -rf /
if ! patch -p1 -i x; then sed -i s/a/b/ y; fi
python3 - "$1" <<'EOF2'
import os
EOF2
echo "$(realpath .)" | tee out; f; ./local.sh; ../gcc-$V/configure
x=`dirname "$0"`; (cd d && ls); for i in 1; do cat $i; done
find . -name x -exec chmod +x {} +; exec env A=1 nice -n 5 gzip
bash -c 'grep -q x y; unzip z'; timeout 5 chroot "$CH" apt-get update
""")
rc = subprocess.run([sys.executable, "-I", SP, sh], capture_output=True, text=True)
check("script_programs: every command node (nesting, backticks, subshells, runners, bash -c), no builtins/functions/paths/options/heredoc text; unzip has no record",
      rc.returncode == 1 and "unclassified: unzip" in rc.stderr and rc.stdout.split() == ["apt-get", "bash", "cat", "chmod", "chroot", "dirname", "env", "find", "grep", "gzip", "ls", "make", "nice", "patch",
                                                    "python3", "realpath", "sed", "tar", "tee", "timeout", "unzip"], (rc.returncode, rc.stdout, rc.stderr))
sys.path.insert(0, os.path.join(os.path.dirname(VERIFY), "farm"))
import script_programs as sp  # noqa: E402
RECS = sp.load_classes()
rn = {n for n, r in RECS.items() if r["class"] == "runner"}
check("program_classes: every runner of the scanner has an argv/flag/string rule; leaf, runner and opaque all occur; a record per program in the profile's scanned set",
      {"env", "nice", "timeout", "chroot", "xargs", "find", "sh", "bash", "exec"} <= rn and {r["class"] for r in RECS.values()} == {"leaf", "runner", "opaque"}
      and {"cp", "make", "patch", "python3", "tar", "sed"} <= set(RECS), sorted(rn))
cd = doc()
check("verdict check validates program_classes (the committed file is clean)", [b for b in envcheck.check(cd) if "program_classes" in b] == [], None)
open(sh, "w").write('$CC -c a.c\n"${TOOL}" x\nok=1; true\n')
rc = subprocess.run([sys.executable, "-I", SP, sh], capture_output=True, text=True)
check("script_programs: a command name that is not literal is reported and exits 1", rc.returncode == 1 and rc.stderr.count("dynamic:") == 2, (rc.returncode, rc.stderr))
open(sh, "w").write("make\nzzz-not-a-program\n$CC x\n")
rc, out, err = ec("scripts", "--profile", PN, sh)
check("envcheck scripts: a program outside the profile and a non-literal name are mismatches",
      rc == 2 and "runs zzz-not-a-program, not in the profile's programs" in out and "command name is not literal: $CC" in out and "runs make" not in out, (rc, out))
open(sh, "w").write("make\ngcc\n")
rc, out, err = ec("scripts", "--profile", PN, sh)
check("envcheck scripts: programs of the profile pass", rc == 0 and out.strip() == "envcheck: OK", (rc, out))
d = doc()
d["profiles"][PN]["programs"].append("no-record-prog")
rc, out, err = ec("check", "--env", write(d, "norec.json"))
check("a program of a profile without a record in program_classes.json is a mismatch (totality)",
      rc == 2 and "env: profile %s: program no-record-prog has no record in farm/program_classes.json" % PN in out, (rc, out))
d = doc()
d["profiles"][PN]["prerequisites"].append("not-in-programs")
rc, out, err = ec("check", "--env", write(d, "pre.json"))
check("a prerequisite that is not among the profile's programs is a mismatch", rc == 2 and "programs lack prerequisites ['not-in-programs']" in out, (rc, out))

# read roots: the system headers a host root reads must belong to pinned packages
rr = os.path.join(TD, "rrepo")
os.makedirs(os.path.join(rr, "src"))
open(os.path.join(rr, "src", "a.c"), "w").write("#include <stdio.h>\n#include <GL/gl.h>\nint main(void){return 0;}\n")
rf = os.path.join(TD, "readroots.json")
json.dump({"format": "fnvnx-readroots/1", "roots": [{"name": "t", "cwd": "src", "flags": ["-std=c11"], "sources": ["a.c"]},
                                                  {"name": "absent", "cwd": "nosrc", "flags": [], "sources": ["x.c"]}]}, open(rf, "w"))
envcheck.READROOTS = rf
d = doc()
bad = [b for b in envcheck.check(d, rr) if "read root" in b]
check("read root: headers of pinned packages (stdio.h, GL/gl.h) pass; a root whose sources are absent is skipped", bad == [], bad)
for pn in ("libgl-dev", "libglx-dev", "libegl-dev"):
    d["packages"].pop(pn, None)
bad = [b for b in envcheck.check(d, rr) if "read root" in b]
check("read root: a header owned by a package that is not pinned is a mismatch naming root, file and package",
      any("env: read root t reads /usr/include/GL/gl.h, owned by libgl-dev (not a package of env.json)" in b for b in bad), bad)
open(os.path.join(rr, "src", "a.c"), "w").write("#include <no/such/header.h>\n")
bad = [b for b in envcheck.check(doc(), rr) if "read root" in b]
check("read root: a missing header is a mismatch", any("env: read root t:" in b and "no/such/header.h" in b for b in bad), bad)
envcheck.READROOTS = os.path.join(VERIFY, "readroots.json")

# ---- farm/resolve_profile.py multiarch: a foreign member's clauses by dpkg's rule (synthetic indexes)
sys.path.insert(0, os.path.join(os.path.dirname(VERIFY), "farm"))
import resolve_profile as rp  # noqa: E402
def _r(n, arch="amd64", dep="", **k):
    d = {"Package": n, "Version": "1", "Architecture": arch, "Depends": dep, "Filename": "x/" + n, "SHA256": "0"}
    d.update(k)
    return d
_best = {x["Package"]: x for x in [_r("wine64", dep="libc6"), _r("libc6"), _r("lib-all", arch="all"), _r("fgn", **{"Multi-Arch": "foreign"}), _r("libz")]}
_bf = {x["Package"]: x for x in [_r("wine32", arch="i386", dep="libc6 (>= 1), lib-all, fgn, libz, virt"), _r("libc6", arch="i386"),
       _r("fgn", arch="i386", **{"Multi-Arch": "foreign"}), _r("libz", arch="i386"), _r("prov1", arch="i386", Provides="virt")]}
_m = rp.resolve(_best, ["wine64", "wine32:i386"], "i386", _bf)
check("resolve multiarch: foreign clauses take the foreign package, the `all` package and the native Multi-Arch: foreign one, and a foreign provider",
      _m == ["fgn", "lib-all", "libc6", "libc6:i386", "libz:i386", "prov1:i386", "wine32:i386", "wine64"], _m)
_bf["wine32"]["Depends"] += ", missing-pkg"
try:
    rp.resolve(_best, ["wine32:i386"], "i386", _bf)
    check("resolve multiarch: an unsatisfiable clause is an error", False, "no exit")
except SystemExit as e:
    check("resolve multiarch: an unsatisfiable clause is an error", "missing-pkg" in str(e), str(e))

shutil.rmtree(TD, ignore_errors=True)
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
