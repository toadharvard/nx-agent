#!/usr/bin/python3.12
"""mkunits.py BUILD SRC LOG OUT - the units of GMP's library from the compile list ccrec.py recorded.
One unit per recorded compile, OUT/u/<object path>.c: the defines the Makefile gave that compile (-DNAME[=VALUE], as given, common ones first),
then the source text (the srcdir's file, or the build directory's own generated file), with a #line naming it. The headers of both trees go to OUT/inc/build and
OUT/inc/src with their layout (the recipe's include order is build, src, as `-I. -I../srcdir` was).
Every compile of the log must have the common flags of the first one (the -DOPERATION_x flags aside), else the tool stops."""
import json
import os
import re
import shutil
import sys

build, src, log, out = [os.path.realpath(p) for p in sys.argv[1:5]]
supplied = sys.argv[5:]   # -I directories the recipe supplies itself (include_uses: another root's headers): their flag is dropped, the include path of the root has them
rows = [json.loads(l) for l in open(log)]
if not rows:
    sys.exit("mkunits: empty compile list")
for r in rows:   # the object's path in the build tree, whatever directory the sub-make compiled in
    r["out"] = os.path.relpath(os.path.realpath(os.path.join(r["cwd"], r["out"])), build)
# Per compile: the defines (-D) become text of the unit; the -I directories must each be one the unit view has (the build tree, the source tree, the compile's own directory, the
# directory of its source: the headers of the last two are written beside the unit below, the first two are the recipe's `include`, or a directory the recipe supplies); -g and -O2 are
# the recipe's own flags. Anything else would be lost, so it is an error.
real = lambda r, d: os.path.realpath(os.path.join(r["cwd"], d))
def own_dirs(r):   # the compile's own directory in the build tree and in the source tree, and the directory of its source file
    rel = os.path.relpath(os.path.realpath(r["cwd"]), build)
    return {os.path.realpath(r["cwd"]), os.path.realpath(os.path.join(src, rel)), os.path.dirname(real(r, r["src"]))}
def check_flags(r):
    for f in r["flags"]:
        if f.startswith("-D") or f in ("-g", "-O2"):
            continue
        if f == "-ffloat-store":
            continue   # GCC manual (Optimize Options): -ffloat-store only keeps values from living in wider registers; the guest is aarch64, whose FP registers hold exactly the declared type (FLT_EVAL_METHOD 0), so it changes no result
        if f.startswith("-W") and not f.startswith(("-Werror", "-Wl,", "-Wa,", "-Wp,")):
            continue   # a warning option only chooses which diagnostics are printed, never the object code (GCC manual, "Options to Request or Suppress Warnings")
        if f.startswith("-I") and (f[2:] in supplied or real(r, f[2:]) in own_dirs(r) | {build, src}):
            continue
        sys.exit("mkunits: %s: the flag %s cannot be carried by a unit" % (r["out"], f))
# A quoted #include of a C source (mpfr/jn.c includes "jyn_asympt.c", GMP's mul_fft.c "generic/add_n_sub_n.c") cannot name a file of the unit view: every C file there is a unit.
# The included text is put in place of the line, exactly as the preprocessor does (the line's own #if context stays around it), with #line directives for both files. Searched as cc
# searches a quoted include: the including file's directory, then the compile's -I directories (own directories, source tree, build tree).
def where_of(p):
    return ("src/" + os.path.relpath(p, src)) if p.startswith(src + "/") else ("build/" + os.path.relpath(p, build))
INC_C = re.compile(rb'^[ \t]*#[ \t]*include[ \t]+"([^"]+\.c)"[^\n]*$', re.M)
def block_comment_starts(text):
    """{offset of each line start that lies inside a /* */ comment}: the scan C's translation phases give (comments, string and character literals, line comments); a #include
    inside a comment is no include."""
    inside, i, n, in_c = set(), 0, len(text), False
    while i < n:
        c = text[i:i + 1]
        if in_c:
            if text.startswith(b"*/", i):
                in_c, i = False, i + 2
                continue
        elif text.startswith(b"/*", i):
            in_c, i = True, i + 2
            continue
        elif text.startswith(b"//", i):
            j = text.find(b"\n", i)
            i = n if j < 0 else j
            continue
        elif c in (b'"', b"'"):
            j = i + 1
            while j < n and text[j:j + 1] != c and text[j:j + 1] != b"\n":
                j += 2 if text[j:j + 1] == b"\\" else 1
            i = j + 1
            continue
        if c == b"\n" and in_c:
            inside.add(i + 1)
        i += 1
    return inside
def inline_c(path, r, depth):
    if depth > 8:
        sys.exit("mkunits: C sources include each other too deep at %s" % path)
    text = open(path, "rb").read()
    def sub(m):
        name = m.group(1).decode()
        for d in [os.path.dirname(path)] + sorted(own_dirs(r)) + [src, build]:
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                lineno = text.count(b"\n", 0, m.start()) + 2
                return (b'#line 1 "%s"\n' % where_of(cand).encode() + inline_c(cand, r, depth + 1) + b'\n#line %d "%s"' % (lineno, where_of(path).encode()))
        return m.group(0)
    inside = block_comment_starts(text)
    return INC_C.sub(lambda m: m.group(0) if m.start() in inside else sub(m), text)
units, seen = {}, {}
for r in rows:
    check_flags(r)
    key = (r["out"], r["src"], tuple(f for f in r["flags"] if f.startswith("-D")))
    if key in seen:
        continue
    seen[key] = 1
    if r["out"] in units:
        sys.exit("mkunits: two different compiles write %s" % r["out"])
    units[r["out"]] = r
# A source that #includes a C source (mpn/generic/mul_fft.c under WANT_ADDSUB) is not examined: the unit's own preprocessing opens an included file only when its condition holds, and a
# C source there is no header of OUT/inc, so an active include of one is a red preprocessing stage, never a silent difference.
for o, r in sorted(units.items()):
    path = os.path.realpath(os.path.join(r["cwd"], r["src"]))
    text = inline_c(path, r, 0)
    where = where_of(path)
    defs = ["#define %s" % (f[2:].replace("=", " ", 1) if "=" in f else f[2:] + " 1") for f in r["flags"] if f.startswith("-D")]   # -DNAME is NAME defined as 1 (C99 6.10.2 as cc documents it)
    name = os.path.splitext(o)[0] + ".c"
    dest = os.path.join(out, "u", name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(("\n".join(defs + ['#line 1 "%s"' % where]) + "\n").encode() + text)
# A quoted #include resolves first in the directory of the including file (mpf/fits_sint.c includes its sibling "fits_s.h"). The unit is not in that directory, so the headers of each
# unit's source directory are written beside the unit. Two source directories that map to one unit directory with different headers of one name are a stop: the unit could not tell them apart.
sib = {}
for o, r in sorted(units.items()):
    udir = os.path.join(out, "u", os.path.dirname(o))
    for sdir in sorted(d for d in own_dirs(r) if os.path.isdir(d)):
        for dp, _dn, fns in sorted(os.walk(sdir)):   # a header found beside the unit may include one below its own directory (mpfr's mparam.h includes "generic/mparam.h")
            for fn in sorted(fns):
                if not fn.endswith(".h"):
                    continue
                rel = os.path.relpath(os.path.join(dp, fn), sdir)
                data = open(os.path.join(dp, fn), "rb").read()
                key = (udir, rel)
                if key in sib and sib[key] != data:
                    sys.exit("mkunits: %s/%s: two source directories give the unit directory different headers of this name" % (os.path.dirname(o), rel))
                if key not in sib:
                    sib[key] = data
                    os.makedirs(os.path.dirname(os.path.join(udir, rel)), exist_ok=True)
                    open(os.path.join(udir, rel), "wb").write(data)
for tag, root in (("build", build), ("src", src)):
    for d, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith(".h"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    dst = os.path.join(out, "inc", tag, os.path.relpath(p, root))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(p, dst)
print("mkunits: %d units, %d with per-file defines (wrapper units)" % (len(units), sum(1 for r in units.values() if any(f.startswith("-D") and f not in ("-DHAVE_CONFIG_H", "-D__GMP_WITHIN_GMP") for f in r["flags"]))))
