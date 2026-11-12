#!/usr/bin/env python3
"""Content-addressed store and tree manifests (base of the hermetic build sandbox, rule R12).

Layout: STORE/obj/<aa>/<sha256>      file contents, mode 0444, mtime 0
        STORE/obj/<aa>/<sha256>.x    same content with exec bit, mode 0555 (separate object)
        STORE/obj/<aa>/<name>.r<n>   replica n >= 1 of an object: the same bytes and mode in a new inode, made when the inode before it
                                     is full (MAX_LINKS names): an inode serves at most MAX_LINKS hard links (EMLINK of ext4 is closed
                                     by rule), so materialize places any number of files of one content. Identity is unchanged.
        STORE/tree/<aa>/<hash>.json  canonical manifests (<aa> = first two hex digits)

Tree hash definition:
  canon(obj) = json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True) as ASCII bytes.
  entry of a regular file: {"t":"f","h":sha256(content),"x":0|1}   (x = any exec bit set)
  entry of a symlink:      {"t":"l","to":link text}                (never followed)
  entry of a directory:    {"t":"d","h":treehash}
  directory manifest:      canon({"format":"fnvnx-tree/1","entries":{name: entry}}); treehash = sha256(that)
  Names and link texts must be valid UTF-8 (else error). Other file types (fifo, socket, device) are errors.
  Hash of a top-level file/symlink input = sha256(canon(its entry)); of a directory = its treehash.
  Empty directories are kept. Nothing else (mtime, owner, mode other than exec) is part of the hash.
"""
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "contract"))
import treehash  # noqa: E402  (the one home of the tree-hash rule; stdlib only)

FORMAT = treehash.FORMAT
MAX_LINKS = 60000  # hard links one inode of the store serves (ext4: 65000); past it a replica is made

CasError = treehash.CasError
canon = treehash.canon
tree_hash = treehash.tree_hash

def stage_key(doc):
    return hashlib.sha256(canon(doc)).hexdigest()

def _text(s, what):
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        raise CasError("non-UTF-8 %s: %r" % (what, s))
    return s

def _publish(tmp, final):
    """Atomically give tmp its final name unless it exists already (never replaces)."""
    try:
        os.link(tmp, final)
    except FileExistsError:
        pass
    finally:
        os.unlink(tmp)

def _put(store, kind, name, data=None, mode=0o444):
    d = os.path.join(store, kind, name[:2])
    os.makedirs(d, exist_ok=True)
    final = os.path.join(d, name)
    if os.path.exists(final):
        return final
    tmp = os.path.join(d, ".tmp-%d-%s" % (os.getpid(), os.urandom(6).hex()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.utime(tmp, (0, 0))
    _publish(tmp, final)
    return final

def _ingest_file(path, store, x):
    st = os.lstat(path)
    if store is None:  # hash only: nothing is written
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    d = os.path.join(store, "obj")
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp-%d-%s" % (os.getpid(), os.urandom(6).hex()))
    h = hashlib.sha256()
    n = 0
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f, open(tmp, "wb") as out:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
                out.write(b)
                n += len(b)
            after = os.fstat(f.fileno())
        if n != st.st_size or after.st_size != n:
            raise CasError("file changed size while read: %s" % path)
        hx = h.hexdigest()
        os.chmod(tmp, 0o555 if x else 0o444)
        os.utime(tmp, (0, 0))
        final_dir = os.path.join(d, hx[:2])
        os.makedirs(final_dir, exist_ok=True)
        final = os.path.join(final_dir, hx + (".x" if x else ""))
        if os.path.exists(final):
            os.unlink(tmp)
        else:
            _publish(tmp, final)
        return hx
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise

def _entry(path, rel, store, excl, used, skip=()):
    st = os.lstat(path)
    m = st.st_mode
    if stat.S_ISLNK(m):
        return {"t": "l", "to": _text(os.readlink(path), "link text: " + path)}
    if stat.S_ISREG(m):
        x = 1 if m & 0o111 else 0
        return {"t": "f", "h": _ingest_file(path, store, x), "x": x}
    if stat.S_ISDIR(m):
        entries = {}
        for name in sorted(os.listdir(path)):
            r = rel + "/" + name if rel else name
            if r in excl:
                used.add(r)
                continue
            if name in skip:
                continue
            entries[_text(name, "file name in " + path)] = _entry(os.path.join(path, name), r, store, excl, used, skip)
        data = canon({"format": FORMAT, "entries": entries})
        h = hashlib.sha256(data).hexdigest()
        if store is not None:
            _put(store, "tree", h + ".json", data)
        return {"t": "d", "h": h}
    raise CasError("unsupported file type (not file/dir/symlink): %s" % path)

def ingest(path, store, exclude=()):
    excl = set(exclude)
    used = set()
    e = _entry(path, "", store, excl, used)
    if excl - used:
        raise CasError("exclude paths not found: %s" % sorted(excl - used))
    if e["t"] == "d":
        return e["h"]
    data = canon(e)
    _put(store, "tree", hashlib.sha256(data).hexdigest() + ".json", data)
    return hashlib.sha256(data).hexdigest()

def manifest(h, store):
    p = os.path.join(store, "tree", h[:2], h + ".json")
    try:
        with open(p, "rb") as f:
            return json.loads(f.read())
    except FileNotFoundError:
        raise CasError("no such tree in store: " + h)

def _is_dir_manifest(m):
    return m.get("format") == FORMAT

def _walk(m, store, rel, out):
    for name, e in m["entries"].items():
        r = rel + "/" + name if rel else name
        out.append((r, e))
        if e["t"] == "d":
            _walk(manifest(e["h"], store), store, r, out)

def listing(h, store):
    m = manifest(h, store)
    if not _is_dir_manifest(m):
        return [("", m)]
    out = []
    _walk(m, store, "", out)
    return sorted(out, key=lambda t: t[0])

def _obj(store, e):
    return os.path.join(store, "obj", e["h"][:2], e["h"] + (".x" if e["x"] else ""))

def _replica(src, n):
    """Path of replica N of object SRC (N 0 is the object); a missing replica is a copy (a reflink where the filesystem has it)."""
    r = src if n == 0 else "%s.r%d" % (src, n)
    if n and not os.path.exists(r):
        tmp = os.path.join(os.path.dirname(src), ".tmp-%d-%s" % (os.getpid(), os.urandom(6).hex()))
        with open(src, "rb") as a, open(tmp, "wb") as b:
            try:
                fcntl.ioctl(b.fileno(), 0x40049409, a.fileno())  # FICLONE
            except OSError:
                shutil.copyfileobj(a, b)
        os.chmod(tmp, os.stat(src).st_mode & 0o777)
        os.utime(tmp, (0, 0))
        _publish(tmp, r)
    return r

def _link_obj(src, dest):
    """Hard-link the content of object SRC at DEST from the first replica whose inode has room (MAX_LINKS) and does not refuse."""
    n = 0
    while True:
        r = _replica(src, n)
        if os.stat(r).st_nlink < MAX_LINKS:
            try:
                return os.link(r, dest)
            except FileExistsError:
                raise
            except OSError as ex:
                if ex.errno != errno.EMLINK:
                    raise
        n += 1

def _place(e, store, dest, dest_top):
    if e["t"] == "f":
        src = _obj(store, e)
        try:
            _link_obj(src, dest)
        except OSError as ex:
            if ex.errno == errno.EXDEV:
                raise CasError("cannot hardlink across filesystems: store %s -> dest %s" % (store, dest_top))
            raise
    elif e["t"] == "l":
        os.symlink(e["to"], dest)
        os.utime(dest, (0, 0), follow_symlinks=False)
    else:
        os.mkdir(dest)
        os.chmod(dest, 0o755)
        for name, c in manifest(e["h"], store)["entries"].items():
            _place(c, store, os.path.join(dest, name), dest_top)
        os.utime(dest, (0, 0))

def materialize(h, store, dest):
    if os.path.lexists(dest):
        raise CasError("dest exists: " + dest)
    m = manifest(h, store)
    _place(m if not _is_dir_manifest(m) else {"t": "d", "h": h}, store, dest, dest)

def diff(a, b, store):
    la, lb = dict(listing(a, store)), dict(listing(b, store))
    out = []
    for r in sorted(set(la) | set(lb)):
        if r not in lb:
            out.append((r, "removed"))
        elif r not in la:
            out.append((r, "added"))
        elif la[r]["t"] != lb[r]["t"]:
            out.append((r, "changed"))
        elif la[r]["t"] != "d" and la[r] != lb[r]:
            out.append((r, "changed"))
    return out

def _sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def verify(store):
    bad = []
    od = os.path.join(store, "obj")
    for sub in sorted(os.listdir(od)) if os.path.isdir(od) else []:
        for n in sorted(os.listdir(os.path.join(od, sub))):
            if n.startswith(".tmp-"):
                continue
            p = os.path.join(od, sub, n)
            if _sha_file(p) != n.split(".")[0] or n[:2] != sub:
                bad.append("obj/%s/%s: content does not match name" % (sub, n))
    td = os.path.join(store, "tree")
    for sub in sorted(os.listdir(td)) if os.path.isdir(td) else []:
        for n in sorted(os.listdir(os.path.join(td, sub))):
            if n.startswith(".tmp-"):
                continue
            p = os.path.join(td, sub, n)
            with open(p, "rb") as f:
                data = f.read()
            if hashlib.sha256(data).hexdigest() + ".json" != n:
                bad.append("tree/%s/%s: content does not match name" % (sub, n))
                continue
            m = json.loads(data)
            if canon(m) != data:
                bad.append("tree/%s/%s: not canonical" % (sub, n))
                continue
            ents = m["entries"].values() if _is_dir_manifest(m) else [m]
            for e in ents:
                ref = _obj(store, e) if e["t"] == "f" else (
                    os.path.join(store, "tree", e["h"][:2], e["h"] + ".json") if e["t"] == "d" else None)
                if ref and not os.path.exists(ref):
                    bad.append("tree/%s/%s: missing reference %s" % (sub, n, os.path.relpath(ref, store)))
    return bad

def main(argv):
    usage = ("usage: cas.py ingest STORE PATH [--exclude REL]... | materialize STORE HASH DEST | "
             "ls STORE HASH | diff STORE A B | verify STORE")
    try:
        cmd, args = argv[0], argv[1:]
        if cmd == "ingest":
            excl, pos, i = [], [], 0
            while i < len(args):
                if args[i] == "--exclude" and i + 1 < len(args):
                    excl.append(args[i + 1])
                    i += 2
                else:
                    pos.append(args[i])
                    i += 1
            if len(pos) != 2:
                raise IndexError
            print(ingest(pos[1], pos[0], excl))
        elif cmd == "materialize" and len(args) == 3:
            materialize(args[1], args[0], args[2])
        elif cmd == "ls" and len(args) == 2:
            for r, e in listing(args[1], args[0]):
                print("%s\t%s\t%s" % (r, e["t"], e["to"] if e["t"] == "l" else e["h"]))
        elif cmd == "diff" and len(args) == 3:
            for r, k in diff(args[1], args[2], args[0]):
                print("%s\t%s" % (k, r))
        elif cmd == "verify" and len(args) == 1:
            bad = verify(args[0])
            for b in bad:
                print("CORRUPT " + b)
            return 2 if bad else 0
        else:
            raise IndexError
    except IndexError:
        print(usage, file=sys.stderr)
        return 1
    except (CasError, OSError) as e:
        print("cas: error: %s" % e, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
