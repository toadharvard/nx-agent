#!/usr/bin/env python3
"""Tests for verify/gate_lift.py (gates "order", "addr32", "eh") on tiny REAL product roots built through recipe.py in temp git repos: no double of any
product symbol. Lifted roots: the lifted C++ unit is the output of a generator step (cxx_origin.lifted_by) that writes a file-scope asm body chosen by a
tracked input; the runtime unit rt.S defines fnvnx_lift_cpu_bytes; the pack is a content-pinned external fetched from a stand-in of fnv-nx-build
(ARTIFACTS_URL). Native roots: the pinned guest compiler of verify/guest_compiler.json (fetched; red with the reason when it cannot be).
Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox) and the compilers of verify/env.json."""
import atexit
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))))     # checkout root
VERIFY = os.path.join(REAL, "ports", "fnv-nx", "verify")
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(VERIFY, "tests"))
import cas  # noqa: E402
import recipe  # noqa: E402
import test_guest as tg  # noqa: E402
import test_plugins as tp  # noqa: E402
from test_plugins import BASE, ROOT, T, check, git, w  # noqa: E402

LIFT = T + "lift/gates/"
RT = ".data\n.globl fnvnx_lift_cpu_bytes\n.type fnvnx_lift_cpu_bytes,%object\n.size fnvnx_lift_cpu_bytes,4\nfnvnx_lift_cpu_bytes: .word 536\n.text\n.globl fnvl_dispatch\n.type fnvl_dispatch,%function\nfnvl_dispatch: ret\n.size fnvl_dispatch,.-fnvl_dispatch\n"
FN = ".text\n.globl f\n.type f,%function\nf:\n{}\n.size f,.-f\n"
GEN = ('import os\nbody = open("/in/ports/fnv-nx/toy/body.s").read()\nos.makedirs("/out/gen")\n'
       'lines = ["asm(\\n"] + [\'"\' + l + \'\\\\n"\\n\' for l in body.split("\\n") if l] + [");\\n"]\n'
       'open("/out/gen/lifted.cc", "w").write("".join(lines))\n')
G, R = "green", "red"
# body: (order, addr32) of the guest accesses of the body (tool tests: tests/test_gate_order.py has the full table of the rule)
BODIES = {"ordered": (FN.format("tst w19,#3\n b.ne 1f\n ldar w0,[x19]\n1: ret"), G, G),
          "plain_store": (FN.format("str w0,[x19]\n ret"), R, G),
          "guest_index": (FN.format("ldrb w0,[x19,x20]\n dmb ishld\n ret"), G, R)}
ART = BASE + "/artifacts.git"


def publish(name, populate):
    """-> {name, repo, ref, commit, tree_sha256} of a tree committed to the stand-in of fnv-nx-build."""
    d = os.path.join(BASE, "pub-" + name)
    os.makedirs(d)
    populate(d)
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", name)
    git(d, "push", "-q", ART, "HEAD:refs/heads/" + name)
    subprocess.run(["git", "clone", "-q", ART, d + "-co", "-b", name], check=True, capture_output=True)
    return {"commit": git(d, "rev-parse", "HEAD").strip(), "tree_sha256": cas.ingest(d + "-co", BASE + "/hs-" + name, [".git"]), "ref": name}


def ext(name, pub, at):
    return {"name": name, "repo": "fnv-nx-build", "ref": pub["ref"], "commit": pub["commit"], "tree_sha256": pub["tree_sha256"], "at": at}


def inputs_doc(facts):
    """a contract_inputs.json that declares the EH facts external (the other keys are valid and unused by the eh gate)"""
    d = {"format": "fnvnx-contract-inputs/1", "external": [facts], "exe": "facts/exe", "msvc": "facts/msvc", "universe": "facts/universe.tsv",
         "root_results": {"units": "units.json", "addrmap": "addrmap.tsv", "aliases": "aliases.ld"}, "ranges": {"exe": "facts/ranges.tsv", "module": "facts/%s/ranges.tsv"}}
    return d


def lifted_repo(name, body, pack, ehfacts=None, rt=RT):
    """A repo with the REAL tools of lift/gates and a lifted product root: rt.S (runtime object), generated lifted.cc (body)."""
    rec = {"format": "fnvnx-recipe/1", "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
           "flags": {"c": ["-mabi=ilp32"], "cxx": ["-mabi=ilp32", "-O1"], "asm": ["-mabi=ilp32"]}, "include": [], "units": "all",
           "generated": [{"tool": T + "gen/lift.py", "args": [], "inputs": [ROOT + "/body.s"], "outputs": ["gen/lifted.cc"]}],
           "external": [pack]}
    files = {T + "gen/lift.py": GEN, ROOT + "/body.s": body, ROOT + "/a.cc": None}
    if rt is not None:
        files[ROOT + "/rt.S"] = rt
    repo = tp.make_repo(name, rec, {"lifted_by": T + "gen/lift.py", "pack": pack["name"]}, files,
                        [{"path": T + "lift/gates", "class": "tool", "gates": []}])
    os.makedirs(os.path.join(repo, LIFT), exist_ok=True)
    for f in ("gate_order.py", "gate_eh.py"):
        shutil.copyfile(os.path.join(REAL, LIFT, f), os.path.join(repo, LIFT, f))
    if ehfacts is not None:
        doc = inputs_doc(ehfacts["ext"])
        if ehfacts["key"]:
            doc["ehfacts"] = ehfacts["key"]
        w(repo, T + "verify/contract_inputs.json", json.dumps(doc))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tools")
    return repo


def gate(repo, name, cache):
    out = os.path.join(BASE, "out-%s-%s" % (os.path.basename(repo), name))
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_lift.py"), "--gate", name], cwd=repo,
                       env=dict(os.environ, GATE_OUT=out, GATE_CACHE=cache), capture_output=True, text=True)
    f = os.path.join(out, name + ".json")
    return p.returncode, (json.load(open(f)) if os.path.exists(f) else {}), p.stdout + p.stderr


def h(a):
    return "0x%08x" % a


def eh_facts(b):
    return {"text": [h(b), h(b + 0x1000)], "failed_funcinfo": [], "failed_throwinfo": [], "thrower_escapes": [], "seh_red": [], "throw_sites": [], "registrations": [], "helpers": [],
            "funcinfo": [{"addr": h(b + 0x800), "stubs": [], "unwind": [[-1, h(b + 0x30)]], "tries": []}], "scopes": [], "throwinfo": [], "conts": {}}


def main():
    subprocess.run(["git", "init", "-q", "--bare", ART], check=True)
    os.environ["ARTIFACTS_URL"] = ART
    cache = os.path.join(BASE, "cache")
    # --- no root of these kinds: universe 0, green, all three gates (a host root with C++ is no item either)
    host = tp.make_repo("host", tp.REC, "native")
    for n in ("order", "addr32", "eh"):
        rc, r, o = gate(host, n, cache)
        check("%s: no lifted or native guest root: universe 0, green" % n, rc == 0 and r.get("status") == G and r["universe"] == 0, o)
    # --- lifted roots: order (order + align rows) and addr32 (addrwrap rows) by the rule of gate_order.py over every object of the root
    pk = publish("pack0", lambda d: open(d + "/pack.txt", "w").write("pack\n"))
    pack = ext("pack", pk, "/ext/pack")
    for case, (body, want_o, want_a) in sorted(BODIES.items()):
        repo = lifted_repo("l-" + case, body, pack)
        for n, want in (("order", want_o), ("addr32", want_a)):
            rc, r, o = gate(repo, n, cache)
            check("lifted %s: gate %s is %s" % (case, n, want), r.get("status") == want and (rc == 0) == (want == G) and r.get("universe", 0) >= 1
                  and (want != G or r["certified"] == r["universe"]) and (want == G or r["uncertified"]), (rc, r, o))
    rc, r, o = gate(lifted_repo("l-rt", BODIES["ordered"][0], pack, rt=None), "order", cache)
    check("lifted root without the runtime object: red, input missing", rc == 2 and r.get("status") == R and "input missing" in r["detail"], (rc, r))
    # --- eh: the pack (external of the recipe) against the EH facts external named by contract_inputs.json `ehfacts`
    def pack_pub(name, drop=()):
        def pop(d):
            rows = [("%08x" % a, "L_%08x" % a, "code") for a in (0x400030,) if a not in drop]
            open(d + "/entries.tsv", "w").write("".join("\t".join(r) + "\n" for r in rows) + "# table_id 00\n")
            json.dump({"images": 1}, open(d + "/gate_liftgen.json", "w"))
        return ext("pack", publish(name, pop), "/ext/pack")
    fx = publish("facts0", lambda d: json.dump(eh_facts(0x400000), open(d + "/ehtables.json", "w")))
    efact = {"ext": ext("facts", fx, "/ext/facts"), "key": "facts"}
    rc, r, o = gate(lifted_repo("e-none", BODIES["ordered"][0], pack_pub("pack1"), dict(efact, key=None)), "eh", cache)
    check("eh: contract_inputs.json declares no ehfacts: red, input missing", rc == 2 and r.get("status") == R and "input missing" in r["detail"], (rc, r))
    rc, r, o = gate(lifted_repo("e-green", BODIES["ordered"][0], pack_pub("pack2"), efact), "eh", cache)
    check("eh: every table target is a lifted entry: green, 1/1", rc == 0 and r.get("status") == G and (r["certified"], r["universe"]) == (1, 1), (rc, r, o))
    rc, r, o = gate(lifted_repo("e-red", BODIES["ordered"][0], pack_pub("pack3", drop=(0x400030,)), efact), "eh", cache)
    check("eh: a table target that is no lifted entry: red, 0/1", rc == 2 and r.get("status") == R and (r["certified"], r["universe"]) == (0, 1)
          and r["uncertified"], (rc, r, o))
    os.environ.pop("ARTIFACTS_URL")        # the native cases fetch the pinned compiler from the real artifacts repository
    # --- native guest roots: the plugin certificate (fnvnx_order for order, fnvnx_addr for addr32), no build needed to be red
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    rec = tg.guest_rec(pin["external"])
    for plug, n in (("fnvnx_order", "order"), ("fnvnx_addr", "addr32")):
        nop = copy.deepcopy(rec)
        nop["plugins"] = [p for p in nop["plugins"] if p["name"] != plug]
        g = tg.guest_repo("n-no-" + plug, nop, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n"})
        rc, r, o = gate(g, n, cache)
        check("native guest root without %s: gate %s red, units uncertified" % (plug, n), rc == 2 and r.get("status") == R and r["universe"] == 2
              and r["certified"] == 0, (rc, r))
        g = tg.guest_repo("n-ok-" + n, rec, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n"})
        rc, r, o = gate(g, n, cache)
        check("native guest root on the pinned compiler: gate %s green, 2/2" % n, rc == 0 and r.get("status") == G and (r["certified"], r["universe"]) == (2, 2), (rc, r, o))
    g = tg.guest_repo("n-eh", rec, pin)
    rc, r, o = gate(g, "eh", cache)
    check("native guest root: gate eh has no universe, green", rc == 0 and r.get("status") == G and r["universe"] == 0, (rc, r, o))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
