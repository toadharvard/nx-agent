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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

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
    case("link.roots: two product roots, the second's objects are link inputs after the first's; green", r["status"] == "green" and r["elf"], err_text(r))
    s = syms(r["elf"]["path"])
    case("the ELF defines f_a (own unit) before f_b (input root), crt0 at 0", s.get("f_a", 1) < s.get("f_b", 0) and s.get("__fnvnx_guest_start") == 0, s)
    w = words(r["elf"]["path"])
    case("crt0 header words written: +8 FNXG, +16 link base 0, +20 page aligned span, +24 flags 1 (PIE)",
          w[2] == 0x47584e46 and w[4] == 0 and w[5] and w[5] % 0x1000 == 0 and w[6] == 1, w)
    ids = r["link_roots"]
    case("objects.json lists the input root with the digest of its objects.json content and its linked objects (kept beside the result)",
          len(ids) == 1 and ids[0]["root"] == LIB and re.fullmatch("[0-9a-f]{64}", ids[0]["sha256"]) and r["objects"][0]["unit"] == "a.c"
          and [o["unit"] for o in ids[0]["objects"]] == ["l.c"] and os.path.isfile(ids[0]["objects"][0]["object"]), ids)
    g = json.load(open(r["ld_gate"]["ld.json"]["path"]))
    case("the gate file `ld` is green and an output of the build", g["status"] == "green" and g["name"] == "ld" and g["universe"] == g["certified"] > 2, g)
    r2 = build(repo, SS, BASE + "/o-two2")
    case("second build: every stage a cache hit, same ELF, same input digest", r2["stages"]["run"] == 0 and r2["elf"]["sha256"] == r["elf"]["sha256"]
          and digests(r2) == digests(r), r2["stages"])
    bad = mk("two-red", main_rec(roots=[LIB]), libs=[(LIB, (LIB_REC, {"l.c": "int f_b(void) { return undeclared_thing; }\n"}))])
    rb = build(bad, SS)
    case("an input root that is red makes the link red: stage ld:<root> naming the red input root", rb["status"] == "red"
          and rb["failure"]["stage"] == "ld:" + ROOT and any(LIB in x for x in rb["failure"]["stderr"]) and rb["elf"] is None, err_text(rb))

    # (b) a weak undefined reference: the linker resolves it to 0 and drops it; the inputs' symbol tables show it
    W = {"toy/w.c": 'extern void wf(void) __attribute__((weak));\nvoid f_w(void) { if (wf) wf(); }\n', "toy/b.c": "int f_b(void) { return 1; }\n"}
    rw = build(mk("weak", main_rec(), W), SS)
    case("weak undefined reference: red at ldcheck:<root>, naming it", rw["status"] == "red" and rw["failure"]["stage"] == "ldcheck:" + ROOT
          and any("weak:wf" in x for x in rw["failure"]["stderr"]) and rw["elf"] is None, err_text(rw))
    # (c) an R_AARCH64_P32_ABS32 in .rodata of a position independent link
    A = {"toy/r.c": '__asm__(".section .rodata\\n.balign 4\\n.word f_b\\n.text\\n");\n', "toy/b.c": "int f_b(void) { return 1; }\n"}
    rr = build(mk("abs32", main_rec(), A), SS)
    case("R_AARCH64_P32_ABS32 in .rodata: red (the linker refuses a read-only dynamic relocation, -z text), no ELF", rr["status"] == "red"
          and rr["failure"]["stage"] in ("ld:" + ROOT, "ldcheck:" + ROOT) and rr["elf"] is None, err_text(rr))
    # (d) the crt0 header: wrong magic, and no crt0 at all
    B = {"toy/b.c": "int f_b(void) { return 1; }\n"}
    rm = build(mk("nomagic", main_rec(), dict(B, **{"toy/a.c": A_C.replace("0x47584e46", "0x12345678")})), SS)
    case("a missing FNXG magic: red at ldcheck:<root>, header id", rm["status"] == "red" and rm["failure"]["stage"] == "ldcheck:" + ROOT
          and any("header:" in x and "FNXG" in x for x in rm["failure"]["stderr"]), err_text(rm))
    rn = build(mk("nocrt0", main_rec(), dict(B, **{"toy/a.c": A_C.replace(TR.HDR, "")})), SS)
    case("no crt0 header at all (no entry symbol): red", rn["status"] == "red" and rn["failure"]["stage"] in ("ld:" + ROOT, "ldcheck:" + ROOT), err_text(rn))

    # (e) validation: cycle, self, link of an input root, not a product root, flags, libs
    def errs(rec, libs, name):
        repo = mk(name, rec, libs=libs)
        return recipe.check(rec, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    cyc = errs(main_rec(roots=[LIB]), [(LIB, (dict(LIB_REC, link=dict(TR.RECIPE["link"], roots=[ROOT])), {"l.c": "int f_b(void) { return 1; }\n"}))], "cycle")
    case("link.roots: a cycle is a check error, listing the cycle", any("cycle" in e and ROOT in e and LIB in e for e in cyc), cyc)
    case("link.roots: a root that links is no input root", any("has a link of its own" in e for e in cyc), cyc)
    e2 = errs(main_rec(roots=[ROOT, T + "verify", LIB, LIB]), [LB], "badroots")
    case("link.roots: itself, a tool root and a duplicate are errors, all listed at once",
          any("the root itself" in e for e in e2) and any("is a tool root" in e and "product root" in e for e in e2) and any("listed twice" in e for e in e2), e2)
    e3 = errs(main_rec(flags=["-m", "aarch64linux32", "--gc-sections", "-e", "x"]), [], "flags")
    case("link.flags: only the emulation; --gc-sections and -e are errors", sum("not an emulation option" in e for e in e3) == 3 and
          any("--gc-sections" in e for e in e3) and any("-e is" in e or "-e " in e for e in e3), e3)
    case("link.flags: the emulation is required", any("emulation" in e for e in errs(main_rec(flags=[]), [], "noemu")))
    e5 = errs(main_rec(libs={"external": "nope", "dirs": ["/abs"], "names": ["a b"], "startfiles": [], "endfiles": []}), [], "badlibs")
    case("link.libs: a malformed entry is an error", any("link.libs" in e for e in e5), e5)
    e6 = errs(main_rec(libs={"external": "nope", "dirs": ["lib"], "names": ["x"], "startfiles": [], "endfiles": []}), [], "nolibext")
    case("link.libs.external must name an external", any("names no external" in e for e in e6), e6)

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
    case("link.libs: a member of a guest library of an external is linked where an input needs it", rl["status"] == "green"
          and "lib_fn" in syms(rl["elf"]["path"]), err_text(rl))
    rl2 = build(mk("libs-miss", dict(main_rec(libs=dict(libs, names=["nope"])), external=[ex]), L), SS)
    case("link.libs: a library that is not there is a red ld:<root>", rl2["status"] == "red" and rl2["failure"]["stage"] == "ld:" + ROOT, err_text(rl2))
    rl3 = build(mk("libs-none", main_rec(), L), SS)
    case("without the library the same undefined reference is red (ld)", rl3["status"] == "red" and rl3["failure"]["stage"] == "ld:" + ROOT, err_text(rl3))


    # (k) kinds: exe and shared host images (LP64), and the target of the link
    def host_rec(kind, **kw):
        r = TR.mut(os_layer=[], constants=None, include=[ROOT + "/inc"], flags={"c": ["-O1", "-ffreestanding", "-fPIC", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"],
                                                                       "cxx": [], "asm": []})
        r.pop("generated")
        r["link"] = dict({"output": "x.exe" if kind == "exe" else "x.so", "kind": kind, "flags": ["-m", "aarch64linux"]}, **kw)
        return r
    NONE = {"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/link.ld": None}
    re_ = build(mk("exe", host_rec("exe"), dict(NONE, **{"toy/e.c": "void _start(void) { for (;;) ; }\n"})), SS)
    case("kind exe: a host executable (ELF64, no script, entry set) links green; no crt0 header rule", re_["status"] == "green"
          and TR.elf_class(re_["elf"]["path"]) == 64 and json.load(open(re_["ld_gate"]["ld.json"]["path"]))["status"] == "green", err_text(re_))
    SH = {"toy/s.c": "int f_a(void) { return 1; }\nint f_hidden(void) { return 2; }\n", "toy/exp.map": "{ global: f_a; local: *; };\n"}
    rs = build(mk("shared", host_rec("shared", version_script="exp.map"), dict(NONE, **SH)), SS)
    d = syms(rs["elf"]["path"]) if rs["status"] == "green" else {}
    case("kind shared: a shared object with a version script links green; the exported name is defined", rs["status"] == "green" and "f_a" in d, err_text(rs))
    rs2 = build(mk("shared-bad", host_rec("shared", version_script="exp.map"), dict(NONE, **dict(SH, **{"toy/exp.map": "{ global: f_a; nothere; local: *; };\n"}))), SS)
    case("kind shared: a name the version script exports that is not defined is red at ldcheck:<root>", rs2["status"] == "red"
          and rs2["failure"]["stage"] == "ldcheck:" + ROOT and any("export:nothere" in x for x in rs2["failure"]["stderr"]), err_text(rs2))
    UND = dict(NONE, **{"toy/s.c": "int so_fn(void);\nint f_a(void) { return so_fn(); }\n", "toy/exp.map": "{ global: f_a; local: *; };\n"})
    rs3 = build(mk("shared-und", host_rec("shared", version_script="exp.map"), UND), SS)
    case("kind shared: an undefined symbol no library defines is red at ldcheck:<root>", rs3["status"] == "red" and rs3["failure"]["stage"] == "ldcheck:" + ROOT
          and any("so_fn" in x for x in rs3["failure"]["stderr"]), err_text(rs3))
    so = BASE + "/libtoyso.so"
    open(BASE + "/so.c", "w").write("int so_fn(void) { return 5; }\n")
    subprocess.run([CC, "-shared", "-nostdlib", "-fPIC", "-o", so, BASE + "/so.c"], check=True)
    rs4 = build(mk("shared-lib", host_rec("shared", version_script="exp.map", libs={"system": [so]}), UND), SS)
    case("kind shared: link.libs.system defines the name through its .dynsym: green", rs4["status"] == "green", err_text(rs4))
    e7 = errs(host_rec("shared"), [], "novs")
    case("kind shared needs a version_script, a pie needs a script, a pie takes aarch64linux32 only", any("version_script" in e for e in e7)
          and any("a pie" in e or "pie" in e for e in errs(main_rec(flags=["-m", "aarch64linux"]), [], "piemu")), e7)
    rt = build(mk("target", dict(main_rec(), flags=TR.RECIPE["flags"] and {"c": ["-O1", "-ffreestanding", "-fPIE"], "cxx": [], "asm": []}), {"toy/b.c": "int f_b(void) { return 1; }\n"}), SS)
    case("a link whose objects have another target (LP64 objects, ILP32 emulation): red, naming the object", rt["status"] == "red"
          and rt["failure"]["stage"] == "ld:" + ROOT and any("a.c" in x and "target" in x for x in rt["failure"]["stderr"]), err_text(rt))

    # (m) link.libs archive of an external: the members the linker pulls are recorded (and kept as objects); an unused member is not
    def libpop(d, m1="int f_b(void) { return 21; }\n"):
        os.makedirs(d + "/lib")
        for n, body in (("m1", m1), ("m2", "int f_unused(void) { return 1; }\n")):
            open("%s/%s.c" % (d, n), "w").write(body)
            subprocess.run([CC, "-mabi=ilp32", "-fPIE", "-O1", "-ffreestanding", "-c", "-o", "%s/%s.o" % (d, n), "%s/%s.c" % (d, n)], check=True)
            os.remove("%s/%s.c" % (d, n))
        subprocess.run(["aarch64-linux-gnu-ar", "rcs", d + "/lib/libtoy.a", d + "/m1.o", d + "/m2.o"], check=True)
        os.remove(d + "/m1.o")
        os.remove(d + "/m2.o")
    lc, lh = TR.publish("tlib", libpop)
    lx = {"name": "tlib", "repo": "fnv-nx-build", "ref": "tlib", "commit": lc, "tree_sha256": lh, "at": "/ext/tlib"}
    arec = dict(main_rec(libs={"external": "tlib", "dirs": ["lib"], "names": ["toy"], "startfiles": [], "endfiles": []}), external=[lx])
    arepo = mk("archive", arec, {"toy/b.c": None})
    rl = build(arepo, SS)
    la = rl.get("link_archives", [])
    case("link.libs archive: the member the linker pulled (m1.o, for f_b) is recorded with archive path, archive sha256, member bytes sha256 and a kept object; "
          "the unused member m2.o is not", rl["status"] == "green" and [(m["archive"], m["member"]) for m in la] == [("/ext/tlib/lib/libtoy.a", "m1.o")]
          and re.fullmatch("[0-9a-f]{64}", la[0]["sha256"]) and os.path.isfile(la[0]["object"]) and "m2.o" not in repr(la), err_text(rl) + repr(la))
    ea = errs(dict(arec, link=dict(arec["link"], libs={"external": "tlib", "dirs": ["../x"], "names": ["toy"], "startfiles": [], "endfiles": []})), [], "outside")
    case("link.libs: a directory outside the external is refused (an archive must lie inside a declared external)", any("link.libs" in e for e in ea), ea)

    rc_, lsr, _, lso = TR.gate(arepo, "linkstrict", BASE + "/gate-ls", BASE + "/gate-cache")
    rc2, osr, _, oso = TR.gate(arepo, "objstub", BASE + "/gate-os", BASE + "/gate-cache")
    case("link.libs archive: linkstrict and objstub judge the pulled member as an object (their universe counts its symbols) and accept the root",
          rc_ == 0 and lsr and lsr["status"] == "green" and lsr["universe"] >= 3 and osr and osr["universe"] >= 1, (lsr, osr, lso[-300:], oso[-300:]))
    # a pulled member that is a stub (a trap body) is red in objstub; one with an undefined symbol nothing defines is a red build (and so red gates)
    def archive_repo(name, body):
        c, h = TR.publish("tlib-" + name, lambda d: libpop(d, body))
        x = dict(lx, name="tlib", ref=name, commit=c, tree_sha256=h)
        return mk("archive-" + name, dict(arec, external=[x]), {"toy/b.c": None})
    rstub = archive_repo("stub", "int f_b(void) { __builtin_trap(); }\n")
    _, osr2, _, oso2 = TR.gate(rstub, "objstub", BASE + "/gate-os2", BASE + "/gate-cache2")
    case("link.libs archive: a pulled member that is a stub (trap body) makes objstub red", build(rstub, SS)["status"] == "green" and osr2 and osr2["status"] == "red", (osr2, oso2[-300:]))
    rund = archive_repo("undef", "int nowhere(void);\nint f_b(void) { return nowhere(); }\n")
    rr2 = build(rund, SS)
    _, lsr2, _, _ = TR.gate(rund, "linkstrict", BASE + "/gate-ls2", BASE + "/gate-cache2")
    case("link.libs archive: a pulled member with an undefined symbol nothing defines: the link is red, and so is linkstrict",
          rr2["status"] == "red" and lsr2 and lsr2["status"] == "red", (err_text(rr2), lsr2))
    # (n) the link check that objects.json equals the global and weak symbols read from the objects themselves (a second ELF reader: ldcheck's)
    o0 = rl["objects"][0]
    d0 = [d for d in rl["defined"] if d["object"] == o0["unit"]]
    objs0 = [(o0["unit"], o0["object"])]
    case("objects.json vs objects: the recorded list equals what the object defines", d0 and recipe.defined_mismatch("t", d0, objs0) == [], d0)
    case("objects.json vs objects: a symbol listed but not defined, and a defined one not listed, are both findings",
          len(recipe.defined_mismatch("t", d0 + [dict(d0[0], symbol="ghost")], objs0)) == 1 and len(recipe.defined_mismatch("t", d0[1:], objs0)) == 1 and len(d0) >= 1, d0)

    # (l) variants: one root, an aarch64 LP64 variant and an x86_64 variant, linked into two images; a wrong-target link is red
    FL = ["-O1", "-ffreestanding", "-fPIC", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"]
    X86 = {"cc": "x86_64-linux-gnu-gcc-13", "cxx": "x86_64-linux-gnu-g++-13"}
    VROOT = T + "vlib"
    vlib = dict(LIB_REC, flags={"c": ["-mabi=ilp32", "-fPIE"] + FL[:0] + ["-O1", "-ffreestanding"], "cxx": [], "asm": []},
                variants={"lp64": {"flags": {"c": FL, "cxx": [], "asm": []}}, "x86": {"compiler": X86, "flags": {"c": FL, "cxx": [], "asm": []}},
                          "lp64b": {"flags": {"c": FL, "cxx": [], "asm": []}}})
    VF = {"l.c": "int f_b(void) { return 14; }\n"}

    def image(name, emu, roots, cc=None):
        r = dict(host_rec("exe"), link={"output": name + ".exe", "kind": "exe", "flags": ["-m", emu], "roots": roots})
        r.pop("constants", None)
        if cc:
            r["compiler"] = cc
        return r
    IMG = {"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/link.ld": None,
           "toy/e.c": "int f_b(void);\nvoid _start(void) { f_b(); for (;;) ; }\n"}
    IB, IC = T + "imgx", T + "imgbad"
    vrepo = mk("variants", image("toy", "aarch64linux", [VROOT + "@lp64"]), IMG,
              libs=[(VROOT, (vlib, VF)), (IB, (dict(image("imgx", "elf_x86_64", [VROOT + "@x86"], X86), flags={"c": FL, "cxx": [], "asm": []}), {"e.c": IMG["toy/e.c"]})),
                    (IC, (image("imgbad", "aarch64linux", [VROOT + "@x86"]), {"e.c": IMG["toy/e.c"]}))])
    ra = build(vrepo, SS)
    case("variants: image A (aarch64 LP64) links the lp64 variant of the root; green, the input is listed as root@lp64", ra["status"] == "green"
          and [x["root"] for x in ra["link_roots"]] == [VROOT + "@lp64"] and TR.elf_class(ra["elf"]["path"]) == 64, err_text(ra))
    rx = recipe.build(vrepo, IB, SS, BASE + "/o-imgx")
    case("variants: image B (x86_64) links the x86 variant of the same root; green, e_machine 62",
          rx["status"] == "green" and open(rx["elf"]["path"], "rb").read()[18:20] == (62).to_bytes(2, "little"), err_text(rx))
    rbad = recipe.build(vrepo, IC, SS, BASE + "/o-imgbad")
    case("variants: a wrong-target link (x86_64 objects into an aarch64 image) is red, naming the object and both targets", rbad["status"] == "red"
          and rbad["failure"]["stage"] == "ld:" + IC and any("l.c" in x and "target" in x for x in rbad["failure"]["stderr"]), err_text(rbad))
    vl = recipe.build(vrepo, VROOT + "@lp64", SS, BASE + "/o-vl")
    vb = recipe.build(vrepo, VROOT + "@lp64b", SS, BASE + "/o-vlb")
    v0 = recipe.build(vrepo, VROOT, SS, BASE + "/o-v0")
    case("variants: each (root, variant) is its own build: root, root@lp64, root@lp64b green with objects of their own; the root keeps its name in the key",
          all(x["status"] == "green" for x in (vl, vb, v0)) and v0["root"] == VROOT and vl["root"] == VROOT + "@lp64"
          and not set(vl["stage_keys"]) & set(vb["stage_keys"]) and not set(vl["stage_keys"]) & set(v0["stage_keys"]), (vl["stage_keys"][:2], vb["stage_keys"][:2]))
    case("variants: two variants with the same effective recipe still differ in every stage key (the variant name is in the key)",
          vl["objects"][0]["sha256"] == vb["objects"][0]["sha256"] and vl["stage_keys"] != vb["stage_keys"], "")
    adm = recipe.load_admit(vrepo)
    case("variants: the effective recipe replaces only the keys the variant names", recipe.load_recipe(vrepo, adm, VROOT + "@x86")["compiler"] == X86
          and recipe.load_recipe(vrepo, adm, VROOT + "@x86")["include"] == vlib["include"] and "variants" not in recipe.load_recipe(vrepo, adm, VROOT), "")
    ev = errs(image("toy", "aarch64linux", [VROOT + "@nope"]), [(VROOT, (vlib, VF))], "novariant")
    case("variants: link.roots naming a variant the root does not declare is a check error", any("no variant" in e for e in ev), ev)
    ev2 = recipe.check(dict(vlib, variants={"a b": {"flags": {}}, "c": {"link": {}}}), VROOT, recipe.tracked(vrepo), adm, None, vrepo)
    case("variants: a bad name or a key other than compiler/flags is a check error", any("variants must be" in e for e in ev2), ev2)

    # (f) determinism: two cold builds, two stores, the same bytes
    c1 = recipe.build(repo, ROOT, BASE + "/cold1", BASE + "/oc1")
    c2 = recipe.build(repo, ROOT, BASE + "/cold2", BASE + "/oc2")
    case("two cold builds (two stores): the same ELF bytes, objects and input digests",
          c1["status"] == c2["status"] == "green" and open(c1["elf"]["path"], "rb").read() == open(c2["elf"]["path"], "rb").read()
          and digests(c1) == digests(c2) and c1["elf"]["sha256"] == r["elf"]["sha256"], (c1["stages"], c2.get("failure")))

    # no subprocess call of a linker or compiler outside the recipe is left in link/
    leaks = subprocess.run(["grep", "-rnE", r"subprocess|os\.system|os\.exec", "--include=*.py", "--exclude-dir=tests", os.path.join(TR.VERIFY, "..", "link")], capture_output=True, text=True).stdout
    case("link/ holds no subprocess call (the linker runs in the recipe's stage only)", leaks.strip() == "", leaks)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
