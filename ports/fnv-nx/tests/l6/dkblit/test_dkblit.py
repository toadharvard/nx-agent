"""test_dkblit.py: tests of render/d3d9/backend/nx/dk_blit.c (deko3d backend: NxOp_Blit and NxOp_GenMips).
    python3 -I tests/l6/dkblit/test_dkblit.py [--no-mutations]    needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK)
 a  static: the file is <= 300 lines, lines <= 160 chars, no stub-class word, no empty function body, no weak symbol
 b  cross build (aarch64, -Werror) by build_dk.sh; dk_blit.o defines the two handlers and calls only the image-transfer library calls and the contract symbols
 c  host run of the real product file under ASan + UBSan against a recording double (host_main.c): the primitive, views, rects, flags and barriers of every
    blit class, every GenMips level and slice, every rejection with its message
 d  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
SRC = "dk_blit.c"
EXPECT_CHECKS = 102
HANDLERS = {"nxdk_op_blit", "nxdk_op_gen_mips"}
UNDEF_OK = {"nxchk_err", "nxdk_s", "nxdk_img", "nxdk_box_view", "nxdk_scale_point", "nxdk_query_pause", "nxdk_query_resume", "dk_fmt", "dkImageFormatGetFlags"}
LIBCALL = re.compile(r"^dkCmdBuf(Barrier|CopyImage|BlitImage|ResolveImage)$")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
MUST_NAMES = [
    "copy_events", "copy_sub_events", "cube_scale_layers", "cube_copy_events", "copy_compressed_events", "copy_depth_events", "copy_ignores_filter",
    "view_src_is_base", "view_dst_is_base", "blit_front_dst", "blit_linear_events", "blit_nearest_convert", "blit_linear_convert", "blit_rects_exact",
    "err_engine_format", "err_engine_3d", "scale_events", "scale_view_id", "depth_scale_events", "scale_down_to_8", "err_ms_dst", "err_ms_dst_same",
    "err_compressed_size", "err_compressed_format", "err_compressed_dst", "err_depth_formats", "err_depth_to_colour", "err_colour_to_depth",
    "resolve_events", "resolve_ignores_filter", "resolve_level_events", "err_resolve_subrect", "err_resolve_offset", "err_resolve_scale",
    "err_resolve_format", "err_resolve_dst_partial", "err_resolve_depth", "err_resolve_engine", "err_resolve_cube_dst", "err_resolve_3d_dst",
    "err_argc", "err_argc0", "err_src_missing", "err_dst_missing", "err_dst_oob_id", "err_filter", "err_src_level", "err_dst_level",
    "err_src_layer_2d", "err_dst_layer_2d", "err_cube_layer", "err_3d_layer", "ok_3d_last_slice", "err_zero_sw", "err_zero_sh", "err_zero_dw",
    "err_zero_dh", "err_src_x", "err_src_y", "err_dst_x", "err_dst_y", "err_src_overflow", "err_dst_overflow", "err_level_extent",
    "err_extent_small_src", "gm_nearest_events", "gm_linear_events", "gm_view_base", "gm_view_id", "gm_npot_events", "gm_clamped_copy",
    "gm_cube_layers", "gm_3d_slices", "gm_3d_odd_slices", "gm_3d_ratio_slices", "gm_3d_linear_rejected", "gm_single_level", "gm_front_single_level",
    "gm_3d_single_level_linear", "gm_stops_on_error", "gm_no_engine_nearest", "err_gm_argc", "err_gm_argc0", "err_gm_missing", "err_gm_filter",
    "err_gm_ms", "err_gm_oob_id", "box_args_ok", "cmd_everywhere", "err_engine_dst", "err_engine_src", "scale_height_only", "scale_dst_view_id",
    "err_resolve_subsrc", "err_resolve_subsrc_h", "err_resolve_subsrc_w", "err_resolve_dstw", "err_resolve_dsth", "err_src_level_eq_count", "err_src_overflow_y",
    "err_level_extent_y",
]

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
txt = open(os.path.join(NXD, SRC)).read()
check(len(txt.splitlines()) <= 300, SRC + " is longer than 300 lines")
check(all(len(l) <= 160 for l in txt.splitlines()), SRC + " has a line longer than 160 characters")
m = FORBID.search(txt); check(not m, SRC + " contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", txt) and "__attribute__" not in txt, SRC + " has an empty function body or an attribute")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dkblit_")
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    o = os.path.join(out, "dk_blit.o")
    und = {l.split()[1] for l in sh([NM, "-u", o]).stdout.splitlines() if len(l.split()) == 2}
    dfn = {l.split()[2]: l.split()[1] for l in sh([NM, "--defined-only", o]).stdout.splitlines() if len(l.split()) == 3}
    ext = {s for s, t in dfn.items() if t in "TDBR"}
    check(ext == HANDLERS, "defined global symbols differ: extra %s missing %s" % (sorted(ext - HANDLERS), sorted(HANDLERS - ext)))
    bad = {s for s in und if not (s in UNDEF_OK or LIBCALL.match(s))}
    check(not bad, "unexpected undefined symbols: %s" % sorted(bad))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, "-C", o]).stdout, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"),
           "-I" + NXD, "-I" + BACK]
    fl = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc
    o1, o2 = exe + "_p.o", exe + "_h.o"
    r = sh(fl + ["-c", os.path.join(srcdir, SRC), "-o", o1])
    if r.returncode: return r
    r = sh(fl + ["-c", os.path.join(HERE, "host_main.c"), "-o", o2])
    if r.returncode: return r
    return sh(fl + [o1, o2, "-o", exe])

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
    if n != EXPECT_CHECKS: return None, "check count %d, expected %d" % (n, EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

n, why = run_host(NXD, "base")
check(n is not None, "host run: " + why)
if n is not None: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("copy without the leading barrier", "bar(); dkCmdBufCopyImage", "dkCmdBufCopyImage"),
    ("copy without the trailing barrier", "&dr, 0); bar();", "&dr, 0);"),
    ("blit without the leading barrier", "bar(); dkCmdBufBlitImage", "dkCmdBufBlitImage"),
    ("blit without the trailing barrier", "DkBlitFlag_FilterNearest, 0); bar();", "DkBlitFlag_FilterNearest, 0);"),
    ("resolve without the leading barrier", "bar(); dkCmdBufResolveImage", "dkCmdBufResolveImage"),
    ("resolve without the trailing barrier", "&dv); bar();", "&dv);"),
    ("scale without the leading barrier", "bar(); nxdk_query_pause();", "nxdk_query_pause();"),
    ("scale without the trailing barrier", "nxdk_query_resume(); bar();", "nxdk_query_resume();"),
    ("barrier flags: image cache only", "(DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache)", "(DkInvalidateFlags_Image)"),
    ("barrier kind", "DkBarrier_Full, BAR_CACHES", "DkBarrier_Fragments, BAR_CACHES"),
    ("scale without pausing the counter", "nxdk_query_pause(); nxdk_scale_point", "nxdk_scale_point"),
    ("scale without resuming the counter", "NULL); nxdk_query_resume();", "NULL);"),
    ("scale with a ramp", "nxdk_scale_point(nxdk_s.cmd, s, d, NULL)", "nxdk_scale_point(nxdk_s.cmd, s, d, (const uint16_t*)(uintptr_t)1)"),
    ("copy ignores the size", "si->fmt == di->fmt && sameSize &&", "si->fmt == di->fmt &&"),
    ("copy ignores the format", "si->fmt == di->fmt && sameSize &&", "sameSize &&"),
    ("copy flags", "&dr, 0); bar();\n        return 0;", "&dr, 1); bar();\n        return 0;"),
    ("copy source and destination swapped", "&sv, &sr, &dv, &dr, 0", "&dv, &dr, &sv, &sr, 0"),
    ("blit source and destination swapped", "&sv, &sr, &dv, &dr, filter", "&dv, &dr, &sv, &sr, filter"),
    ("resolve source and destination swapped", "ResolveImage(nxdk_s.cmd, &sv, &dv)", "ResolveImage(nxdk_s.cmd, &dv, &sv)"),
    ("same size compared by width only", "s->w == d->w && s->h == d->h;", "s->w == d->w;"),
    ("multisampled destination accepted", "if (di->samples > 1) {", "if (0) {"),
    ("copy of equal sample counts not required", "&& si->samples == di->samples", ""),
    ("compressed destination accepted", "(sf.flags | df.flags) & DKFMT_COMPRESSED", "sf.flags & DKFMT_COMPRESSED"),
    ("compressed source accepted", "(sf.flags | df.flags) & DKFMT_COMPRESSED", "df.flags & DKFMT_COMPRESSED"),
    ("depth against colour accepted", "depth != ((df.flags & DKFMT_DEPTH) != 0) ||", ""),
    ("depth with different formats accepted", "(depth && si->fmt != di->fmt)", "0"),
    ("multisampled source not resolved", "if (si->samples > 1) return resolve(s, d);", ""),
    ("depth sampled linearly", "(!filter || depth) && !sameSize", "!filter && !sameSize"),
    ("linear scale through point", "(!filter || depth) && !sameSize", "!sameSize"),
    ("nearest blit of equal size by point scale", "(!filter || depth) && !sameSize", "(!filter || depth)"),
    ("filter flag always nearest", "filter ? DkBlitFlag_FilterLinear : DkBlitFlag_FilterNearest", "DkBlitFlag_FilterNearest"),
    ("filter flag always linear", "filter ? DkBlitFlag_FilterLinear : DkBlitFlag_FilterNearest", "DkBlitFlag_FilterLinear"),
    ("blit alpha factor", "DkBlitFlag_FilterNearest, 0); bar();", "DkBlitFlag_FilterNearest, 1); bar();"),
    ("source without 2D engine accepted", "if (!engine2d(si) || !engine2d(di)) {\n        nxdk_err(\"Blit: format", "if (!engine2d(di)) {\n        nxdk_err(\"Blit: format"),
    ("destination without 2D engine accepted", "if (!engine2d(si) || !engine2d(di)) {\n        nxdk_err(\"Blit: format", "if (!engine2d(si)) {\n        nxdk_err(\"Blit: format"),
    ("3D image on the 2D engine", "im->type != NxImgType_3D &&", ""),
    ("resolve of a depth source", "if (dk_fmt(si->fmt).flags & DKFMT_DEPTH) {", "if (0) {"),
    ("resolve of a partial source width", "const int whole = s->w == lvl(si->w, s->level) &&", "const int whole ="),
    ("resolve of a partial source height", "s->h == lvl(si->h, s->level) && d->w", "d->w"),
    ("resolve with scaling", "d->w == s->w && d->h == s->h &&", ""),
    ("resolve onto a partial level width", "d->w == lvl(di->w, d->level) && d->h", "d->h"),
    ("resolve onto a partial level height", "&& d->h == lvl(di->h, d->level);", ";"),
    ("resolve without a 2D engine check", "|| !engine2d(si) || !engine2d(di)) {\n        nxdk_err(\"Blit: multisampled", ") {\n        nxdk_err(\"Blit: multisampled"),
    ("resolve with a format conversion", "si->fmt != di->fmt ||", ""),
    ("resolve onto a cube or 3D image", "di->type != NxImgType_2D ||", ""),
    ("level extent without clamping", "v >>= l; return v ? v : 1;", "return v >> l;"),
    ("cube with 5 layers", "NxImgType_Cube ? 6", "NxImgType_Cube ? 5"),
    ("3D layers not halved per level", "NxImgType_3D ? lvl(im->d, l) : 1", "NxImgType_3D ? im->d : 1"),
    ("level equal to the count accepted", "return l < im->mips &&", "return l <= im->mips &&"),
    ("layer equal to the count accepted", "z < layers_of(im, l) &&", "z <= layers_of(im, l) &&"),
    ("zero width accepted", "layers_of(im, l) && w && h &&", "layers_of(im, l) && h &&"),
    ("zero height accepted", "layers_of(im, l) && w && h &&", "layers_of(im, l) && w &&"),
    ("x extent wraps", "(uint64_t)x + w <=", "x + w <="),
    ("y extent wraps", "(uint64_t)y + h <=", "y + h <="),
    ("rect outside the level (x)", "(uint64_t)x + w <= lvl(im->w, l)", "(uint64_t)x + w <= im->w"),
    ("rect outside the level (y)", "(uint64_t)y + h <= lvl(im->h, l)", "(uint64_t)y + h <= im->h"),
    ("view id not resolved to its base", "const NxDkImage* base_of(const NxDkImage* im) { return im && im->isView ? nxdk_img(im->base) : im; }",
     "const NxDkImage* base_of(const NxDkImage* im) { return im; }"),
    ("view id kept in the sub-resource", "sr && sr->isView ? sr->base : a[0]", "a[0]"),
    ("view id kept in the destination", "dr && dr->isView ? dr->base : a[6]", "a[6]"),
    ("filter 2 accepted", "a[12] > 1 ||", "a[12] > 2 ||"),
    ("source never validated", "!sub_ok(si, s.level, s.layer, s.x, s.y, s.w, s.h) ||", ""),
    ("destination never validated", "|| !sub_ok(di, d.level, d.layer, d.x, d.y, d.w, d.h)", ""),
    ("blit word count", "NEED(13, Blit);", "NEED(12, Blit);"),
    ("blit source level and layer swapped", "a[1] & 0xFFFF, a[1] >> 16", "a[1] >> 16, a[1] & 0xFFFF"),
    ("blit destination level and layer swapped", "a[7] & 0xFFFF, a[7] >> 16", "a[7] >> 16, a[7] & 0xFFFF"),
    ("blit rect order", "a[2], a[3], a[4], a[5] };", "a[3], a[2], a[4], a[5] };"),
    ("blit destination rect order", "a[8], a[9], a[10], a[11] };", "a[8], a[9], a[11], a[10] };"),
    ("view rect x and y swapped", "{ s->x, s->y, s->layer, s->w, s->h, 1 }", "{ s->y, s->x, s->layer, s->w, s->h, 1 }"),
    ("view box without the layer", "{ s->x, s->y, s->layer, s->w, s->h, 1 }", "{ s->x, s->y, 0, s->w, s->h, 1 }"),
    ("view box depth", "s->w, s->h, 1 }", "s->w, s->h, 2 }"),
    ("view level", "nxdk_box_view(s->im, s->level, s->layer, box", "nxdk_box_view(s->im, 0, s->layer, box"),
    ("view layer", "nxdk_box_view(s->im, s->level, s->layer, box", "nxdk_box_view(s->im, s->level, 0, box"),
    ("GenMips word count", "NEED(2, GenMips);", "NEED(1, GenMips);"),
    ("GenMips filter 2 accepted", "a[1] > 1 || im->samples > 1", "a[1] > 2 || im->samples > 1"),
    ("GenMips multisampled image accepted", "a[1] > 1 || im->samples > 1", "a[1] > 1"),
    ("GenMips linear 3D accepted", "im->type == NxImgType_3D && a[1] == 1 && im->mips > 1", "0"),
    ("GenMips linear rejected for every 3D image", "a[1] == 1 && im->mips > 1", "a[1] == 1"),
    ("GenMips nearest 3D rejected", "im->type == NxImgType_3D && a[1] == 1", "im->type == NxImgType_3D"),
    ("GenMips from level 0", "for (uint32_t l = 1; l < im->mips; l++)", "for (uint32_t l = 0; l < im->mips; l++)"),
    ("GenMips misses the last level", "for (uint32_t l = 1; l < im->mips; l++)", "for (uint32_t l = 1; l + 1 < im->mips; l++)"),
    ("GenMips 3D slice without the odd centre", "(2ull * z + 1) * sd", "(2ull * z) * sd"),
    ("GenMips 3D slice scaled by the layer count", "/ (2ull * layers)", "/ layers"),
    ("GenMips 2D layer always 0", "NxImgType_3D ? (uint32_t)(((2ull * z + 1) * sd) / (2ull * layers)) : z;",
     "NxImgType_3D ? (uint32_t)(((2ull * z + 1) * sd) / (2ull * layers)) : 0;"),
    ("GenMips source level", "const NxDkSub s = { im, id, l - 1, zs,", "const NxDkSub s = { im, id, l, zs,"),
    ("GenMips destination level", "const NxDkSub d = { im, id, l, z,", "const NxDkSub d = { im, id, l - 1, z,"),
    ("GenMips source extent", "lvl(im->w, l - 1), lvl(im->h, l - 1) };", "lvl(im->w, l), lvl(im->h, l - 1) };"),
    ("GenMips destination extent", "lvl(im->w, l), lvl(im->h, l) };", "lvl(im->w, l), lvl(im->h, l - 1) };"),
    ("GenMips continues after an error", "if (xfer(&s, &d, a[1])) return;", "(void)xfer(&s, &d, a[1]);"),
    ("GenMips filter ignored", "xfer(&s, &d, a[1])) return;", "xfer(&s, &d, 0)) return;"),
    ("GenMips view id kept", "raw->isView ? raw->base : a[0]", "a[0]"),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None:
    for i, (name, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        shutil.copy(os.path.join(NXD, SRC), d)
        t = open(os.path.join(d, SRC)).read()
        if t.count(a) != 1:
            check(False, "mutation '%s': anchor occurs %d times" % (name, t.count(a))); continue
        open(os.path.join(d, SRC), "w").write(t.replace(a, b))
        m, why = run_host(d, "m%d" % i)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
