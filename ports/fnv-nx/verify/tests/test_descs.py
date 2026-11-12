#!/usr/bin/env python3
"""Tests for the `descs` fact (lift/descs, recipe `objects_of`, `forbid_sections`): the sorted union of the `.fnvnx.disp` sections of every
admitted product root's objects but the excepted ones, a recipe output that a consumer takes by `uses`. Toy roots in temp git repos, real sandbox
stages. Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox). Scratch: test_recipe's BASE, removed at exit."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402

recipe, T, ROOT, BASE, check = TR.recipe, TR.T, TR.ROOT, TR.BASE, TR.check
VERIFY = os.path.dirname(HERE)
TOOL_SRC = os.path.join(VERIFY, "..", "lift", "descs", "descs.py")
REC_SRC = os.path.join(VERIFY, "..", "lift", "descs", "build.json")
D = T + "lift/descs"
PA, PB, PC = T + "pa", T + "pb", T + "pc"
ASM = '.section .fnvnx.disp,"a",%%progbits\n.ascii "%s"\n'
USE = ("import os, sys\nos.makedirs('/out/gen', exist_ok=True)\nt = open('/uses/%s/descs' % sys.argv[1]).read()\n"
       "open('/out/gen/used.c', 'w').write('const char descs_text[] = \"' + t.replace('\\n', '\\\\n') + '\";\\n')\n")


def drec(**kw):
    r = json.load(open(REC_SRC))
    r["generated"][0]["tool"] = D + "/descs.py"
    r["generated"][0]["objects_of"] = {"except": [ROOT]}
    r["flags"] = TL.LIB_REC["flags"]
    r.update(kw)
    return r


def consumer(**kw):
    r = TL.main_rec()
    r["generated"] = [{"tool": T + "gen/use.py", "args": [D], "inputs": [], "outputs": ["gen/used.c"]}]
    r["uses"] = [{"root": D, "output": "descs"}]
    r.update(kw)
    return r


def repo_of(name, roots, crec=None, drc=None, extra=None, rec=None):
    libs = [(D, (drc or drec(), {"descs.py": open(TOOL_SRC).read()}))] + [(p, (rec or dict(TL.LIB_REC), f)) for p, f in roots.items()]
    files = dict(TL.BASE_FILES, **{"gen/use.py": USE, "toy/b.c": "int f_b(void) { return 1; }\n"})
    files.update(extra or {})
    return TL.mk(name, crec or consumer(), files, libs=libs)


def oracle(results):
    """The union of the sections, read with pyelftools (the reader of bridgegen.py --obj): the independent expectation."""
    from elftools.elf.elffile import ELFFile
    out = set()
    for r in results:
        for o in r["objects"]:
            with open(o["object"], "rb") as fh:
                s = ELFFile(fh).get_section_by_name(".fnvnx.disp")
                data = s.data() if s is not None else b""
            out.update(x for x in data.decode("ascii").split("\n") if x)
    return sorted(out)


def used_text(r):
    return open(next(x for x in r["objects"] if x["unit"] == "gen/used.c")["object"], "rb").read()


A1, B1 = "stack:i:\\nthis:v:i\\n", "fast:q:i,i,s12g\\nstack:a8hf:s16hf,d\\nstack:i:\\n"
ROOTS = {PA: {"a.S": ASM % A1, "x.c": "int pa_x;\n"}, PB: {"b.S": ASM % B1}, PC: {"c.c": "int pc_x;\n"}}
EXPECT = ["fast:q:i,i,s12g", "stack:a8hf:s16hf,d", "stack:i:", "this:v:i"]
UNION = b"fast:q:i,i,s12g\nstack:a8hf:s16hf,d\nstack:i:\nthis:v:i\n"


def main():
    sys.path.insert(0, os.path.join(VERIFY, "..", "lift", "descs"))
    import descs as DT
    # ---- the tool alone: grammar, sortedness, final LF
    good = ["this:v:i", "this:i:i,i,d", "fast:q:i,i,s12g", "stack:i:", "stack:a8hf:s16hf,d", "stack:a16hv8:s16hv16,q", "stack:m24:s8r,f", "this:a4x:i", "stack:v:s8hq,s8hh"]
    bad = ["", "that:v:i", "this:v", "this:x:i", "this:v:i,", "this:v:,i", "this:v:s12", "this:v:sg", "stack:ahf:i", "stack:v:s8hz", "stack:v:i i", "Stack:v:i", "stack:a8:i:"]
    check("descs.py grammar: every conv:ret:args form of the contract is accepted", all(DT.LINE.fullmatch(x) for x in good), [x for x in good if not DT.LINE.fullmatch(x)])
    check("descs.py grammar: forms outside it are refused", not any(DT.LINE.fullmatch(x) for x in bad), [x for x in bad if DT.LINE.fullmatch(x)])
    for what, data, word in (("no final LF", b"stack:i:", "no final LF"), ("a line outside the grammar", b"stack:i:\nbogus\n", "outside the grammar"),
                             ("unsorted lines", b"this:v:i\nstack:i:\n", "not sorted"), ("duplicate lines", b"stack:i:\nstack:i:\n", "not sorted"),
                             ("a non-ASCII byte", b"stack:i:\n\xc3\xa9\n", "not ASCII"), ("an empty line", b"stack:i:\n\nthis:v:i\n", "outside the grammar")):
        try:
            DT.read_section(data)
            ok = False
        except ValueError as e:
            ok = word in str(e)
        check("descs.py section: %s is red" % what, ok)
    check("descs.py section: the empty section is no descriptor, a valid one is read", DT.read_section(b"") == [] and DT.read_section(b"stack:i:\nthis:v:i\n") == ["stack:i:", "this:v:i"])

    subprocess.run(["git", "init", "-q", "--bare", BASE + "/artifacts.git"], check=True)
    os.environ["ARTIFACTS_URL"] = BASE + "/artifacts.git"
    SS = BASE + "/store"
    # ---- the union over every admitted product root but the excepted one
    ra = repo_of("da", ROOTS)
    adm = recipe.load_admit(ra)
    e0 = recipe.check(recipe.load_recipe(ra, adm, D), D, recipe.tracked(ra), adm, None, ra)
    check("the descs root's recipe (lift/descs/build.json shape) passes the recipe check", not e0, e0)
    r = recipe.build(ra, ROOT, SS, BASE + "/o-da")
    txt = used_text(r) if r["status"] == "green" else b""
    check("the consumer reads the sorted union (4 distinct descriptors, the shared one once) through `uses`", r["status"] == "green" and UNION in txt, TL.err_text(r) + str(txt[-200:]))
    ro = [recipe.build(ra, p, SS, BASE + "/o-p%d" % i) for i, p in enumerate((PA, PB, PC))]
    check("the union equals the pyelftools reader over the same objects (bridgegen.py --obj's reader)",
          all(x["status"] == "green" for x in ro) and oracle(ro) == EXPECT, oracle(ro))
    r2 = recipe.build(ra, ROOT, SS, BASE + "/o-da2")
    check("a second build is all cache hits with the same stage keys", r2["stages"]["run"] == 0 and r2["stage_keys"] == r["stage_keys"], r2["stages"])
    # ---- a new product root is read by the next build: the rule is over the admitted set, not a list
    r3 = recipe.build(repo_of("db", dict(ROOTS, **{T + "pd": {"d.S": ASM % "stack:d:d\\n"}})), ROOT, SS, BASE + "/o-db")
    check("a newly admitted product root's descriptors enter the union (no list names it)", r3["status"] == "green" and b"stack:d:d\n" in used_text(r3) and r3["stage_keys"] != r["stage_keys"],
          TL.err_text(r3))
    # ---- the excepted root: its own section is forbidden, so the exception loses nothing
    rf = recipe.build(repo_of("dc", ROOTS, crec=consumer(forbid_sections=[".fnvnx.disp"])), ROOT, SS, BASE + "/o-dc")
    check("forbid_sections: a root without the section is green", rf["status"] == "green", TL.err_text(rf))
    rr = recipe.build(repo_of("de", ROOTS, crec=consumer(forbid_sections=[".fnvnx.disp"]), extra={"toy/h.S": ASM % "stack:i:\\n"}), ROOT, SS, BASE + "/o-de")
    check("forbid_sections: an object of the root with .fnvnx.disp is red, stage forbid:<root> (the cycle guard of lift/bridges)", rr["status"] == "red" and rr["failure"]["stage"] == "forbid:" + ROOT,
          TL.err_text(rr))
    # ---- malformed facts are red at the generate stage, naming the object
    for i, (what, body) in enumerate((("no final LF", '.section .fnvnx.disp,"a",%progbits\n.ascii "stack:i:"\n'), ("a line outside the grammar", ASM % "bogus\\n"),
                                      ("unsorted lines", ASM % "this:v:i\\nstack:i:\\n"))):
        rm = recipe.build(repo_of("dm%d" % i, dict(ROOTS, **{T + "pe": {"e.S": body}})), ROOT, SS, BASE + "/o-dm%d" % i)
        check("a malformed section (%s) makes the consumer red at the descs generate stage, naming the object" % what,
              rm["status"] == "red" and "gen:" in str(rm["failure"]) and "e.S" in str(rm["failure"]), TL.err_text(rm))
    # ---- checks
    e1 = recipe.check(consumer(forbid_sections=[]), ROOT, recipe.tracked(ra), adm, None, ra)
    check("forbid_sections: an empty list is a check error", any("forbid_sections" in e for e in e1), e1)
    e2 = recipe.check(drec(generated=[dict(drec()["generated"][0], objects_of={"except": [T + "ghost"]})]), D, recipe.tracked(ra), adm, None, ra)
    check("objects_of: an except that is no admitted product root is a check error", any("objects_of" in e for e in e2), e2)
    cr = dict(TL.LIB_REC, uses=[{"root": D, "output": "descs"}])
    rc_ = repo_of("cy", {PA: ROOTS[PA]}, rec=cr)
    cyc = recipe.check(cr, PA, recipe.tracked(rc_), recipe.load_admit(rc_), None, rc_)
    check("a root that uses the descs output but is not excepted is a cycle (the producer reads its objects)", any("cycle" in e for e in cyc), cyc)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
