#!/usr/bin/env python3
"""Tests for the one set of files of a pinned python module (envcheck.record_files: the wheel's RECORD): the digest, the mount and the red cases,
on a toy wheel that keeps a library in a sibling *.libs directory (as gmpy2 and numpy do). No game data."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import scratch  # noqa: E402

scratch.install()
import envcheck  # noqa: E402
import runtime  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:300]) if not ok else ""))
    if not ok:
        FAILED.append(name)


TD = tempfile.mkdtemp(prefix="record-test-")
SITE = os.path.join(TD, "site")


def h(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def wheel(name="toyrec", libs=b"libdata-1", record=True):
    """Install a toy wheel into SITE: the package, a sibling toyrec.libs directory, a top-level extension file and the dist-info."""
    shutil.rmtree(SITE, ignore_errors=True)
    files = {name + "/__init__.py": b"X = 1\n", name + ".libs/libtoy.so": libs, name + "_ext.so": b"ext", name + "-1.dist-info/METADATA": ("Name: %s\nVersion: 1\n" % name).encode(),
             name + "-1.dist-info/INSTALLER": b"pip\n"}
    for p, d in files.items():
        os.makedirs(os.path.dirname(os.path.join(SITE, p)), exist_ok=True)
        open(os.path.join(SITE, p), "wb").write(d)
    if record:
        rows = ["%s,%s,%d" % (p, h(d), len(d)) for p, d in sorted(files.items()) if not p.endswith("INSTALLER")] + [name + "-1.dist-info/RECORD,,"]
        open(os.path.join(SITE, name + "-1.dist-info/RECORD"), "w").write("\n".join(rows) + "\n")
    return name


sys.path.insert(0, SITE)
d = wheel()
check("record_files holds the sibling *.libs, the top-level extension file and the dist-info, not the installer's files",
      sorted(envcheck.record_files(d)) == ["toyrec-1.dist-info/METADATA", "toyrec.libs/libtoy.so", "toyrec/__init__.py", "toyrec_ext.so"], sorted(envcheck.record_files(d)))
check("record_tops: the package, the sibling libs, the extension file and the dist-info", envcheck.record_tops(d) == ["toyrec", "toyrec-1.dist-info", "toyrec.libs", "toyrec_ext.so"], envcheck.record_tops(d))
check("an intact wheel has no problem", envcheck.record_problems(d) == [], envcheck.record_problems(d))
dg = envcheck.wheel_digest(d)
wheel()
open(os.path.join(SITE, "toyrec.libs/libtoy.so"), "wb").write(b"libdata-2")
check("a .libs file that changed on disk is red (differs from its RECORD hash) and changes the digest", any("libtoy.so" in p for p in envcheck.record_problems(d)) and envcheck.wheel_digest(d) != dg, envcheck.record_problems(d))
wheel()
os.remove(os.path.join(SITE, "toyrec.libs/libtoy.so"))
check("a RECORD entry missing on disk (the .libs file) is red", any("libtoy.so" in p and "missing" in p for p in envcheck.record_problems(d)), envcheck.record_problems(d))
wheel()
open(os.path.join(SITE, "toyrec.libs/extra.so"), "wb").write(b"not shipped")
check("a file under the module's paths that the RECORD does not list is red", any("extra.so" in p and "not listed" in p for p in envcheck.record_problems(d)), envcheck.record_problems(d))
wheel(record=False)
try:
    envcheck.record_files(d)
    check("a declared module with no RECORD is red", False)
except envcheck.RecordError as e:
    check("a declared module with no RECORD is red", "no RECORD" in str(e), e)
wheel()
# the mount: the interpreter runtime takes the RECORD's paths, the sibling *.libs among them (no import name reaches it)
store = os.path.join(TD, "store")
paths = [os.path.realpath(str(envcheck.metadata.distribution(d).locate_file(t))) for t in envcheck.record_tops(d)]
rt = runtime.build(store, sys.executable, [], paths)
ats = [i["at"] for i in rt["inputs"]]
libs = os.path.realpath(os.path.join(SITE, "toyrec.libs"))
check("the runtime mounts the sibling toyrec.libs directory and the top-level extension file", libs in ats and os.path.realpath(os.path.join(SITE, "toyrec_ext.so")) in ats, ats[-6:])
rt0 = runtime.build(store, sys.executable, [], [p for p in paths if not p.endswith(".libs")])
check("the runtime id changes when the .libs directory is not mounted (it is in the key)", rt0["runtime_id"] != rt["runtime_id"])
open(os.path.join(SITE, "toyrec.libs/libtoy.so"), "wb").write(b"libdata-3")
check("the runtime id changes when a .libs file changes", runtime.build(store, sys.executable, [], paths)["runtime_id"] != rt["runtime_id"])
try:
    runtime.build(store, sys.executable, [], [os.path.join(SITE, "toyrec.libs/nothing.so")])
    check("a path that does not exist is an error", False)
except runtime.RtError as e:
    check("a path that does not exist is an error", "does not exist" in str(e), e)
shutil.rmtree(TD, ignore_errors=True)
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
