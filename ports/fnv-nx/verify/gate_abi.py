#!/usr/bin/env python3
"""Gate "abi" (fnvnx-gate/1, VERDICT.md class 8): a reference and the definition it binds to have one calling contract (rule, universe and proof:
link/abi.py docstring RULE, UNIVERSE, PROOF).
    python3 -I gate_abi.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/abi.json
Universe: the rows abi.py defines (abi.check), over the C++ objects of every admitted product root with "target": "guest" and "cxx_origin": "native"
(objects from gate_build.ensure; the contract names are the plugin's `.fnvnx.sympair` of each object), plus the input facts abi.py's PROOF rests on, as items
of the same gate:
  odr    `odr` signatures (link/odr.py check): every COMDAT group present in two or more of the root's objects, copies identical (reported as `odr:<sig>`);
  layout every `.fnvnx.layout` key (without a `nokey:` flag, whose key is not unique by construction) seen in two or more objects has one layout in all of
         them (size, alignment, fields, bases, flags; `layout:<key>`): the same aggregate name is the same type in all units, which is what lets abi pass a
         by-value aggregate by its name alone. (link/odr.py proves the COMDAT-copy fact only; this item closes the layout fact abi.py's PROOF names.)
Red iff an item is uncertified, the root's build is not green, an object has no `.fnvnx.sympair` section or cannot be parsed (red item naming the object). A C++
unit is an object of the root's recipe (recipe.CXX_EXT). No such root: universe 0, green."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "link"))
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import abi  # noqa: E402
import elfobj  # noqa: E402
import factcache  # noqa: E402
import gate_argorder  # noqa: E402
import gate_build  # noqa: E402
import l2sections  # noqa: E402
import objsections  # noqa: E402
import odr  # noqa: E402
import recipe  # noqa: E402


def read_objects(objs):
    """OBJS: [(id, path)] -> (store {(id, 'sympair'): rows}, {layout key: {id: layout tuple}}, red items {id: reason})."""
    store, layouts, red = {}, {}, {}
    for oid, path in objs:
        try:
            elf = elfobj.read(path)
            sp = elf.section(".fnvnx.sympair")
            if sp is None:
                red[oid + ":sympair"] = "the object has no .fnvnx.sympair section"
                continue
            store[(oid, "sympair")] = l2sections.parse_sympair(sp, oid + ":.fnvnx.sympair")
            for lay in objsections.parse_layout(objsections.section(elf, ".fnvnx.layout"), oid + ":.fnvnx.layout"):
                if not any(f.startswith("nokey:") for f in lay.flags):
                    layouts.setdefault(lay.key, {})[oid] = (lay.size, lay.align, lay.fields, lay.bases, lay.flags)
        except (ValueError, OSError) as e:
            red[oid + ":unreadable"] = str(e)[:300]
    return store, layouts, red


def _enc(x):
    """a COMDAT fingerprint (tuples, ints, str, bytes) as JSON: bytes are {"bytes": hex}, tuples are lists; equal fingerprints encode equal"""
    if isinstance(x, bytes):
        return {"bytes": x.hex()}
    if isinstance(x, (tuple, list)):
        return [_enc(e) for e in x]
    return x


def judge_objects(objs, facts=None):
    """-> (universe, certified, ids, detail) of the objects OBJS [(id, path)] of one root. FACTS (factcache.Facts): the COMDAT copies of an object
    (odr.copies) are a fact of the object's bytes; the checks across the objects always run, over the facts."""
    store, layouts, red = read_objects(objs)
    uni, abi_red = abi.check(store)
    items = list(uni)
    reds = {k: v for k, v in abi_red.items()}
    try:
        if facts is None:
            odr_uni, odr_red = odr.check([p for _, p in objs])
        else:
            odr_uni, odr_red = odr.check([p for _, p in objs], lambda p: facts.get(
                ["comdat", factcache.file_digest(p)], lambda: {k: _enc(v) for k, v in odr.copies(p).items()}))
    except (ValueError, OSError) as e:        # odr cannot parse an object: no verdict on the copies, the gate is red for it
        odr_uni, odr_red = ["unreadable"], {"unreadable": str(e)[:300]}
    items += ["odr:" + s for s in odr_uni]
    reds.update({"odr:" + s: r for s, r in odr_red.items()})
    for key, per in sorted(layouts.items()):
        if len(per) < 2:
            continue
        items.append("layout:" + key)
        vals = sorted(per.items())
        if any(v != vals[0][1] for _, v in vals[1:]):
            first = next(o for o, v in vals[1:] if v != vals[0][1])
            reds["layout:" + key] = "layout in %s differs from %s" % (first, vals[0][0])
    items += list(red)
    reds.update(red)
    return len(items), len(items) - len(reds), ["%s [%s]" % (i, r) for i, r in sorted(reds.items())], \
        "%d items: %d contract rows, %d odr signatures, %d shared layouts, %d unreadable" % (
            len(items), len(uni), len(odr_uni), sum(1 for i in items if i.startswith("layout:")), len(red))


def judge_root(repo, cache, root, facts=None):
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    try:
        rec = recipe.load_recipe(repo, roots, root)
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    if res["status"] != "green":
        return 1, 0, [root + ":build"], "build %s" % res["status"]
    objs = [(root + "/" + o["unit"], o["object"]) for o in res["objects"] if o["unit"].endswith(recipe.CXX_EXT)]
    return judge_objects(objs, facts)


def main():
    repo, tmp = ".", None
    cache = os.environ.get("GATE_CACHE")
    if not cache:
        cache = tmp = tempfile.mkdtemp(prefix="gate-abi-")
    try:
        u = c = 0
        ids, why = [], []
        roots = gate_argorder.native_guest(repo)
        try:
            facts = factcache.Facts(repo, os.environ.get("GATE_CACHE"), "abi", "ports/fnv-nx/verify/gate_abi.py")
        except factcache.ClosureError as e:
            roots, facts = [], None
            why.append(str(e)[:600])
            u, c, ids = 1, 0, ["gate:closure"]
        for r in roots:
            ru, rc, rids, rd = judge_root(repo, cache, r, facts)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why) or ("%d items of %d native guest roots" % (u, len(roots)) if roots else "no native guest root (universe 0)")
    res = {"format": "fnvnx-gate/1", "name": "abi", "status": "red" if why else "green", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "abi.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("abi: %s %d/%d %s%s" % (res["status"], c, u, detail[:200], " [%s]" % facts.stats() if facts else ""))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
