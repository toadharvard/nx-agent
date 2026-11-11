#!/usr/bin/env python3
"""test_gl4.py: tests of render/d3d9/backend (GL 4.5 backend v4, task B2a).  python3 -I tests/l6/gl4/test_gl4.py [--no-mutations]
 a  every NxFormat x {2D, Cube, 3D}: update every level/layer, read back byte-equal
 b  sub-box updates (pitched, offset, BCn blocks, 3D slices, cube faces), rejected updates
 c  Blit: copy, 2x point, linear vs CPU reference (<= 1 LSB), depth, MSAA resolve, conversion, swapchain
 d  GenMips vs CPU references (2D, NPOT, cube, 3D)
 e  ids reused only after destroy, mem/shader destroy, tables (version 3 while B2b is pending), malformed stream
 f  v3 ops: draws, UBO, vertex/index buffers, textures with mips, clears, readback
 g  mutation checks (single-line breaks in gl4_image.c) and the build gate (no B2b entries -> static assert)
Oracle = CPU code in tests/l6/gl4 (patterns, filters, format table); LP64 host build, identity guest addresses."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXINC = os.path.join(ROOT, "render", "native", "include")
ENV = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
TESTS = ["t_main.c", "t_parts_ab.c", "t_parts_cd.c", "t_parts_ef.c"]
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(backend, out, defs=("-DNXGL4_B2B_PENDING",)):
    srcs = sorted(os.path.join(backend, f) for f in os.listdir(backend) if f.startswith("gl4_") and f.endswith(".c"))
    srcs += [os.path.join(HERE, t) for t in TESTS]
    r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-O1", *defs, "-I", backend, "-I", NXINC, "-I", HERE, *srcs, "-lEGL", "-lGL", "-lm", "-o", out])
    return r.returncode == 0, r.stderr[-1500:]


def run(exe, parts="abcdef"):
    r = sh([exe, parts], env=ENV, timeout=300)
    got = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, got


def main():
    tmp = tempfile.mkdtemp(prefix="gl4t_")
    try:
        exe = os.path.join(tmp, "t_gl4")
        ok, err = build(BACK, exe)
        if not ok:
            print(err); fails.append("build"); return
        r, got = run(exe)
        for p in "abcdef":
            if p not in got: fails.append("part " + p + " missing"); print("PART %s: no result" % p); continue
            n, f = got[p]; print("PART %s: %d checks, %d failed" % (p, n, f))
            if f or n == 0: fails.append("part " + p)
        if r.returncode and not fails: fails.append("exit status %d" % r.returncode)
        for l in r.stdout.splitlines():
            if l.startswith(("FAIL", " ")): print(l)
        # build gate: without -DNXGL4_B2B_PENDING gl4_core.c must not compile (tables lack the B2b entries)
        g = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-fsyntax-only", "-I", BACK, "-I", NXINC, os.path.join(BACK, "gl4_core.c")])
        gate = g.returncode != 0 and "B2b" in g.stderr and "static assertion" in g.stderr
        print("PART g (build gate): %d checks, %d failed" % (1, 0 if gate else 1))
        if not gate: fails.append("build gate")
        if "--no-mutations" not in sys.argv:
            muts = [  # (part that must catch it, old line text, replacement)
                ("b", "memcpy(tight + (s * rows + r) * rb, src + (s * rows + r) * rowPitch, rb);", "memcpy(tight + (s * rows + r) * rb, src + (s * rows + r) * rb, rb);"),
                ("e", "if (!im || im->isView || id == g4.swapImage)", "if (!im || id == g4.swapImage)"),
                ("a", "GLint zo = im->target == GL_TEXTURE_CUBE_MAP ? (GLint)layer : (GLint)z;", "GLint zo = (GLint)z;"),
                ("f", "data += gl4_row_bytes(&f, box[3]) * gl4_rows(&f, box[4]) * box[5];", "data += gl4_row_bytes(&f, box[3]);"),
                ("e", "memset(&g4.img[v], 0, sizeof g4.img[v]);", "g4.img[v].tex = 0;"),
                ("b", "pitch < rb || need > a[10])", "pitch < rb || need > (1ull << 40))"),
                ("a", "(uint64_t)b[0] + b[3] > lw ||", "(uint64_t)b[0] + b[3] >= lw ||"),
            ]
            caught = 0
            for i, (part, old, new) in enumerate(muts):
                d = os.path.join(tmp, "mut%d" % i); shutil.copytree(BACK, d)
                p = os.path.join(d, "gl4_image.c"); s = open(p).read()
                assert s.count(old) == 1, "mutation %d does not match exactly once: %s" % (i, old)
                open(p, "w").write(s.replace(old, new))
                ok, err = build(d, os.path.join(d, "t"))
                if not ok: print("mutation %d: build failed\n%s" % (i, err)); fails.append("mutation %d build" % i); continue
                r, res = run(os.path.join(d, "t"), part)
                hit = r.returncode != 0 or res.get(part, (0, 0))[1] > 0 or part not in res
                caught += hit
                print("mutation %d (part %s): %s" % (i, part, "caught" if hit else "NOT CAUGHT"))
                if not hit: fails.append("mutation %d not caught" % i)
                shutil.rmtree(d)
            print("PART h: %d mutations, %d caught" % (len(muts), caught))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


main()
print("ALL OK" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
