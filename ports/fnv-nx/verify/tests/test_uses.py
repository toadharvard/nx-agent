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

recipe, T, ROOT, BASE, check = TR.recipe, TR.T, TR.ROOT, TR.BASE, TR.check
P = T + "prod"
MK = ("import os, sys\nos.makedirs('/out/gen', exist_ok=True)\n"
      "for n in ('handheld', 'docked'):\n    open('/out/gen/ident_%s.bin' % n, 'wb').write((sys.argv[1] + n).encode())\n#PAD\n")
USE = ("import hashlib, os, sys\nos.makedirs('/out/gen', exist_ok=True)\n"
       "n = sum(len(open('/uses/%s/%s' % (sys.argv[1], k), 'rb').read()) for k in ('handheld', 'docked'))\n"
       "open('/out/gen/used.c', 'w').write('const int used_len_%d = 1;\\n' % n)\n")
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
    check("uses: the consumer's generate stage reads both outputs of the producer at /uses/<root>/<name> (ident_handheld/docked), content visible: used_len_%d" % 24,
          r["status"] == "green" and "used_len_24" in TL.syms(r["elf"]["path"]) if r["status"] == "green" else False, TL.err_text(r) + str(list(s)[:5]))
    r2 = recipe.build(ra, ROOT, SS, BASE + "/o-ua2")
    check("uses: a second build is all cache hits with the same stage keys", r2["stages"]["run"] == 0 and r2["stage_keys"] == r["stage_keys"], r2["stages"])
    # key: a producer output change changes the consumer's keys; a producer change that keeps the output byte-identical does not
    rb = recipe.build(repo_of("ub", pfiles=PF, prec=dict(PREC, generated=[dict(PREC["generated"][0], args=["gamma"])])), ROOT, SS, BASE + "/o-ub")
    check("uses: the producer's output changed (other bytes): the consumer's stage keys change", rb["status"] == "green" and rb["stage_keys"] != r["stage_keys"], "")
    rc = recipe.build(repo_of("uc", pfiles=dict(PF, **{"mk.py": MK.replace("#PAD", "# a comment: the tool changes, the output does not")})), ROOT, SS, BASE + "/o-uc")
    check("uses: the producer's tool changed but its output is byte-identical: the consumer's stage keys do not change", rc["status"] == "green"
          and rc["stage_keys"] == r["stage_keys"], "")

    def errs(rec, name, prec=PREC):
        repo = repo_of(name, prec=prec, crec=rec)
        return recipe.check(rec, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    e1 = errs(consumer(uses=[{"root": P, "output": "nope"}]), "red1")
    check("uses: an undeclared output is a check error", any("declares no output 'nope'" in e for e in e1), e1)
    e2 = errs(consumer(uses=[{"root": T + "ghost", "output": "x"}]), "red2")
    check("uses: a root that is not admitted is a check error", any("not an admitted root" in e for e in e2), e2)
    e3 = errs(consumer(), "red3", prec=dict(PREC, uses=[{"root": ROOT, "output": "used"}]))
    check("uses: a cycle between distinct roots (uses of a root that uses this one) is a check error", any("cycle" in e for e in e3), e3)
    g = consumer()
    g["generated"] = [dict(g["generated"][0], inputs=[P + "/p.c"])]
    e4 = errs(g, "red4")
    check("a stage reading another root's file by path (generated input) is a check error pointing at `uses`", any("another root" in e and "uses" in e for e in e4), e4)
    e5 = errs(consumer(outputs={"used": "gen/other.c"}), "red5")
    check("outputs: a name that maps to no generated output of the root is a check error", any("outputs must be" in e for e in e5), e5)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
