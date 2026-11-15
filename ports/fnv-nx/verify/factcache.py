#!/usr/bin/env python3
"""factcache.py - facts of a gate, kept in GATE_CACHE under a key that covers everything the fact reads (rule: a gate whose inputs did not change
reads its cached facts and does not recompute). stdlib + importclosure; python3 -I.

    f = Facts(repo, cache, gate, entry)        ENTRY: repo-relative .py file of the gate (its import closure is the code part of every key)
    f.get(parts, compute, keep=None) -> the JSON value   PARTS: JSON-able content digests of the inputs of one fact (sha256 of object bytes, flags ...)
Key = sha256(closure_key(import closure of ENTRY, the interpreter, the env id), gate, PARTS). The fact is GATE_CACHE/facts/<gate>/<key>.json,
written atomically (a reader sees all of it or none). COMPUTE() -> JSON-able, a pure function of what the key names: it must give the same value
with an empty cache (a gate run without GATE_CACHE computes every fact and stores none). The value is returned as stored (JSON round trip),
the same on a hit and on a miss, so a gate never sees a difference between them. An import-closure finding (dynamic import, unresolved module ...)
is an error: the closure is then not a proof of what the key covers, and the gate is red for it (Facts raises ClosureError).
Not covered (stated, importclosure.py): programs started as subprocesses and files read as data are the PARTS of the fact."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import envcheck  # noqa: E402
import importclosure  # noqa: E402
import recipe  # noqa: E402

import hashlib  # noqa: E402


class ClosureError(Exception):
    pass


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def file_digest(path):
    return importclosure.file_sha(path)


class Facts:
    def __init__(self, repo, cache, gate, entry):
        self.cache, self.gate, self.hits, self.misses = cache, gate, 0, 0
        roots = recipe.load_admit(repo)
        dirs = [t for r in roots.values() for t in r.get("trees", [r["path"]])]   # a root with `trees` (gate_admit.py) is the union of them
        tr = {f for f in recipe.tracked(repo) if any(recipe.under(f, d) for d in dirs)}
        env = os.path.join(repo, recipe.ENV_JSON)
        with open(env) as f:
            eid = envcheck.env_id(json.load(f))
        files, finds = importclosure.import_closure(entry, repo, tr, isolated=True, pinned=importclosure.pinned_imports(env))
        if finds:
            raise ClosureError("%s import closure: " % entry + "; ".join("%s %s:%d %s" % f for f in finds[:6]))
        self.base = importclosure.closure_key(files, repo, eid)
        self.files = files

    def key(self, parts):
        return digest(self.base, self.gate, parts)

    def get(self, parts, compute, keep=None):
        k = self.key(parts)
        path = os.path.join(self.cache, "facts", self.gate, k[:2], k + ".json") if self.cache else None
        if path and os.path.isfile(path):
            try:
                with open(path) as f:
                    v = json.load(f)
                self.hits += 1
                return v
            except (OSError, ValueError):
                pass   # a torn or unreadable file is a miss: the fact is computed again and replaces it
        v = json.loads(json.dumps(compute()))
        self.misses += 1
        if path and (keep is None or keep(v)):   # KEEP(value): a value that is a finding about one run (a mismatch seen once) is not a fact
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(v, f, sort_keys=True)
            os.replace(tmp, path)
        return v

    def path(self, parts):
        k = self.key(parts)
        return os.path.join(self.cache, "facts", self.gate, k[:2], k + ".json") if self.cache else None

    def known(self, parts):
        """a fact of PARTS is stored (and readable)"""
        p = self.path(parts)
        try:
            return bool(p) and os.path.isfile(p) and json.load(open(p)) is not None
        except (OSError, ValueError):
            return False

    def put(self, parts, value):
        """store the fact VALUE of PARTS (a gate that proves it by a run of its own)"""
        self.get(parts, lambda: value)

    def stats(self):
        return "facts %d hit %d computed" % (self.hits, self.misses)
