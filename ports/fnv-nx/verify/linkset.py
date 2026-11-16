"""The link set of a guest root: the ONE accessor of "what can define a symbol in the link this root enters" (gates imports and linkstrict read it; nothing is listed).
A root enters the link of the product ELF as RECIPE.md "Link: the guest ELF" derives it, and the definers of that link are:
  1. its MEMBERS: the root itself and every link candidate of its target (recipe.link_candidates: admitted product roots with a recipe and no link, in the recipe each has for
     the target; never an image, a test root or a root of another target; not the root's `uses`);
  2. the pinned RUNTIME: the start and end files and the `-l` libraries that the guest driver's own link command names (gate_guestlink.derive: the `-###` probe of the
     compiler external that guest_compiler.json pins, minus the objects the onedef and entry rules exclude), read from that external's checkout through recipe.exttree;
  3. the names the link SCRIPT assigns (link/guest.json's script: undefset.script_defined) and the image entry the link assigns by --defsym (recipe.IMAGE_ENTRY, when a guest
     image declares `link.entry`).
  4. the names the pinned ld synthesises itself for a link (e.g. _GLOBAL_OFFSET_TABLE_): a function of ld, the target's link form and the guest script, not of any image. They are
     derived from ld's own answer for each member's own objects (recipe.synth_set: the symbols the linked output defines, GLOBAL or LOCAL, that no input object or script assignment defines;
     one cached stage per member and kind, `ldsynth:<root>:<kind>`), source `ld:<member>:<kind>`. The kind is `pie` for a guest root, and for a host root the link form its recipe declares (`shared`, `exe` when none).
A HOST root's definers are its own objects (member specs), the synthesised names, and the defined .dynsym exports of every file of its recipe's `link.libs.system`, each read from the pinned
deb of env.json that owns it (envcheck.system_lib_errors; a file owned by no pinned deb is ValueError `host-lib-unpinned`). It has no guest runtime and no guest script.
Result form: {symbol: {source: [(bind, type)]}} with bind in GLOBAL/WEAK/UNIQUE and type as recipe.STYPE names; source is a member spec, `rt:<file>` or `script`."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import envcheck  # noqa: E402
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
    A guest root adds the runtime and the script; a host root adds its pinned system libraries (host_system_defs). Both add the synthesised names. A red member build is ValueError."""
    out = {}
    for m in link_members(root, admit, repo):
        res = gate_build.ensure(m, cache, repo)[0]
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (m, res["failure"]["stage"]))
        for d in res["defined"]:
            out.setdefault(d["symbol"], {}).setdefault(m, []).append((d["bind"], d["type"]))
    guest = recipe.entry_of(admit, root).get("target") == "guest"
    extra = runtime_defs(repo, cache) if guest else host_system_defs(root, admit, repo)
    for n, srcs in extra.items():
        for s, ents in srcs.items():
            out.setdefault(n, {}).setdefault(s, []).extend(ents)
    for n, srcs in synth_defs(root, admit, repo, cache).items():
        for s, ents in srcs.items():
            out.setdefault(n, {}).setdefault(s, []).extend(ents)
    return out


def synth_defs(root, admit, repo, cache):
    """{name: {"ld:<member>:<kind>": [(GLOBAL, NOTYPE)]}}: the names the pinned ld synthesises for the links ROOT enters, from ld's own answer for each member's OWN objects
    (recipe.synth_names: GOT, TLS and PLT relocations the objects really carry make ld define exactly the names they need). Guest: kind pie with the guest script; host: the
    root's own link form (its recipe `link.kind`), `shared` and `exe` when the root declares none. One cached stage per member and kind."""
    guest = recipe.entry_of(admit, root).get("target") == "guest"
    prof = recipe.load_guest_link(repo) if guest else None
    out = {}
    for m in link_members(root, admit, repo):
        res = gate_build.ensure(m, cache, repo)[0]
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (m, res["failure"]["stage"]))
        lk = recipe.load_recipe(repo, admit, m).get("link")
        kinds = ["pie"] if guest else ([lk["kind"]] if isinstance(lk, dict) and lk.get("kind") else ["shared", "exe"])
        objs = [(o["unit"], o["object"]) for o in res["objects"]]
        for kind, names in recipe.synth_names(repo, m, cache, kinds, recipe.TREE + "/" + prof["script"] if prof else None, objs).items():
            merge(out, "ld:%s:%s" % (m, kind), {n: [("GLOBAL", "NOTYPE")] for n in names})
    return out


def host_system_defs(root, admit, repo, owners=None):
    """{symbol: {source: [(bind, type)]}} of the defined .dynsym exports of every file of the root's recipe `link.libs.system` (source `sys:<file>`), each file read from the
    pinned deb that owns it. A file owned by no pinned deb (or not a file) is ValueError host-lib-unpinned. A recipe with no link has none."""
    lk = recipe.load_recipe(repo, admit, root).get("link")
    files = ((lk.get("libs") or {}).get("system") or []) if isinstance(lk, dict) else []
    bad = envcheck.system_lib_errors(files, json.load(open(os.path.join(repo, recipe.ENV_JSON)))["packages"], owners if owners is not None else envcheck.file_owners())
    if bad:
        raise ValueError("%s: host-lib-unpinned: %s" % (root, "; ".join(bad)))
    out = {}
    for f in files:
        merge(out, "sys:" + f, {n: [(BIND.get(b, "GLOBAL"), recipe.STYPE.get(t, str(t)))] for n, b, _, shndx, t in syms_any(open(os.path.realpath(f), "rb").read(), 11) if shndx != 0 and n and b in BIND})
    return out
