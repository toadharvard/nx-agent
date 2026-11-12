#!/usr/bin/env python3
"""Gates "order" (class B), "addr32" (class F) and "eh" (class D) of VERDICT.md (fnvnx-gate/1): the product gates owned by L3a.
    python3 -I gate_lift.py --gate order|addr32|eh        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT
                                                           writes GATE_OUT/<gate>.json
The driver is impure (it builds roots through recipe.py, reads the cache, runs the tools); the rules are in the pure tool root
ports/fnv-nx/lift/gates (gate_order.py, gate_eh.py), which the driver runs as processes on the cached build results.
Roots come from admit.json, never from arguments. A root is judged by its admit.json declaration `cxx_origin`, never by a name:
  lifted  {"lifted_by", "pack"}   order: gate_order's `order` and `align` rows, addr32: its `addrwrap` rows, over every object of the root
          (the runtime object of rt/enter.c is the object of the root that defines the 4-byte symbol fnvnx_lift_cpu_bytes: sizeof(struct cpu)).
          eh: gate_eh over the pack (the external of the recipe that `pack` names) and the EH facts (the external that the key `ehfacts` of
          contract_inputs.json names; layout ehtables.json and <module>/ehtables.json, as the facts of the other contract gates).
  native  "native", "target" "guest"   order: gate_argorder's certificate for fnvnx_order, addr32: the same certificate for fnvnx_addr.
          The plugin rewrites every access (order: ldar/stlr) or every integer-to-pointer conversion (addr32) of the C++ it compiles; the
          certificate is that the root's plugin list is the rule's list in the rule's order with the rule's arguments, the plugin .so is a
          declared input of the CC stage of each unit (so in its key) and the unit's object was built. eh: native roots have no universe
          (the compiler emits the EH tables of its own code).
  A root of another kind (a host root, a root without C++ units) has no item in these gates.
A missing input is red naming it and its owner, never skipped. Results of the tool runs are cached under GATE_CACHE/lift/<key>.json, key = the
tool's source hash and the content of its inputs. Universe: the items the tools count (order: order + align rows; addr32: addrwrap rows;
eh: table targets), plus one item `<root>:inputs` or `<root>:rule` for a root whose input is missing or whose tool run is red with no row.
No admitted root of these kinds: universe 0, green."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_argorder  # noqa: E402
import gate_build  # noqa: E402
import gate_contract  # noqa: E402
import recipe  # noqa: E402

GATES = ("order", "addr32", "eh")
TOOLS = os.path.join("ports", "fnv-nx", "lift", "gates")
CPU_SYMBOL = "fnvnx_lift_cpu_bytes"
PLUGIN = {"order": "fnvnx_order", "addr32": "fnvnx_addr"}
ROWS = {"order": ("order", "align"), "addr32": ("addrwrap",)}   # the rows of gate_order.py that each gate takes


def kind_of(ent):
    o = ent.get("cxx_origin")
    if isinstance(o, dict) and set(o) == {"lifted_by", "pack"}:
        return "lifted"
    return "native" if o == "native" and ent.get("target") == "guest" else None


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def run_tool(repo, cache, tool, args, outs, extra_key, env_extra=None):
    """Run python3 -I TOOL ARGS with a GATE_OUT of its own -> {out name: result dict}; the results are cached by (tool hash, EXTRA_KEY)."""
    key = hashlib.sha256(json.dumps([sha(os.path.join(repo, TOOLS, tool)), tool, extra_key], sort_keys=True).encode()).hexdigest()
    cf = os.path.join(cache, "lift", key + ".json")
    if os.path.isfile(cf):
        return json.load(open(cf))
    tmp = tempfile.mkdtemp(prefix="gate-lift-")
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
        env.update(env_extra or {})
        env["GATE_OUT"] = tmp
        p = subprocess.run([sys.executable, "-I", os.path.join(repo, TOOLS, tool)] + args, cwd=repo, env=env, capture_output=True, text=True)
        res = {}
        for o in outs:
            f = os.path.join(tmp, o + ".json")
            if not os.path.isfile(f):
                res[o] = {"status": "red", "universe": 1, "certified": 0, "uncertified": ["tool"],
                          "detail": "%s exit %d, no %s.json: %s" % (tool, p.returncode, o, (p.stderr or p.stdout).strip()[-300:])}
            else:
                res[o] = json.load(open(f))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    tf = cf + ".tmp%d" % os.getpid()
    with open(tf, "w") as f:
        json.dump(res, f, sort_keys=True)
    os.replace(tf, cf)
    return res


def fold(root, rows):
    """Rows [result dict] of one tool run -> (universe, certified, ids, detail). A row red with certified == universe (a rule violation of the
    tool that is not an item) adds one uncertified item <root>:rule."""
    u = sum(r["universe"] for r in rows)
    c = sum(r["certified"] for r in rows)
    ids = [root + ":" + i for r in rows for i in r["uncertified"]]
    red = [r for r in rows if r["status"] != "green"]
    if red and c == u:
        u, ids = u + 1, ids + [root + ":rule"]
    return u, c, ids, "; ".join(r["detail"] for r in (red or rows))[:600]


def judge_lifted_order(gate, root, repo, cache):
    res, _ = gate_build.ensure(root, cache, repo)
    if res["status"] == "red":
        return 1, 0, [root + ":build"], "build red at stage %s" % res["failure"]["stage"]
    objs = sorted(res["objects"], key=lambda o: o["unit"])
    holders = sorted({d["object"] for d in res["defined"] if d["symbol"] == CPU_SYMBOL})
    if len(holders) != 1:
        return 1, 0, [root + ":inputs"], ("input missing: no object of the lifted root defines %s (the runtime object of rt/enter.c; admit.json "
                                          "names no runtime root, owner L1/L3a)" % CPU_SYMBOL) if not holders else \
            "input error: %d objects define %s: %s" % (len(holders), CPU_SYMBOL, ", ".join(holders))
    rt = next(o for o in objs if o["unit"] == holders[0])
    r = run_tool(repo, cache, "gate_order.py", [rt["object"]] + [o["object"] for o in objs if o is not rt], ("order", "addrwrap", "align"),
                 [rt["sha256"], [o["sha256"] for o in objs if o is not rt]], {"JOBS": "4"})
    u, c, ids, d = fold(root, [r[g] for g in ROWS[gate]])
    return u, c, ids, d


def eh_inputs(repo, cache, root, rec, ws):
    """-> (pack dir, facts dir, pack tree, facts tree); raises gate_contract.Red"""
    doc = gate_contract.load_inputs(os.path.join(repo, gate_contract.INPUTS))
    ef = doc.get("ehfacts")
    if not ef:
        raise gate_contract.Red("input missing: %s declares no ehfacts (the external that holds ehtables.json of the exe and every module; owner L3a)"
                                % gate_contract.INPUTS)
    pack = recipe.load_admit(repo)[root]["cxx_origin"]["pack"]
    pe = [e for e in rec.get("external", []) if isinstance(e, dict) and e.get("name") == pack]
    if len(pe) != 1:
        raise gate_contract.Red("input missing: the recipe of %s has no external %r (cxx_origin.pack)" % (root, pack))
    store, extc = os.path.join(cache, "store"), os.path.join(cache, "external")
    pk = recipe.fetch_externals(pe, store, extc, os.path.join(ws, "pack"))[pack]
    ex = recipe.fetch_externals(doc["external"], store, extc, os.path.join(ws, "facts"))
    name, _, sub = ef.partition("/")
    return pk["host"], os.path.join(ex[name]["host"], sub), pk["tree"], ex[name]["tree"]


def judge_lifted_eh(root, repo, cache):
    rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
    ws = tempfile.mkdtemp(prefix="gate-lift-ext-")
    try:
        pack, facts, pt, ft = eh_inputs(repo, cache, root, rec, ws)
        r = run_tool(repo, cache, "gate_eh.py", [pack, facts], ("eh",), [pt, ft])
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return fold(root, [r["eh"]])


def judge_root(gate, root, repo, cache, kind):
    try:
        if kind == "native":
            return gate_argorder.judge_root(root, repo, cache, PLUGIN[gate]) if gate != "eh" else (0, 0, [], "native: no EH universe")
        if gate == "eh":
            return judge_lifted_eh(root, repo, cache)
        return judge_lifted_order(gate, root, repo, cache)
    except gate_contract.Red as e:
        return 1, 0, [root + ":inputs"], str(e)[:600]
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except (recipe.BuildRed, OSError, KeyError, ValueError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)


def main(argv):
    if len(argv) != 2 or argv[0] != "--gate" or argv[1] not in GATES:
        print("usage: gate_lift.py --gate order|addr32|eh", file=sys.stderr)
        return 1
    gate, repo = argv[1], "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-lift-cache-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        admit = recipe.load_admit(repo)
        roots = [(r, kind_of(admit[r])) for r in gate_build.product_roots(repo)]
        roots = [(r, k) for r, k in roots if k]
        u = c = 0
        ids, why = [], []
        for r, k in roots:
            ru, rc, rids, rd = judge_root(gate, r, repo, cache, k)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why) or ("%d items in %d roots" % (u, len(roots)) if roots else "no lifted or native guest root (universe 0)")
    res = {"format": "fnvnx-gate/1", "name": gate, "status": "red" if why else "green", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], gate + ".json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("%s: %s %d/%d %s" % (gate, res["status"], c, u, detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
