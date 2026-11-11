#!/usr/bin/env python3
"""Hermetic build sandbox (rule R12 BUILD-PURE): a stage sees ONLY its declared inputs, so its outputs are a
function of the stage key. Needs root, Linux x86-64. Stage document "fnvnx-stage/1":
  {"format","stage","argv":[abs tool, args], "cwd","env":{COMPLETE env},"inputs":[{"at","tree"}],"outputs":[dirs]}
  optional "class": "build" | "verification" (part of the key; verdict.py provenance requires it); optional "tool"
  (the declared tool kind toolflags.py checks; part of the key).
key = cas.stage_key(document + {"sandbox": {sandbox.py sha256, cas.py sha256}}). Results: STORE/keys/<key>/result.json.
Sandbox: new mount/net/uts/pid/ipc namespaces; tmpfs root; inputs hardlinked from the CAS and bound read-only;
outputs fresh rw dirs; fresh /proc (time/stat/sys masked), /dev = null+zero, tmpfs /tmp; hostname fnvnx; no interface up;
stdin /dev/null; umask 022; ASLR off; no core files; all capabilities dropped; seccomp: getrandom -> ENOSYS (x32 ABI and foreign arch killed).
Measured (tests/test_sandbox.py case 9), NOT closed, listed in result.json "open_channels":
  wall_clock           clock_gettime/time/statx show real time (vDSO). PR_SET_TSC=SIGSEGV + seccomp on the clock syscalls
                       was tried: dynamic date/python/gcc/make crash at startup (SIGSEGV), coarse clocks stay readable. Dropped.
  entropy              getrandom is closed (ENOSYS) and /dev/urandom, /dev/random are a constant 1 MiB file (so os.urandom
                       and Python's multiprocessing import work and return the same bytes in every run), but AT_RANDOM and
                       RDRAND still give fresh bytes.
  cpu_count_scheduling sched_getaffinity/nproc show the host CPUs; order of parallel children is not controlled.
  kernel_version       uname shows the host kernel release.
  memory_size          sysinfo shows the host memory size.
  inode_readdir_order  inode numbers differ per run; readdir order is filesystem-hash order (not sorted).
  procfs               /proc/self/stat start time counts from host boot; other /proc files not masked remain.
Closed: network, hostname, pid numbering (command is pid 1), host files, environment, uid/gid (0/0), ASLR, leftovers.
"""
import ctypes
import hashlib
import json
import os
import platform
import resource
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN_CHANNELS = ["wall_clock", "entropy", "cpu_count_scheduling", "kernel_version", "memory_size",
                 "inode_readdir_order", "procfs"]
RESERVED = ["/proc", "/dev", "/tmp", "/oldroot"]
MS_RDONLY, MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 1, 32, 4096, 16384, 1 << 18
libc = ctypes.CDLL(None, use_errno=True)

class SbxError(Exception):
    pass

def _c(rc, what):
    if rc != 0:
        raise SbxError("%s: %s" % (what, os.strerror(ctypes.get_errno())))

def _mount(src, tgt, fs=None, flags=0, data=None):
    _c(libc.mount(src and src.encode(), tgt.encode(), fs and fs.encode(), ctypes.c_ulong(flags),
                  data and data.encode()), "mount %s" % tgt)

def _bind_ro(src, tgt, ro=True):
    _mount(src, tgt, None, MS_BIND | MS_REC)
    if ro:
        _mount(None, tgt, None, MS_BIND | MS_REMOUNT | MS_RDONLY)

def sandbox_id():
    return {n: cas._sha_file(os.path.join(HERE, n)) for n in ("sandbox.py", "cas.py")}

def key_of(doc):
    return cas.stage_key(dict(doc, sandbox=sandbox_id()))

def check_doc(doc):
    ok = (isinstance(doc, dict) and doc.get("format") == "fnvnx-stage/1" and isinstance(doc.get("stage"), str)
          and isinstance(doc.get("argv"), list) and doc["argv"] and all(isinstance(a, str) for a in doc["argv"])
          and doc["argv"][0].startswith("/") and isinstance(doc.get("cwd"), str) and doc["cwd"].startswith("/")
          and isinstance(doc.get("env"), dict) and isinstance(doc.get("inputs"), list)
          and isinstance(doc.get("outputs"), list) and set(doc) - {"class", "tool"} == {"format", "stage", "argv", "cwd", "env", "inputs", "outputs"}
          and doc.get("class", "build") in ("build", "verification"))
    if not ok:
        raise SbxError("malformed stage document")
    for i in doc["inputs"]:
        if not (isinstance(i, dict) and set(i) == {"at", "tree"} and isinstance(i["at"], str)):
            raise SbxError("malformed input entry")
    paths = [i["at"] for i in doc["inputs"]] + list(doc["outputs"])
    for p in paths:
        if not p.startswith("/") or os.path.normpath(p) != p or p == "/":
            raise SbxError("bad path %r (absolute, normalized, not /)" % p)
    for a in paths:
        if any(a == r or a.startswith(r + "/") for r in RESERVED):
            raise SbxError("path %s collides with the sandbox's own %s" % (a, RESERVED))
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise SbxError("paths nest or repeat: %s, %s" % (a, b))
    if platform.machine() != "x86_64" or os.geteuid() != 0:
        raise SbxError("needs root on x86_64")

def _seccomp():
    """getrandom -> ENOSYS; x32 ABI (nr >= 0x40000000) and non-x86-64 arch -> kill."""
    ins = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E), (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0),
           (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000), (0x15, 0, 1, 318), (0x06, 0, 0, 0x00050000 | 38),
           (0x06, 0, 0, 0x7FFF0000)]
    buf = ctypes.create_string_buffer(b"".join(struct.pack("HBBI", *i) for i in ins))
    fprog = struct.pack("HxxxxxxQ", len(ins), ctypes.addressof(buf))
    _c(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
    _c(libc.prctl(22, 2, ctypes.c_char_p(fprog)), "seccomp")

def _setup_root(doc, root, run, srcs):
    _mount(None, "/", None, MS_REC | MS_PRIVATE)
    _mount("tmpfs", root, "tmpfs", 0, "mode=755")
    for n, i in enumerate(doc["inputs"]):
        src, tgt = srcs[n], root + i["at"]
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.isdir(src) and not os.path.islink(src):
            os.mkdir(tgt)
        elif os.path.isfile(src) and not os.path.islink(src):
            open(tgt, "w").close()
        else:
            raise SbxError("input %s is a symlink; only files and directories can be mounted" % i["at"])
        _bind_ro(src, tgt)
    for n, o in enumerate(doc["outputs"]):
        os.makedirs(root + o)
        _bind_ro(os.path.join(run, "out", str(n)), root + o, ro=False)
    os.makedirs(root + doc["cwd"], exist_ok=True)
    for d in ("proc", "dev", "tmp", "oldroot"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    _mount("proc", root + "/proc", "proc")
    for f in ("uptime", "stat", "loadavg"):
        _bind_ro("/dev/null", root + "/proc/" + f)
    _mount("tmpfs", root + "/proc/sys", "tmpfs", MS_RDONLY, "size=0")
    for f in ("null", "zero"):
        open(root + "/dev/" + f, "w").close()
        _bind_ro("/dev/" + f, root + "/dev/" + f, ro=False)
    os.mkdir(root + "/dev/shm")   # private and empty in every run: POSIX semaphores and shared memory (multiprocessing)
    _mount("tmpfs", root + "/dev/shm", "tmpfs", 0, "mode=1777")
    for f in ("urandom", "random"):   # a constant: entropy read from a device is a function of the sandbox version
        with open(root + "/dev/" + f, "wb") as g:
            for n in range(32768):
                g.write(hashlib.sha256(b"fnvnx-sandbox-urandom/1:%d" % n).digest())
        os.chmod(root + "/dev/" + f, 0o444)
    _mount("tmpfs", root + "/tmp", "tmpfs", 0, "mode=1777")
    _c(libc.syscall(155, root.encode(), (root + "/oldroot").encode()), "pivot_root")
    os.chdir("/")
    _c(libc.umount2(b"/oldroot", 2), "umount old root")
    os.rmdir("/oldroot")

def _pid1(doc, root, run, logs, errfd, srcs):
    try:
        _setup_root(doc, root, run, srcs)
        _c(libc.sethostname(b"fnvnx", 5), "sethostname")
        os.dup2(os.open("/dev/null", os.O_RDONLY), 0)
        os.dup2(logs[0], 1)
        os.dup2(logs[1], 2)
        os.closerange(3, errfd)
        os.closerange(errfd + 1, 4096)
        os.chdir(doc["cwd"])
        os.umask(0o022)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _c(libc.personality(0x0040000), "personality")
        for cap in range(64):  # drop every capability: no remount-rw, no mount, no chroot
            if libc.prctl(24, cap, 0, 0, 0) != 0:
                break
        _seccomp()
        os.execve(doc["argv"][0], doc["argv"], doc["env"])
    except BaseException as e:
        os.write(errfd, ("%s: %s" % (type(e).__name__, e)).encode()[:1000])
    os._exit(127)

def launch(doc, run, root, srcs):
    """Fork A (unshares the namespaces) -> fork B (pid 1 of the new pid namespace, becomes the command)."""
    efd = os.pipe()
    logs = [os.open(os.path.join(run, n), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644) for n in ("stdout", "stderr")]
    pid = os.fork()
    if pid == 0:
        try:
            # NEWNS|NEWUTS|NEWIPC|NEWPID|NEWNET
            _c(libc.unshare(0x00020000 | 0x04000000 | 0x08000000 | 0x20000000 | 0x40000000), "unshare")
            b = os.fork()
            if b == 0:
                os.close(efd[0])
                _pid1(doc, root, run, logs, efd[1], srcs)
            _, st = os.waitpid(b, 0)
            os._exit(os.waitstatus_to_exitcode(st) % 256 if st & 0x7f == 0 else 128 + (st & 0x7f))
        except BaseException as e:
            os.write(efd[1], str(e).encode()[:1000])
            os._exit(126)
    os.close(efd[1])
    _, st = os.waitpid(pid, 0)
    err = os.read(efd[0], 2000).decode()
    for f in logs + [efd[0]]:
        os.close(f)
    if err:
        raise SbxError("sandbox setup/exec failed: " + err)
    return os.waitstatus_to_exitcode(st)

def placed(store, tree):
    """Directory (or file) of the store that holds tree TREE placed as hardlinks of the objects: made once per store and tree (atomic
    rename; a loser of the race drops its copy), then bind-mounted read-only into every stage that declares the tree. This is only where
    the input lives on the host: what the stage sees is the tree, as before."""
    d = os.path.join(store, "placed", tree)
    if not os.path.lexists(d):
        os.makedirs(os.path.dirname(d), exist_ok=True)
        tmp = "%s.tmp-%d-%s" % (d, os.getpid(), os.urandom(4).hex())
        cas.materialize(tree, store, tmp)
        try:
            os.rename(tmp, d)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True) if os.path.isdir(tmp) else os.unlink(tmp)
    return d

def execute(store, doc, key):
    run = os.path.join(store, "run", "%s.%d.%s" % (key, os.getpid(), os.urandom(4).hex()))
    root = os.path.join(run, "root")
    try:
        srcs = [placed(store, i["tree"]) for i in doc["inputs"]]
        for n in range(len(doc["outputs"])):
            os.makedirs(os.path.join(run, "out", str(n)))
        os.makedirs(root)
        rc = launch(doc, run, root, srcs)
        return {"format": "fnvnx-stage-result/1", "key": key, "stage": doc["stage"], "rc": rc,
                "outputs": {o: cas.ingest(os.path.join(run, "out", str(n)), store) for n, o in enumerate(doc["outputs"])},
                "stdout": cas.ingest(os.path.join(run, "stdout"), store),
                "stderr": cas.ingest(os.path.join(run, "stderr"), store), "open_channels": list(OPEN_CHANNELS)}
    finally:
        shutil.rmtree(run, ignore_errors=True)

def _store_result(store, key, res):
    d = os.path.join(store, "keys", key)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp-%d-%s" % (os.getpid(), os.urandom(4).hex()))
    with open(tmp, "wb") as f:
        f.write(cas.canon(res))
    cas._publish(tmp, os.path.join(d, "result.json"))

def _load(store, key):
    p = os.path.join(store, "keys", key, "result.json")
    return json.load(open(p)) if os.path.exists(p) else None

def cmd_run(store, path, cold, result_path):
    doc = json.load(open(path))
    check_doc(doc)
    key = key_of(doc)
    old = _load(store, key)
    if old is not None and not cold:
        res = old
    else:
        res = execute(store, doc, key)
        if old is None:
            _store_result(store, key, res)
        else:
            bad = [k for k in ("rc", "stdout", "stderr", "outputs") if res[k] != old[k]]
            if bad:
                lines = []
                for o in doc["outputs"]:
                    if res["outputs"][o] != old["outputs"][o]:
                        lines += ["%s %s" % (os.path.join(o, r), k) for r, k in cas.diff(old["outputs"][o], res["outputs"][o], store)]
                print("NOT A FUNCTION OF THE KEY: differs in %s; %d differing paths" % (",".join(bad), len(lines)), file=sys.stderr)
                for ln in lines[:20]:
                    print("  " + ln, file=sys.stderr)
                return 3
            res = old
    text = cas.canon(res).decode()
    print(text)
    if result_path:
        open(result_path, "w").write(text + "\n")
    return res["rc"]

def cmd_stage(a):
    opts = {"--store": None, "--name": None, "--cwd": "/", "-o": None}
    inputs, outputs, env, i = [], [], {}, 0
    while i < len(a) and a[i] != "--":
        if a[i] in opts and i + 1 < len(a):
            opts[a[i]] = a[i + 1]
        elif a[i] == "--input" and i + 1 < len(a):
            at, _, host = a[i + 1].partition("=")
            inputs.append({"at": at, "tree": cas.ingest(host, opts["--store"])})
        elif a[i] == "--output" and i + 1 < len(a):
            outputs.append(a[i + 1])
        elif a[i] == "--env" and i + 1 < len(a):
            k, _, v = a[i + 1].partition("=")
            env[k] = v
        else:
            raise SbxError("bad option " + a[i])
        i += 2
    if opts["--store"] is None or opts["--name"] is None or opts["-o"] is None or a[i:i + 1] != ["--"]:
        raise SbxError("usage: stage --store S --name N [--input AT=HOST]... [--output P]... [--env K=V]... [--cwd D] -o F -- argv...")
    doc = {"format": "fnvnx-stage/1", "stage": opts["--name"], "argv": a[i + 1:], "cwd": opts["--cwd"], "env": env,
           "inputs": sorted(inputs, key=lambda x: x["at"]), "outputs": sorted(outputs)}
    check_doc(doc)
    open(opts["-o"], "wb").write(cas.canon(doc) + b"\n")

def main(argv):
    try:
        if argv[:1] == ["stage"]:
            return cmd_stage(argv[1:]) or 0
        if argv[:2] == ["run", "--store"] or argv[:2] == ["get", "--store"]:
            store, rest, flags = argv[2], [], {}
            it = iter(argv[3:])
            for x in it:
                if x == "--result":
                    flags["result"] = next(it)
                elif x == "--cold":
                    flags["cold"] = True
                else:
                    rest.append(x)
            if argv[0] == "run" and len(rest) == 1:
                return cmd_run(store, rest[0], flags.get("cold", False), flags.get("result"))
            if argv[0] == "get" and len(rest) == 3:
                res = _load(store, rest[0])
                if res is None or rest[1] not in res["outputs"]:
                    raise SbxError("no stored output %s for key %s" % (rest[1], rest[0]))
                cas.materialize(res["outputs"][rest[1]], store, rest[2])
                return 0
        print("usage: sandbox.py stage ... | run --store S stage.json [--cold] [--result R] | get --store S KEY PATH DEST",
              file=sys.stderr)
        return 1
    except (SbxError, cas.CasError, OSError, StopIteration, ValueError, KeyError) as e:
        print("sandbox: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
