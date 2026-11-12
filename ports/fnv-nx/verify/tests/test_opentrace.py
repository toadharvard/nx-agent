#!/usr/bin/env python3
"""Tests for verify/opentrace.py. Run: python3 -I ports/fnv-nx/verify/tests/test_opentrace.py"""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "opentrace.py")
SYS = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"]
IGN = ["/proc", "/dev", "/sys"]
FAILS = testcase.FAILED


def with_tmp(fn):
    def run():
        t = tempfile.mkdtemp(prefix="opentrace-test-")
        try:
            fn(t)
            return True
        finally:
            shutil.rmtree(t, ignore_errors=True)
    return run


def ot(*args, cwd=None, env=None):
    e = dict(os.environ if env is None else env)
    return subprocess.run([sys.executable, "-I", TOOL, *args], cwd=cwd, env=e,
                          capture_output=True, text=True)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def manifest(t, **kw):
    m = {"stage": "t", "inputs": [t + "/in"], "system": SYS, "system_id": "sid",
         "outputs": [t + "/out"], "caches": [t + "/cache"], "ignore": IGN, "env": []}
    m.update(kw)
    p = t + "/m.json"
    write(p, json.dumps(m))
    return p


def report(t):
    with open(t + "/r.json") as f:
        return json.load(f)




TRACE = r'''100 execve("/usr/bin/sh", ["sh"], 0x1 /* 3 vars */) = 0
100 chdir("/w/in") = 0
100 clone(child_stack=NULL, flags=CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x1) = 101
101 chdir("d") = 0
101 openat(AT_FDCWD, "../a.txt", O_RDONLY|O_CLOEXEC) = 3</w/in/a.txt>
100 openat(AT_FDCWD, "rel.txt", O_RDONLY <unfinished ...>
102 openat(AT_FDCWD, "/w/out/o.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644) = 4</w/out/o.txt>
100 <... openat resumed>) = 5</w/in/rel.txt>
102 openat(AT_FDCWD, "/w/out/o.txt", O_RDONLY) = 6</w/out/o.txt>
100 openat(AT_FDCWD, "/w/out/old.txt", O_RDONLY) = 7</w/out/old.txt>
100 openat(AT_FDCWD, "/etc/nope", O_RDONLY) = -1 ENOENT (No such file or directory)
100 openat(AT_FDCWD, "/home/x/missing", O_RDONLY) = -1 ENOENT (No such file or directory)
100 openat(AT_FDCWD, "/proc/self/maps", O_RDONLY) = 8</proc/1/maps>
100 openat(AT_FDCWD, "/home/x/secret", O_RDONLY) = 9</home/x/secret>
100 openat(AT_FDCWD, "/home/x/wonly", O_WRONLY|O_CREAT, 0644) = 10</home/x/wonly>
100 openat(AT_FDCWD, "/w/cache/c1", O_RDONLY) = 11</w/cache/c1>
100 openat(AT_FDCWD, "/w/other", O_RDONLY|O_DIRECTORY) = 12</w/other>
100 openat(3</w/in/d>, "z", O_RDONLY) = 13</w/in/d/z>
100 execve("/opt/tool/bin/cc", ["cc"], 0x1 /* 3 vars */) = 0
'''


@case("check: fork+chdir+relative+unfinished+failed")
@with_tmp
def _(t):
    write(t + "/tr.txt", TRACE)
    m = manifest(t)
    with open(m) as f:
        d = json.load(f)
    d.update(inputs=["/w/in"], outputs=["/w/out"], caches=["/w/cache"])
    write(m, json.dumps(d))
    p = ot("check", "--manifest", m, "--trace", t + "/tr.txt", "--out", t + "/r.json",
           cwd=t)
    assert p.returncode == 2, (p.returncode, p.stderr)
    r = report(t)
    assert r["format"] == "fnvnx-opentrace/1" and r["verdict"] == "red"
    cls = {x["path"]: (x["class"], x["how"]) for x in r["red"]}
    assert cls == {"/w/out/old.txt": ("stale", "open"),
                   "/home/x/secret": ("outside", "open"),
                   "/w/other": ("outside", "listdir"),
                   "/opt/tool/bin/cc": ("outside", "exec")}, cls
    assert r["counts"]["input"] == 3, r["counts"]   # a.txt, rel.txt, d/z
    assert r["counts"]["own"] == 1 and r["counts"]["cache"] == 1
    assert r["probe_outside"] == 1, r["probe_outside"]
    assert [x["path"] for x in r["red"]] == sorted(cls)


GOOD = 'cat "$1/a.txt" > "$2/o.txt"; cat "$2/o.txt" >/dev/null'


def run(t, script, extra=(), cwd=None, env=None, **mk):
    write(t + "/in/a.txt", "hello\n")
    os.makedirs(t + "/out", exist_ok=True)
    m = manifest(t, **mk)
    return ot("run", "--manifest", m, "--out", t + "/r.json", "--keep-trace",
              t + "/trace.txt", "--", "sh", "-c", script, "x", t + "/in", t + "/out",
              *extra, cwd=cwd, env=env)


@case("run: declared input + own output -> green")
@with_tmp
def _(t):
    p = run(t, GOOD)
    r = report(t)
    assert p.returncode == 0 and r["verdict"] == "green", (p.stderr, r)
    assert r["counts"].get("input", 0) >= 1 and r["counts"].get("own", 0) >= 1, r["counts"]
    assert r["cmd_rc"] == 0


@case("run: undeclared read -> red outside")
@with_tmp
def _(t):
    write(t + "/secret.txt", "s")
    p = run(t, GOOD + '; cat "$3" >/dev/null', [t + "/secret.txt"])
    r = report(t)
    assert p.returncode == 2 and r["verdict"] == "red"
    assert {"path": t + "/secret.txt", "class": "outside", "pid": r["red"][0]["pid"],
            "how": "open"} in r["red"], r["red"]


@case("run: pre-existing output read -> red stale")
@with_tmp
def _(t):
    write(t + "/out/old.txt", "old")
    p = run(t, GOOD + '; cat "$2/old.txt" >/dev/null')
    r = report(t)
    assert p.returncode == 2
    assert any(x["path"] == t + "/out/old.txt" and x["class"] == "stale" for x in r["red"]), r["red"]


@case("key: stable, changes with input content and env value")
@with_tmp
def _(t):
    write(t + "/in/a.txt", "one")
    m = manifest(t, env=["ORDER_KEYS"])
    e = {k: v for k, v in os.environ.items() if k != "ORDER_KEYS"}
    k = lambda env: ot("key", "--manifest", m, env=env).stdout.strip()
    k1 = k(e)
    assert len(k1) == 16 and k1 == k(e)
    write(t + "/in/a.txt", "two")
    k2 = k(e)
    assert k2 != k1
    k3 = k(dict(e, ORDER_KEYS="1"))
    assert k3 != k2 and k3 == k(dict(e, ORDER_KEYS="1")) and k3 != k(dict(e, ORDER_KEYS="2"))


@case("run: child with cd + relative path resolves")
@with_tmp
def _(t):
    write(t + "/in/sub/keep", "")
    write(t + "/in/x", "x")
    write(t + "/in/a.txt", "x")
    m = manifest(t)
    p = ot("run", "--manifest", m, "--out", t + "/r.json", "--", "sh", "-c",
           "(cd sub && cat ../x >/dev/null)", cwd=t + "/in")
    r = report(t)
    assert p.returncode == 0 and r["verdict"] == "green", (p.stderr, r["red"])
    assert r["counts"]["input"] >= 1


if __name__ == "__main__":
    print("%d failed" % len(FAILS) if FAILS else "all passed")
    sys.exit(1 if FAILS else 0)
