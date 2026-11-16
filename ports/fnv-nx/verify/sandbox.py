#!/usr/bin/env python3
"""Hermetic build sandbox (rule R12 BUILD-PURE): a stage sees ONLY its declared inputs, so its outputs are a
function of the stage key. Needs root, Linux x86-64. Stage document "fnvnx-stage/1":
  {"format","stage","argv":[abs tool, args], "cwd","env":{COMPLETE env},"inputs":[{"at","tree"}],"outputs":[dirs]}
  optional "class": "build" | "verification" (part of the key; verdict.py provenance requires it); optional "tool"
  (the declared tool kind toolflags.py checks; part of the key).
key = cas.stage_key(document + {"sandbox": {sandbox.py sha256, cas.py sha256}}). Results: STORE/keys/<key>/result.json.
Sandbox: new mount/net/uts/pid/ipc namespaces; tmpfs root; inputs hardlinked from the CAS and bound read-only (a VIEW input, a tree that holds
unitview.PLACEHOLDER, is its all-placeholder base placed once, bound read-only, with each real file's object bound read-only over its
placeholder path: O(real files) mounts per stage, never a tree per view);
outputs fresh rw dirs; fresh /proc (time/stat/sys masked), /dev = null+zero, tmpfs /tmp; hostname fnvnx; no interface up;
stdin /dev/null; umask 022; ASLR off; no core files; all capabilities dropped; seccomp: getrandom is answered by the supervisor with the stage's entropy stream, SHA-256 in counter mode over the stage key (the stream of /dev/urandom) (SECCOMP_RET_USER_NOTIF; x32 ABI and foreign arch killed).
Optional stage fields: "ro_root" (every write outside the outputs fails); "links" [{"at","to"}] (symlinks of the root layout, in the key: a link cannot be an
input mount); "scratch" [paths] (a writable tmpfs per path, nothing survives, not an output, in the key); "unkeyed" {"env": {K: V}, "timeout": seconds}
(NOT in the key: the job count and the time limit change how long a stage takes, not what it computes; a stage past its timeout is killed as a whole
process group and the run raises SbxError naming the timeout). tests/test_sandbox.py case 13.
Measured (tests/test_sandbox.py case 9), NOT closed, listed in result.json "open_channels":
  wall_clock           clock_gettime/time/statx show real time (vDSO). PR_SET_TSC=SIGSEGV + seccomp on the clock syscalls
                       was tried: dynamic date/python/gcc/make crash at startup (SIGSEGV), coarse clocks stay readable. Dropped.
  entropy              getrandom, /dev/urandom and /dev/random all return one sha256 stream that is a function of the stage key (stream(): so os.urandom,
                       OpenSSL and Python's multiprocessing return the same bytes when the stage runs again), but AT_RANDOM (the kernel
                       puts 16 fresh bytes in the auxiliary vector at exec; a program uses them for its stack canary and pointer guard) and RDRAND
                       still give fresh bytes.
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
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import unitview  # noqa: E402

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
    """The stage key: the document and the sandbox's own files, WITHOUT `unkeyed` (the job count and the timeout change the time a stage takes, not what it
    computes: a stage that needs a different value for either has the same key and the same record)."""
    return cas.stage_key(dict({k: v for k, v in doc.items() if k != "unkeyed"}, sandbox=sandbox_id()))

def check_doc(doc):
    ok = (isinstance(doc, dict) and doc.get("format") == "fnvnx-stage/1" and isinstance(doc.get("stage"), str)
          and isinstance(doc.get("argv"), list) and doc["argv"] and all(isinstance(a, str) for a in doc["argv"])
          and doc["argv"][0].startswith("/") and isinstance(doc.get("cwd"), str) and doc["cwd"].startswith("/")
          and isinstance(doc.get("env"), dict) and isinstance(doc.get("inputs"), list)
          and isinstance(doc.get("outputs"), list) and set(doc) - {"class", "tool", "ro_root", "links", "scratch", "unkeyed"} == {"format", "stage", "argv", "cwd", "env", "inputs", "outputs"}
          and doc.get("ro_root", False) in (True, False)
          and doc.get("class", "build") in ("build", "verification"))
    if not ok:
        raise SbxError("malformed stage document")
    for i in doc["inputs"]:
        if not (isinstance(i, dict) and set(i) == {"at", "tree"} and isinstance(i["at"], str)):
            raise SbxError("malformed input entry")
    links, scratch, unk = doc.get("links", []), doc.get("scratch", []), doc.get("unkeyed", {})
    if not (isinstance(links, list) and all(isinstance(l, dict) and set(l) == {"at", "to"} and isinstance(l["at"], str) and isinstance(l["to"], str) and l["to"] for l in links)
            and isinstance(scratch, list) and all(isinstance(p, str) for p in scratch)
            and isinstance(unk, dict) and set(unk) <= {"env", "timeout"}
            and all(isinstance(k, str) and isinstance(v, str) for k, v in unk.get("env", {}).items())
            and (unk.get("timeout") is None or (isinstance(unk["timeout"], int) and not isinstance(unk["timeout"], bool) and unk["timeout"] > 0))):
        raise SbxError("malformed links, scratch or unkeyed")
    paths = [i["at"] for i in doc["inputs"]] + list(doc["outputs"]) + [l["at"] for l in links] + list(scratch)
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

SYS_GETRANDOM, SYS_SECCOMP = 318, 317
SECCOMP_RET_USER_NOTIF = 0x7FC00000


def stream(seed, off, n):
    """Bytes [OFF, OFF+N) of the entropy stream of a stage, a function of its stage key SEED (a hex string): block i is sha256(b"fnvnx-sandbox-entropy/2:" + SEED + b":%d" % i), 32 bytes (SHA-256 in
    counter mode). /dev/urandom and /dev/random are its first MiB, and getrandom serves the same stream (per calling thread, from offset 0), so every way a program reads entropy returns the same
    bytes whenever the stage is run again, and different stages (different keys) do not share a stream."""
    first, last = off // 32, (off + n + 31) // 32
    pre = b"fnvnx-sandbox-entropy/2:" + seed.encode() + b":"
    return b"".join(hashlib.sha256(pre + b"%d" % i).digest() for i in range(first, last))[off - first * 32:off - first * 32 + n]


def _seccomp():
    """getrandom -> a user notification answered by the supervisor (fork A, _supervise); x32 ABI (nr >= 0x40000000) and non-x86-64 arch -> kill.
    -> the listener fd (the caller hands it to the supervisor)."""
    ins = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E), (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0),
           (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000), (0x15, 0, 1, SYS_GETRANDOM), (0x06, 0, 0, SECCOMP_RET_USER_NOTIF),
           (0x06, 0, 0, 0x7FFF0000)]
    buf = ctypes.create_string_buffer(b"".join(struct.pack("HBBI", *i) for i in ins))
    fprog = struct.pack("HxxxxxxQ", len(ins), ctypes.addressof(buf))
    _c(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
    fd = libc.syscall(SYS_SECCOMP, 1, 8, ctypes.c_char_p(fprog))   # SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_NEW_LISTENER
    if fd < 0:
        raise SbxError("seccomp: %s" % os.strerror(ctypes.get_errno()))
    return fd


class _Iov(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("len", ctypes.c_size_t)]


def _supervise(listener, child, seed):
    """Answer the getrandom notifications of the sandboxed command until it exits: each caller thread gets the next bytes of the stage's entropy stream (stream(seed)), written into its
    buffer with process_vm_writev; the result is the byte count. -> the wait status of CHILD (fork B)."""
    import fcntl
    import select
    offs = {}
    poll = select.poll()
    poll.register(listener, select.POLLIN)
    status = None
    while True:
        w, st = os.waitpid(child, os.WNOHANG)
        if w:
            status = st
            break
        if not poll.poll(50):
            continue
        req = bytearray(80)   # struct seccomp_notif: id u64, pid u32, flags u32, seccomp_data{nr s32, arch u32, ip u64, args[6] u64}
        try:
            fcntl.ioctl(listener, 0xC0502100, req, True)   # SECCOMP_IOCTL_NOTIF_RECV
        except OSError:
            continue    # the target died before it was received, or every holder of the filter is gone
        nid, pid, _fl, _nr, _arch, _ip = struct.unpack_from("<QIIiIQ", req, 0)
        buf, n = struct.unpack_from("<QQ", req, 32)
        val, err = 0, 0
        try:
            fcntl.ioctl(listener, 0x40082102, struct.pack("<Q", nid))   # SECCOMP_IOCTL_NOTIF_ID_VALID
            n = min(n, 1 << 25)
            if n:
                data = stream(seed, offs.get(pid, 0), n)
                local, remote = _Iov(ctypes.cast(ctypes.c_char_p(data), ctypes.c_void_p), n), _Iov(buf, n)
                got = libc.process_vm_writev(pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
                if got < 0:
                    err = -ctypes.get_errno()
                else:
                    val = got
                    offs[pid] = offs.get(pid, 0) + got
            fcntl.ioctl(listener, 0x40082102, struct.pack("<Q", nid))
        except OSError:
            continue    # the target is gone: nothing to answer
        try:
            fcntl.ioctl(listener, 0xC0182101, struct.pack("<QqiI", nid, val, err, 0))   # SECCOMP_IOCTL_NOTIF_SEND
        except OSError:
            pass
    return status

def _setup_root(doc, root, run, srcs):
    ro_flag = MS_RDONLY if doc.get("ro_root") else 0   # a stage with "ro_root": every write outside its outputs fails (EROFS)
    _mount(None, "/", None, MS_REC | MS_PRIVATE)
    _mount("tmpfs", root, "tmpfs", 0, "mode=755")
    for n, i in enumerate(doc["inputs"]):
        (src, binds), tgt = srcs[n], root + i["at"]
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.isdir(src) and not os.path.islink(src):
            os.mkdir(tgt)
        elif os.path.isfile(src) and not os.path.islink(src):
            open(tgt, "w").close()
        else:
            raise SbxError("input %s is a symlink; only files and directories can be mounted" % i["at"])
        _bind_ro(src, tgt)
        for rel, obj in binds:   # a view: the real file over its placeholder (the base is read-only, the object is mounted read-only)
            _bind_ro(obj, tgt + "/" + rel)
    for n, o in enumerate(doc["outputs"]):
        os.makedirs(root + o)
        _bind_ro(os.path.join(run, "out", str(n)), root + o, ro=False)
    for l in doc.get("links", []):   # the symlinks of the declared machine's root layout (a link cannot be an input mount): part of the key
        os.makedirs(os.path.dirname(root + l["at"]), exist_ok=True)
        os.symlink(l["to"], root + l["at"])
    for p in doc.get("scratch", []):   # a writable scratch directory: a tmpfs that nothing survives (not an output); a build that works out of tree
        os.makedirs(root + p)
        _mount("tmpfs", root + p, "tmpfs", 0, "mode=755")
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
    os.mkdir(root + "/dev/shm")   # private and empty in every run: POSIX semaphores and shared memory (multiprocessing). Writable even in a ro_root stage: it is not an output
    _mount("tmpfs", root + "/dev/shm", "tmpfs", 0, "mode=1777")   # (nothing survives the run, so the "writes only /out" rule is unchanged), and a tool that forks workers needs its semaphores
    for f in ("urandom", "random"):   # entropy read from a device is a function of the stage key
        with open(root + "/dev/" + f, "wb") as g:
            g.write(stream(key_of(doc), 0, 1 << 20))
        os.chmod(root + "/dev/" + f, 0o444)
    _mount("tmpfs", root + "/tmp", "tmpfs", ro_flag, "mode=1777")
    _c(libc.syscall(155, root.encode(), (root + "/oldroot").encode()), "pivot_root")
    os.chdir("/")
    _c(libc.umount2(b"/oldroot", 2), "umount old root")
    os.rmdir("/oldroot")
    if doc.get("ro_root"):   # the root tmpfs (cwd, the parents of the input mounts) read-only; the outputs, bound rw, stay the only writable paths
        _mount(None, "/", None, MS_BIND | MS_REMOUNT | MS_RDONLY)

def _pid1(doc, root, run, logs, errfd, srcs, sock):
    try:
        _setup_root(doc, root, run, srcs)
        _c(libc.sethostname(b"fnvnx", 5), "sethostname")
        os.dup2(os.open("/dev/null", os.O_RDONLY), 0)
        os.dup2(logs[0], 1)
        os.dup2(logs[1], 2)
        keep = sorted({errfd, sock})
        prev = 2
        for k in keep:
            os.closerange(prev + 1, k)
            prev = k
        os.closerange(prev + 1, 4096)
        os.chdir(doc["cwd"])
        os.umask(0o022)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _c(libc.personality(0x0040000), "personality")
        for cap in range(64):  # drop every capability: no remount-rw, no mount, no chroot
            if libc.prctl(24, cap, 0, 0, 0) != 0:
                break
        lfd = _seccomp()
        ss = socket.socket(fileno=sock)
        socket.send_fds(ss, [b"L"], [lfd])   # the listener goes to the supervisor, fork A
        ss.close()
        os.close(lfd)
        os.execve(doc["argv"][0], doc["argv"], dict(doc["env"], **doc.get("unkeyed", {}).get("env", {})))
    except BaseException as e:
        os.write(errfd, ("%s: %s" % (type(e).__name__, e)).encode()[:1000])
    os._exit(127)

def launch(doc, run, root, srcs):
    """Fork A (unshares the namespaces) -> fork B (pid 1 of the new pid namespace, becomes the command)."""
    seed = key_of(doc)   # the entropy of the stage is a function of its key
    efd = os.pipe()
    logs = [os.open(os.path.join(run, n), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644) for n in ("stdout", "stderr")]
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()   # one process group: a timeout kills the whole stage
            # The forked child holds a copy of every descriptor that a sibling thread of the parent has open at this instant, among them files open for
            # writing (a tool being copied into a store, an object being ingested). Such a copy makes an exec of that file by another stage fail with
            # ETXTBSY for as long as the child lives, which is the whole of the mount setup. The child keeps only what it uses: stdin/out/err, the error
            # pipe and the two logs.
            prev = 2
            for k in sorted({efd[0], efd[1], *logs}):
                os.closerange(prev + 1, k)
                prev = max(prev, k)
            os.closerange(prev + 1, 1 << 16)
            # NEWNS|NEWUTS|NEWIPC|NEWPID|NEWNET
            _c(libc.unshare(0x00020000 | 0x04000000 | 0x08000000 | 0x20000000 | 0x40000000), "unshare")
            sk = socket.socketpair()
            b = os.fork()
            if b == 0:
                os.close(efd[0])
                sk[0].close()
                _pid1(doc, root, run, logs, efd[1], srcs, sk[1].detach())
            sk[1].close()
            st = None
            try:   # B sends the seccomp listener before it execs; if it died first, the socket is at EOF
                _, fds, _fl, _ad = socket.recv_fds(sk[0], 1, 1)
            except OSError:
                fds = []
            if fds:
                st = _supervise(fds[0], b, seed)
                os.close(fds[0])
            if st is None:
                _, st = os.waitpid(b, 0)
            os._exit(os.waitstatus_to_exitcode(st) % 256 if st & 0x7f == 0 else 128 + (st & 0x7f))
        except BaseException as e:
            os.write(efd[1], str(e).encode()[:1000])
            os._exit(126)
    os.close(efd[1])
    timeout = doc.get("unkeyed", {}).get("timeout")
    if timeout:
        deadline, st = time.monotonic() + timeout, None
        while st is None:
            w, st0, ru = os.wait4(pid, os.WNOHANG)
            if w:
                st = st0
            elif time.monotonic() > deadline:
                os.killpg(pid, 9)
                os.wait4(pid, 0)
                for f in logs + [efd[0]]:
                    os.close(f)
                raise SbxError("stage %s: timeout after %d s (killed)" % (doc["stage"], timeout))
            else:
                time.sleep(0.05)
    else:
        _, st, ru = os.wait4(pid, 0)     # the usage of fork A includes the descendants it waited for (fork B, the command and what it reaped): the stage's CPU
    err = os.read(efd[0], 2000).decode()
    for f in logs + [efd[0]]:
        os.close(f)
    if err:
        raise SbxError("sandbox setup/exec failed: " + err)
    return os.waitstatus_to_exitcode(st), round(ru.ru_utime + ru.ru_stime, 3)

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

MAX_LINKS = 60000   # below the 65000 links of an inode on ext4: a placeholder inode serves at most this many names
_SPLIT = {}   # {(store, directory hash): (base hash, [(path, entry)] of the non-placeholder files)}, a pure function of the manifest
_HAS_PH = {}


def _has_ph(store, h):
    """True iff the tree of directory manifest H holds the placeholder object somewhere (it is a VIEW)."""
    k = (store, h)
    if k not in _HAS_PH:
        _HAS_PH[k] = any(e["t"] == "f" and e["h"] == unitview.PH_SHA or e["t"] == "d" and _has_ph(store, e["h"])
                         for e in cas.manifest(h, store)["entries"].values())
    return _HAS_PH[k]


def _split(store, h):
    """(base, reals) of view H: BASE = the hash of the manifest with every file entry replaced by the placeholder entry (every directory,
    symlink and name stays: a function of the real tree's structure alone, the same for every unit); REALS = [(tree-relative path, file
    entry)] of the files that are not the placeholder. The base manifests are written to the store."""
    k = (store, h)
    if k in _SPLIT:
        return _SPLIT[k]
    ents, reals = {}, []
    ph = {"t": "f", "h": unitview.PH_SHA, "x": 0}
    for name, e in cas.manifest(h, store)["entries"].items():
        if e["t"] == "d":
            b, rs = _split(store, e["h"])
            ents[name] = {"t": "d", "h": b}
            reals += [(name + "/" + p, x) for p, x in rs]
        elif e["t"] == "f":
            ents[name] = ph
            if e["h"] != unitview.PH_SHA:
                reals.append((name, e))
        else:
            ents[name] = e
    data = cas.canon({"format": cas.FORMAT, "entries": ents})
    b = hashlib.sha256(data).hexdigest()
    cas._put(store, "tree", b + ".json", data)
    _SPLIT[k] = (b, reals)
    return _SPLIT[k]


def _place_base(store, e, dest, state):
    if e["t"] == "f":   # a placeholder: its own small file, a new inode after every MAX_LINKS names
        if state["n"] % MAX_LINKS == 0:
            with open(dest, "wb") as f:
                f.write(unitview.PLACEHOLDER)
            os.chmod(dest, 0o444)
            os.utime(dest, (0, 0))
            state["first"] = dest
        else:
            os.link(state["first"], dest)
        state["n"] += 1
    elif e["t"] == "l":
        os.symlink(e["to"], dest)
        os.utime(dest, (0, 0), follow_symlinks=False)
    else:
        os.mkdir(dest)
        for name, c in cas.manifest(e["h"], store)["entries"].items():
            _place_base(store, c, os.path.join(dest, name), state)
        os.chmod(dest, 0o755)
        os.utime(dest, (0, 0))


def placed_base(store, base):
    """The directory of the store that holds the all-placeholder tree BASE (made once per store and base, atomic rename)."""
    d = os.path.join(store, "placed-base", base)
    if not os.path.lexists(d):
        os.makedirs(os.path.dirname(d), exist_ok=True)
        tmp = "%s.tmp-%d-%s" % (d, os.getpid(), os.urandom(4).hex())
        _place_base(store, {"t": "d", "h": base}, tmp, {"n": 0, "first": None})
        try:
            os.rename(tmp, d)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return d


def placed_input(store, tree):
    """(host path, [(path in the tree, host object file)]) of an input tree. A view (a directory tree that holds the placeholder): the
    placed base and the objects of its real files, which the sandbox binds over the placeholder paths. Any other tree: placed(), no binds."""
    m = cas.manifest(tree, store)
    if cas._is_dir_manifest(m) and _has_ph(store, tree):
        base, reals = _split(store, tree)
        return placed_base(store, base), [(p, cas._obj(store, e)) for p, e in sorted(reals)]
    return placed(store, tree), []

TIME_FORMAT = "fnvnx-stage-time/1"


def time_path(store, key):
    """the timing sidecar of a stage key: store/<key>.time. NOT part of the key, NOT part of the record (keys/<key>/result.json) and NOT compared by store-merge"""
    return os.path.join(store, key + ".time")


def compared(doc):
    """The record fields two executions of one key must agree on. A `-ftime-report` stage's stderr is a measurement, not a function of the key (the TIMEREPORT
    of the @time variant): it is kept but not compared."""
    return ("rc", "stdout", "outputs") if "-ftime-report" in doc.get("argv", []) else ("rc", "stdout", "stderr", "outputs")


def write_time(store, key, t):
    """publish the sidecar {wall_s, cpu_s} of one execution (a miss); a hit never calls this. Atomic (private temp name, rename)."""
    p = time_path(store, key)
    tmp = "%s.tmp-%d-%s" % (p, os.getpid(), os.urandom(4).hex())
    with open(tmp, "w") as f:
        json.dump({"format": TIME_FORMAT, "wall_s": t["wall_s"], "cpu_s": t["cpu_s"]}, f, sort_keys=True)
    os.replace(tmp, p)


def read_time(store, key):
    """the sidecar of KEY or None (a stage that was only ever a hit in this store, or a store merged from one that did not time it)"""
    try:
        with open(time_path(store, key)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("format") == TIME_FORMAT else None


def execute(store, doc, key):
    return execute_timed(store, doc, key)[0]


def execute_timed(store, doc, key):
    """-> (result record, {wall_s, cpu_s}): the record is a function of the key; the timing (wall seconds of the launch, child user+sys seconds from wait4
    rusage) is a measurement and goes to the sidecar (write_time), never into the record"""
    run = os.path.join(store, "run", "%s.%d.%s" % (key, os.getpid(), os.urandom(4).hex()))
    root = os.path.join(run, "root")
    try:
        srcs = [placed_input(store, i["tree"]) for i in doc["inputs"]]
        for n in range(len(doc["outputs"])):
            os.makedirs(os.path.join(run, "out", str(n)))
        os.makedirs(root)
        t0 = time.monotonic()
        rc, cpu = launch(doc, run, root, srcs)
        wall = round(time.monotonic() - t0, 3)
        return {"format": "fnvnx-stage-result/1", "key": key, "stage": doc["stage"], "rc": rc,
                "outputs": {o: cas.ingest(os.path.join(run, "out", str(n)), store) for n, o in enumerate(doc["outputs"])},
                "stdout": cas.ingest(os.path.join(run, "stdout"), store),
                "stderr": cas.ingest(os.path.join(run, "stderr"), store), "open_channels": list(OPEN_CHANNELS)}, {"wall_s": wall, "cpu_s": cpu}
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
        res, tm = execute_timed(store, doc, key)
        write_time(store, key, tm)
        if old is None:
            _store_result(store, key, res)
        else:
            bad = [k for k in compared(doc) if res[k] != old[k]]
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
