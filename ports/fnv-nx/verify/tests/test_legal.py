#!/usr/bin/env python3
"""Check of gate legal (verify/gate_legal.py, rule L1) and its manifest builder (verify/legal_manifest.py) on a toy private repository and a toy public one:
a clean tree is certified; a tracked file with the bytes of a private file is red `copy`; an empty file is certified; a file above the cap is red; a
malformed manifest, no manifest and a manifest of another cap are handled; the builder's manifest is sorted, unique, within the cap. Plain script."""
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(HERE, "..", "..", "contract")]
import gate_legal as G  # noqa: E402
import legal_manifest as M  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:400]))
    if not ok:
        FAILED.append(name)


def git(d, *a):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, check=True, text=True).stdout


tmp = tempfile.mkdtemp(prefix="legal-")
priv = os.path.join(tmp, "priv")
os.makedirs(priv)
git(priv, "init", "-q", "-b", "gamedata/x")
open(os.path.join(priv, "a.bin"), "wb").write(b"PRIVATE-BYTES-1\n")
open(os.path.join(priv, "dup.bin"), "wb").write(b"PRIVATE-BYTES-1\n")
open(os.path.join(priv, "big.bin"), "wb").write(b"B" * 200)
open(os.path.join(priv, "empty"), "wb").write(b"")
git(priv, "add", "-A")
git(priv, "commit", "-q", "-m", "p")
git(priv, "checkout", "-q", "-b", "l3a-lift/src/y")
open(os.path.join(priv, "b.cc"), "wb").write(b"int private_decomp;\n")
git(priv, "add", "-A")
git(priv, "commit", "-q", "-m", "q")
mf = os.path.join(tmp, "manifest.tsv")
M.main([os.path.join(priv, ".git"), mf, "100", "refs/heads/gamedata", "refs/heads/l3a-lift"])
text = open(mf).read()
cap, hs = G.parse_manifest(text)
sha = lambda b: hashlib.sha256(b).hexdigest()
check("manifest: header, cap, sorted unique rows; empty and above-cap blobs left out; one row for the two equal blobs",
      cap == 100 and hs == {sha(b"PRIVATE-BYTES-1\n"), sha(b"int private_decomp;\n")} and text.startswith("# fnvnx-legal-manifest/1 cap=100\n"), (cap, hs))
check("manifest: the second branch is read", sha(b"int private_decomp;\n") in hs)

pub = os.path.join(tmp, "pub")
os.makedirs(pub)
git(pub, "init", "-q")
open(os.path.join(pub, "ours.py"), "w").write("print(1)\n")
open(os.path.join(pub, "empty.txt"), "w").write("")
git(pub, "add", "-A")
git(pub, "commit", "-q", "-m", "x")
u, c, bad, detail = G.judge(pub, G.tracked(pub), (cap, hs))
check("a clean tree is certified (n/N = 2/2)", (u, c, bad) == (2, 2, []), (u, c, bad))
open(os.path.join(pub, "copied.cc"), "wb").write(b"int private_decomp;\n")
git(pub, "add", "-A")
u, c, bad, detail = G.judge(pub, G.tracked(pub), (cap, hs))
check("a tracked file with the bytes of a private file is red `copy`", (u, c) == (3, 2) and bad == ["copied.cc [copy]"], (u, c, bad))
open(os.path.join(pub, "huge.dat"), "wb").write(b"H" * 101)
git(pub, "add", "-A")
u, c, bad, detail = G.judge(pub, G.tracked(pub), (cap, hs))
check("a file above the manifest cap is red `above-cap`", "huge.dat [above-cap]" in bad and (u, c) == (4, 2), (u, c, bad))
u, c, bad, detail = G.judge(pub, G.tracked(pub), None)
check("no manifest: every tracked file is red", (u, c) == (4, 0) and len(bad) == 4 and all("[no-manifest]" in b for b in bad), (u, c, bad))
for t, why in (("", "empty"), ("# fnvnx-legal-manifest/1 cap=0\n", "cap 0"), ("# fnvnx-legal-manifest/1 cap=10\nabc\t1\n", "short hash"),
               ("# fnvnx-legal-manifest/1 cap=10\n" + "b" * 64 + "\t1\n" + "a" * 64 + "\t1\n", "unsorted"), ("# fnvnx-legal-manifest/1 cap=10\n" + "a" * 64 + "\t11\n", "size above cap"),
               ("# fnvnx-legal-manifest/1 cap=10\n" + "a" * 64 + "\t1", "no final LF")):
    try:
        G.parse_manifest(t)
        check("a malformed manifest is refused: " + why, False)
    except ValueError:
        check("a malformed manifest is refused: " + why, True)
check("manifest not declared (this checkout's pin absent): load_manifest returns None, never a pass", G.load_manifest(pub, os.path.join(tmp, "cache")) is None)
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
