#!/usr/bin/env python3
"""recipe.store_export / store_import: a cache entry is copied under its key, byte-checked on import. Pure (no sandbox run): the record is stored as the sandbox stores it.
Run: python3 -I <this file>."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cas  # noqa: E402
import recipe  # noqa: E402
import sandbox  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


def red(fn):
    try:
        fn()
    except recipe.RecipeError as e:
        return str(e.args[0])
    return None


with tempfile.TemporaryDirectory() as td:
    st = os.path.join(td, "store")
    out = os.path.join(td, "out")
    os.makedirs(os.path.join(out, "bin"))
    open(os.path.join(out, "bin", "tool"), "w").write("#!x\n")
    os.chmod(os.path.join(out, "bin", "tool"), 0o755)
    open(os.path.join(out, "lib.a"), "w").write("lib\n")
    so, se = os.path.join(td, "so"), os.path.join(td, "se")
    open(so, "w").write("")
    open(se, "w").write("warn\n")
    K = "k" * 64
    rec = {"rc": 0, "stdout": cas.ingest(so, st), "stderr": cas.ingest(se, st), "outputs": {"/out": cas.ingest(out, st)}, "open_channels": []}
    sandbox._store_result(st, K, rec)
    cas._put(st, "stage-docs", K + ".json", b'{"stage": "toy"}\n')
    # another entry in the store that the export must not carry
    other = os.path.join(td, "other")
    open(other, "w").write("other\n")
    sandbox._store_result(st, "o" * 64, dict(rec, stdout=cas.ingest(other, st)))
    ex = os.path.join(td, "ex")
    n = recipe.store_export(st, K, ex)
    check("export copies the closure of the key and nothing else", sorted(os.listdir(os.path.join(ex, "keys"))) == [K] and n > 5, n)
    check("the other key's object is not in the export", not any(f.startswith(cas.ingest(other, st)[:8]) for _, _, fs in os.walk(os.path.join(ex, "tree")) for f in fs))
    st2 = os.path.join(td, "store2")
    recipe.store_import(ex, st2, K)
    check("import round trip: the record, the stage document and the output tree are in the new store", sandbox._load(st2, K) == rec
          and os.path.isfile(os.path.join(st2, "stage-docs", K[:2], K + ".json")) and cas.listing(rec["outputs"]["/out"], st2) == cas.listing(rec["outputs"]["/out"], st))
    check("importing twice is a no-op (equal bytes)", recipe.store_import(ex, st2, K) is not None)
    check("an export of an unknown key is red", red(lambda: recipe.store_export(st, "z" * 64, os.path.join(td, "e2"))) is not None)
    # tampering
    bad = os.path.join(td, "bad")
    shutil.copytree(ex, bad)
    obj = next(os.path.join(r, f) for r, _, fs in os.walk(os.path.join(bad, "obj")) for f in fs if not f.endswith(".x"))
    os.chmod(obj, 0o644)
    open(obj, "ab").write(b"x")
    check("an object whose bytes differ from its name is refused, the store unchanged", red(lambda: recipe.store_import(bad, os.path.join(td, "s3"), K)) is not None and not os.path.exists(os.path.join(td, "s3", "keys")))
    miss = os.path.join(td, "miss")
    shutil.copytree(ex, miss)
    os.chmod(obj.replace(bad, miss), 0o644)
    os.unlink(obj.replace(bad, miss))
    check("an export missing an object of the closure is refused", red(lambda: recipe.store_import(miss, os.path.join(td, "s4"), K)) is not None)
    more = os.path.join(td, "more")
    shutil.copytree(ex, more)
    os.makedirs(os.path.join(more, "keys", "q" * 64))
    check("an export that holds another key is refused", red(lambda: recipe.store_import(more, os.path.join(td, "s5"), K)) is not None)
    check("import of a key the directory lacks is refused", red(lambda: recipe.store_import(ex, os.path.join(td, "s6"), "y" * 64)) is not None)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
