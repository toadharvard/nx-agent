"""The link set of a guest root: the ONE accessor of "what can define a symbol in the link this root enters" (gates imports and linkstrict read it; nothing is listed).
A root enters the link of the product ELF as RECIPE.md "Link: the guest ELF" derives it, and the definers of that link are:
  1. its MEMBERS: the root itself and every link candidate of its target (recipe.link_candidates: admitted product roots with a recipe and no link, in the recipe each has for
     the target; never an image, a test root or a root of another target; not the root's `uses`);
  2. the pinned RUNTIME: the start and end files and the `-l` libraries that the guest driver's own link command names (gate_guestlink.derive: the `-###` probe of the
     compiler external that guest_compiler.json pins, minus the objects the onedef and entry rules exclude), read from that external's checkout through recipe.exttree;
  3. the names the link SCRIPT assigns (link/guest.json's script: undefset.script_defined) and the image entry the link assigns by --defsym (recipe.IMAGE_ENTRY, when a guest
     image declares `link.entry`).
Names the pinned ld synthesises itself for a link (e.g. _GLOBAL_OFFSET_TABLE_) come from the reference link stage (recipe.Job.ld_synthesised), which exists only while an
image is linked; they are not in this set and stay undefined for a root that links no image.
Result form: {symbol: {source: [(bind, type)]}} with bind in GLOBAL/WEAK/UNIQUE and type as recipe.STYPE names; source is a member spec, `rt:<file>` or `script`."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_build  # noqa: E402
import gate_guestlink  # noqa: E402
import recipe  # noqa: E402
import relocs  # noqa: E402
import undefset  # noqa: E402
from ldcheck import syms_any  # noqa: E402

BIND = {1: "GLOBAL", 2: "WEAK", 10: "UNIQUE"}


def link_members(root, admit, repo):
    """ROOT itself and every link candidate of an image of its target (recipe.link_candidates). -> sorted specs."""
    spec = recipe.split_spec(root)[0]
    return sorted({root} | set(recipe.link_candidates(admit, spec, recipe.entry_of(admit, root).get("target"), repo)))


def object_defs(data):
    """{symbol: [(bind, type)]} of the defined global, weak and unique symbols of the ELF relocatable bytes DATA."""
    out = {}
    for n, bind, _, shndx, typ in syms_any(data, 2):
        if shndx != 0 and bind in BIND and typ not in (3, 4) and n:
            out.setdefault(n, []).append((BIND[bind], recipe.STYPE.get(typ, str(typ))))
    return out


def merge(into, source, defs):
    for n, ents in defs.items():
        into.setdefault(n, {}).setdefault(source, []).extend(ents)


def runtime_defs(repo, cache):
    """The definers that are not roots: the pinned runtime's files and the script's names -> {symbol: {source: [(bind, type)]}}. ValueError/RecipeError/BuildRed as the derivation."""
    derived, _gone, pin = gate_guestlink.derive(repo, cache)
    host = recipe.exttree(pin["external"], cache)
    out = {}
    for p in derived["startfiles"] + derived["endfiles"]:
        merge(out, "rt:" + p, object_defs(open(os.path.join(host, p), "rb").read()))
    for n in derived["names"]:
        ar = next((os.path.join(host, d, "lib%s.a" % n) for d in derived["dirs"] if os.path.isfile(os.path.join(host, d, "lib%s.a" % n))), None)
        if ar is None:
            raise ValueError("library lib%s.a is in none of the derived directories %s of the pinned runtime" % (n, derived["dirs"]))
        for _m, _, body in relocs.ar_members(ar):
            merge(out, "rt:lib%s.a" % n, object_defs(body))
    prof = recipe.load_guest_link(repo)
    script = open(os.path.join(repo, recipe.TREE, prof["script"])).read()
    admit = recipe.load_admit(repo)
    entry = any(recipe.entry_of(admit, r).get("target") == "guest" and (recipe.load_recipe(repo, admit, r).get("link") or {}).get("entry")
                for r in gate_build.buildable_roots(repo))
    merge(out, "script", {n: [("GLOBAL", "NOTYPE")] for n in undefset.script_defined(script) | ({recipe.IMAGE_ENTRY} if entry else set())})
    return out


def definers(root, admit, repo, cache):
    """{symbol: {source: [(bind, type)]}} over the whole link that ROOT enters: its members' objects (gate_build.ensure, objects.json `defined`), the runtime and the script.
    A guest root only; a host root has no pinned guest runtime, so its members alone. A red member build is ValueError."""
    out = {}
    for m in link_members(root, admit, repo):
        res = gate_build.ensure(m, cache, repo)[0]
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (m, res["failure"]["stage"]))
        for d in res["defined"]:
            out.setdefault(d["symbol"], {}).setdefault(m, []).append((d["bind"], d["type"]))
    if recipe.entry_of(admit, root).get("target") == "guest":
        for n, srcs in runtime_defs(repo, cache).items():
            for s, ents in srcs.items():
                out.setdefault(n, {}).setdefault(s, []).extend(ents)
    return out
