#!/usr/bin/env python3
"""Tests for the VIEW placement of verify/sandbox.py: a view (a tree that holds unitview.PLACEHOLDER) is its all-placeholder base, placed once
per store, with the object of every real file bound read-only over its placeholder path. Cases: (1) the tree a stage sees equals
cas.materialize(view) in listing, content, links and exec bits, for a tree with symlinked directories and `..`; (2) O(real files) binds, no
per-view placed tree; (3) a placeholder inode serves at most MAX_LINKS names (EMLINK is closed by rule); (4) the base is the same for two
views of one tree; (5) an OSError of the sandbox (EMLINK) is a red stage naming the stage. Needs root and busybox (static). Plain script."""
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import sandbox  # noqa: E402
import unitstage  # noqa: E402
import unitview  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="views-test-")
FAILED = []
SCRIPT = r'''V=$1; cd "$V"
echo "== names"; find . | sort
echo "== files"; find . -type f | sort | while read f; do echo "-- $f"; cat "$f"; [ -x "$f" ] && echo "(exec)"; done
echo "== links"; find . -type l | sort | while read l; do echo "$l -> $(readlink "$l")"; done
echo "== dotdot"; cat lnk/../x.h; cat lnk/z.h; cat lnk/../../top.h; cat lnk/../y.h
'''


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + str(detail)[:500]) if (detail and not ok) else ""), flush=True)
    if not ok:
        FAILED.append(name)


def w(p, data, mode=0o644):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    os.chmod(p, mode)


def main():
    if os.geteuid() != 0 or not shutil.which("busybox"):
        print("FAIL prerequisites: need root and busybox")
        return 1
    store = os.path.join(ROOT, "store")
    bb = os.path.join(ROOT, "bb")
    os.makedirs(bb)
    shutil.copy(shutil.which("busybox"), os.path.join(bb, "busybox"))
    os.chmod(os.path.join(bb, "busybox"), 0o755)
    for ap in subprocess.run([os.path.join(bb, "busybox"), "--list"], capture_output=True, text=True).stdout.split():
        if ap != "busybox":
            os.symlink("busybox", os.path.join(bb, ap))
    real = os.path.join(ROOT, "real")
    w(real + "/top.h", b"top\n")
    w(real + "/a/x.h", b"x\n")
    w(real + "/a/y.h", b"y\n")
    w(real + "/a/b/z.h", b"z\n", 0o755)
    w(real + "/c/d/e.h", b"e\n")
    os.symlink("a/b", real + "/lnk")          # a symlinked directory: `lnk/..` is the real parent a
    os.symlink("../top.h", real + "/a/up.h")   # a symlinked file
    os.makedirs(real + "/empty")
    root = cas.ingest(real, store)
    cache = {}
    keep = ["top.h", "a/x.h", "a/b/z.h"]
    view = unitview.view(store, root, keep, cache)
    mat = os.path.join(ROOT, "mat")
    cas.materialize(view, store, mat)
    host = subprocess.run([os.path.join(bb, "busybox"), "sh", "-c", SCRIPT, "sh", mat], capture_output=True, text=True)
    # the stage: busybox, the script and the view at /v; the listing is written to /out/res.txt
    sc = os.path.join(ROOT, "script")
    w(sc + "/s.sh", (SCRIPT + "\n").encode())
    doc = {"format": "fnvnx-stage/1", "stage": "views", "cwd": "/work", "env": {"PATH": "/bin"}, "outputs": ["/out"],
           "argv": ["/bin/sh", "-c", "/bin/sh /in/s.sh /v > /out/res.txt 2>&1; grep -c ' /v/' /proc/self/mountinfo > /out/mounts.txt"],
           "inputs": sorted([{"at": "/bin", "tree": cas.ingest(bb, store)}, {"at": "/in", "tree": cas.ingest(sc, store)},
                             {"at": "/v", "tree": view}], key=lambda i: i["at"])}
    dp = os.path.join(ROOT, "doc.json")
    json.dump(doc, open(dp, "w"))
    r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "sandbox.py"), "run", "--store", store, dp], capture_output=True, text=True)
    res = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    out = os.path.join(ROOT, "out")
    if res:
        cas.materialize(res["outputs"]["/out"], store, out)
    seen = open(out + "/res.txt").read() if res else ""
    want = host.stdout.replace(mat, "/v")
    check("the sandbox ran the listing stage on the view", r.returncode == 0 and bool(res), r.stderr)
    check("the tree a stage sees equals cas.materialize(view): names, contents, symlinks, exec bits, `..` through a symlinked directory",
          seen == want and "(exec)" in seen and "lnk -> a/b" in seen and "#error fnvnx" in seen, (seen[:600], want[:600]))
    nm = int(open(out + "/mounts.txt").read().strip() or 0) if res else -1
    check("O(real files) binds: one bind per real file (%d real files) and nothing per placeholder" % len(keep), nm == len(keep), nm)
    check("no per-view placed tree is made", not os.path.exists(os.path.join(store, "placed", view)))
    # the base is the same for two views of one tree (content-addressed by the all-placeholder manifest)
    view2 = unitview.view(store, root, ["a/y.h"], cache)
    b1, _ = sandbox._split(store, view)
    b2, _ = sandbox._split(store, view2)
    check("two views of one tree share one base; the base is the view with no real file", b1 == b2 == unitview.view(store, root, [], cache), (b1, b2))
    check("the base directory is placed once and the second call reuses it", sandbox.placed_input(store, view)[0] == sandbox.placed_input(store, view2)[0])
    # (3) a placeholder inode serves at most MAX_LINKS names
    old = sandbox.MAX_LINKS
    sandbox.MAX_LINKS = 3
    try:
        big = os.path.join(ROOT, "big")
        for i in range(10):
            w("%s/f%d.h" % (big, i), b"f%d\n" % i)
        broot = cas.ingest(big, store)
        bview = unitview.view(store, broot, [], {})
        d, binds = sandbox.placed_input(store, bview)
        inodes = {}
        for n in os.listdir(d):
            st = os.lstat(os.path.join(d, n))
            inodes[st.st_ino] = st.st_nlink
        check("10 placeholder names with MAX_LINKS 3: 4 inodes, none above 3 links, no bind", sorted(inodes.values()) == [1, 3, 3, 3] and not binds, (inodes, binds))
    finally:
        sandbox.MAX_LINKS = old
    # (5) an OSError of the sandbox is a red stage naming the stage
    real_exec = sandbox.execute

    def boom(store, doc, key):
        raise OSError(errno.EMLINK, "Too many links")
    sandbox.execute = boom
    try:
        d2 = dict(doc, stage="pp:some/unit.cc")
        try:
            unitstage.run_stage(os.path.join(ROOT, "store2"), d2, False)
            got = None
        except sandbox.SbxError as e:
            got = e
        check("EMLINK of the sandbox is an SbxError naming the stage (a red stage, not a crash)",
              got is not None and getattr(got, "stage", None) == "pp:some/unit.cc" and "pp:some/unit.cc" in str(got) and "Too many links" in str(got), got)
    finally:
        sandbox.execute = real_exec
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print("FAIL crashed: %r" % ex)
        rc = 1
    shutil.rmtree(ROOT, ignore_errors=True)
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    sys.exit(rc)
