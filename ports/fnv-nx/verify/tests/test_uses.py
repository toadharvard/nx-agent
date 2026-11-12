#!/usr/bin/env python3
"""Tests for cross-root outputs of verify/recipe.py (RECIPE.md "Outputs and uses"): a root declares `outputs`, another admitted root takes them by
`uses` as stage inputs at /uses/<root>/<name>; the stage key holds the output's content hash. Toy roots in temp git repos, real sandbox stages.
Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox). Scratch: test_recipe's BASE, removed at exit."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

recipe, T, ROOT, BASE = TR.recipe, TR.T, TR.ROOT, TR.BASE
P = T + "prod"
MK = ("import os, sys\nos.makedirs('/out/gen', exist_ok=True)\n"
      "for n in ('handheld', 'docked'):\n    open('/out/gen/ident_%s.bin' % n, 'wb').write((sys.argv[1] + n).encode())\n#PAD\n")
USE = ("import hashlib, os, sys\nos.makedirs('/out/gen', exist_ok=True)\n"
       "n = sum(len(open('/uses/%s/%s' % (sys.argv[1], k), 'rb').read()) for k in ('handheld', 'docked'))\n"
       "open('/out/gen/used.c', 'w').write('const int used_len_%d = 1;\\n' % n)\n")
LST = "import os, sys\nos.makedirs('/out/gen', exist_ok=True)\nopen('/out/gen/srcs.txt', 'w').write('\\n'.join(sys.argv[1:]) + '\\n')\n"
PREC = dict(TL.LIB_REC, generated=[{"tool": P + "/mk.py", "args": ["alpha"], "inputs": [],
                                    "outputs": ["gen/ident_handheld.bin", "gen/ident_docked.bin"]}],
            outputs={"handheld": "gen/ident_handheld.bin", "docked": "gen/ident_docked.bin"})
PF = {"p.c": "int p_x;\n", "mk.py": MK}


def consumer(**kw):
    r = TL.main_rec()
    r["generated"] = [{"tool": T + "gen/use.py", "args": [P], "inputs": [], "outputs": ["gen/used.c"]}]
    r["uses"] = [{"root": P, "output": "handheld"}, {"root": P, "output": "docked"}]
    r["outputs"] = {"used": "gen/used.c"}
    r.update(kw)
    return r


def repo_of(name, prec=PREC, pfiles=PF, crec=None):
    return TL.mk(name, crec or consumer(), dict(TL.BASE_FILES, **{"gen/use.py": USE, "toy/b.c": "int f_b(void) { return 1; }\n"}), libs=[(P, (prec, pfiles))])


def main():
    subprocess.run(["git", "init", "-q", "--bare", BASE + "/artifacts.git"], check=True)
    os.environ["ARTIFACTS_URL"] = BASE + "/artifacts.git"
    SS = BASE + "/store"
    ra = repo_of("ua")
    r = recipe.build(ra, ROOT, SS, BASE + "/o-ua")
    s = TL.syms(r["elf"]["path"]) if r["status"] == "green" else {}
    case("uses: the consumer's generate stage reads both outputs of the producer at /uses/<root>/<name> (ident_handheld/docked), content visible: used_len_24",
          r["status"] == "green" and "used_len_24" in TL.syms(r["elf"]["path"]) if r["status"] == "green" else False, TL.err_text(r) + str(list(s)[:5]))
    r2 = recipe.build(ra, ROOT, SS, BASE + "/o-ua2")
    case("uses: a second build is all cache hits with the same stage keys", r2["stages"]["run"] == 0 and r2["stage_keys"] == r["stage_keys"], r2["stages"])
    # key: a producer output change changes the consumer's keys; a producer change that keeps the output byte-identical does not
    rb = recipe.build(repo_of("ub", pfiles=PF, prec=dict(PREC, generated=[dict(PREC["generated"][0], args=["gamma"])])), ROOT, SS, BASE + "/o-ub")
    case("uses: the producer's output changed (other bytes): the consumer's stage keys change", rb["status"] == "green" and rb["stage_keys"] != r["stage_keys"], "")
    rc = recipe.build(repo_of("uc", pfiles=dict(PF, **{"mk.py": MK.replace("#PAD", "# a comment: the tool changes, the output does not")})), ROOT, SS, BASE + "/o-uc")
    case("uses: the producer's tool changed but its output is byte-identical: the consumer's stage keys do not change", rc["status"] == "green"
          and rc["stage_keys"] == r["stage_keys"], "")

    def errs(rec, name, prec=PREC):
        repo = repo_of(name, prec=prec, crec=rec)
        return recipe.check(rec, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    e1 = errs(consumer(uses=[{"root": P, "output": "nope"}]), "red1")
    case("uses: an undeclared output is a check error", any("declares no output 'nope'" in e for e in e1), e1)
    e2 = errs(consumer(uses=[{"root": T + "ghost", "output": "x"}]), "red2")
    case("uses: a root that is not admitted is a check error", any("not an admitted root" in e for e in e2), e2)
    e3 = errs(consumer(), "red3", prec=dict(PREC, uses=[{"root": ROOT, "output": "used"}]))
    case("uses: a cycle between distinct roots (uses of a root that uses this one) is a check error", any("cycle" in e for e in e3), e3)
    g = consumer()
    g["generated"] = [dict(g["generated"][0], inputs=[P + "/p.c"])]
    e4 = errs(g, "red4")
    case("a stage reading another root's file by path (generated input) is a check error pointing at `uses`", any("another root" in e and "uses" in e for e in e4), e4)
    e5 = errs(consumer(outputs={"used": "gen/other.c"}), "red5")
    case("outputs: a name that maps to no generated output of the root is a check error", any("outputs must be" in e for e in e5), e5)
    # generated `sources`: units listed by a generate stage as <external>:<path> lines
    def srcpop(body1):
        def f(d):
            os.makedirs(d + "/src")
            open(d + "/src/s1.c", "w").write(body1)
            open(d + "/src/s2.c", "w").write("int s2_fn(void) { return 4; }\n")
        return f
    c1, h1 = TR.publish("srcs1", srcpop("int s1_fn(void) { return 3; }\n"))
    ext = {"name": "tsrc", "repo": "fnv-nx-build", "ref": "srcs1", "commit": c1, "tree_sha256": h1, "at": "/ext/tsrc"}

    def srepo(name, args, e=ext, **kw):
        r = TL.main_rec()
        r["external"] = [e]
        r["generated"] = [{"tool": T + "gen/lst.py", "args": args, "inputs": [], "outputs": ["gen/srcs.txt"]}]
        r["sources"] = "gen/srcs.txt"
        r.update(kw)
        return TL.mk(name, r, dict(TL.BASE_FILES, **{"gen/lst.py": LST, "toy/b.c": "int f_b(void) { return 1; }\n"}))
    rs1 = recipe.build(srepo("s1", ["tsrc:src/s1.c"]), ROOT, SS, BASE + "/o-s1")
    case("sources: the unit listed by the generate stage (a file of an external) is compiled and linked: unit tsrc/src/s1.c, symbol s1_fn",
          rs1["status"] == "green" and "tsrc/src/s1.c" in rs1["units"] and "s1_fn" in TL.syms(rs1["elf"]["path"]), TL.err_text(rs1))
    rs2 = recipe.build(srepo("s2", ["tsrc:src/s1.c", "tsrc:src/s2.c"]), ROOT, SS, BASE + "/o-s2")
    o1 = {o["unit"]: o["sha256"] for o in rs1["objects"]}
    o2 = {o["unit"]: o["sha256"] for o in rs2["objects"]}
    case("sources: a changed list adds only the new unit: the other units keep their objects and their compile-stage keys (cache hits)",
          rs2["status"] == "green" and set(o2) - set(o1) == {"tsrc/src/s2.c"} and all(o2[u] == o1[u] for u in o1)
          and len(set(rs1["stage_keys"]) & set(rs2["stage_keys"])) >= len(o1), rs2["stages"])
    c3, h3 = TR.publish("srcs3", srcpop("int s1_fn(void) { return 30; }\n"))
    rs3 = recipe.build(srepo("s3", ["tsrc:src/s1.c"], e=dict(ext, commit=c3, tree_sha256=h3, ref="srcs3")), ROOT, SS, BASE + "/o-s3")
    case("sources: the unit's keys include its content hash: another content of the same path has another preprocessing key and object",
          rs3["status"] == "green" and rs3["pp_keys"]["tsrc/src/s1.c"] != rs1["pp_keys"]["tsrc/src/s1.c"] and {o["unit"]: o["sha256"] for o in rs3["objects"]}["tsrc/src/s1.c"] != o1["tsrc/src/s1.c"], "")
    for what, args in (("a path outside the external (..)", ["tsrc:../x.c"]), ("an external the recipe does not declare", ["nope:src/s1.c"]),
                       ("a file the external does not hold", ["tsrc:src/zzz.c"])):
        rr = recipe.build(srepo("sr-" + str(abs(hash(what)) % 10000), args), ROOT, SS, BASE + "/o-sr")
        case("sources: a bad source is red (stage sources:<root>)", rr["status"] == "red" and rr["failure"]["stage"] == "sources:" + ROOT, TL.err_text(rr), key=what)
    rk = srepo("sk", ["tsrc:src/s1.c"], sources="gen/other.txt")
    ek = recipe.check(json.load(open(rk + "/" + ROOT + "/build.json")), ROOT, recipe.tracked(rk), recipe.load_admit(rk), None, rk)
    case("sources: a name that is no generated output of the root is a check error", any("sources must name" in e for e in ek), ek)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
