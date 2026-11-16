#!/usr/bin/env python3
"""The tree of a deb is the deb's own data (recipe.Job._deb_tree): the pinned file of the mirror, checked against its sha256, unpacked with dpkg-deb -x; nothing is read from the
host's installed files. The assembled root has no dangling symlink (recipe.rootfs_dangling).
  - a toy mirror (a git repo holding debs/toy.deb) at a commit: the tree holds the archive's files and links, an absolute link target made relative;
  - a deb whose bytes differ from the pinned sha256 is red; a missing file is red;
  - a dangling link (an alternatives-style link, a target outside the closure) in an assembled root is reported, a link into the sandbox's own directories and a good chain are not.
Run: python3 -I <this file>. Needs git and dpkg-deb."""
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    pk = os.path.join(td, "pkg")
    os.makedirs(pk + "/DEBIAN")
    os.makedirs(pk + "/usr/bin")
    open(pk + "/DEBIAN/control", "w").write("Package: toy\nVersion: 1\nArchitecture: all\nMaintainer: t <t@t>\nDescription: toy\n")
    open(pk + "/usr/bin/busybox", "w").write("bb\n")
    os.symlink("busybox", pk + "/usr/bin/sh")
    os.symlink("../../etc/alternatives/python3", pk + "/usr/bin/python3")   # the deb ships this link; the host's alternatives are not the deb's
    os.symlink("/usr/bin/busybox", pk + "/usr/bin/abs")
    mir = os.path.join(td, "mirror")
    os.makedirs(mir + "/debs")
    subprocess.run(["dpkg-deb", "--build", pk, mir + "/debs/toy.deb"], check=True, capture_output=True)
    git(mir, "init", "-q")
    git(mir, "add", "-A")
    git(mir, "commit", "-q", "-m", "m")
    commit = git(mir, "rev-parse", "HEAD").strip()
    sha = hashlib.sha256(open(mir + "/debs/toy.deb", "rb").read()).hexdigest()
    recipe.deb_mirror = lambda: {"commit": commit}
    ext = os.path.join(td, "ext")
    os.makedirs(ext)
    recipe.set_artifacts(ext, mir)
    store = os.path.join(td, "store")
    os.makedirs(store)
    j = recipe.Job.__new__(recipe.Job)
    j.store, j.extcache = store, ext
    tmp = os.path.join(td, "tmp")
    os.makedirs(tmp)
    t = j._deb_tree(tmp, "toy", {"file": "toy.deb", "sha256": sha, "version": "1"})
    d = os.path.join(tmp, "deb-toy")
    check("the tree holds the archive's own files", open(d + "/usr/bin/busybox").read() == "bb\n" and os.readlink(d + "/usr/bin/sh") == "busybox", os.listdir(d + "/usr/bin"))
    check("the archive's link target is kept, whatever the host's alternatives say", os.readlink(d + "/usr/bin/python3") == "../../etc/alternatives/python3")
    check("an absolute target is made relative to the package tree", os.readlink(d + "/usr/bin/abs") == "busybox", os.readlink(d + "/usr/bin/abs"))
    check("the tree is ingested by content", isinstance(t, str) and len(t) == 64, t)
    tmp2 = os.path.join(td, "tmp2")
    os.makedirs(tmp2)
    try:
        j._deb_tree(tmp2, "toy", {"file": "toy.deb", "sha256": "0" * 64, "version": "1"})
        check("a deb that differs from the pinned sha256 is red", False)
    except recipe.BuildRed as e:
        check("a deb that differs from the pinned sha256 is red", "pins" in str(e.args), e.args)
    tmp3 = os.path.join(td, "tmp3")
    os.makedirs(tmp3)
    try:
        j._deb_tree(tmp3, "toy", {"file": "nope.deb", "sha256": sha, "version": "1"})
        check("a deb missing in the mirror is red", False)
    except recipe.BuildRed:
        check("a deb missing in the mirror is red", True)
    # the assembled root
    u = os.path.join(td, "union")
    os.makedirs(u + "/usr/bin")
    os.makedirs(u + "/etc")
    open(u + "/usr/bin/real", "w").write("x")
    os.symlink("real", u + "/usr/bin/ok")
    os.symlink("/usr/bin/real", u + "/usr/bin/abs")
    os.symlink("usr/bin", u + "/bin")
    os.symlink("/bin/ok", u + "/usr/bin/chain")
    os.symlink("/proc/self/exe", u + "/usr/bin/exe")
    check("a good chain, an absolute link and a link into the sandbox's own directory are no dangling link", recipe.rootfs_dangling(u) == [], recipe.rootfs_dangling(u))
    os.symlink("../../etc/alternatives/python3", u + "/usr/bin/python3")
    check("an alternatives-style link whose target is in no deb is dangling", recipe.rootfs_dangling(u) == ["/usr/bin/python3 -> ../../etc/alternatives/python3"], recipe.rootfs_dangling(u))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
