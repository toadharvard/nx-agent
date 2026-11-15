#!/usr/bin/env python3
"""Gate "abi" (fnvnx-gate/1, VERDICT.md class 8): a reference and the definition it binds to have one calling contract (rule, universe and proof:
link/abi.py docstring RULE, UNIVERSE, PROOF).
    python3 -I gate_abi.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/abi.json
Universe: the rows abi.py defines (abi.check), over the C++ objects of every admitted product root with "target": "guest" and "cxx_origin": "native"
(objects from gate_build.ensure; the contract names are the plugin's `.fnvnx.sympair` of each object), plus the input facts abi.py's PROOF rests on, as items
of the same gate:
  odr    `odr` signatures (link/odr.py check): every COMDAT group present in two or more of the root's objects, copies identical (reported as `odr:<sig>`);
  crossing every symbol the contract pairs across the x86/native boundary (a `.fnvnx.sympair` row with an MSVC name and a kind other than `data`) has a row in `.fnvnx.crossing`
         (written for every external function of a unit: `symbol<TAB>term,...` or `symbol<TAB>-`) in some unit that declares it, and no term (ids of crossing_forbidden in
         toolchain/msvcmodel/DATAMODEL.json; an unknown id is red): item `crossing:<symbol>`.
  layout every `.fnvnx.layout` key of external linkage (8th column `external`, paired by the key AND the Itanium column, so two unnamed member classes that share one key are two entities; `internal`, `none` and `c` records are distinct entities per unit and never pair by
         name: C's cross-unit rule is 6.2.7 compatibility of declared types, not a tag name) (without a `nokey:` flag, whose key is not unique by construction) seen in two or more objects has one layout in all of
         them (size, alignment, fields, bases, flags; `layout:<key>`): the same aggregate name is the same type in all units, which is what lets abi pass a
         by-value aggregate by its name alone. (`c` records, whose tag has no linkage, are compared through the external symbols that declare them: item `ctype:<symbol>`, C11 6.2.7p2, link/ctypes_compat.py, from `.fnvnx.decltype`; a C unit without that section is red.) (link/odr.py proves the COMDAT-copy fact only; this item closes the layout fact abi.py's PROOF names.)
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
import gate_argorder  # noqa: E402
import gate_build  # noqa: E402
import l2sections  # noqa: E402
import objsections  # noqa: E402
import odr  # noqa: E402
import ctypes_compat  # noqa: E402
import recipe  # noqa: E402


DATAMODEL = os.path.join(HERE, "..", "toolchain", "msvcmodel", "DATAMODEL.json")


def crossing_terms():
    """the term ids of crossing_forbidden in the generated DATAMODEL table (the one place the predicate is stated; no type list in the gate)"""
    with open(DATAMODEL) as f:
        return {t["id"] for t in json.load(f)["crossing_forbidden"]["terms"]}


def parse_crossing(data, where):
    """`.fnvnx.crossing`: LF-terminated lines `symbol<TAB>term[,term...]` or `symbol<TAB>-` (no term holds), a symbol once -> {symbol: tuple of terms}"""
    text = data.decode("utf-8")
    if text and not text.endswith("\n"):
        raise ValueError("%s: no final LF" % where)
    out = {}
    for n, ln in enumerate(text.split("\n")[:-1], 1):
        f = ln.split("\t")
        if len(f) != 2 or not f[0] or not f[1]:
            raise ValueError("%s: line %d: not symbol<TAB>terms" % (where, n))
        if f[0] in out:
            raise ValueError("%s: line %d: %s twice" % (where, n, f[0]))
        out[f[0]] = () if f[1] == "-" else tuple(f[1].split(","))
    return out
def envid():
    """the declared environment of the result: env.json (the pinned toolchain the plugin's sections come from)"""
    return gate_build.file_sha(os.path.join(HERE, "env.json"))


def read_objects(objs):
    """OBJS: [(id, path)] -> (store {(id, 'sympair'): rows}, {layout key: {id: layout tuple}}, red items {id: reason}, {id: {symbol: declared C type}}, {id: {symbol: crossing terms}})."""
    store, layouts, red, decls = {}, {}, {}, {}
    cross = {}
    for oid, path in objs:
        try:
            elf = elfobj.read(path)
            sp = elf.section(".fnvnx.sympair")
            if sp is None:
                red[oid + ":sympair"] = "the object has no .fnvnx.sympair section"
                continue
            store[(oid, "sympair")] = l2sections.parse_sympair(sp, oid + ":.fnvnx.sympair")
            lays = objsections.parse_layout(objsections.section(elf, ".fnvnx.layout"), oid + ":.fnvnx.layout")
            dt = elf.section(".fnvnx.decltype")     # RULE DECLTYPE-COMPLETE (link/ctypes_compat.py): every GLOBAL/WEAK symbol of the object has exactly one line, `sym<TAB>type` or `sym<TAB>-reason` (reason from the plugin's closed set); the typed lines are compared by C11 6.2.7
            types, why = ctypes_compat.parse_decltype_lines(dt if dt is not None else b"", oid + ":.fnvnx.decltype")
            have = {n for n, bind, _t, _sh, _v, _sz, _o in elf.symbols() if n and bind in (1, 2)}     # STB_GLOBAL, STB_WEAK
            for n in sorted(have - set(types) - set(why)):
                red[oid + ":decltype:missing:" + n] = "global/weak symbol %s has no .fnvnx.decltype line" % n
            for n in sorted((set(types) | set(why)) - have):
                red[oid + ":decltype:extra:" + n] = ".fnvnx.decltype line for %s, which is not a global/weak symbol of the object" % n
            decls[oid] = types
            cr = elf.section(".fnvnx.crossing")
            if cr is not None:
                cross[oid] = parse_crossing(cr, oid + ":.fnvnx.crossing")
            for lay in lays:
                if lay.linkage == "external" and not any(f.startswith("nokey:") for f in lay.flags):
                    layouts.setdefault(lay.key + ("  [" + lay.itanium + "]" if lay.itanium else ""), {})[oid] = (lay.size, lay.align, lay.fields, lay.bases, lay.flags)
        except (ValueError, OSError) as e:
            red[oid + ":unreadable"] = str(e)[:300]
    return store, layouts, red, decls, cross


def asm_findings(objs):
    """OBJS: [(id, path)] of the assembly units of a root (recipe.ASM_EXT) -> {item: reason}: an assembled unit has no declared types (RULE DECLTYPE-COMPLETE: the plugin
    writes `.fnvnx.decltype` for C and C++ units only), so a unit of the assembly language that carries the section is red"""
    red = {}
    for oid, path in objs:
        try:
            has = elfobj.read(path).section(".fnvnx.decltype") is not None
        except (ValueError, OSError) as e:
            red[oid + ":unreadable"] = str(e)[:300]
            continue
        if has:
            red[oid + ":decltype:asm"] = "an assembly unit carries a .fnvnx.decltype section"
    return red


def judge_objects(objs, asm=(), universe=None, va_forwarders=frozenset()):
    """-> (universe, certified, ids, detail) of the objects OBJS [(id, path)] of one root."""
    store, layouts, red, decls, cross = read_objects(objs)
    uni, abi_red = abi.check(store)
    items = list(uni)
    reds = {k: v for k, v in abi_red.items()}
    asm_red = asm_findings(asm)
    items += [oid for oid, _ in asm]
    reds.update(asm_red)
    try:
        odr_uni, odr_red = odr.check([p for _, p in objs])
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
    cit, cred = ctypes_compat.check(decls)
    items += ["ctype:" + x for x in cit]
    reds.update({"ctype:" + x: r for x, r in cred.items()})
    # crossing_forbidden: a symbol the contract pairs across the x86 / native boundary (a `.fnvnx.sympair` row with an MSVC name that is not data: bridges,
    # import bodies, exports, dispatch entries) has no term of the generated predicate (DATAMODEL.json), and some unit that declares it carries a row for it
    try:
        known = crossing_terms()
    except (OSError, ValueError, KeyError) as e:
        known = None
        reds["crossing:table"] = "DATAMODEL.json crossing_forbidden unreadable: %s" % str(e)[:200]
        items.append("crossing:table")
    boundary = {}
    for (oid, kind), rows in store.items():
        if kind == "sympair":
            for sym, ms, k, df in rows:
                if ms and k not in ("data", "noemit"):     # noemit: the unit defines it, GCC wrote it nowhere (sympair_kinds.inc): no symbol of the object crosses
                    boundary.setdefault(sym, []).append(oid)
    for sym, oids in sorted(boundary.items()):
        if universe is not None and sym not in universe:
            continue        # not in the crossing universe (IAT bindings, called exports, dispatch descriptors): the symbol never crosses the boundary, so no term of it is judged
        items.append("crossing:" + sym)
        got = [cross[o][sym] for o in oids if sym in cross.get(o, {})]
        if known is None:
            continue
        if not got:
            reds["crossing:" + sym] = "no .fnvnx.crossing row in any unit that declares it (%s)" % ", ".join(sorted(oids))[:200]
            continue
        terms = {t for g in got for t in g}
        if terms == {"va_list"} and sym in va_forwarders:
            continue        # a va_list crossing is allowed iff it is a msvcrt_va forwarder (bridgefacts crt/msvcrt_va.tsv): it walks the x86 va_list as guest memory
        if "va_list" in terms and terms - known == set() and sym not in va_forwarders:
            items.append("abi:va:" + sym)
            reds["abi:va:" + sym] = "a va_list crossing symbol with no msvcrt_va forwarder record"
            continue
        if terms - known:
            reds["crossing:" + sym] = "unknown term %s (the table has %s)" % (sorted(terms - known), sorted(known))
        elif terms:
            reds["crossing:" + sym] = "crosses the x86 boundary with %s (S11/S12/S25/S26 of DATAMODEL.json)" % ",".join(sorted(terms))
    items += list(red)
    reds.update(red)
    return len(items), len(items) - len(reds), ["%s [%s]" % (i, r) for i, r in sorted(reds.items())], \
        "%d items: %d contract rows, %d odr signatures, %d shared layouts, %d unreadable" % (
            len(items), len(uni), len(odr_uni), sum(1 for i in items if i.startswith("layout:")), len(red))


def judge_root(repo, cache, root):
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    try:
        rec = recipe.load_recipe(repo, roots, root)
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    if res["status"] != "green":
        return 1, 0, [root + ":build"], "build %s" % res["status"]
    objs = [(root + "/" + o["unit"], o["object"], o["sha256"]) for o in res["objects"] if o["unit"].endswith(recipe.CC_EXT + recipe.CXX_EXT)]
    asm = [(root + "/" + o["unit"], o["object"], o["sha256"]) for o in res["objects"] if o["unit"].endswith(recipe.ASM_EXT)]
    # the item is the root's C and C++ objects as a set (the ODR and layout joins read all of them): keyed by their ids and sha256 and the tools that read them
    key = {"gate": "abi", "objects": [[i, h] for i, _, h in objs], "asm": [[i, h] for i, _, h in asm], "tools": gate_build.tool_digest(__file__, abi, odr, elfobj, l2sections, objsections), "env": envid()}
    v, _ = gate_build.memo(cache, "abi", key, lambda: list(judge_objects([(i, p) for i, p, _ in objs], [(i, p) for i, p, _ in asm])))
    return tuple(v)


def main():
    repo, tmp = ".", None
    cache = os.environ.get("GATE_CACHE")
    if not cache:
        cache = tmp = tempfile.mkdtemp(prefix="gate-abi-")
    try:
        u = c = 0
        ids, why = [], []
        roots = gate_argorder.native_guest(repo)
        for r in roots:
            ru, rc, rids, rd = judge_root(repo, cache, r)
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
    print("abi: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
