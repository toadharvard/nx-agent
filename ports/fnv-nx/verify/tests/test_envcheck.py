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
      and set(d0["python"]["modules"]) == {"capstone", "numpy", "unicorn", "pyelftools"}, d0["programs"])

def mutate(f):
    d = doc()
    f(d)
    return d

CASES = [
    ("package version", mutate(lambda d: d["packages"].__setitem__("strace", "0.0")), "env: package strace: want 0.0, have "),
    ("missing package", mutate(lambda d: d["packages"].__setitem__("no-such-package-xyz", "1")),
     "env: package no-such-package-xyz: want 1, have not installed"),
    ("python module version", mutate(lambda d: d["python"]["modules"].__setitem__("capstone", "0.0")),
     "env: python module capstone: want 0.0, have "),
    ("python version", mutate(lambda d: d["python"].__setitem__("version", "2.7.0")), "env: python.version: want 2.7.0, have "),
    ("missing program", mutate(lambda d: d["programs"].append("no-such-program-xyz")), "env: program no-such-program-xyz: want in PATH, have missing"),
    ("program not owned by a listed package", mutate(lambda d: d["packages"].pop("busybox-static")), "env: program busybox: want owned by a listed package"),
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
apt = [ln for ln in lines if ln.startswith("apt-get install")]
pip = [ln for ln in lines if " pip install " in ln]
check("setup --print: apt-get update, one install line with every package=version, pip with every module==version, then envcheck check",
      p.returncode == 0 and lines[0] == "apt-get update" and len(apt) == 1 and len(pip) == 1 and "--break-system-packages" in pip[0]
      and all(("%s=%s" % kv) in apt[0].split() for kv in d0["packages"].items())
      and all(("%s==%s" % kv) in pip[0].split() for kv in d0["python"]["modules"].items())
      and lines[-1].endswith("envcheck.py check --env " + ENVF) and len(lines) == 4, p.stdout[:300] + p.stderr)
# the run mode (real apt-get, real pip) is not run here: it changes the machine. Every farm runs it for real and ends with envcheck.

shutil.rmtree(TD, ignore_errors=True)
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
