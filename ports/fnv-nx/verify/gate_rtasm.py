#!/usr/bin/env python3
"""Gate "rtasm" (fnvnx-gate/1, class A): the guest runtime's assembly objects order their memory accesses (CLAUDE.md, "memory ordering of runtime calls").
    python3 -I gate_rtasm.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/rtasm.json
RULE.  The guest runtime (newlib, libstdc++, libgcc, libnx) is built by msvcmodel with every plugin; its C and C++ bodies order their own accesses (gate order).
The remaining assembly is closed here: every memory access of an assembled object is certified by one stated class, every other access is red, with no allow-list.
  (a) frame: the access is in the routine's own frame and no escape of a frame address reaches it on any path from the entry (rule D1 of link/a64order.py,
      with the whole frame as one escaping range, ASM-006 for stale pointers); a call or a store of a frame address ends the class for what follows.
  (d) atomic: an ordered form (ldar, stlr, ldaxr, stlxr, LSE acquire-release forms: rule O), whose ordering is the documented one of the instruction (Arm ARM).
  (c) kernel call: `svc` and its argument moves are no memory accesses; they are no item, and a routine made of them only has none.
  Not yet closed (reported red, the input is not in the record): (b) runs before threads (the link record's call graph), and a jmp_buf parameter register (the declared prototype).
UNIVERSE.  Every object of every admitted product root with "target": "guest" that carries no plugin record section (no section whose name starts `.fnvnx`): every C or
C++ unit built by the game's compiler carries them (the plugins write them for every unit), an assembled one never does, so the complement is exactly the assembly
objects, one rule over the objects themselves (it needs no -g and no name list; it applies to the objects of an external's archive members as to recipe units).
Cross-check, a red item `<object>:class` where it fails: for a recipe unit, "has an assembly suffix (recipe.ASM_EXT: .s, .S)" and "carries no record section" agree.  Items: one per memory-access instruction (`<object>:<symbol>+0xOFF`), one per object that cannot be
parsed.  Red iff an item is uncertified or a root's build is not green.  No assembly object: universe 0, green.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "link"))
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import a64order  # noqa: E402
import elfobj  # noqa: E402
import gate_build  # noqa: E402
import recipe  # noqa: E402


def is_asm_object(path):
    """True iff the ELF object has no section named `.fnvnx*`: the assembly objects of the runtime (see UNIVERSE)"""
    return not any(n.startswith(".fnvnx") for n in elfobj.read(path).names)


def judge_objects(objs, suffix_asm=None):
    """OBJS [(id, path)] -> (universe, certified, uncertified item ids, detail).  SUFFIX_ASM {id: bool} the recipe's suffix fact where there is one: it must agree
    with is_asm_object; the objects that are not assembly are not in the universe."""
    items, bad, n_asm = 0, [], 0
    for oid, path in objs:
        asm = is_asm_object(path)
        if suffix_asm is not None and oid in suffix_asm and suffix_asm[oid] != asm:
            items += 1
            bad.append("%s:class [recipe suffix says %s, the object %s plugin record sections]" % (oid, "assembly" if suffix_asm[oid] else "C/C++", "has no" if asm else "carries"))
            continue
        if not asm:
            continue
        n_asm += 1
        try:
            rows = a64order.scan_asm(path)
        except (ValueError, OSError, KeyError, IndexError, TypeError) as e:
            items += 1
            bad.append("%s [scan failed: %s]" % (oid, str(e)[:120]))
            continue
        for item, kind in rows:
            items += 1
            if kind:
                bad.append("%s:%s [%s]" % (oid, item, kind))
    return items, items - len(bad), bad, "%d assembly objects of %d" % (n_asm, len(objs))


def judge_root(repo, cache, root):
    roots = recipe.load_admit(repo)
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    if res["status"] != "green":
        return 1, 0, [root + ":build"], "build %s" % res["status"]
    objs = [(root + "/" + o["unit"], o["object"]) for o in res["objects"]]
    return judge_objects(objs, {root + "/" + o["unit"]: o["unit"].endswith(recipe.ASM_EXT) for o in res["objects"]})


def main():
    repo, tmp = ".", None
    cache = os.environ.get("GATE_CACHE")
    if not cache:
        cache = tmp = tempfile.mkdtemp(prefix="gate-rtasm-")
    try:
        u = c = 0
        ids, why = [], []
        adm = recipe.load_admit(repo)
        roots = [r for r in gate_build.product_roots(repo) if adm[recipe.split_spec(r)[0]].get("target") == "guest"]
        for r in roots:
            ru, rc, rids, rd = judge_root(repo, cache, r)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why) or ("%d items of %d guest roots" % (u, len(roots)) if roots else "no guest root (universe 0)")
    res = {"format": "fnvnx-gate/1", "name": "rtasm", "status": "red" if why else "green", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "rtasm.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("rtasm: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
