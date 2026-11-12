#!/usr/bin/env python3
"""Gate "datamodel" (fnvnx-gate/1, VERDICT.md class A: data model): every unit of a guest product root is compiled by the pinned game
compiler in the exe's data model.
    python3 -I gate_datamodel.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/datamodel.json
Universe (through gate_build.quantify): the admitted product roots with "target": "guest" (a host root has none); per root one item
`<root>:compiler` plus one item `<root>/<unit>` per unit of the build. No guest root: universe 0, green.
Certificate, read from the build's own outputs (objects.json of the root, the CC stage documents of its units in GATE_CACHE/store/stage-docs,
found by the keys objects.json lists, the document whose "stage" is `cc:<unit>`); nothing is decided by a name in a file:
  compiler item: the build is green, the root's recipe compiler is the external of verify/guest_compiler.json (recipe.guest_errors: name,
    repo, ref, commit, tree_sha256, at, driver paths), and every CC document mounts at the pin's `at` the pin's tree_sha256 and starts
    with the pin's driver (the object was built by that tree, not by a name).
  unit item, all of: a CC stage document exists for the unit; under the recipe's flags the compiler's predefined macros (recipe.predefined, `-dM -E`)
    say __SIZE_TYPE__ is unsigned int, __PTRDIFF_TYPE__ is int and __CHAR_UNSIGNED__ is undefined (the exe's data model, plain char signed); its argv holds the whole semantic set of the pin ("flags"."semantics", the one source
    recipe.SEMANTICS_FLAGS reads) for the unit's language as ONE ordered run, exactly as recipe.semantics_for derives it from the pinned driver
    (the set minus the flags the driver's own diagnostic says do not apply to that language; the gate re-runs that probe itself), and no other
    -f/-m token except a stage's own (-fplugin, -frandom-seed, -ffile-prefix-map, -fpreprocessed) and the per-root class recipe.PER_ROOT; and
    EVERY C and C++ unit of a guest root, any origin, holds every plugin of recipe.PLUGIN_SOURCES that its front end can load, each as the declared
    input /plug/<name> and the argv flag -fplugin=/plug/<name>/<name>.so, and no plugin it cannot load. Loadable = needs no symbol that only the
    other front end defines (recipe.plugin_languages: nm -D of cc1, cc1plus and the .so, re-derived by the gate from the built plugins: a static
    fact of the ELF files; a plugin using C++-front-end symbols has no C instance). A missing loadable plugin is red; no name list, no exception list.
    The documents are the stage keys, so the object is the one these flags and inputs built.
Relies on the owner-accepted item "the compiler is correct": the pinned compiler under -mabi=ilp32 has the exe's data model."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

ILP32 = "-mabi=ilp32"
# The exe's Visual C++ 2008 data model as the compiler states it (predefined macros under the unit's flags): size_t unsigned int, ptrdiff_t int, plain char signed
MODEL = {"__SIZE_TYPE__": "unsigned int", "__PTRDIFF_TYPE__": "int"}


def cc_docs(store, res):
    """{unit: CC stage document} of a build result: the stage documents of its stage keys whose stage is `cc:<unit>`."""
    out = {}
    for k in res.get("stage_keys", []):
        p = os.path.join(store, "stage-docs", k[:2], k + ".json")
        if os.path.isfile(p):
            d = json.load(open(p))
            if str(d.get("stage", "")).startswith("cc:"):
                out[d["stage"][3:]] = d
    return out


STAGE_F = ("-fplugin", "-frandom-seed=", "-ffile-prefix-map=", "-fpreprocessed", "-ffunction-sections", "-fdata-sections")   # -f tokens a stage itself adds or a root may carry


def unit_why(u, doc, sem, pin, plugs=None, macros=None):
    """Reasons a unit's CC stage document does not certify it ([] = certified). SEM: the semantic flags the pinned driver applies to this unit's
    language (recipe.semantics_for), or None to skip that clause. PLUGS: the plugins the unit's front end can load (recipe.plugin_languages), in order. MACROS: the compiler's predefined macros for the
    unit's language under the recipe's flags (recipe.predefined), or None to skip that clause."""
    if doc is None:
        return ["no CC stage document"]
    argv, ins = doc.get("argv", []), {i["at"]: i["tree"] for i in doc.get("inputs", [])}
    why = [] if ILP32 in argv else ["argv lacks " + ILP32]
    if sem is not None:
        n = len(sem)
        if not any(argv[i:i + n] == sem for i in range(len(argv) - n + 1)):   # the recipe's run: the whole set, in order, contiguous
            why += ["argv lacks " + f for f in sem if f not in argv] or ["argv does not hold the semantic flags as one ordered run"]
        # and no other semantic flag: every -f/-m token is in the set, a stage's own, or the per-root class
        why += ["argv holds " + t for t in argv[1:] if t.startswith(("-f", "-m", "-O")) and t not in sem and not t.startswith(STAGE_F)
                and not recipe.PER_ROOT.fullmatch(t)]
    if u.endswith(recipe.CC_EXT + recipe.CXX_EXT):   # EVERY plugin the unit's front end can load, on every guest C and C++ unit, any origin, no exception list
        for n in recipe.PLUGIN_SOURCES if plugs is None else plugs:
            if "/plug/" + n not in ins:
                why.append("no declared input /plug/" + n)
            if "-fplugin=/plug/%s/%s.so" % (n, n) not in argv:
                why.append("argv lacks -fplugin for " + n)
        why += ["argv loads %s, which this front end cannot load" % n for n in ([] if plugs is None else recipe.PLUGIN_SOURCES) if n not in plugs and "/plug/" + n in ins]
    if macros is not None:
        why += ["%s is %r, not %r" % (k, macros.get(k), v) for k, v in MODEL.items() if macros.get(k) != v]
        why += ["plain char is unsigned (__CHAR_UNSIGNED__ is defined)"] if "__CHAR_UNSIGNED__" in macros else []
    ex = pin["external"]
    if ins.get(ex["at"]) != ex["tree_sha256"]:
        why.append("compiler tree at %s is not the pin's" % ex["at"])
    if not argv or not argv[0].startswith(ex["at"] + "/"):
        why.append("driver is not inside the pinned compiler")
    return why


def judge_root(root, repo, cache):
    """-> (universe, certified, ids, detail) of one root (gate_build.quantify convention)."""
    roots = recipe.load_admit(repo)
    if roots[root].get("target") != "guest":
        return 0, 0, [], "host root"
    pin, files = recipe.load_pin(repo), recipe.tracked(repo)
    rec = recipe.load_recipe(repo, roots, root)
    item = root + ":compiler"
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        us = [root + "/" + u for u in recipe.units_of(rec, root, files)] if isinstance(rec, dict) else []
        return len(us) + 1, 0, [item] + us, "invalid recipe: %s" % "; ".join(e.errors)[:300]
    units = [root + "/" + u for u in res.get("units", [])]
    if res["status"] != "green":
        return len(units) + 1, 0, [item] + units, "build %s" % res["status"]
    docs = cc_docs(os.path.join(cache, "store"), res)
    bad, why = [], []
    g = recipe.guest_errors(rec, root, roots, pin)
    if g:
        bad.append(item)
        why.append("; ".join(g)[:300])
    cc, cxx = recipe.host_compilers(rec, cache)
    sem = {k: recipe.semantics_for(cxx if k == "cxx" else cc, k, origin=roots[root].get("cxx_origin")) for k in ("c", "cxx", "asm")}   # the pinned driver's own answer per language
    mac = {k: recipe.predefined(cxx if k == "cxx" else cc, k, sem[k]) for k in ("c", "cxx", "asm")}   # the compiler's own statement of the data model
    sos = {p["name"]: p["path"] for p in res.get("plugins", [])}
    langs = recipe.plugin_languages(cc, cxx, sos) if sos else {"c": [], "cxx": []}   # the plugins each front end loads: a fact of the ELF files
    for u in res["units"]:
        w = unit_why(u, docs.get(u), sem["c" if u.endswith(recipe.CC_EXT) else "cxx" if u.endswith(recipe.CXX_EXT) else "asm"], pin,
                     langs["c" if u.endswith(recipe.CC_EXT) else "cxx"], mac["c" if u.endswith(recipe.CC_EXT) else "cxx" if u.endswith(recipe.CXX_EXT) else "asm"])
        if w or g:
            bad.append(root + "/" + u)
            if w:
                why.append("%s: %s" % (u, ", ".join(w)))
    n = len(units) + 1
    return n, n - len(bad), bad, "; ".join(why)[:300] or "%d units" % len(units)


def main():
    import shutil
    import tempfile
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-datamodel-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        u, c, ids, detail = gate_build.quantify(repo, lambda r: judge_root(r, repo, cache))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    if u == 0 and detail != gate_build.NO_ROOT:
        detail = "no guest root (universe 0)"
    res = {"format": "fnvnx-gate/1", "name": "datamodel", "status": "red" if c != u else "green", "universe": u, "certified": c,
           "uncertified": ids if c != u else [], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "datamodel.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("datamodel: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if c != u else 0


if __name__ == "__main__":
    sys.exit(main())
