#!/usr/bin/env python3
"""unitview: pure helpers of unitstage.py (no sandbox, no subprocess): deps.d parsing, kernel-style path resolution inside a CAS
tree, and the VIEW of a tree: a tree manifest with EVERY name of the real tree (all directories, files, symlinks) but real content
only for a chosen set of files; every other file is the placeholder object (content PLACEHOLDER). Views are built from the real
manifests alone: no file is copied, the store gains one placeholder object plus the manifests of the directories on the paths of
the chosen files (a directory without chosen files has one memoized all-placeholder manifest, the same for every unit)."""
import hashlib
import posixpath
import re

import cas

PLACEHOLDER = b"#error fnvnx: file outside the declared closure\n"
PH_SHA = hashlib.sha256(PLACEHOLDER).hexdigest()
# the compiler's own rendering of PLACEHOLDER's #error: `FILE:LINE:COL: error: #error fnvnx: ...` (a line of its own)
_PH_MSG = re.compile(r"^(/.+?):\d+:\d+: error: #error fnvnx: file outside the declared closure$", re.M)
MAX_HOPS = 40


class UnitError(Exception):
    pass


def parse_deps(text):
    """Sorted unique prerequisites of the single rule `x: ...` in text (GCC -MT x output). Paths are returned AS WRITTEN (no
    `..` folding: with symlinked directories that is not the path the kernel opened); resolve() gives the real file."""
    toks, cur, have, closed, i, n = [], [], False, False, 0, len(text)

    def flush():
        nonlocal cur, have
        toks.extend(["".join(cur)] * have)
        cur, have = [], False

    def add(s):
        nonlocal have
        if closed:
            raise UnitError("deps.d: second rule or text after the rule")
        cur.append(s)
        have = True
    while i < n:
        c = text[i]
        if c == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            k, nxt = j - i, text[j:j + 1]
            if nxt in (" ", "\t", "\n", "#"):
                if k // 2:
                    add("\\" * (k // 2))
                if k % 2 == 0:
                    i = j
                    continue
                if nxt == "\n":
                    flush()
                else:
                    add(nxt)
                i = j + 1
                continue
            add("\\" * k)
            i = j
        elif c == "$":
            if text[i + 1:i + 2] != "$":
                raise UnitError("deps.d: single $")
            add("$")
            i += 2
        elif c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            closed = True
            i += 1
        elif c == "#":
            raise UnitError("deps.d: unescaped #")
        else:
            add(c)
            i += 1
    flush()
    if not toks or toks[0] != "x:":
        raise UnitError("deps.d: not a rule for target x")
    for t in toks[1:]:
        if not t.startswith("/"):
            raise UnitError("deps.d: relative path %r" % t)
    return sorted(set(toks[1:]))


def placeholder_paths(stderr):
    """Paths named by the placeholder's own #error message in a compiler's stderr (as the compiler printed them)."""
    return sorted(set(_PH_MSG.findall(stderr)))


def _man(store, h, cache):
    if h not in cache:
        cache[h] = cas.manifest(h, store)
    return cache[h]


def resolve(store, root, rel, cache):
    """The regular file the kernel opens for tree-relative path REL (symlinks followed inside the tree, `..` taken on the real
    directory reached so far), as a tree-relative path without links. Leaving the tree, loops, a missing name: UnitError."""
    todo, cur, hops = rel.split("/"), [], 0
    while todo:
        p = todo.pop(0)
        if p in ("", "."):
            continue
        if p == "..":
            if not cur:
                raise UnitError("%s leaves the tree" % rel)
            cur.pop()
            continue
        h = root
        for c in cur:
            h = _man(store, h, cache)["entries"][c]["h"]
        e = _man(store, h, cache)["entries"].get(p)
        if e is None:
            raise UnitError("%s: no such file in the tree" % rel)
        if e["t"] == "l":
            hops += 1
            if hops > MAX_HOPS or e["to"].startswith("/"):
                raise UnitError("symlink %s loops or leaves the tree" % rel)
            todo = e["to"].split("/") + todo
        elif e["t"] == "d":
            cur.append(p)
        elif todo and any(t not in ("", ".") for t in todo):
            raise UnitError("%s: not a directory" % rel)
        else:
            return "/".join(cur + [p])
    raise UnitError("%s is a directory" % rel)


def view(store, root, keep, cache):
    """Hash of the view of tree ROOT: every name of the tree, real content for the files KEEP (tree-relative, no links) only."""
    trie = {}
    for p in keep:
        n = trie
        for c in p.split("/"):
            n = n.setdefault(c, {})
    ph = {"t": "f", "h": PH_SHA, "x": 0}
    cas._put(store, "obj", PH_SHA, PLACEHOLDER)

    def go(h, t):
        if not t and ("pure", h) in cache:
            return cache[("pure", h)]
        ents = {}
        for name, e in _man(store, h, cache)["entries"].items():
            if e["t"] == "d":
                ents[name] = {"t": "d", "h": go(e["h"], t.get(name, {}))}
            else:
                ents[name] = e if e["t"] == "l" or name in t else ph
        data = cas.canon({"format": cas.FORMAT, "entries": ents})
        out = hashlib.sha256(data).hexdigest()
        cas._put(store, "tree", out + ".json", data)
        if not t:
            cache[("pure", h)] = out
        return out
    return go(root, trie)
