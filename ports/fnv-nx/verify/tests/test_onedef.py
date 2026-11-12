#!/usr/bin/env python3
"""Tests for verify/gate_onedef.py (gate "onedef", clauses (a) (b) (c') (c'')): the pure judges, and the gate itself in toy repos built with the real
compiler and linker through verify/recipe.py (images that link two library roots). Plain script: exit 0 on pass. Needs root (sandbox).
Scratch: test_recipe's BASE, removed at exit."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402

VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_onedef as go  # noqa: E402

T, BASE = TR.T, TR.BASE
FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else ": %s" % (str(detail)[:400],)))
    if not ok:
        FAILED.append(name)


FL = ["-O1", "-ffreestanding", "-fPIC", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"]
LIB_REC = {"format": "fnvnx-recipe/1", "compiler": TR.RECIPE["compiler"], "flags": {"c": FL, "cxx": [], "asm": []}, "include": [], "units": "all"}
IMG = dict(LIB_REC, include=[], link={"output": "x.exe", "kind": "exe", "flags": ["-m", "aarch64linux"], "roots": [T + "lib1", T + "lib2"]})
NONE = {"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/link.ld": None, "toy/constants.tsv": None, "gen/gen.py": None,
        "toy/inc/toy.h": None, "toy/inc/gen.h": None}
START = "void _start(void) { for (;;) ; }\n"
COMDAT_S = '#include "h.inc"\n'
H_INC = '.section .text.h,"axG",@progbits,h,comdat\n.weak h\n.type h,%function\nh: mov w0, #K\n ret\n.size h,.-h\n'


def mkrepo(name, libs, extra=None, img=IMG):
    """libs {path: (recipe, {file: text})}; extra {repo file: text} (e.g. headers); the image is the toy root."""
    repo = TR.make_repo(name, rec=img, files=dict(NONE, **{"toy/e.c": START}))
    adm = json.load(open(repo + "/" + T + "verify/admit.json"))
    for path, (r, fs) in libs.items():
        adm["roots"].append({"path": path, "class": "product", "target": "host", "gates": [], "recipe": path + "/build.json"})
        TR.w(repo, path[len(T):] + "/build.json", json.dumps(r))
        for f, text in fs.items():
            TR.w(repo, path[len(T):] + "/" + f, text)
    if any(p.startswith(T + "hdr") for p in (extra or {})) or any(T + "hdr" in r["include"] for r, _ in libs.values()):
        adm["roots"].append({"path": T + "hdr", "class": "doc", "gates": []})
    for f, text in (extra or {}).items():
        TR.w(repo, f[len(T):], text)
    adm["roots"].sort(key=lambda x: x["path"])
    TR.w(repo, "verify/admit.json", json.dumps(adm))
    TR.git(repo, "add", "-A")
    TR.git(repo, "commit", "-q", "-m", "roots")
    return repo


def run(repo, tag):
    rc, r, _, out = TR.gate(repo, "onedef", BASE + "/go-" + tag, BASE + "/gc-" + tag)
    return r, out


def kinds(r):
    return sorted({u.split(":")[0] for u in r["uncertified"]}) if r else None


def main():
    # pure judges
    its, fs = go.judge_image("img", {"a": ([("f", 1, None)], {}), "b": ([("f", 2, None), ("g", 1, None)], {})})
    check("(a) a name defined by two roots, strong and weak, is dup-def; a name of one root is certified",
          [(f[1], f[2]) for f in fs] == [("f", "dup-def")] and ("img", "g") in its, fs)
    its, fs = go.judge_image("img", {"a": ([("h", 2, "h")], {"h": "D1"}), "b": ([("h", 2, "h")], {"h": "D1"})})
    check("(a)/(b) the same COMDAT instance in two roots with one digest is green", not fs, fs)
    its, fs = go.judge_image("img", {"a": ([("h", 2, "h")], {"h": "D1"}), "b": ([("h", 2, "h")], {"h": "D2"})})
    check("(b) COMDAT instances of two roots with two digests are comdat-differs", [f[2] for f in fs] == ["comdat-differs"], fs)
    its, fs = go.judge_image("img", {"a": ([("h", 2, "h")], {"h": "D1"})})
    check("(b) an instance of one root needs no comparison", not fs, fs)
    its, fs = go.judge_files({"r1/x.c": ("r1", "S"), "r2/y.c": ("r2", "S"), "r1/z.c": ("r1", "Z")}, {"i/a.h": "H", "j/b.h": "H", "k/c.h": "K"})
    check("(c') a unit whose content is a unit of another root is unit-copy; (c'') a header under two paths is header-copy",
          sorted((f[1], f[2]) for f in fs) == [("i/a.h", "header-copy"), ("j/b.h", "header-copy"), ("r1/x.c", "unit-copy"), ("r2/y.c", "unit-copy")], fs)
    its, fs = go.judge_files({"r1/x.c": ("r1", "S"), "r1/w.c": ("r1", "S")}, {})
    check("(c') two units of ONE root with one content are not a copy across roots", not fs, fs)

    # the gate in toy repos
    L1 = {"a.c": "int fa(void) { return 1; }\n"}
    L2 = {"b.c": "int fb(void) { return 2; }\n"}
    r, out = run(mkrepo("one", {T + "lib1": (LIB_REC, L1), T + "lib2": (LIB_REC, L2)}), "one")
    check("one definition per name across two linked roots: green", r and r["status"] == "green", (r, out[-300:]))
    r, out = run(mkrepo("strong", {T + "lib1": (LIB_REC, {"a.c": "int dup(void) { return 1; }\n"}), T + "lib2": (LIB_REC, {"b.c": "int dup(void) { return 2; }\n"})}), "strong")
    check("a strong duplicate in two roots is red (dup-def)", r and r["status"] == "red" and "dup-def" in r["detail"], (r, out[-300:]))
    wk = "__attribute__((weak)) int dup(void) { return %d; }\n"
    r, out = run(mkrepo("weak", {T + "lib1": (LIB_REC, {"a.c": wk % 1}), T + "lib2": (LIB_REC, {"b.c": wk % 2})}), "weak")
    check("a weak duplicate in two roots is red (dup-def)", r and r["status"] == "red" and "dup-def" in r["detail"], (r, out[-300:]))
    r, out = run(mkrepo("mixed", {T + "lib1": (LIB_REC, {"a.c": "int dup(void) { return 1; }\n"}), T + "lib2": (LIB_REC, {"b.c": wk % 2})}), "mixed")
    check("a strong and a weak definition in two roots is red (dup-def)", r and r["status"] == "red" and "dup-def" in r["detail"], (r, out[-300:]))
    r, out = run(mkrepo("same", {T + "lib1": (LIB_REC, {"a.c": "int dup(void) { return 1; }\nint dup2(void) { return 3; }\n"}), T + "lib2": (LIB_REC, L2)}), "same")
    check("one root defining a name once is green", r and r["status"] == "green", (r, out[-300:]))

    hdr = {T + "hdr/h.inc": H_INC}
    HI = [T + "hdr"]
    shared = {T + "lib1": (dict(LIB_REC, include=HI, flags={"c": FL, "cxx": [], "asm": ["-DK=1"]}), {"a.S": COMDAT_S + "// lib1\n"}),
              T + "lib2": (dict(LIB_REC, include=HI, flags={"c": FL, "cxx": [], "asm": ["-DK=1"]}), {"b.S": COMDAT_S + "// lib2\n"})}
    r, out = run(mkrepo("comdat-ok", shared, hdr), "cok")
    check("a COMDAT from one shared header in two roots is green; one shared header included by two roots is green", r and r["status"] == "green", (r, out[-300:]))
    diff = {T + "lib1": shared[T + "lib1"], T + "lib2": (dict(LIB_REC, include=HI, flags={"c": FL, "cxx": [], "asm": ["-DK=2"]}), {"b.S": COMDAT_S + "// lib2\n"})}
    r, out = run(mkrepo("comdat-diff", diff, hdr), "cdiff")
    check("a COMDAT with different flags in two roots is red (comdat-differs)", r and r["status"] == "red" and "comdat-differs" in r["detail"], (r, out[-300:]))

    body = "int fcopy(void) { return 9; }\n"
    r, out = run(mkrepo("unitcopy", {T + "lib1": (LIB_REC, {"a.c": body}), T + "lib2": (LIB_REC, {"b.c": body})}), "uc")
    check("a source file copied into a second root (same sha256, new path) is red (unit-copy)", r and r["status"] == "red" and "unit-copy" in r["detail"], (r, out[-300:]))
    r, out = run(mkrepo("hdrcopy", {T + "lib1": (LIB_REC, L1), T + "lib2": (LIB_REC, L2)}, {T + "hdr/h.inc": H_INC, T + "lib1/copy.inc": H_INC}), "hc")
    check("a header copied under a new path is red (header-copy)", r and r["status"] == "red" and "header-copy" in r["detail"], (r, out[-300:]))

    import shutil
    shutil.rmtree(BASE, ignore_errors=True)
    print("FAILED: %s" % FAILED if FAILED else "all passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
