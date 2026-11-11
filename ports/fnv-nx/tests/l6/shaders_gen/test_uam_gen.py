#!/usr/bin/env python3
"""test_uam_gen.py: provenance of render/shaders/patches/uam_gen.patch (the nine files meson generates for uam, kept as tracked patch content).
  python3 -I tests/l6/shaders_gen/test_uam_gen.py --uam-tree DIR [--bison P] [--flex P] [--python P]
DIR = the pinned tools/uam tree (fnv-nx-build 5a5afc2b..., a git archive: no .git); it is copied, never written.
Steps: the tree's tree_sha256 equals the pin; uam.patch applied; the generation commands of patches/README.md run in a copy with the tool versions
recorded there (bison 3.8.2, flex 2.6.4, mako 1.4.3: the versions are checked and printed); the nine outputs equal, byte for byte, the files that
uam_gen.patch adds. Needs no game data. The tools are host tools of this test only; the build itself reads the patch."""
import hashlib, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SH = os.path.join(ROOT, "render", "shaders"); sys.path.insert(0, os.path.join(ROOT, "verify")); import cas  # noqa: E402
PIN_TREE = "8dbb502db95ccd73e5be58e83a4862be2067540ed5640b65364a71ef9d67201b"
GEN = [("glsl/glsl_parser.cpp", "glsl/glsl_parser.h"), ("glsl/glsl_lexer.cpp",), ("glsl/ir_expression_operation.h", "glsl/ir_expression_operation_constant.h", "glsl/ir_expression_operation_strings.h"),
       ("glsl/glcpp/glcpp-parse.c", "glcpp-parse.h"), ("glsl/glcpp/glcpp-lex.c",)]
FILES = ["glsl/glsl_parser.cpp", "glsl/glsl_parser.h", "glsl/glsl_lexer.cpp", "glsl/ir_expression_operation.h", "glsl/ir_expression_operation_constant.h",
         "glsl/ir_expression_operation_strings.h", "glsl/glcpp/glcpp-parse.c", "glsl/glcpp/glcpp-parse.h", "glsl/glcpp/glcpp-lex.c"]
fails = []


def check(ok, what):
    if not ok: fails.append(what); print("FAIL: " + what)
    return ok


def run(cmd, cwd, **kw): return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


def main():
    a = sys.argv[1:]; opt = dict(zip(a[::2], a[1::2]))
    tree = opt.get("--uam-tree") or sys.exit("usage: test_uam_gen.py --uam-tree DIR [--bison P] [--flex P] [--python P]")
    bison, flex, py = opt.get("--bison", "bison"), opt.get("--flex", "flex"), opt.get("--python", sys.executable)
    check(cas.tree_hash(tree) == PIN_TREE, "the tree is not the pinned tools/uam tree (tree_sha256 %s)" % PIN_TREE)
    v = run([bison, "--version"], ".").stdout.splitlines()[0]; check(v.endswith("3.8.2"), "bison is %r, not 3.8.2" % v); print(v)
    v = run([flex, "--version"], ".").stdout.strip(); check(v == "flex 2.6.4", "flex is %r, not 2.6.4" % v); print(v)
    mv = run([py, "-c", "import mako;print(mako.__version__)"], ".").stdout.strip(); check(mv == "1.4.3", "mako is %r, not 1.4.3" % mv); print("mako " + mv)
    with tempfile.TemporaryDirectory() as t:
        base, regen = os.path.join(t, "base"), os.path.join(t, "regen")
        shutil.copytree(tree, base); shutil.copytree(tree, regen)
        for d in (base, regen):
            r = run(["patch", "-p1", "-s", "-i", os.path.join(SH, "uam.patch")], d); check(r.returncode == 0, "uam.patch: " + r.stderr[-300:])
        r = run(["patch", "-p1", "-s", "-i", os.path.join(SH, "patches", "uam_gen.patch")], base); check(r.returncode == 0, "uam_gen.patch: " + r.stderr[-300:])
        g = os.path.join(regen, "mesa-imported", "glsl")
        cmds = [([bison, "-o", "glsl_parser.cpp", "-p", "_mesa_glsl_", "--defines=glsl_parser.h", "glsl_parser.yy"], g, None),
                ([flex, "-o", "glsl_lexer.cpp", "glsl_lexer.ll"], g, None)]
        for k, o in (("enum", "ir_expression_operation.h"), ("constant", "ir_expression_operation_constant.h"), ("strings", "ir_expression_operation_strings.h")):
            cmds.append(([py, "ir_expression_operation.py", k], g, o))
        gc = os.path.join(g, "glcpp")
        cmds += [([bison, "-o", "glcpp-parse.c", "-p", "glcpp_parser_", "--defines=glcpp-parse.h", "glcpp-parse.y"], gc, None), ([flex, "-o", "glcpp-lex.c", "glcpp-lex.l"], gc, None)]
        for cmd, cwd, out in cmds:
            r = run(cmd, cwd)
            if not check(r.returncode == 0, "%s: %s" % (" ".join(cmd[:2]), r.stderr[-300:])): continue
            if out: open(os.path.join(cwd, out), "w").write(r.stdout)
        n = 0
        for f in FILES:
            pa, pb = os.path.join(base, "mesa-imported", f), os.path.join(regen, "mesa-imported", f)
            ok = os.path.isfile(pa) and os.path.isfile(pb) and open(pa, "rb").read() == open(pb, "rb").read()
            n += check(ok, "%s: regenerated output differs from uam_gen.patch" % f)
        print("%d/%d generated files equal the patch content" % (n, len(FILES)))


main()
if fails: print("%d FAILED" % len(fails)); sys.exit(1)
print("ALL OK")
