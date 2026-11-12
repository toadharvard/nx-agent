#!/usr/bin/env python3
"""test_nxcheck.py: render/backend/nxgpu4_check.c, the backend-neutral nxgpu v4 argument and stream checks.
  python3 -I tests/l6/nxcheck/test_nxcheck.py [--no-mutations]
 a  neutrality: nxgpu4_check.c is compiled ALONE with a plain C compiler (-std=c11 -Wall -Werror), in a directory that holds
    only nxgpu4.h, nxgpu4_check.h and the source, so no GL header can be found; the preprocessor's include list names no
    GL file, and the sources have no GL token (GL_*, GLenum, glXxx, p_gl*, g4)
 b  accept / reject cases taken from the gl4 tests (t_parts_i BeginPass4, t_parts_j layouts and BindVtxN, t_parts_m reserved
    w[3] bits): the verdict AND the first message of every reject (the text the GL backend reports and deko3d will report)
 c  every NxAttrType4 has its byte size, NxAttr4_Count and above have none
 d  mutations of nxgpu4_check.c: a single-token break must make a case of (b) or (c) fail
The driver defines nxchk_err and an image table (the neutral view), the way a backend does."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
SRC = ["nxgpu4.h", "nxgpu4_check.h", "nxgpu4_check.c", "nx/gpu_abi.h", "nx/state.h"]   # the ABI headers only
fails = []; nchecks = 0


def check(ok, what):
    global nchecks
    nchecks += 1
    if not ok:
        fails.append(what); print("FAIL: " + what)


def stage(d):
    for f in SRC:
        os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True); shutil.copy(os.path.join(BACK, f), os.path.join(d, f))


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---- the cases: (name, accept?, first message substring, [ops]); op = (NxOp name, [args]) | ("layout", id, count, inst, div, [attrs])
# images of the driver (id: w x h, mips, samples, kind): 1 colour 64x64 m7 | 2 depth 64x64 m7 | 3 colour 32x32 m6 | 4 colour 64x64 x4 |
# 5 cube 16 m5 | 6 3D 8x8x8 m4 | 7 compressed 64x64 | 8 view | 9 depth 32x32 | 10 depth 64x64 x4 | swapchain = 1
def bp(*a): return ("NxOp_BeginPass4", list(a))
def bind(*a): return ("NxOp_BindVtxN", list(a))
def lay(i, n, inst=0, div0=0, attrs=()): return ("layout", i, n, inst, div0, list(attrs))
def at(off, stream, typ, loc): return "{%d,%d,%d,%d}" % (off, stream, typ, loc)
def st(slot, w3lo, w3hi=0): return ("NxOp_DefineState", [slot, 0, 0, 0, 0, 0, 0, 0, 0, 0, w3lo, w3hi])


CASES = [
    # BeginPass4 (colorCount, (image, sub) x n, (depth image, sub))
    ("pass: one colour target", 1, "", [bp(1, 1, 0, 0, 0)]),
    ("pass: colour 0 names the swapchain image", 1, "", [bp(1, 0, 0, 0, 0)]),
    ("pass: colour + depth same size", 1, "", [bp(1, 1, 0, 2, 0)]),
    ("pass: depth only", 1, "", [bp(0, 2, 0)]),
    ("pass: mip level 1 of 64x64 matches a 32x32 target", 1, "", [bp(2, 1, 1, 3, 0, 0, 0)]),
    ("pass: cube face 5 of mip 2", 1, "", [bp(1, 5, 2 | 5 << 16, 0, 0)]),
    ("pass: 3D slice 3 of mip 1", 1, "", [bp(1, 6, 1 | 3 << 16, 0, 0)]),
    ("pass: four colour targets", 1, "", [bp(4, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0)]),
    ("pass: multisampled colour + depth", 1, "", [bp(1, 4, 0, 10, 0)]),
    ("pass: no target", 0, "pass: no target", [bp(0, 0, 0)]),
    ("pass: five colour targets", 0, "BeginPass4: ", [bp(5, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0)]),
    ("pass: too few words", 0, "BeginPass4: 2 words for 1 colour targets", [bp(1, 1)]),
    ("pass: sizes differ", 0, "pass: target 1 is 32x32 x1 samples, the pass is 64x64 x1", [bp(2, 1, 0, 3, 0, 0, 0)]),
    ("pass: sample counts differ", 0, "x4 samples", [bp(2, 1, 0, 4, 0, 0, 0)]),
    ("pass: depth sample count differs", 0, "pass: target 1 is 64x64 x4", [bp(1, 1, 0, 10, 0)]),
    ("pass: depth size differs", 0, "pass: target 1 is 32x32", [bp(1, 1, 0, 9, 0)]),
    ("pass: level out of range", 0, "level 7 layer 0 outside image 1", [bp(1, 1, 7, 0, 0)]),
    ("pass: layer of a 2D image", 0, "layer 1 outside image 1", [bp(1, 1, 1 << 16, 0, 0)]),
    ("pass: cube layer 6", 0, "layer 6 outside image 5", [bp(1, 5, 6 << 16, 0, 0)]),
    ("pass: 3D slice out of the level's depth", 0, "layer 4 outside image 6", [bp(1, 6, 3 | 4 << 16, 0, 0)]),
    ("pass: colour target is a depth image", 0, "target 0 (image 2) is not a colour image", [bp(1, 2, 0, 0, 0)]),
    ("pass: depth target is a colour image", 0, "target 1 (image 3) is not a depth-stencil image", [bp(1, 1, 0, 3, 0)]),
    ("pass: compressed colour target", 0, "target 0 (image 7) is not a colour image", [bp(1, 7, 0, 0, 0)]),
    ("pass: view as target", 0, "target 0 (image 8) is not a colour image", [bp(1, 8, 0, 0, 0)]),
    ("pass: dead image", 0, "target 0 (image 11) is not a colour image", [bp(1, 11, 0, 0, 0)]),
    # DefineLayout4
    ("layout: two float attributes", 1, "", [lay(1, 2, attrs=[at(0, 0, 2, 0), at(12, 0, 1, 1)])]),
    ("layout: empty", 1, "", [lay(1, 0)]),
    ("layout: 16 attributes on 16 streams", 1, "", [lay(2, 16, attrs=[at(0, i, 3, i) for i in range(16)])]),
    ("layout: instanced stream with a divisor", 1, "", [lay(3, 1, 1 << 2, 1, attrs=[at(0, 2, 3, 0)])]),
    ("layout: offset at the limit", 1, "", [lay(3, 1, attrs=[at(2048, 0, 3, 0)])]),
    ("layout: too few words", 0, "DefineLayout4: 3 words, need 51", [("NxOp_DefineLayout4", [1, 0, 0])]),
    ("layout: id 0", 0, "DefineLayout4: id 0", [lay(0, 0)]),
    ("layout: id past the table", 0, "DefineLayout4: id 257", [lay(257, 0)]),
    ("layout: 17 attributes", 0, "17 attributes", [lay(1, 17)]),
    ("layout: instance stream bit 16", 0, "instance streams 0x10000", [lay(1, 0, 1 << 16)]),
    ("layout: attribute type Count", 0, "attribute 0 (type 17 stream 0 location 0 offset 0) is invalid", [lay(1, 1, attrs=[at(0, 0, 17, 0)])]),
    ("layout: stream 16", 0, "stream 16", [lay(1, 1, attrs=[at(0, 16, 3, 0)])]),
    ("layout: location 16", 0, "location 16", [lay(1, 1, attrs=[at(0, 0, 3, 16)])]),
    ("layout: location used twice", 0, "attribute 1 (type 2 stream 0 location 4 offset 12)", [lay(1, 2, attrs=[at(0, 0, 3, 4), at(12, 0, 2, 4)])]),
    ("layout: offset past the limit", 0, "offset 2049", [lay(1, 1, attrs=[at(2049, 0, 3, 0)])]),
    ("layout: instance stream with divisor 0", 0, "stream 2", [lay(1, 1, 1 << 2, 0, attrs=[at(0, 2, 3, 0)])]),
    # BindVtxN (stream, block, offset, size, stride)
    ("bind: stream 0", 1, "", [bind(0, 1, 0, 64, 16)]),
    ("bind: stream 15, stride at the limit", 1, "", [bind(15, 1, 0, 64, 2048)]),
    ("bind: unbinds (block 0 size 0)", 1, "", [bind(3, 0, 0, 0, 0)]),
    ("bind: 4 words", 0, "BindVtxN: 4 words", [bind(0, 1, 0, 64)]),
    ("bind: stream 16", 0, "BindVtxN: stream 16 stride 16", [bind(16, 1, 0, 64, 16)]),
    ("bind: stride past the limit", 0, "BindVtxN: stream 0 stride 2049 (max 2048)", [bind(0, 1, 0, 64, 2049)]),
    # DefineState w[3] bits: NXRS_M_* use bits 0-3 and 32-63; bits 4..31 are reserved
    ("state: w3 bits 0-3 and the high word", 1, "", [st(1, 0xF, 0xFFFFFFFF)]),
    ("state: w3 zero", 1, "", [st(1, 0)]),
    ("state: reserved bit 4", 0, "DefineState 1: reserved w[3] bits 0x10", [st(1, 0x10)]),
    ("state: reserved bit 31", 0, "bits 0x80000000", [st(2, 0x80000000, 5)]),
    ("state: slot 4097 with reserved bits is not looked at", 1, "", [st(4097, 0xF0)]),
    ("state: slot 0 with reserved bits is not looked at", 1, "", [st(0, 0xF0)]),
    ("state: slot 4096 is looked at", 0, "DefineState 4096", [st(4096, 0xF0)]),
    # the stream as a whole: ops the checks do not know are skipped by their argc; End stops; the first bad op rejects
    ("stream: unknown op with 3 args then a good pass", 1, "", [("77", [1, 2, 3]), bp(1, 1, 0, 0, 0)]),
    ("stream: good pass, then a bad layout", 0, "DefineLayout4: id 0", [bp(1, 1, 0, 0, 0), lay(0, 0)]),
    ("stream: two bad ops report the first", 0, "BeginPass4: 2 words", [bp(1, 1), bind(16, 1, 0, 64, 16)]),
    ("stream: End hides what follows", 1, "", [("NxOp_End", []), bp(0, 0, 0)]),
]


def c_op(op):
    if op[0] == "layout":
        _, i, n, inst, div0, attrs = op
        return "{ NxVtxAttr4 at[] = {%s}; put_layout(&w, %d, %d, 0x%x, %d, at, %d); }" % (", ".join(attrs) or "{0}", i, n, inst, div0, len(attrs))
    name, args = op
    return "put(&w, %s, %d%s);" % (name, len(args), "".join(", (uint32_t)(%su)" % a for a in args))


DRIVER = r'''
#include "nxgpu4_check.h"
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
static char msg[512]; static int nerr, first = 1;
void nxchk_err(const char* f, ...) { va_list ap; va_start(ap, f); if (first) { vsnprintf(msg, sizeof msg, f, ap); first = 0; } va_end(ap); nerr++; }
typedef struct { uint32_t w, h, d, mips, samples; uint8_t type, isView, isDepth, compressed; } Img;
static const NxChkImage IMG[] = {
    {0},
    {64, 64, 1, 7, 1, NxImgType_2D, 0, 0, 0}, {64, 64, 1, 7, 1, NxImgType_2D, 0, 1, 0}, {32, 32, 1, 6, 1, NxImgType_2D, 0, 0, 0},
    {64, 64, 1, 1, 4, NxImgType_2D, 0, 0, 0}, {16, 16, 1, 5, 1, NxImgType_Cube, 0, 0, 0}, {8, 8, 8, 4, 1, NxImgType_3D, 0, 0, 0},
    {64, 64, 1, 7, 1, NxImgType_2D, 0, 0, 1}, {64, 64, 1, 7, 1, NxImgType_2D, 1, 0, 0}, {32, 32, 1, 6, 1, NxImgType_2D, 0, 1, 0},
    {64, 64, 1, 1, 4, NxImgType_2D, 0, 1, 0},
};
static int image(void* ctx, uint32_t id, NxChkImage* o) { (void)ctx; if (!id || id >= sizeof IMG / sizeof *IMG) return 0; *o = IMG[id]; return 1; }
typedef struct { uint32_t v[600]; uint32_t n; } W;
static void put(W* w, uint32_t op, uint32_t argc, ...) { va_list ap; w->v[w->n++] = op | argc << 16; va_start(ap, argc); for (uint32_t i = 0; i < argc; i++) w->v[w->n++] = va_arg(ap, uint32_t); va_end(ap); }
static void put_layout(W* w, uint32_t id, uint32_t n, uint32_t inst, uint32_t div0, const NxVtxAttr4* at, uint32_t nat) {
    NxVtxLayout4 l; memset(&l, 0, sizeof l); l.attrCount = (uint8_t)n; l.instanceStreams = inst;
    for (int i = 0; i < NX4_MAX_STREAMS; i++) l.divisor[i] = div0;
    for (uint32_t i = 0; i < nat && i < NX4_MAX_ATTRS; i++) l.attr[i] = at[i];
    uint32_t words = sizeof l / 4;
    w->v[w->n++] = NxOp_DefineLayout4 | (1 + words) << 16; w->v[w->n++] = id; memcpy(w->v + w->n, &l, sizeof l); w->n += words;
}
static NxChkEnv ENV = {0, image, 1, 2048, 2048};
static int bad, total;
static void expect(const char* name, int accept, const char* sub, const W* w) {
    nerr = 0; first = 1; msg[0] = 0;
    int ok = nxchk_stream(&ENV, w->v, w->v + w->n);
    int pass = ok == accept && (accept ? nerr == 0 : nerr > 0 && strstr(msg, sub));
    total++;
    if (!pass) { bad++; printf("CASE FAIL %s: verdict %d (want %d), errors %d, first '%s', want '%s'\n", name, ok, accept, nerr, msg, sub); }
}
int main(void) {
    W w;
'''


def make_driver():
    out = [DRIVER]
    for name, ok, sub, ops in CASES:
        out.append("    memset(&w, 0, sizeof w);")
        out += ["    " + c_op(o) for o in ops]
        out.append('    expect("%s", %d, "%s", &w);' % (name, ok, sub))
    # c: attribute sizes; pass_check output
    # NxAttr4_Float1 .. Float16_4 in enum order
    out.append("    static const uint32_t SZ[] = {%s};" % ", ".join(map(str, [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8])))
    out.append("    for (uint32_t t = 0; t < sizeof SZ / 4; t++) { total++; if (nxchk_attr4_bytes(t) != SZ[t]) { bad++; printf(\"CASE FAIL attr size %u: %u\\n\", t, nxchk_attr4_bytes(t)); } }")
    out.append("    for (uint32_t t = NxAttr4_Count; t < NxAttr4_Count + 3; t++) { total++; if (nxchk_attr4_bytes(t)) { bad++; printf(\"CASE FAIL attr %u is not a type\\n\", t); } }")
    out.append("    { NxChkPass p; uint32_t pw = 0, ph = 0, ps = 0; uint32_t a[] = {2, 1, 1, 3, 0, 0, 0}; total++;")
    out.append("      if (nxchk_pass_parse4(&ENV, a, 7, &p) || nxchk_pass_check(&ENV, &p, &pw, &ph, &ps) || pw != 32 || ph != 32 || ps != 1) { bad++; printf(\"CASE FAIL pass size out %u %u %u\\n\", pw, ph, ps); } }")
    out.append('    printf("DRIVER %d cases, %d failed\\n", total, bad);')
    out.append("    return bad != 0;\n}")
    return "\n".join(out)


def build_run(d, src_text=None):
    """compile nxgpu4_check.c (optionally with a mutated text) + the driver in a directory with no GL; returns (compile ok, output)"""
    stage(d)
    if src_text is not None:
        open(os.path.join(d, "nxgpu4_check.c"), "w").write(src_text)
    open(os.path.join(d, "driver.c"), "w").write(make_driver())
    cmd = ["gcc", "-std=c11", "-Wall", "-Werror", "-O1", "-I", d, os.path.join(d, "nxgpu4_check.c"), os.path.join(d, "driver.c"), "-o", os.path.join(d, "drv")]
    r = sh(cmd)
    if r.returncode: return False, r.stderr
    r = sh([os.path.join(d, "drv")])
    return r.returncode == 0, r.stdout


MUTS = [
    ("samples not compared", "else if (tw != *w || th != *h || im.samples != *samples) {", "else if (tw != *w || th != *h) {"),
    ("five colour targets allowed", "a[0] > 4 ||", "a[0] > 5 ||"),
    ("reserved w3 mask shrunk", "#define W3_RESERVED 0xFFFFFFF0ull", "#define W3_RESERVED 0xFFFFFFE0ull"),
    ("attribute offset limit off by one", "at->offset > (uint32_t)e->maxAttrOffset", "at->offset >= (uint32_t)e->maxAttrOffset"),
    ("duplicate locations allowed", "(used >> at->location & 1) ||", "0 ||"),
    ("stride limit off by one", "a[4] > (uint32_t)e->maxVtxStride", "a[4] >= (uint32_t)e->maxVtxStride"),
    ("cube has 7 layers", "im->type == NxImgType_Cube ? 6u", "im->type == NxImgType_Cube ? 7u"),
    ("Float3 is 16 bytes", "case NxAttr4_Float3: return 12;", "case NxAttr4_Float3: return 16;"),
    ("divisor 0 allowed on instance streams", "((l.instanceStreams >> at->stream & 1) && !l.divisor[at->stream])", "0"),
    ("level range not checked", "level >= im.mips || ", ""),
    ("compressed colour target allowed", "im.isDepth || im.compressed", "im.isDepth"),
    ("End does not stop the stream", "if (op == NxOp_End) break;", ""),
]


def part_a():
    with tempfile.TemporaryDirectory() as d:
        stage(d)
        r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-I", d, "-c", os.path.join(d, "nxgpu4_check.c"), "-o", os.path.join(d, "x.o")])
        check(r.returncode == 0, "nxgpu4_check.c compiles alone: " + r.stderr[:300])
        r = sh(["gcc", "-std=c11", "-I", d, "-E", "-H", os.path.join(d, "nxgpu4_check.c"), "-o", os.devnull])
        heads = [l.split()[-1] for l in r.stderr.splitlines() if re.match(r"^\.+ ", l)]
        check(not any("/GL/" in h or "glcorearb" in h or "gl4" in os.path.basename(h) for h in heads), "no GL header is included: %s" % heads)
        check(len(heads) > 0, "the include list was read")
    for f in ["nxgpu4_check.h", "nxgpu4_check.c"]:
        t = open(os.path.join(BACK, f)).read()
        check(not re.search(r"\bGL_\w+|\bGL(enum|int|uint|boolean|sizei)\b|\bp_gl\w+|\bg4\b|\bgl4_\w+|#\s*include\s*<GL", re.sub(r"/\*.*?\*/", "", t, flags=re.S)), f + " has no GL token")
    check(not re.search(r"\bdefault\s*:", open(os.path.join(NXB, "nxgpu4_check.c")).read()), "no default: label")


def main():
    part_a()
    with tempfile.TemporaryDirectory() as d:
        ok, out = build_run(d)
        m = re.search(r"DRIVER (\d+) cases, (\d+) failed", out)
        check(ok and m is not None, "cases (b, c): " + out[-600:])
        if m:
            print("cases: %s, failed %s" % (m.group(1), m.group(2)))
            check(int(m.group(1)) >= 30, "at least 30 cases (%s)" % m.group(1))
    if "--no-mutations" not in sys.argv:
        src = open(os.path.join(NXB, "nxgpu4_check.c")).read()
        for name, old, new in MUTS:
            check(src.count(old) == 1, "mutation '%s' matches once" % name)
            if src.count(old) != 1: continue
            with tempfile.TemporaryDirectory() as d:
                ok, out = build_run(d, src.replace(old, new))
                check(not ok, "mutation caught: " + name)
    print("nxcheck: %d checks, %d failed" % (nchecks, len(fails)))
    print("ALL OK" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
