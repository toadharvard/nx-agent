#!/usr/bin/env python3
"""test_dkpass.py: tests of render/d3d9/backend/nx/dk_pass.c (deko3d backend: render passes, Clear, sRGB-write rebind, internal target bind).
    python3 -I tests/l6/dkpass/test_dkpass.py [--no-mutations]     needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK,
                                                                   default /home/user/work/l6a/dk)
 a  static: dk_pass.c <= 300 lines, lines <= 160 chars, no stub-class words, no empty function body, no attribute
 b  cross build (aarch64, -Werror) by build_dk.sh into an own directory; dk_pass.o defines exactly the six handlers (no weak symbol) and
    leaves undefined only the stream state, the embedder checks, dk_fmt, the neutral checks and deko3d / libc calls
 c  host run of the real dk_pass.c + nxgpu4_check.c + dk_fmt.c under ASan + UBSan (no recovery) against a recording deko3d double (host_main.c);
    nxdk_img, nxdk_img_view_id, nxdk_chk_env and nxdk_err are doubles: every check line 1, the exact number of checks, the named checks present
 d  mutations of a copy of dk_pass.c: every one must be caught (build error, sanitizer, a failing or a missing check)
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6a/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
PRODUCT = "dk_pass.c"
EXTRA = [os.path.join(BACK, "nxgpu4_check.c"), os.path.join(NXD, "dk_fmt.c")]
EXPECT_CHECKS = 139
MUST_NAMES = ["p4_one_pass", "p4_four_pass", "p4_level3", "p4_cube_face4", "p4_swap_pass", "p4_ms_pass", "p4_starts_srgb_off", "v3_pass",
              "v3_load_clear_ignores_srgb", "v3_viewport_from_words", "v3_stencil_without_stencil_format", "srgb_on", "srgb_off", "srgb_no_change_on",
              "srgb_outside_pass", "srgb_without_view_slot_plain", "swap_has_no_srgb_view", "rej_3d_target", "rej_non_render_format", "rej_dead_image",
              "rej_swap_missing", "rej_size_mismatch", "rej_sample_mismatch", "rej_count5", "rej_clear_no_pass", "clear_all", "clear_colour_all_targets",
              "clear_srgb_rebinds_and_restores", "clear_restores_state_scissor", "clear_restores_whole_pass", "clear_keeps_bound_state",
              "clear_stencil", "clear_z16_stencil_only_nothing", "clear_without_depth_buffer", "end_pass_resets", "rej_target_bind_in_pass",
              "target_bind_front", "target_bind_never_srgb", "target_bind_level_full_size", "target_bind_cube_face"]
HANDLERS = {"nxdk_op_begin_pass", "nxdk_op_begin_pass4", "nxdk_op_end_pass", "nxdk_op_clear", "nxdk_pass_srgb", "nxdk_target_bind"}
UNDEF = {"nxdk", "nxdk_s", "nxdk_err", "nxchk_err", "nxdk_img", "nxdk_img_view_id", "nxdk_chk_env", "nxchk_pass_check", "nxchk_pass_parse4", "dk_fmt",
         "memcpy", "memset"}
LIBCALL = re.compile(r"^dk[A-Z]\w*$")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
p = os.path.join(NXD, PRODUCT)
if not os.path.exists(p): check(False, "missing product file " + PRODUCT); print("FAILED"); sys.exit(1)
txt = open(p).read()
check(len(txt.splitlines()) <= 300, PRODUCT + " is longer than 300 lines")
check(max(len(l) for l in txt.splitlines()) <= 160, PRODUCT + " has a line longer than 160 characters")
m = FORBID.search(txt); check(not m, PRODUCT + " contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", txt), PRODUCT + " has an empty function body")
check("__attribute__" not in txt, PRODUCT + " uses an attribute")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dkpass_")
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out], env=dict(os.environ, FNVNX_DK=DK))
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    o = os.path.join(out, "dk_pass.o")
    check(os.path.exists(o), "dk_pass.o was not built")
    und, dfn, weak = set(), set(), []
    for line in sh([NM, o]).stdout.splitlines():
        t = line.split()
        if len(t) == 2 and t[0] == "U": und.add(t[1])
        if len(t) == 3 and t[1] in "TDBRC": dfn.add(t[2])
        if len(t) == 3 and t[1] in "wWvV": weak.append(t[2])
    check(dfn == HANDLERS, "defined symbols differ: extra %s missing %s" % (sorted(dfn - HANDLERS), sorted(HANDLERS - dfn)))
    check(not weak, "weak symbols: %s" % weak)
    bad = {s for s in und if not (s in UNDEF or LIBCALL.match(s))}
    check(not bad, "unexpected undefined symbols: %s" % sorted(bad))
    check("nxchk_pass_check" in und and "nxchk_pass_parse4" in und, "the neutral pass checks are not used")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"),
           "-I" + NXD, "-I" + BACK]
    cmd = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc + \
          [os.path.join(HERE, "host_main.c"), os.path.join(srcdir, PRODUCT)] + EXTRA + ["-o", exe]
    return sh(cmd)

def run_host(srcdir, tag):
    exe = os.path.join(tmp, "host_" + tag)
    b = host(srcdir, exe)
    if b.returncode: return None, "build: " + tail(b.stderr, 6)
    r = sh([exe], env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"), timeout=120)
    if r.returncode: return None, "exit %d: %s" % (r.returncode, tail(r.stderr, 6))
    chk, n = {}, 0
    for line in r.stdout.splitlines():
        m = re.match(r"CHK (\S+) ([01])$", line)
        if m: chk[m.group(1)] = chk.get(m.group(1), 1) & int(m.group(2))
        m = re.match(r"NCHK (\d+)$", line)
        if m: n = int(m.group(1))
    bad = [k for k, v in chk.items() if not v]
    if bad: return None, "failed checks: " + " ".join(bad[:8])
    if n != EXPECT_CHECKS or len(chk) != EXPECT_CHECKS: return None, "check count %d (%d names), expected %d" % (n, len(chk), EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

n, why = run_host(NXD, "base")
check(n is not None, "host run: " + why)
if n is not None: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
SR = "nxdk_s.cmd, nxdk_s.passColor, nxdk_s.passColors, &nxdk_s.passDepth, "
MUT = [
    ("layer offset dropped", "v->layerOffset = t->layer;", ""),
    ("mip level offset dropped", "v->mipLevelOffset = t->level; ", ""),
    ("cube not a 2D view", "if (t->im->type == NxImgType_Cube) v->type = DkImageType_2D;", ""),
    ("sRGB view without the view-slot test", "if (srgb && nxdk_img_view_id(t->id, 1) != t->id)", "if (srgb)"),
    ("sRGB format override dropped", "v->format = dk_fmt(t->im->fmt).srgb;", ";"),
    ("3D target accepted", "if (s->im->type == NxImgType_3D) {", "if (0) {"),
    ("non-render format accepted", "if (!depth && !(dk_fmt(s->im->fmt).flags & DKFMT_RENDER))", "if (0)"),
    ("passSrgb not reset at open", " nxdk_s.passSrgb = 0; nxdk_s.inPass = 1;", " nxdk_s.inPass = 1;"),
    ("swap image keeps an id", "s->id = sw ? 0 : image;", "s->id = image;"),
    ("v3 colour 0 is not the swap image", "a[0] ? a[0] : nxdk_chk_env()->swapImage", "a[0]"),
    ("pass colour count fixed to 1", "nxdk_s.passColors = p->nColor;", "nxdk_s.passColors = 1;"),
    ("clear red from the blue byte", "(rgba8 & 255) / 255.0f, (rgba8 >> 8 & 255)", "(rgba8 >> 16 & 255) / 255.0f, (rgba8 >> 8 & 255)"),
    ("stencil mask 0", "s ? 0xFF : 0", "0"),
    ("stencil without a stencil format", " && (dk_fmt(d->im->fmt).flags & DKFMT_STENCIL)", ""),
    ("depth always cleared", "const int z = (flags & NxClear_Depth) != 0", "const int z = 1"),
    ("clear of an absent depth buffer", "if (!d->im) return;", ""),
    ("load-clear scissor dropped", "        scissor(0, 0, w, h);\n        if (a[2] & NxClear_Color)", "        if (a[2] & NxClear_Color)"),
    ("v3 viewport from the target", "begin_end(a[6], a[7]);", "begin_end(w, h);"),
    ("viewport origin 0", "{ 0.5f, 0.5f, (float)w", "{ 0.0f, 0.0f, (float)w"),
    ("blend constant not reset", "    dkCmdBufSetBlendConst(nxdk_s.cmd, 1.0f, 1.0f, 1.0f, 1.0f);\n", ""),
    ("depth bias not reset", "    dkCmdBufSetDepthBias(nxdk_s.cmd, 0.0f, 0.0f, 0.0f);\n", ""),
    ("state scissor not synced", "nxdk_s.scissor[2] = (int32_t)w;", "nxdk_s.scissor[2] = 0;"),
    ("Clear outside a pass accepted", 'if (!nxdk_s.inPass) { nxdk_err("Clear:', 'if (0) { nxdk_err("Clear:'),
    ("Clear scissor restore dropped", "    else scissor(0, 0, nxdk_s.passW, nxdk_s.passH);", "    else ;"),
    ("Clear restores the state scissor always", " && NXRS_GET(nxdk_s.st[nxdk_s.boundState].rs.w[2], NXRS_R_SCISSOR, 1))", ")"),
    ("Clear ignores the bound state", "if (nxdk_s.boundState && NXRS_GET(", "if (0 && NXRS_GET("),
    ("Clear sRGB rebind dropped", "        if (srgb != nxdk_s.passSrgb) bind(" + SR + "srgb);\n", ""),
    ("Clear sRGB restore dropped", "        if (srgb != nxdk_s.passSrgb) bind(" + SR + "nxdk_s.passSrgb);\n", ""),
    ("pass_srgb no-change guard dropped", "if (on == nxdk_s.passSrgb) return;", ""),
    ("pass_srgb without a rebind", "    bind(" + SR + "on);\n", ""),
    ("pass_srgb outside a pass", 'if (!nxdk_s.inPass) { nxdk_err("pass_srgb:', 'if (0) { nxdk_err("pass_srgb:'),
    ("EndPass keeps boundState", "nxdk_s.inPass = 0; nxdk_s.boundState = 0; }", "nxdk_s.inPass = 0; }"),
    ("EndPass keeps inPass", "{ (void)a; (void)argc; nxdk_s.inPass = 0;", "{ (void)a; (void)argc;"),
    ("target_bind inside a pass", 'if (nxdk_s.inPass) { nxdk_err("target_bind', 'if (0) { nxdk_err("target_bind'),
    ("target_bind level size dropped", "nxchk_lvl(dst->im->w, dst->level), h = nxchk_lvl(dst->im->h, dst->level)", "dst->im->w, h = dst->im->h"),
    ("target_bind without the format check", "if (!usable(dst, 0, 0)) return;", ""),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None:
    for i, (name, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        t = open(os.path.join(NXD, PRODUCT)).read()
        if t.count(a) != 1:
            check(False, "mutation '%s': anchor occurs %d times" % (name, t.count(a))); continue
        open(os.path.join(d, PRODUCT), "w").write(t.replace(a, b))
        m, why = run_host(d, "m%d" % i)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
