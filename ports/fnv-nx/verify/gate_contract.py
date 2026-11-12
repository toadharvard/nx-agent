#!/usr/bin/env python3
"""Gate "contract" family (fnvnx-gate/1, class 8 of VERDICT.md): the whole-binary gates of contract/ judged on the link of the product root.
    python3 -I gate_contract.py --gate NAME     NAME: n1 n2_data n2_call n2_vcall xc xunit rtti pcrel bodysel
    cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/NAME.json (gatefile.py shape).
One entry per gate in gates.json, so each is its own line of the verdict; the stages are the ones of contract/verdict.py (exefacts, n1,
objfacts, registry, xunit, rtti, pcrel, bodysel), run with the same arguments, in the order of their dependencies, each once per input set:
N3 (the static native-body criterion of the pi plugin's `.fnvnx.n3`) reaches bodysel through the `bind` stage (contract/bind.py: exe addresses of the objects' N3 rows through the N1 certificate, written as n3.tsv) and bodysel's `--n3`; a function without an `ok` N3 row is LIFTED.
their outputs are kept in GATE_CACHE/contract/<key>/ (key = sha256 of contract_inputs.json, the git tree under test and the content of
the objects and ELF of the root), so the nine gates cost one run of the chain and a cold run gives the same files.

Universe (the convention of build, objstub, linkstrict, determinism: gate_build.quantify): the admitted product roots of admit.json. Every gate is
judged on every root and the items are summed; no product root: universe 0, green. A root that links a guest image and lacks objects, the ELF, a result
file or an input is RED with the reason (item `<root>:inputs`), never skipped. A root that links no image has universe 0 for the link gates
(abs disp reach reachlink gpa) and, when it declares no "game" directory, for the game gates too; a root with "game" but no link is red. Admission (gate_admit rule 3 + REQUIRED_PRODUCT) is what forces
each of these gates to exist before a root can be admitted.

Per-root inputs are outputs of the root's own build and link, read from the out dir of gate_build.ensure (beside objects.json), the names
declared in contract_inputs.json "root_results": units.json (the exe's unit/function table), addrmap.tsv, aliases.ld (the link stage's maps),
entries.tsv (liftall's entries, gate disp only). A file the root's recipe/link does not emit yet makes the gate red for that root naming it
(owner: L1 link stage). The game's units are those under the root-relative directory build.json declares as "game" (no default).

Inputs shared by every root (verify/contract_inputs.json, "fnvnx-contract-inputs/1"; no other source, nothing comes from an argument):
  external     content-pinned inputs of fnv-nx-build, the same keys and the same fetch as a recipe's externals (recipe.fetch_externals:
               exact commit, tree_sha256 checked): the exe-derived private facts never enter this repository
  exe msvc universe   `NAME/path` inside an external: FalloutNV.exe, the pristine MSVC objects (dir of u<i>.obj), L3's universe.tsv
  root_results {units, addrmap, aliases[, entries]} file names in the root's out dir
  ranges       {exe: NAME/path, module: NAME/path with %s = the dll's stem}: where an image's range facts are in an external
  The image set of gpa/reach/reachlink is not an input: contract/images.py derives it (transitive closure of the normal and delay-load import
  directories from the exe, resolved against the files of externals exe and redist). An image without range facts is a red item of those gates.
A gate whose input is missing is RED with the reason, never skipped: no inputs file, an invalid one, an external that does not fetch or does
not match its hash, a root that has no link, no "game" (only an image root needs one), does not build or lacks a result file.
n2_data n2_call n2_vcall xc (registry.py counts, rows = functions that are not N2): not failures in themselves (the function becomes LIFTED, a
legal outcome); the invariant that closes the class is that EVERY row is turned into LIFTED by bodysel. Universe = the functions the gate
judged, certified = judged minus the rows whose address bodysel does not give LIFTED (the function would run a native body that fails its contract)."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import gate_build  # noqa: E402
import iat  # noqa: E402
import images  # noqa: E402
import gatefile  # noqa: E402
import recipe  # noqa: E402

INPUTS = "ports/fnv-nx/verify/contract_inputs.json"
FORMAT = "fnvnx-contract-inputs/1"
KEYS = {"format", "external", "exe", "msvc", "universe", "root_results", "ranges"}
PATHS = ("exe", "msvc", "universe")
RESULTS = {"units": "the root's link stage", "addrmap": "the root's link stage", "aliases": "the root's link stage"}
# inputs of the link-side gates; a key is required only by the gates that read it (a gate without its key is red, naming it and its owner)
OPT_PATHS = {"reach_requests": ("reach, reachlink", "L5: platform manifest reach_requests.tsv"),
             "dynsites": ("gpa", "L5: dynsites.tsv")}
OPT_KEYS = set(OPT_PATHS) | {"keys", "bridge"}
CONTRACT_GATES = ("n1", "n2_data", "n2_call", "n2_vcall", "xc", "xunit", "rtti", "pcrel", "bodysel")
LINK_GATES = ("abs", "disp", "reach", "reachlink", "gpa")
GATES = CONTRACT_GATES + LINK_GATES
GATE_KEYS = {"reach": ["reach_requests"], "reachlink": ["reach_requests"], "gpa": ["dynsites"]}
IMAGE_GATES = ("reach", "reachlink", "gpa")
REGISTRY = {"n2_data": "data", "n2_call": "call", "n2_vcall": "vcall", "xc": "xc"}
NM, READELF = "/usr/bin/aarch64-linux-gnu-nm", "/usr/bin/aarch64-linux-gnu-readelf"
CONTRACT = os.path.join(HERE, "..", "contract")


class Red(Exception):
    pass


def load_inputs(path):
    """-> the inputs document, or raises Red naming every problem."""
    if not os.path.isfile(path):
        raise Red("no %s: the exe-derived inputs of the contract gates (exe, units.json, pristine MSVC objects, universe.tsv, addrmap, aliases) "
                  "are not declared as content-pinned externals of fnv-nx-build" % INPUTS)
    try:
        doc = json.load(open(path))
    except ValueError as e:
        raise Red("%s is not JSON: %s" % (INPUTS, e))
    err = []
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise Red("%s: format is not %s" % (INPUTS, FORMAT))
    if not KEYS <= set(doc) or set(doc) - KEYS - OPT_KEYS:
        err.append("keys must be %s, and may add %s" % (", ".join(sorted(KEYS)), ", ".join(sorted(OPT_KEYS))))
    exts = doc.get("external")
    if not isinstance(exts, list) or not exts:
        err.append("external must be a non-empty list")
    else:
        for i, e in enumerate(exts):
            if not isinstance(e, dict) or set(e) != recipe.EXT_KEYS or not all(isinstance(v, str) for v in e.values()):
                err.append("external[%d] needs exactly %s (strings)" % (i, ", ".join(sorted(recipe.EXT_KEYS))))
        names = [e.get("name") for e in exts if isinstance(e, dict)]
        if len(set(names)) != len(names):
            err.append("external names are not unique")
        for k in PATHS + tuple(k for k in ("reach_requests", "dynsites", "keys", "bridge") if k in doc):
            v = doc.get(k)
            if not isinstance(v, str) or "/" not in v or v.split("/", 1)[0] not in names or ".." in v.split("/"):
                err.append("%s must be NAME/path inside a declared external" % k)
    rg = doc.get("ranges")
    if not (isinstance(rg, dict) and set(rg) == {"exe", "module"} and all(isinstance(v, str) and "/" in v and ".." not in v.split("/")
                                                                            and v.split("/", 1)[0] in [e.get("name") for e in exts or [] if isinstance(e, dict)]
                                                                            for v in rg.values()) and rg["module"].count("%s") == 1):
        err.append("ranges needs exactly exe and module (NAME/path inside a declared external; module holds one %s)")
    rr = doc.get("root_results")
    if not (isinstance(rr, dict) and set(rr) - {"entries"} == set(RESULTS) and all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._-]+", v) for v in rr.values())):
        err.append("root_results must map %s (and may map entries) to file names of the root's out dir" % ", ".join(sorted(RESULTS)))
    if err:
        raise Red("%s: %s" % (INPUTS, "; ".join(err)))
    return doc


def n2_item(universe_rows, counts, fail_addrs, verdicts):
    """(universe, certified, uncertified ids) of one registry gate. fail_addrs: addresses of its rows; verdicts: {address: 'native'|'lifted'}."""
    bad = sorted(a for a in set(fail_addrs) if verdicts.get(a) != "lifted")
    return sum(counts.values()), sum(counts.values()) - len(bad), ["%08x" % a for a in bad]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def assemble(link, res, game, out, rr):
    """The link directory of the stages: fixed/ (the root's objects; src_ for the game's), out/ (addrmap, aliases), guest/fnvnx.elf."""
    for d in ("fixed", "out", "guest", "game"):
        os.makedirs(os.path.join(link, d))
    for o in res["objects"]:
        is_game = o["unit"].startswith(game.strip("/") + "/")
        name = ("src_" if is_game else "") + o["unit"].replace("/", "_") + ".o"
        os.symlink(o["object"], os.path.join(link, "fixed", name))
        if is_game:
            os.symlink(o["object"], os.path.join(link, "game", name))
    os.symlink(res["elf"]["path"], os.path.join(link, "guest", "fnvnx.elf"))
    with open(os.path.join(os.path.dirname(link), "inputs.rsp"), "w") as f:      # ld.py's response file: one quoted path per object of the link
        f.write("".join('"%s"\n' % o["object"] for o in res["objects"]))
    for k, dst in (("addrmap", "out/addrmap.tsv"), ("aliases", "out/aliases.ld")):
        os.symlink(os.path.join(out, rr[k]), os.path.join(link, dst))


def P2(ext, v):
    n, rel = v.split("/", 1)
    return os.path.join(ext[n]["host"], rel)


def py(script, *args):
    return [sys.executable, "-I", os.path.join(CONTRACT, script)] + list(args)


def images_of(doc, ext):
    """([(name, pe path, ranges path)] of the images that have range facts, [names without]) of contract.images.image_set."""
    have, lack = [], []
    bridge = set()          # a bridge dll is never an image (contract/dllkind.py): the dlls with rows in the declared bridge.tsv
    if doc.get("bridge"):
        bridge = {r["dll"] for r in iat.bridge_rows(P2(ext, doc["bridge"]))[0]}
    for name, path in images.image_set(P2(ext, doc["exe"]), [ext[n]["host"] for n in ("exe", "redist")], bridge):
        rel = doc["ranges"]["exe"] if name.endswith(".exe") else doc["ranges"]["module"] % name.rsplit(".", 1)[0]
        rg = P2(ext, rel)
        if os.path.isfile(rg):
            have.append((name, path, rg))
        else:
            lack.append(name)
    return have, lack


def stages(w, doc, ext, rp, only=None):
    """{stage: (argv, [files it writes])} with paths inside the work dir W (contract/verdict.py's arguments). ONLY: a stage name; the image set (it
    reads the exe) is derived only for the stages that take it (gpa, reach, reachlink), so the other stages can be built without it."""
    P = lambda k: rp[k] if k in rp else P2(ext, doc[k])
    link = w + "/link"
    imgs = []
    if only is None or only in IMAGE_GATES:
        for name, pe, rg in images_of(doc, ext)[0]:
            imgs += ["--image", "%s=%s,%s" % (name, pe, rg)]
    bridge = ["--bridge", P2(ext, doc["bridge"])] if doc.get("bridge") else []
    opt = lambda k: P2(ext, doc[k]) if k in doc else None
    st = {
        "exefacts": (py("exefacts.py", "--exe", P("exe"), "--units", rp["units"], "--out", w + "/facts.json"), ["facts.json"]),
        "n1": (py("n1.py", "--exe", P("exe"), "--units", rp["units"], "--msvc", P("msvc"), "--out", w + "/n1.json"), ["n1.json", "gate/n1.json"]),
        "objfacts": (py("objfacts.py", "--link", link, "--facts", w + "/facts.json", "--out", w + "/obj.json"), ["obj.json"]),
        "registry": (py("registry.py", "--facts", w + "/facts.json", "--obj", w + "/obj.json", "--n1", w + "/n1.json", "--link", link,
                        "--out", w + "/gates"), ["gates/gates.json", "gates/bodysel.tsv"]),
        "xunit": (py("gate_xunit.py", "--objects", link + "/game", "--nm", NM), ["gate/xunit.json"]),
        "rtti": (py("gate_rtti.py", "--objects", link + "/game", "--readelf", READELF), ["gate/rtti.json"]),
        "pcrel": (py("gate_pcrel.py", "--elf", link + "/guest/fnvnx.elf", "--objects", link + "/fixed", "--work", w + "/pcrel",
                     "--readelf", READELF, "--nm", NM), ["gate/pcrel.json"]),
        "bind": (py("bind.py", "--obj", w + "/obj.json", "--n1", w + "/n1.json", "--units", rp["units"], "--out", w + "/bind.json", "--n3-out", w + "/n3.tsv"),
                 ["bind.json", "n3.tsv"]),
        "bodysel": (py("bodysel.py", "--universe", P("universe"), "--n1", w + "/n1.json", "--gates", w + "/gates", "--n3", w + "/n3.tsv",
                       "--out", w + "/bodysel.tsv"),
                    ["bodysel.tsv", "gate/bodysel.json"]),
        "abs": (py("../link/gate_abs.py", "--inputs", w + "/inputs.rsp", "--elf", link + "/guest/fnvnx.elf"), ["gate/abs.json"]),
        "gpa": (py("gpa_names.py", "--sites", opt("dynsites") or "", *imgs, *bridge, "--out", w + "/gpa"), ["gpa/gpa_names.tsv", "gate/gpa.json"]),
        "reach": (py("gate_reach.py", "--requests", opt("reach_requests") or "", "--gpa", w + "/gpa/gpa_names.tsv", *imgs, *bridge, "--out", w + "/reach/reach.tsv",
                     "--facts-tsv", w + "/reach/reach.facts.tsv"), ["reach/reach.facts.tsv", "gate/reach.json"]),
        "reachlink": (py("gate_reach.py", "--objects", w + "/inputs.rsp", "--requests", opt("reach_requests") or "", "--certified", w + "/reach/reach.facts.tsv"),
                      ["gate/reachlink.json"]),
    }
    if "entries" in rp:
        st["disp"] = (py("../link/gate_disp.py", "--elf", link + "/guest/fnvnx.elf", "--universe", P("universe"), "--entries", rp["entries"],
                         *(["--keys", opt("keys")] if doc.get("keys") else [])), ["gate/disp.json"])
    return st


NEEDS = {"n1": ["n1"], "xunit": ["xunit"], "rtti": ["rtti"], "pcrel": ["pcrel"], "bodysel": ["exefacts", "n1", "objfacts", "registry", "bind", "bodysel"]}
NEEDS.update({g: NEEDS["bodysel"] for g in REGISTRY})
NEEDS.update({"abs": ["abs"], "disp": ["disp"], "gpa": ["gpa"], "reach": ["gpa", "reach"], "reachlink": ["gpa", "reach", "reachlink"]})


def run_stage(name, w, doc, ext, rp):
    argv, outs = stages(w, doc, ext, rp, name)[name]
    if all(os.path.isfile(os.path.join(w, o)) for o in outs):
        return
    gdir = os.path.join(w, "gate")
    os.makedirs(gdir, exist_ok=True)
    # a gate file of a stage is written to the shared gate dir; xunit/rtti/pcrel/n1/bodysel exit 1 when red and still write it
    r = subprocess.run(argv, cwd=w, env=dict(os.environ, GATE_OUT=gdir), capture_output=True, text=True)
    missing = [o for o in outs if not os.path.isfile(os.path.join(w, o))]
    if missing:
        tail = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["?"])[-1][:200]
        raise Red("stage %s failed (exit %d, no %s): %s" % (name, r.returncode, ", ".join(missing), tail))


def main(argv):
    if len(argv) != 2 or argv[0] != "--gate" or argv[1] not in GATES:
        print("usage: gate_contract.py --gate %s" % "|".join(GATES), file=sys.stderr)
        return 1
    gate, repo, out = argv[1], ".", os.environ["GATE_OUT"]
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-contract-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        u, c, ids, detail = gate_build.quantify(repo, lambda root: judge_root(gate, repo, cache, root))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    os.environ["GATE_OUT"] = out
    gatefile.write(gate, u, c, ids, detail)
    st = "green" if u == c else "red"
    print("%s: %s %d/%d %s" % (gate, st, c, u, detail[:160]))
    return 0 if st == "green" else 2


def judge_root(gate, repo, cache, root):
    """One admitted product ROOT -> (universe, certified, ids, detail); a missing output or input is red with the reason (never raises)."""
    try:
        return judge(gate, repo, cache, root)
    except Red as e:
        return 1, 0, [root + ":inputs"], str(e)[:600]
    except (recipe.RecipeError, recipe.BuildRed, OSError, KeyError, ValueError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)


class Skip(Exception):
    """The root has no universe for this gate (VERDICT.md "Universe rule of the per-root gates"): not red; args[0] is the detail."""


@contextlib.contextmanager
def chain(gate, repo, cache, root):
    """The preamble of judge(), shared with the L2 gates (gate_l2native.py): the universe rule, the root's build, its result files, the stages GATE needs
    run (cached), the externals fetched. Yields (doc, rec, objs, out, rp, ext, w): w holds the stage outputs (bodysel.tsv, gates/ ...); ext {name: {host}}
    is valid inside the with block only. Raises Red, or Skip when the root has no universe."""
    rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
    image = bool(rec.get("link"))
    game = rec.get("game")
    if not image:         # universe rule: the link gates' items come from the root's linked guest ELF
        if gate in LINK_GATES:
            raise Skip("root links no guest image: its objects are judged on the image root that links it (link.roots)")
        if game:
            raise Red("root %s declares \"game\" but links no ELF: the game's units are judged on the image that links them" % root)
        raise Skip("root declares no game directory: no game functions")
    if not isinstance(game, str) or not game:
        raise Red("root %s: build.json declares no \"game\" (the root-relative directory of the game's units; no default)" % root)
    doc = load_inputs(os.path.join(repo, INPUTS))
    for k in GATE_KEYS.get(gate, []):
        if not doc.get(k):
            raise Red("%s declares no %s (%s): gate %s cannot judge" % (INPUTS, k, OPT_PATHS.get(k, ("", "L5/L2: the image set of the stage"))[1], gate))
    objs, out = gate_build.ensure(root, cache, repo)
    if objs["status"] == "red" or not objs["elf"]:
        raise Red("root %s does not build (stage %s)" % (root, objs.get("failure", {}).get("stage", "?")))
    rr = dict(doc["root_results"])
    if gate != "disp":
        rr.pop("entries", None)
    elif "entries" not in rr:
        raise Red("%s declares no root_results.entries (L2/L3a: liftall's entries.tsv): gate disp cannot judge" % INPUTS)
    need = [k for k in rr if k != "entries" or gate == "disp"]
    absent = [rr[k] for k in need if not os.path.isfile(os.path.join(out, rr[k]))]
    if absent:
        raise Red("root %s emits no %s (owner: L1 link stage): the result files of the root's build and link are its only source" % (root, ", ".join(absent)))
    rp = {k: os.path.join(out, v) for k, v in rr.items()}
    tree = gate_build.tree_of(repo)
    key = hashlib.sha256(json.dumps([sha(os.path.join(repo, INPUTS)), tree, root, rec["game"], objs["elf"]["sha256"], [o["sha256"] for o in objs["objects"]],
                                     sorted((k, sha(p)) for k, p in rp.items())], sort_keys=True).encode()).hexdigest()
    w = os.path.join(cache, "contract", key)
    ws = tempfile.mkdtemp(prefix="contract-ext-")
    try:
        ext = recipe.fetch_externals(doc["external"], os.path.join(cache, "store"), os.path.join(cache, "external"), ws)
        os.makedirs(w, exist_ok=True)
        if not os.path.isdir(os.path.join(w, "link")):
            tmpl = w + "/link.tmp%d" % os.getpid()
            assemble(tmpl, objs, rec["game"], out, rr)
            os.rename(tmpl, w + "/link")
        for st in NEEDS[gate]:
            run_stage(st, w, doc, ext, rp)
        yield doc, rec, objs, out, rp, ext, w
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def judge(gate, repo, cache, root):
    """-> (universe, certified, ids, detail) of the gate on ROOT; raises Red."""
    try:
        with chain(gate, repo, cache, root) as (doc, rec, objs, out, rp, ext, w):
            return _judge(gate, repo, cache, root, doc, rp, ext, w)
    except Skip as e:
        return 0, 0, [], str(e)


def _judge(gate, repo, cache, root, doc, rp, ext, w):
    tree = gate_build.tree_of(repo)
    if gate == "reach":       # the facts file the objstub gate reads (gate_objstub.py): written only from a reach run of this tree
        d = os.path.join(cache, "reach", tree)
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(w + "/reach/reach.facts.tsv", d + "/reach.facts.tsv")
    if gate not in REGISTRY:
        r = json.load(open(os.path.join(w, "gate", gate + ".json")))
        u, c, ids, detail = r["universe"], r["certified"], [root + ":" + i for i in r["uncertified"]], r["detail"]
        if gate in IMAGE_GATES:                 # an image of the closure without range facts: a reported red item (the stage judged the rest)
            lack = images_of(doc, ext)[1]
            u, ids = u + len(lack), ids + [root + ":" + n for n in lack]
            detail += "".join("; %s has no range facts (owner L3a/L5)" % n for n in lack)
        return u, c, ids, detail
    verd = {}
    for ln in open(w + "/bodysel.tsv"):
        p = ln.rstrip("\n").split("\t")
        verd[int(p[0], 16)] = p[1]
    g = REGISTRY[gate]
    rows = [int(ln.split("\t")[0], 16) for ln in open(w + "/gates/bodysel.tsv") if ln.split("\t")[1] == g]
    counts = json.load(open(w + "/gates/gates.json"))[g]["counts"]
    u, c, ids = n2_item(None, counts, rows, verd)
    return u, c, [root + ":" + i for i in ids], "judged %d, rows %d, every row must be LIFTED by bodysel" % (u, len(rows))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
