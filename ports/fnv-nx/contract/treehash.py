#!/usr/bin/env python3
"""treehash - the content hash of a file tree (format fnvnx-tree/1), the one home of the hashing rule; stdlib only, no store, no clock, no entropy.
    treehash.tree_hash(path, skip=())   the hash verify/cas.py `ingest(path, STORE)` returns for the same tree, computed without a store.
Rule: a regular file is hashed by its bytes (and its executable bit), a symlink by its UTF-8 target text, a directory by the canonical JSON
{"format": "fnvnx-tree/1", "entries": {name: entry}} of its entries in sorted name order; any other file type is an error. verify/cas.py imports
this module and re-exports FORMAT, CasError, canon and tree_hash (the same objects); the pin check of contract/imagerec.py reads it from here
(an image reader never trusts an unpinned facts tree), and so a pure stage tool can check a pin without importing a host reader."""
import hashlib
import json
import os
import stat

FORMAT = "fnvnx-tree/1"


class CasError(Exception):
    pass


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def text(s, what):
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        raise CasError("non-UTF-8 %s: %r" % (what, s))
    return s


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def entry(path, skip=frozenset()):
    """-> the tree entry {"t": "l"|"f"|"d", ...} of PATH (nothing is written)"""
    m = os.lstat(path).st_mode
    if stat.S_ISLNK(m):
        return {"t": "l", "to": text(os.readlink(path), "link text: " + path)}
    if stat.S_ISREG(m):
        return {"t": "f", "h": file_hash(path), "x": 1 if m & 0o111 else 0}
    if stat.S_ISDIR(m):
        entries = {}
        for name in sorted(os.listdir(path)):
            if name in skip:
                continue
            entries[text(name, "file name in " + path)] = entry(os.path.join(path, name), skip)
        return {"t": "d", "h": hashlib.sha256(canon({"format": FORMAT, "entries": entries})).hexdigest()}
    raise CasError("unsupported file type (not file/dir/symlink): %s" % path)


def tree_hash(path, skip=()):
    """The hash ingest(path, STORE) returns, computed without a store. SKIP: entry names left out wherever they occur (the result then differs
    from ingest's by design)."""
    e = entry(path, frozenset(skip))
    return e["h"] if e["t"] == "d" else hashlib.sha256(canon(e)).hexdigest()
