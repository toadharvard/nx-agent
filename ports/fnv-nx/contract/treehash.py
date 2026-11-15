#!/usr/bin/env python3
"""treehash - the content hash of a file tree (format fnvnx-tree/1), the one home of the hashing rule; stdlib only, no store, no clock, no entropy.
    treehash.walk(path, visitor)        the one traversal; treehash.tree_hash(path, skip=()) is walk with the writing-nothing visitor, and
                                        verify/cas.py `ingest(path, STORE)` is walk with a visitor that also writes the store.
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


class Visitor:
    """What a caller adds to the one traversal. The default visitor writes nothing: tree_hash is walk(path) with it. verify/cas.py passes a
    visitor that also writes the store; the hash is the same by construction, because there is one traversal (walk)."""

    def file_hash(self, path, x):
        """-> the sha256 hex of the regular file PATH (X = its executable bit); a store-writing visitor ingests the bytes here"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()

    def tree(self, h, data):
        """a directory entry with hash H and canonical JSON DATA has been computed"""


def walk(path, visit=None, rel="", exclude=frozenset(), used=None, skip=frozenset()):
    """-> the tree entry {"t": "l"|"f"|"d", ...} of PATH; VISIT (a Visitor) sees every file and every directory. EXCLUDE: repo-relative paths (REL
    is the path of PATH below the walk's root) left out, each one recorded in the set USED; SKIP: entry names left out wherever they occur."""
    visit = visit or Visitor()
    m = os.lstat(path).st_mode
    if stat.S_ISLNK(m):
        return {"t": "l", "to": text(os.readlink(path), "link text: " + path)}
    if stat.S_ISREG(m):
        x = 1 if m & 0o111 else 0
        return {"t": "f", "h": visit.file_hash(path, x), "x": x}
    if stat.S_ISDIR(m):
        entries = {}
        for name in sorted(os.listdir(path)):
            r = rel + "/" + name if rel else name
            if r in exclude:
                if used is not None:
                    used.add(r)
                continue
            if name in skip:
                continue
            entries[text(name, "file name in " + path)] = walk(os.path.join(path, name), visit, r, exclude, used, skip)
        data = canon({"format": FORMAT, "entries": entries})
        h = hashlib.sha256(data).hexdigest()
        visit.tree(h, data)
        return {"t": "d", "h": h}
    raise CasError("unsupported file type (not file/dir/symlink): %s" % path)


def tree_hash(path, skip=()):
    """The hash ingest(path, STORE) returns, computed without a store. SKIP: entry names left out wherever they occur (the result then differs
    from ingest's by design)."""
    e = walk(path, skip=frozenset(skip))
    return e["h"] if e["t"] == "d" else hashlib.sha256(canon(e)).hexdigest()
