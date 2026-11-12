#!/usr/bin/env python3
"""Tests for verify/sandbox.py (rule R12 BUILD-PURE). Plain script: exit 0 on pass, one PASS/FAIL line per case.
Needs root, gcc (static probe) and busybox (static, e.g. busybox-static)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

ROOT = tempfile.mkdtemp(dir="/root/l4/sbx-test" if os.path.isdir("/root/l4/sbx-test") else os.path.expanduser("~"),
                        prefix="sbx-")
FAILED = testcase.FAILED
_n = [0]
PROBE_C = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <fcntl.h>
#include <dirent.h>
#include <unistd.h>
#include <sched.h>
#include <errno.h>
#include <sys/auxv.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/sysinfo.h>
#include <sys/utsname.h>
static void cat(const char *k, const char *p) {
    char b[256]; int n; int fd = open(p, O_RDONLY);
    if (fd < 0) { printf("%s=ERR%d\n", k, errno); return; }
    n = read(fd, b, 255); b[n < 0 ? 0 : n] = 0; close(fd);
    printf("%s=len%d\n", k, n);
}
int main(int argc, char **argv) {
    struct timespec t; struct stat st; struct utsname u; struct sysinfo si; char b[64]; unsigned char r[1];
    if (argc > 1 && !strcmp(argv[1], "clock")) { clock_gettime(CLOCK_REALTIME, &t); printf("%ld.%09ld\n", t.tv_sec, t.tv_nsec); return 0; }
    clock_gettime(CLOCK_REALTIME, &t); printf("clock_ns=%ld%09ld\n", t.tv_sec, t.tv_nsec);
    printf("time_s=%ld\n", (long)time(0));
    close(open("/out/f", O_CREAT | O_WRONLY, 0644)); stat("/out/f", &st);
    printf("file_mtime=%ld\n", st.st_mtime); printf("ino_f=%lu\n", (unsigned long)st.st_ino);
    r[0] = 0; long g = (long)getrandom(r, 1, 0); int ge = errno; printf("getrandom_rc=%ld errno=%d\n", g, ge);
    unsigned char *ar = (unsigned char *)getauxval(AT_RANDOM); printf("at_random=");
    for (int i = 0; i < 16; i++) printf("%02x", ar[i]); printf("\n");
    cpu_set_t cs; sched_getaffinity(0, sizeof cs, &cs); printf("ncpu=%d\n", CPU_COUNT(&cs));
    uname(&u); printf("release=%s\n", u.release);
    sysinfo(&si); printf("totalram=%lu\n", si.totalram * si.mem_unit);
    printf("uid=%d gid=%d euid=%d\n", getuid(), getgid(), geteuid());
    const char *names[] = {"e", "b", "d", "a", "c"};
    for (int i = 0; i < 5; i++) { snprintf(b, 64, "/out/%s", names[i]); close(open(b, O_CREAT | O_WRONLY, 0644)); }
    DIR *d = opendir("/out"); struct dirent *e; printf("readdir=");
    while ((e = readdir(d))) if (e->d_name[0] != '.') printf("%s,", e->d_name); printf("\n");
    int fd = open("/proc/self/stat", O_RDONLY); char s[1024]; int n = read(fd, s, 1023); s[n] = 0;
    char *p = strrchr(s, ')') + 2; for (int i = 0; i < 19; i++) p = strchr(p, ' ') + 1;
    printf("starttime=%ld\n", atol(p));
    cat("proc_uptime", "/proc/uptime"); cat("proc_stat", "/proc/stat"); cat("proc_loadavg", "/proc/loadavg");
    cat("proc_uuid", "/proc/sys/kernel/random/uuid"); cat("dev_urandom", "/dev/urandom");
    return 0;
}
'''


def fresh(name):
    _n[0] += 1
    p = os.path.join(ROOT, "%s-%d" % (name, _n[0]))
    os.makedirs(p)
    return p




def sb(*args, env=None):
    r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "sandbox.py")] + list(args),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


def write(path, data, mode=0o644, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, mode)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def mkstage(store, name, inputs, outputs, argv, env=None, cwd="/work"):
    out = os.path.join(fresh("doc"), "stage.json")
    a = ["stage", "--store", store, "--name", name, "--cwd", cwd, "-o", out]
    for at, host in inputs.items():
        a += ["--input", "%s=%s" % (at, host)]
    for o in outputs:
        a += ["--output", o]
    for k, v in (env or {"PATH": "/bin"}).items():
        a += ["--env", "%s=%s" % (k, v)]
    rc, o, e = sb(*(a + ["--"] + argv))
    if rc != 0:
        raise RuntimeError("stage failed: " + e)
    return out


def run(store, stage, *flags):
    rc, o, e = sb("run", "--store", store, stage, *flags)
    try:
        return rc, json.loads(o), e
    except ValueError:
        return rc, o, e


def main():
    if os.geteuid() != 0 or not shutil.which("busybox") or not shutil.which("gcc"):
        print("FAIL prerequisites: need root, busybox, gcc")
        return 1
    import sandbox  # noqa: E402  (case 0: importable)
    case("importable", hasattr(sandbox, "main"))
    bb = fresh("bb")
    shutil.copy(shutil.which("busybox"), os.path.join(bb, "busybox"))
    os.chmod(os.path.join(bb, "busybox"), 0o755)
    for ap in subprocess.run([os.path.join(bb, "busybox"), "--list"], capture_output=True, text=True).stdout.split():
        if ap != "busybox":
            os.symlink("busybox", os.path.join(bb, ap))
    pd = fresh("probe")
    write(os.path.join(pd, "p.c"), PROBE_C.encode())
    subprocess.run(["gcc", "-static", "-O1", "-o", os.path.join(pd, "probe"), os.path.join(pd, "p.c")], check=True)
    probe = os.path.join(pd, "probe")
    store = fresh("store")
    sh = lambda script, *a: ["/bin/sh", "-c", script, "sh"] + list(a)  # noqa: E731

    # (1) a stage runs, output equals the expected tree
    a = write(os.path.join(fresh("src"), "a"), b"hello\n")
    st1 = mkstage(store, "cat", {"/bin": bb, "/in/a": a}, ["/out"], sh("cat /in/a > /out/b"))
    exp = fresh("exp")
    write(os.path.join(exp, "b"), b"hello\n")
    t0 = time.time()
    rc, res, err = run(store, st1)
    dt = time.time() - t0
    case("1 stage runs, output hash == expected tree", rc == 0 and isinstance(res, dict) and
          res["outputs"]["/out"] == cas.ingest(exp, store), "%s %s" % (rc, err[:300]))
    print("INFO one sandbox run of case 1: %.2f s" % dt)

    # (2) second run is a HIT and runs nothing
    ds = mkstage(store, "date", {"/bin": bb, "/p": probe}, ["/out"], sh("/p clock > /out/t"))
    r1 = run(store, ds)
    calls = []
    real = sandbox.launch
    sandbox.launch = lambda *x, **k: calls.append(1) or real(*x, **k)
    rc2 = sandbox.main(["run", "--store", store, ds])
    sandbox.launch = real
    r2 = run(store, ds)
    case("2 second run is a HIT: nothing launched, same clock-dependent output",
          r1[0] == 0 and not calls and r2[1] == r1[1] and rc2 == 0, "calls=%d" % len(calls))

    # (3) isolation
    code = r'''
for p in /root /home /etc/hostname /etc "$1" /sys /oldroot; do [ -e "$p" ] && echo "EXISTS $p"; done
echo "hostname=$(hostname)"; echo "pid=$$"; echo "ls_dev=$(ls /dev | tr '\n' ' ')"
echo "netdev=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | tr '\n' ' ')"
echo "ifup=[$(ifconfig)]"; nc -w 1 1.1.1.1 80 </dev/null >/dev/null 2>&1; echo "nc_rc_nonzero=$?"
echo "tmp=$(echo y > /tmp/x && cat /tmp/x)"; echo "root=$(ls / | tr '\n' ' ')"
echo "mounts=$(wc -l < /proc/mounts)"'''
    st3 = mkstage(store, "iso", {"/bin": bb}, ["/out"], sh("{ " + code + "\n} > /out/r", store), env={"PATH": "/bin", "A": "1"})
    rc, res, err = run(store, st3)
    d3 = fresh("get")
    sb("get", "--store", store, res["key"], "/out", os.path.join(d3, "o"))
    lines = open(os.path.join(d3, "o", "r")).read().split("\n") if rc == 0 else []
    kv = dict(l.split("=", 1) for l in lines if "=" in l)
    mounts_bad = [l for l in lines if l.startswith("EXISTS")]
    case("3a host paths absent", rc == 0 and not mounts_bad, str(lines[:10]))
    case("3b hostname fnvnx, pid 1, dev={null,zero,random,urandom,shm}, tmp writable",
          kv.get("hostname") == "fnvnx" and kv.get("pid") == "1" and kv.get("ls_dev") == "null random shm urandom zero " and kv.get("tmp") == "y",
          str(kv))
    case("3c network: only down lo, nothing up, connect fails",
          kv.get("netdev") == "lo " and kv.get("ifup") == "[]" and kv.get("nc_rc_nonzero") != "0", str(kv))
    case("3d root holds only declared dirs", kv.get("root", "").split() == ["bin", "dev", "out", "proc", "tmp", "work"], str(kv))
    st3e = mkstage(store, "env", {"/bin": bb}, [], ["/bin/env"], env={"PATH": "/bin", "A": "1", "Z": "two words"})
    rc, res, err = run(store, st3e)
    case("3e environment is exactly the declared one",
          rc == 0 and _sorted_hash(store, res["stdout"]) == sorted([b"PATH=/bin", b"A=1", b"Z=two words"]), str(res)[:300])

    # (4) inputs are read-only
    st4 = mkstage(store, "ro", {"/bin": bb, "/in/a": a, "/in/d": os.path.dirname(a)}, ["/out"],
                  sh('(echo x > /in/a) 2>/dev/null && echo W1; rm /in/a 2>/dev/null && echo W2; '
                     'chmod 777 /in/a 2>/dev/null && echo W3; (echo x > /in/d/new) 2>/dev/null && echo W4; '
                     'mount -o remount,rw /in/a 2>/dev/null && echo W5; echo done > /out/r'))
    rc, res, err = run(store, st4)
    case("4 inputs read-only, CAS intact", rc == 0 and _text(store, res["stdout"]) == "" and cas.verify(store) == [],
          "%s %r" % (rc, _text(store, res["stdout"]) if rc == 0 else err))

    # (5) key semantics
    h1 = write(os.path.join(fresh("k1"), "a"), b"same", mtime=1000)
    h2 = write(os.path.join(fresh("k2"), "q"), b"same", mtime=999999)
    ks = lambda inp, argv=("/bin/true",), env=None, at="/in/a": json.load(open(  # noqa: E731
        mkstage(store, "k", {at: inp}, ["/out"], list(argv), env)))
    base = sandbox.key_of(ks(h1))
    case("5a key independent of host path and mtime", base == sandbox.key_of(ks(h2)))
    h3 = write(os.path.join(fresh("k3"), "a"), b"other")
    case("5b key changes with content/argv/env/at",
          len({base, sandbox.key_of(ks(h3)), sandbox.key_of(ks(h1, ("/bin/false",))),
               sandbox.key_of(ks(h1, env={"PATH": "/x"})), sandbox.key_of(ks(h1, at="/in/b"))}) == 5)

    # (6)/(7) cold verification
    ds6 = mkstage(store, "det", {"/bin": bb, "/in/a": a}, ["/out"], sh("cat /in/a /in/a > /out/b"))
    run(store, ds6)
    rc, o, e = run(store, ds6, "--cold")
    case("6 --cold on a deterministic stage: equal, exit 0", rc == 0, "%s %s" % (rc, e[:200]))
    rc, o, e = run(store, ds, "--cold")
    case("7 --cold on a clock-reading stage: exit 3 naming the path", rc == 3 and "/out/t" in (o if isinstance(o, str) else "") + e,
          "%s %s %s" % (rc, str(o)[:100], e[:200]))

    # (8) failing command
    st8 = mkstage(store, "fail", {"/bin": bb}, ["/out"], sh("echo hi; echo err >&2; exit 7"))
    rc, res, err = run(store, st8)
    rc2, res2, _ = run(store, st8)
    case("8 failing command: rc stored, HIT gives same rc", rc == 7 and rc2 == 7 and res["rc"] == 7 and res == res2
          and _text(store, res["stdout"]) == "hi\n" and _text(store, res["stderr"]) == "err\n", "%s %s" % (rc, rc2))
    # unsupported cases are errors (exit 1), nothing stored
    dj = json.load(open(st8))
    dj["inputs"].append({"at": "/bin/x", "tree": dj["inputs"][0]["tree"]})
    badp = os.path.join(fresh("doc"), "bad.json")
    json.dump(dj, open(badp, "w"))
    rc, o, e = run(store, badp)
    case("8b nested input paths: exit 1, nothing stored", rc == 1 and "nest" in e, "%s %s" % (rc, e[:100]))

    # (9) channel probes
    pst = mkstage(store, "probe", {"/bin": bb, "/p": probe}, ["/out"], sh("/p > /out/o"))
    store2 = fresh("store2")
    pst2 = mkstage(store2, "probe", {"/bin": bb, "/p": probe}, ["/out"], ["/bin/taskset", "-c", "0", "/bin/sh", "-c",
                                                                           "/p > /out/o", "sh"])
    r_a = run(store, pst)
    time.sleep(1.1)
    r_b = run(store2, pst2)

    def probes(r, s):
        d = fresh("pg")
        sb("get", "--store", s, r[1]["key"], "/out", os.path.join(d, "o"))
        return dict(l.split("=", 1) for l in open(os.path.join(d, "o", "o")).read().split("\n") if "=" in l)
    A, B = probes(r_a, store), probes(r_b, store2)
    hostmem = int([l for l in open("/proc/meminfo") if l.startswith("MemTotal")][0].split()[1]) * 1024
    ch = {}
    ch["wall_clock"] = (A["clock_ns"] != B["clock_ns"] or A["time_s"] != B["time_s"],
                        "clock_ns %s vs %s, file mtime %s vs %s" % (A["clock_ns"], B["clock_ns"], A["file_mtime"], B["file_mtime"]))
    ch["entropy"] = (A["at_random"] != B["at_random"] or not A["getrandom_rc"].startswith("-1"),
                     "getrandom: %s, AT_RANDOM %s vs %s, dev_urandom %s" % (A["getrandom_rc"], A["at_random"][:8], B["at_random"][:8], A["dev_urandom"]))
    ch["cpu_count_scheduling"] = (A["ncpu"] != B["ncpu"], "ncpu %s vs %s under taskset; scheduling order not controlled" % (A["ncpu"], B["ncpu"]))
    ch["uid_gid"] = (not (A["uid"] == B["uid"] == "0 gid=0 euid=0"), "uid/gid %s / %s" % (A["uid"], B["uid"]))
    ch["kernel_version"] = (A["release"] == os.uname().release, "uname release %s == host %s" % (A["release"], os.uname().release))
    ch["memory_size"] = (int(A["totalram"]) == hostmem, "sysinfo totalram %s == host MemTotal %d" % (A["totalram"], hostmem))
    ch["inode_readdir_order"] = (A["ino_f"] != B["ino_f"] or A["readdir"] != "a,b,c,d,e,",
                                 "ino %s vs %s, readdir %s (created e,b,d,a,c)" % (A["ino_f"], B["ino_f"], A["readdir"]))
    ch["procfs"] = (A["starttime"] != B["starttime"] or not A["proc_uptime"].startswith("ERR") and A["proc_uptime"] != "len0",
                    "/proc/self/stat starttime %s vs %s; uptime %s stat %s loadavg %s uuid %s" % (
                        A["starttime"], B["starttime"], A["proc_uptime"], A["proc_stat"], A["proc_loadavg"], A["proc_uuid"]))
    opened = set()
    for name in sorted(ch):
        is_open, ev = ch[name]
        print("channel %s: %s (%s)" % (name, "OPEN" if is_open else "closed", ev))
        if is_open:
            opened.add(name)
    case("9a getrandom returns ENOSYS", A["getrandom_rc"] == "-1 errno=38", A["getrandom_rc"])
    case("9b no /dev/urandom, entropy files in /proc empty or absent",
          A["dev_urandom"].startswith("ERR") and A["proc_uuid"] in ("len0",) or A["proc_uuid"].startswith("ERR"), str(A))
    case("9c measured OPEN set == result.json open_channels", opened == set(r_a[1]["open_channels"]),
          "%s vs %s" % (sorted(opened), r_a[1]["open_channels"]))

    # (10) a sandbox with zero host files: busybox only
    s10 = write(os.path.join(fresh("s10"), "s.sh"), b"#!/bin/sh\necho zero host files > /out/r\nls / > /out/ls\n")
    st10 = mkstage(store, "zero", {"/bin/busybox": os.path.join(bb, "busybox"), "/in/s.sh": s10}, ["/out"],
                   ["/bin/busybox", "sh", "/in/s.sh"], env={})
    rc, res, err = run(store, st10)
    case("10 busybox-only sandbox runs a script, empty environment", rc == 0, "%s %s" % (rc, err[:200]))
    # (11) one key executed by several threads of ONE process at once (a verdict gate builds units of one store in a thread pool): the
    # run directory and the result temp file must not be named by the pid alone (FileExistsError on out/0 was the symptom)
    import concurrent.futures
    doc10 = json.load(open(st10))
    key10 = sandbox.key_of(doc10)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(sandbox.execute, store, doc10, key10) for _ in range(6)]
        errs = [f.exception() for f in futs]
    case("11 same key run by 6 threads of one process: no collision, equal results", not any(errs)
          and len({json.dumps(f.result(), sort_keys=True) for f in futs}) == 1, str(errs))
    return 1 if FAILED else 0


def _text(store, h):
    d = fresh("txt")
    p = os.path.join(d, "x")
    cas.materialize(h, store, p)
    return open(p).read()


def _sorted_hash(store, h):
    return sorted(_text(store, h).encode().split(b"\n")[:-1])


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as ex:  # a crash is a failure
        import traceback
        traceback.print_exc()
        print("FAIL crashed: %r" % ex)
        rc = 1
    shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(rc)
