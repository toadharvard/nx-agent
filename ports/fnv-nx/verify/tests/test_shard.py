#!/usr/bin/env python3
"""Tests for recipe.shard_of / shard_partition / shard_spec / store_merge on synthetic data (no compiler, no sandbox): plain script, exit 0 on pass."""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402
import recipe  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("%s %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILED.append(name)
        print("   ", detail)


def mkstore(d, objs=(), keys=None):
    for data in objs:
        h = hashlib.sha256(data).hexdigest()
        cas._put(d, "obj", h, data)
    for k, res in (keys or {}).items():
        os.makedirs("%s/keys/%s" % (d, k))
        open("%s/keys/%s/result.json" % (d, k), "wb").write(cas.canon(res))


def main():
    base = tempfile.mkdtemp(prefix="shard-test-")
    try:
        keys = {"u%d.c" % i: hashlib.sha256(b"k%d" % i).hexdigest() for i in range(40)}
        for n in range(1, recipe.MAX_SHARDS + 1):
            parts = recipe.shard_partition(keys, n)
            flat = [u for p in parts for u in p]
            check("shards N=%d: disjoint and covering" % n, len(parts) == n and len(flat) == len(set(flat)) and set(flat) == set(keys))
        check("shard_of is int(key,16) mod N", recipe.shard_of("ff", 8) == 255 % 8 and recipe.shard_of("10", 3) == 16 % 3)
        check("shard_spec parses i/N", recipe.shard_spec("2/5") == (2, 5))
        for bad in ("0/0", "3/3", "1/9", "x", "-1/2", "01/2"):
            try:
                recipe.shard_spec(bad)
                got = None
            except recipe.RecipeError:
                got = "red"
            check("shard_spec %s is refused" % bad, got == "red")
        a, b, m = base + "/a", base + "/b", base + "/m"
        kk = {"a" * 64: {"rc": 0, "x": 1}, "b" * 64: {"rc": 0, "x": 2}}
        mkstore(a, [b"one", b"two"], {"a" * 64: kk["a" * 64]})
        mkstore(b, [b"two", b"three"], {"a" * 64: kk["a" * 64], "b" * 64: kk["b" * 64]})
        n1 = recipe.store_merge(a, m)
        n2 = recipe.store_merge(b, m)
        check("merge imports by content: a then b (shared object and key not copied twice)", n1["obj"] == 2 and n1["keys"] == 1 and n2["obj"] == 1 and n2["keys"] == 1, (n1, n2))
        check("merged store holds the union", len(list(recipe._store_files(m, "obj"))) == 3 and sorted(os.listdir(m + "/keys")) == sorted(kk))
        check("merge is idempotent", sum(recipe.store_merge(a, m).values()) == 0 and sum(recipe.store_merge(b, m).values()) == 0)
        f = base + "/f"
        mkstore(f, [b"four"], {"a" * 64: {"rc": 1, "x": 1}})
        before = sorted(os.listdir(m + "/keys")), len(list(recipe._store_files(m, "obj")))
        try:
            recipe.store_merge(f, m)
            got = None
        except recipe.RecipeError as e:
            got = " ".join(e.errors)
        check("forged key record: red `store-merge: key <k> differs`, target unchanged", got == "store-merge: key %s differs" % ("a" * 64)
              and (sorted(os.listdir(m + "/keys")), len(list(recipe._store_files(m, "obj")))) == before, got)
        c = base + "/c"
        mkstore(c, [b"five"])
        p = next(p for _, p in recipe._store_files(c, "obj"))
        os.chmod(p, 0o644)
        open(p, "wb").write(b"corrupt")
        try:
            recipe.store_merge(c, m)
            got = None
        except recipe.RecipeError as e:
            got = " ".join(e.errors)
        check("an object whose content is not its name is red", got and "does not match its name" in got, got)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("FAILED: %s" % FAILED if FAILED else "all passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
