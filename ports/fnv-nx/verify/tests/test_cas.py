#!/usr/bin/env python3
"""Tests for verify/cas.py. Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402

ROOT = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix="cas-test-")
FAILED = []
_n = [0]


def fresh(name):
    _n[0] += 1
    p = os.path.join(ROOT, "%s-%d" % (name, _n[0]))
    os.makedirs(p)
    return p


def write(path, data=b"x", mode=0o644, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, mode)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def base_tree(d):
    write(d + "/a.txt", b"alpha")
    write(d + "/sub/b.txt", b"beta")
    write(d + "/sub/run.sh", b"#!/bin/sh\n", 0o755)
    os.symlink("a.txt", d + "/ln")
    return d


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception:
        return True
    return False


def case(name):
    def deco(fn):
        try:
            ok = fn()
            msg = ""
        except Exception as e:  # noqa
            ok, msg = False, " (%s: %s)" % (type(e).__name__, e)
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, msg))
        if not ok:
            FAILED.append(name)
        return fn
    return deco


def store():
    return fresh("store")


def _ingest_proc(args):
    tree, st = args
    cas.ingest(tree, st)


@case("1 equal content, different mtime/mode/order -> equal hash")
def _():
    a = fresh("t")
    base_tree(a)
    b = fresh("t")
    write(b + "/sub/run.sh", b"#!/bin/sh\n", 0o700, mtime=12345)
    os.symlink("a.txt", b + "/ln")
    write(b + "/sub/b.txt", b"beta", 0o600, mtime=999)
    write(b + "/a.txt", b"alpha", 0o664, mtime=1)
    st = store()
    return cas.ingest(a, st) == cas.ingest(b, st)


@case("2 content/rename/exec/symlink/empty dir each change hash")
def _():
    st = store()
    hs = {}

    def variant(tag, fn):
        d = base_tree(fresh("v"))
        fn(d)
        hs[tag] = cas.ingest(d, st)
    hs["base"] = cas.ingest(base_tree(fresh("v")), st)
    variant("content", lambda d: write(d + "/a.txt", b"ALPHA"))
    variant("rename", lambda d: os.rename(d + "/a.txt", d + "/z.txt"))
    variant("exec", lambda d: os.chmod(d + "/a.txt", 0o755))
    variant("link", lambda d: (os.remove(d + "/ln"), os.symlink("b", d + "/ln")))
    variant("emptydir", lambda d: os.mkdir(d + "/empty"))
    return len(set(hs.values())) == len(hs)


@case("3 materialize -> re-ingest same hash; mtimes 0; shared inodes")
def _():
    st = store()
    src = base_tree(fresh("t"))
    h = cas.ingest(src, st)
    dest = os.path.join(fresh("d"), "out")
    cas.materialize(h, st, dest)
    if cas.ingest(dest, st) != h:
        return False
    for dp, dns, fns in os.walk(dest):
        for n in dns + fns + [""]:
            if os.lstat(os.path.join(dp, n)).st_mtime != 0:
                return False
    ino = os.stat(dest + "/a.txt")
    obj = os.path.join(st, "obj", cas.hashlib.sha256(b"alpha").hexdigest()[:2],
                       cas.hashlib.sha256(b"alpha").hexdigest())
    return (ino.st_ino == os.stat(obj).st_ino
            and not os.stat(dest + "/a.txt").st_mode & 0o222)


@case("4 materialize into existing dest errors")
def _():
    st = store()
    h = cas.ingest(base_tree(fresh("t")), st)
    return raises(cas.materialize, h, st, fresh("d"))


@case("5 fifo errors")
def _():
    d = base_tree(fresh("t"))
    os.mkfifo(d + "/pipe")
    return raises(cas.ingest, d, store())


@case("6 exclude leaves out exactly the named path")
def _():
    st = store()
    a = base_tree(fresh("t"))
    write(a + "/junk/x.o", b"o")
    b = base_tree(fresh("t"))
    os.mkdir(b + "/junk")
    c = base_tree(fresh("t"))
    return (cas.ingest(a, st, exclude=("junk/x.o",)) == cas.ingest(b, st)
            and cas.ingest(a, st, exclude=("junk",)) == cas.ingest(c, st)
            and cas.ingest(a, st) != cas.ingest(b, st))


@case("7 diff added/removed/changed")
def _():
    st = store()
    a = base_tree(fresh("t"))
    b = base_tree(fresh("t"))
    os.remove(b + "/sub/b.txt")
    write(b + "/a.txt", b"other")
    write(b + "/new.txt", b"n")
    d = sorted(cas.diff(cas.ingest(a, st), cas.ingest(b, st), st))
    return d == [("a.txt", "changed"), ("new.txt", "added"), ("sub/b.txt", "removed")]


@case("8 verify detects corrupted object")
def _():
    st = store()
    h = cas.ingest(base_tree(fresh("t")), st)
    cli = [sys.executable, "-I", os.path.join(os.path.dirname(HERE), "cas.py")]
    if subprocess.run(cli + ["verify", st], capture_output=True).returncode != 0:
        return False
    s = cas.hashlib.sha256(b"alpha").hexdigest()
    obj = os.path.join(st, "obj", s[:2], s)
    os.chmod(obj, 0o644)
    with open(obj, "wb") as f:
        f.write(b"evil")
    r = subprocess.run(cli + ["verify", st], capture_output=True, text=True)
    return r.returncode == 2 and s in r.stdout + r.stderr


@case("9 stage_key order independent, value sensitive")
def _():
    k = cas.stage_key
    return (k({"a": 1, "b": [1, 2]}) == k({"b": [1, 2], "a": 1})
            and len(k({"a": 1})) == 64 and k({"a": 1}) != k({"a": 2})
            and k([1, 2]) != k([2, 1]) and k("x") != k(["x"]))


@case("10 concurrent ingests leave valid store")
def _():
    st = store()
    t = base_tree(fresh("t"))
    for i in range(50):
        write(t + "/f%d" % i, b"c%d" % i)
    with multiprocessing.Pool(4) as p:
        p.map(_ingest_proc, [(t, st)] * 4)
    r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "cas.py"),
                        "verify", st], capture_output=True)
    return r.returncode == 0


@case("11 2000-file tree ingest + materialize")
def _():
    st = store()
    t = fresh("t")
    for i in range(2000):
        write(t + "/d%d/f%d" % (i % 20, i), b"data %d" % i)
    t0 = time.time()
    h = cas.ingest(t, st)
    t1 = time.time()
    dest = os.path.join(fresh("d"), "o")
    cas.materialize(h, st, dest)
    t2 = time.time()
    print("INFO ingest %.3fs materialize %.3fs" % (t1 - t0, t2 - t1))
    return cas.ingest(dest, st) == h


@case("tree_hash equals ingest, ignores mtimes, skips named entries")
def _():
    d = base_tree(fresh("th"))
    h = cas.tree_hash(d)
    st = fresh("th-store")
    same = h == cas.ingest(d, st) and not os.listdir(fresh("th-empty"))
    os.utime(d + "/a.txt", (5, 5))
    write(d + "/__pycache__/x.pyc", b"junk")
    return same and cas.tree_hash(d, skip=("__pycache__",)) == h and cas.tree_hash(d) != h and cas.tree_hash(d + "/a.txt") == cas.ingest(d + "/a.txt", st)


@case("materialize: an inode serves at most MAX_LINKS names; a replica is made past it (same bytes, mode, identity)")
def _():
    d = fresh("rep")
    for i in range(10):
        write("%s/f%d.h" % (d, i), b"same\n")
    write(d + "/x.sh", b"same\n", 0o755)
    st = fresh("rep-store")
    h = cas.ingest(d, st)
    old = cas.MAX_LINKS
    cas.MAX_LINKS = 4
    try:
        out = fresh("rep-out") + "/t"
        cas.materialize(h, st, out)
        ino = {}
        for n in sorted(os.listdir(out)):
            s = os.lstat(os.path.join(out, n))
            ino.setdefault(s.st_ino, []).append(n)
        e = cas.manifest(h, st)["entries"]["f0.h"]
        obj = cas._obj(st, e)
        counts = sorted(os.stat(p).st_nlink for p in (obj, obj + ".r1", obj + ".r2") if os.path.exists(p))
        out2 = fresh("rep-out2") + "/t"
        cas.materialize(h, st, out2)  # a second place of the same tree: still correct
        same = all(open(os.path.join(out, n), "rb").read() == b"same\n" for n in os.listdir(out)) and cas.ingest(out, st) == h == cas.ingest(out2, st)
        return (all(len(v) <= 4 for v in ino.values()) and len(ino) >= 3 and counts and max(counts) <= 4 and same
                and os.stat(os.path.join(out, "x.sh")).st_mode & 0o111 and not cas.verify(st))
    finally:
        cas.MAX_LINKS = old


@case("materialize: 70000 names of one blob are placed (the filesystem's own link cap is closed by rule)")
def _():
    d = fresh("many")
    write(d + "/blob.h", b"one blob\n")
    st = fresh("many-store")
    h0 = cas.ingest(d, st)
    e = cas.manifest(h0, st)["entries"]["blob.h"]
    import json as _j
    ents = {"n%05d.h" % i: e for i in range(70000)}
    data = cas.canon({"format": cas.FORMAT, "entries": ents})
    import hashlib as _h
    big = _h.sha256(data).hexdigest()
    cas._put(st, "tree", big + ".json", data)
    out = fresh("many-out") + "/t"
    cas.materialize(big, st, out)
    inodes = {os.lstat(os.path.join(out, n)).st_ino for n in os.listdir(out)}
    return len(os.listdir(out)) == 70000 and len(inodes) >= 2 and all(open(os.path.join(out, n), "rb").read() == b"one blob\n" for n in ("n00000.h", "n69999.h"))


def _cleanup():
    for dp, dns, fns in os.walk(ROOT):
        for n in dns:
            os.chmod(os.path.join(dp, n), 0o755)
    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    _cleanup()
    sys.exit(1 if FAILED else 0)
