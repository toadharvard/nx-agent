#!/usr/bin/env python3
"""Tests for the guest ELF link through verify/recipe.py (RECIPE.md "Link"): link.roots, link.libs, the options fixed by the rule and the
`ldcheck` post stage (link/ldcheck.py). Toy product roots in temp git repos, built with the real compiler and linker of env.json in the real
sandbox stages; fixtures are those of test_recipe.py (the toy root is an ILP32 PIE guest linked with the tree's own link/guest.ld).
Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox). Scratch: test_recipe's BASE, removed at exit."""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402

recipe, T, ROOT, BASE = TR.recipe, TR.T, TR.ROOT, TR.BASE
LIB = T + "lib"
LIB2 = T + "lib2"
NM = "aarch64-linux-gnu-nm"
CC = "aarch64-linux-gnu-gcc-13"
LIB_REC = {"format": "fnvnx-recipe/1", "compiler": TR.RECIPE["compiler"], "flags": TR.RECIPE["flags"], "include": [], "units": "all"}


def main_rec(**kw):
    r = TR.mut(os_layer=[], constants=None, include=[ROOT + "/inc"])
    r["link"] = dict(r["link"], **kw)
    r.pop("generated")
    return r


A_C = TR.HDR + '#include "toy.h"\nint f_b(void);\nint f_a(void) { return f_b() + TOY; }\n'
BASE_FILES = {"toy/a.c": A_C, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/constants.tsv": None, "gen/gen.py": None}


def mk(name, rec, files=None, libs=()):
    """A repo of the toy root (recipe REC, FILES overriding the fixtures) and the extra product roots LIBS {path: (recipe, {file: text})}."""
    repo = TR.make_repo(name, rec=rec, files=dict(BASE_FILES, **(files or {})))
    admit = json.load(open(repo + "/" + TR.T + "verify/admit.json"))
    for path, (r, fs) in dict(libs).items():
        admit["roots"].append({"path": path, "class": "product", "target": "host", "gates": [], "recipe": path + "/build.json"})
        TR.w(repo, path[len(T):] + "/build.json", json.dumps(r))
        for f, text in fs.items():
            TR.w(repo, path[len(T):] + "/" + f, text)
    admit["roots"].sort(key=lambda x: x["path"])
    TR.w(repo, "verify/admit.json", json.dumps(admit))
    TR.git(repo, "add", "-A")
    TR.git(repo, "commit", "-q", "-m", "roots")
    return repo


def syms(elf):
    """{name: address} of the ELF symbol table, by binutils' nm."""
    out = subprocess.run([NM, "-n", elf], capture_output=True, text=True, check=True).stdout
    return {l.split()[2]: int(l.split()[0], 16) for l in out.splitlines() if len(l.split()) == 3}


def words(path):
    """The eight words at the first PT_LOAD's file offset (the crt0 header)."""
    d = open(path, "rb").read()
    phoff, phent, phnum = int.from_bytes(d[28:32], "little"), int.from_bytes(d[42:44], "little"), int.from_bytes(d[44:46], "little")
    off = next(int.from_bytes(d[o + 4:o + 8], "little") for o in (phoff + i * phent for i in range(phnum)) if int.from_bytes(d[o:o + 4], "little") == 1)
    return [int.from_bytes(d[off + i:off + i + 4], "little") for i in range(0, 32, 4)]


def build(repo, store, out=None):
    return recipe.build(repo, ROOT, store, out or BASE + "/o-" + os.path.basename(store))


def digests(r):
    return [(x["root"], x["sha256"]) for x in r["link_roots"]]


def err_text(r):
    f = r.get("failure") or {}
    return "%s: %s" % (f.get("stage"), " | ".join(f.get("stderr", [])))


def main():
    subprocess.run(["git", "init", "-q", "--bare", BASE + "/artifacts.git"], check=True)
    os.environ["ARTIFACTS_URL"] = BASE + "/artifacts.git"
    SS = BASE + "/store"
    LB = (LIB, (LIB_REC, {"l.c": "int f_b(void) { return 14; }\n"}))

    # (a) two product roots, one links the other
    repo = mk("two", main_rec(roots=[LIB]), libs=[LB])
    r = build(repo, SS)
    check = TR.check
    check("link.roots: two product roots, the second's objects are link inputs after the first's; green", r["status"] == "green" and r["elf"], err_text(r))
    s = syms(r["elf"]["path"])
    check("the ELF defines f_a (own unit) before f_b (input root), crt0 at 0", s.get("f_a", 1) < s.get("f_b", 0) and s.get("__fnvnx_guest_start") == 0, s)
    w = words(r["elf"]["path"])
    check("crt0 header words written: +8 FNXG, +16 link base 0, +20 page aligned span, +24 flags 1 (PIE)",
          w[2] == 0x47584e46 and w[4] == 0 and w[5] and w[5] % 0x1000 == 0 and w[6] == 1, w)
    ids = r["link_roots"]
    check("objects.json lists the input root with the digest of its objects.json content and its linked objects (kept beside the result)",
          len(ids) == 1 and ids[0]["root"] == LIB and re.fullmatch("[0-9a-f]{64}", ids[0]["sha256"]) and r["objects"][0]["unit"] == "a.c"
          and [o["unit"] for o in ids[0]["objects"]] == ["l.c"] and os.path.isfile(ids[0]["objects"][0]["object"]), ids)
    g = json.load(open(r["ld_gate"]["ld.json"]["path"]))
    check("the gate file `ld` is green and an output of the build", g["status"] == "green" and g["name"] == "ld" and g["universe"] == g["certified"] > 2, g)
    r2 = build(repo, SS, BASE + "/o-two2")
    check("second build: every stage a cache hit, same ELF, same input digest", r2["stages"]["run"] == 0 and r2["elf"]["sha256"] == r["elf"]["sha256"]
          and digests(r2) == digests(r), r2["stages"])
    bad = mk("two-red", main_rec(roots=[LIB]), libs=[(LIB, (LIB_REC, {"l.c": "int f_b(void) { return undeclared_thing; }\n"}))])
    rb = build(bad, SS)
    check("an input root that is red makes the link red: stage ld:<root> naming the red input root", rb["status"] == "red"
          and rb["failure"]["stage"] == "ld:" + ROOT and any(LIB in x for x in rb["failure"]["stderr"]) and rb["elf"] is None, err_text(rb))

    # (b) a weak undefined reference: the linker resolves it to 0 and drops it; the inputs' symbol tables show it
    W = {"toy/w.c": 'extern void wf(void) __attribute__((weak));\nvoid f_w(void) { if (wf) wf(); }\n', "toy/b.c": "int f_b(void) { return 1; }\n"}
    rw = build(mk("weak", main_rec(), W), SS)
    check("weak undefined reference: red at ldcheck:<root>, naming it", rw["status"] == "red" and rw["failure"]["stage"] == "ldcheck:" + ROOT
          and any("weak:wf" in x for x in rw["failure"]["stderr"]) and rw["elf"] is None, err_text(rw))
    # (c) an R_AARCH64_P32_ABS32 in .rodata of a position independent link
    A = {"toy/r.c": '__asm__(".section .rodata\\n.balign 4\\n.word f_b\\n.text\\n");\n', "toy/b.c": "int f_b(void) { return 1; }\n"}
    rr = build(mk("abs32", main_rec(), A), SS)
    check("R_AARCH64_P32_ABS32 in .rodata: red (the linker refuses a read-only dynamic relocation, -z text), no ELF", rr["status"] == "red"
          and rr["failure"]["stage"] in ("ld:" + ROOT, "ldcheck:" + ROOT) and rr["elf"] is None, err_text(rr))
    # (d) the crt0 header: wrong magic, and no crt0 at all
    B = {"toy/b.c": "int f_b(void) { return 1; }\n"}
    rm = build(mk("nomagic", main_rec(), dict(B, **{"toy/a.c": A_C.replace("0x47584e46", "0x12345678")})), SS)
    check("a missing FNXG magic: red at ldcheck:<root>, header id", rm["status"] == "red" and rm["failure"]["stage"] == "ldcheck:" + ROOT
          and any("header:" in x and "FNXG" in x for x in rm["failure"]["stderr"]), err_text(rm))
    rn = build(mk("nocrt0", main_rec(), dict(B, **{"toy/a.c": A_C.replace(TR.HDR, "")})), SS)
    check("no crt0 header at all (no entry symbol): red", rn["status"] == "red" and rn["failure"]["stage"] in ("ld:" + ROOT, "ldcheck:" + ROOT), err_text(rn))

    # (e) validation: cycle, self, link of an input root, not a product root, flags, libs
    def errs(rec, libs, name):
        repo = mk(name, rec, libs=libs)
        return recipe.check(rec, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    cyc = errs(main_rec(roots=[LIB]), [(LIB, (dict(LIB_REC, link=dict(TR.RECIPE["link"], roots=[ROOT])), {"l.c": "int f_b(void) { return 1; }\n"}))], "cycle")
    check("link.roots: a cycle is a check error, listing the cycle", any("cycle" in e and ROOT in e and LIB in e for e in cyc), cyc)
    check("link.roots: a root that links is no input root", any("has a link of its own" in e for e in cyc), cyc)
    e2 = errs(main_rec(roots=[ROOT, T + "verify", LIB, LIB]), [LB], "badroots")
    check("link.roots: itself, a tool root and a duplicate are errors, all listed at once",
          any("the root itself" in e for e in e2) and any("not an admitted product root" in e for e in e2) and any("listed twice" in e for e in e2), e2)
    e3 = errs(main_rec(flags=["-m", "aarch64linux32", "--gc-sections", "-e", "x"]), [], "flags")
    check("link.flags: only the emulation; --gc-sections and -e are errors", sum("not an emulation option" in e for e in e3) == 3 and
          any("--gc-sections" in e for e in e3) and any("-e is" in e or "-e " in e for e in e3), e3)
    check("link.flags: the emulation is required", any("emulation" in e for e in errs(main_rec(flags=[]), [], "noemu")))
    e5 = errs(main_rec(libs={"external": "nope", "dirs": ["/abs"], "names": ["a b"], "startfiles": [], "endfiles": []}), [], "badlibs")
    check("link.libs: a malformed entry is an error", any("link.libs" in e for e in e5), e5)
    e6 = errs(main_rec(libs={"external": "nope", "dirs": ["lib"], "names": ["x"], "startfiles": [], "endfiles": []}), [], "nolibext")
    check("link.libs.external must name an external", any("names no external" in e for e in e6), e6)

    # (g) link.libs: the guest libraries of an external, searched
    def populate(d):
        os.makedirs(d + "/lib")
        open(d + "/lib/t.c", "w").write("int lib_fn(void) { return 3; }\n")
        subprocess.run([CC, "-mabi=ilp32", "-fPIE", "-O1", "-ffreestanding", "-c", d + "/lib/t.c", "-o", d + "/lib/t.o"], check=True)
        subprocess.run(["aarch64-linux-gnu-ar", "rcsD", d + "/lib/libtoy.a", d + "/lib/t.o"], check=True)
        os.remove(d + "/lib/t.o")
        os.remove(d + "/lib/t.c")
    commit, thash = TR.publish("gl", populate)
    ex = {"name": "gl", "repo": "fnv-nx-build", "ref": "gl", "commit": commit, "tree_sha256": thash, "at": "/ext/gl"}
    L = {"toy/b.c": None, "toy/a.c": TR.HDR + '#include "toy.h"\nint lib_fn(void);\nint f_a(void) { return lib_fn() + TOY; }\n'}
    libs = {"external": "gl", "dirs": ["lib"], "names": ["toy"], "startfiles": [], "endfiles": []}
    rl = build(mk("libs", dict(main_rec(libs=libs), external=[ex]), L), SS)
    check("link.libs: a member of a guest library of an external is linked where an input needs it", rl["status"] == "green"
          and "lib_fn" in syms(rl["elf"]["path"]), err_text(rl))
    rl2 = build(mk("libs-miss", dict(main_rec(libs=dict(libs, names=["nope"])), external=[ex]), L), SS)
    check("link.libs: a library that is not there is a red ld:<root>", rl2["status"] == "red" and rl2["failure"]["stage"] == "ld:" + ROOT, err_text(rl2))
    rl3 = build(mk("libs-none", main_rec(), L), SS)
    check("without the library the same undefined reference is red (ld)", rl3["status"] == "red" and rl3["failure"]["stage"] == "ld:" + ROOT, err_text(rl3))

    # (f) determinism: two cold builds, two stores, the same bytes
    c1 = recipe.build(repo, ROOT, BASE + "/cold1", BASE + "/oc1")
    c2 = recipe.build(repo, ROOT, BASE + "/cold2", BASE + "/oc2")
    check("two cold builds (two stores): the same ELF bytes, objects and input digests",
          c1["status"] == c2["status"] == "green" and open(c1["elf"]["path"], "rb").read() == open(c2["elf"]["path"], "rb").read()
          and digests(c1) == digests(c2) and c1["elf"]["sha256"] == r["elf"]["sha256"], (c1["stages"], c2.get("failure")))

    # no subprocess call of a linker or compiler outside the recipe is left in link/
    leaks = subprocess.run(["grep", "-rnE", r"subprocess|os\.system|os\.exec", "--include=*.py", "--exclude-dir=tests", os.path.join(TR.VERIFY, "..", "link")], capture_output=True, text=True).stdout
    check("link/ holds no subprocess call (the linker runs in the recipe's stage only)", leaks.strip() == "", leaks)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
