#!/usr/bin/env python3
"""test_dkscale.py: tests of render/d3d9/backend/nx/dk_scale.c (deko3d backend: the point-scale primitive with internal DKSH programs).
    python3 -I tests/l6/dkscale/test_dkscale.py [--no-mutations]   needs gcc, the devkitA64 cross compiler, libnx / deko3d headers (FNVNX_DK) and uam (UAM)
 a  static: <= 300 lines, no stub-class word, no empty function body, no weak symbol, no attribute
 b  cross build (aarch64, -Werror) by build_dk.sh; undefined symbols of dk_scale.o are deko3d calls, nxdk / nxchk_err hooks and libc
 c  generated header: build_scale_dksh.sh regenerates dk_scale_dksh.h byte for byte (twice, deterministic)
 d  the fragment rule: divx() and gamma_of() are taken from dk_scale_fs.glsl, compiled as C and compared with the integer rule of gl4_scale.c for EVERY
    size pair in 1..128 and every pair of the boundary set up to 16384, every destination texel, with the float estimate and with the estimate pushed one
    below and one above the true quotient; gamma_of against float(ramp >> 8) / 255 for all 768 entries (+ clamping)
 e  host run of the real product file (+ dk_fmt.c) under ASan + UBSan against a recording double (host_main.c): the exact recorded sequence, the viewport /
    scissor / uniform contents for several rect pairs and the three source kinds, ramp on / off, every refusal, init / shutdown balance, the code block
 f  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, struct, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
OK_UND = re.compile(r"^(dk[A-Z]\w*|nxdk\w*|nxchk_err|dk_fmt|memset|memcpy)$")
INC = ["-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + NXD, "-I" + BACK,
       "-I" + os.path.join(ROOT, "tests", "l6", "dkcore", "shim")]

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def tail(s, n=8): return "\n".join(s.strip().splitlines()[-n:])
fails = []
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)

tmp = tempfile.mkdtemp(prefix="dkscale_")
SRC = open(os.path.join(NXD, "dk_scale.c")).read()
FS = open(os.path.join(NXD, "dk_scale_fs.glsl")).read()
HDR = open(os.path.join(NXD, "dk_scale_dksh.h")).read()

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
check(len(SRC.splitlines()) <= 300, "dk_scale.c is longer than 300 lines")
check(max(len(l) for l in SRC.splitlines()) <= 160, "dk_scale.c has a line longer than 160 characters")
m = FORBID.search(SRC); check(not m, "dk_scale.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_scale.c has an empty function body")
check("__attribute__" not in SRC, "dk_scale.c uses an attribute")

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out]); check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    syms = [s for s in sh([NM, "-u", os.path.join(out, "dk_scale.o")]).stdout.split() if s != "U"]
    check({"nxdk_query_pause", "nxdk_target_bind", "nxdk_stage_alloc", "nxchk_err"} <= set(syms), "dk_scale.o does not use the hooks: " + " ".join(syms))
    check(all(OK_UND.match(s) for s in syms), "unexpected undefined symbols: " + " ".join(s for s in syms if not OK_UND.match(s)))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, os.path.join(out, "dk_scale.o")]).stdout, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
env = dict(os.environ); env.setdefault("UAM", "/home/user/work/l6a/uam/bin/uam")
for k in (1, 2):
    r = sh(["sh", os.path.join(NXD, "build_scale_dksh.sh"), os.path.join(tmp, "h%d.h" % k)], env=env)
    check(r.returncode == 0, "build_scale_dksh.sh: " + tail(r.stderr + r.stdout))
try:
    h1, h2 = open(os.path.join(tmp, "h1.h")).read(), open(os.path.join(tmp, "h2.h")).read()
    check(h1 == h2, "the DKSH header build is not deterministic"); check(h1 == HDR, "dk_scale_dksh.h differs from a fresh build_scale_dksh.sh run")
except OSError as e: check(False, "no regenerated header: %s" % e)
PROG = {}   # name -> (control bytes, code bytes)
for n in ("vs", "fs0", "fs1", "fs2"):
    ct, cd = (int(x) for x in re.search(r"SC_%s_CTL = (\d+), SC_%s_CODE = (\d+)" % (n, n), HDR).groups())
    body = bytes(int(x) for x in re.search(r"sc_%s\[\] = \{(.*?)\};" % n, HDR, re.S).group(1).replace("\n", "").strip(",").split(","))
    check(len(body) == ct + cd and struct.unpack_from("<II", body, 8) == (ct, cd), "header array of %s is not a DKSH image of its sizes" % n)
    PROG[n] = (body[:ct], body[ct:])

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
def fn(name):
    f = re.search(r"^(?:int|float) %s\(.*?^\}\n" % name, FS, re.S | re.M); check(f is not None, name + " not found in dk_scale_fs.glsl"); return f.group(0) if f else ""
def to_c(t):   # the GLSL spelling of the two helpers as C: constructors become casts, float literals get the f suffix
    t = re.sub(r"\b(float|uint|int)\(", r"(\1)(", t); return re.sub(r"(\d+\.\d+)", r"\1f", t)
DIVX = to_c(fn("divx")); GAMMA = to_c(fn("gamma_of"))
check("QEST" not in DIVX and "int q = (int)((float)(n) / (float)(m));" in DIVX, "divx is not the float-estimate form the model replaces")
DIVX_M = DIVX.replace("int q = (int)((float)(n) / (float)(m));", "int q = QEST(n, m);")
model = os.path.join(tmp, "model.c")
open(model, "w").write(r'''#include <math.h>
#include <stdio.h>
#include <stdint.h>
typedef unsigned uint;
#define clamp(x, a, b) ((x) < (a) ? (a) : (x) > (b) ? (b) : (x))
#define floor floorf
static uint ramp[48][4]; static int dq, real;
#define QEST(n, m) (real ? (int)((float)(n) / (float)(m)) : (n) / (m) + dq)
''' + DIVX_M + "\n" + GAMMA + r'''
static long long bad, cnt; static int maxerr;
static void pair(int sw, int dw) {
    for (int f = 0; f < dw; f++) {
        int n = (2 * f + 1) * sw, m = 2 * dw; long long ref = ((2LL * f + 1) * sw) / (2LL * dw);
        for (dq = -1; dq <= 1; dq++) { real = 0; cnt++; if (divx(n, m) != ref) bad++; }
        real = 1; cnt++; if (divx(n, m) != ref) bad++;
        int e = (int)((float)n / (float)m) - (int)ref; if (e < 0) e = -e; if (e > maxerr) maxerr = e;
        if (!(ref >= 0 && ref < sw)) bad++;   /* the source texel is inside the source rect */
    }
}
static unsigned pat(int c, int i) { return (unsigned)((i * 257u + c * 4099u + (i >> 3) * 31u) ^ (c << 9)) & 0xFFFF; }
int main(void) {
    for (int sw = 1; sw <= 128; sw++) for (int dw = 1; dw <= 128; dw++) pair(sw, dw);
    int B[] = {1, 2, 3, 255, 256, 257, 4095, 4096, 4097, 8191, 8192, 8193, 16383, 16384};
    for (unsigned a = 0; a < sizeof B / sizeof *B; a++) for (unsigned b = 0; b < sizeof B / sizeof *B; b++) pair(B[a], B[b]);
    printf("DIV %lld %lld %d\n", cnt, bad, maxerr);
    for (int k = 0; k < 768; k++) ramp[k >> 4][(k >> 2) & 3] |= (pat(k / 256, k % 256) >> 8) << ((k & 3) * 8);
    long long gb = 0, gc = 0;
    for (int c = 0; c < 3; c++) for (int v = 0; v < 256; v++) {
        float x = (float)v / 255.0f, want = (float)(pat(c, v) >> 8) / 255.0f; gc++; if (gamma_of(x, c) != want) gb++;
    }
    for (int c = 0; c < 3; c++) { gc += 3; if (gamma_of(-1.0f, c) != (float)(pat(c, 0) >> 8) / 255.0f) gb++; if (gamma_of(2.0f, c) != (float)(pat(c, 255) >> 8) / 255.0f) gb++;
                                  if (gamma_of(0.5f, c) != (float)(pat(c, 128) >> 8) / 255.0f) gb++; }
    printf("GAM %lld %lld\n", gc, gb);
    return 0;
}
''')
b = sh(["gcc", "-O1", "-Wall", "-Werror", "-fno-fast-math", "-ffp-contract=off", model, "-lm", "-o", os.path.join(tmp, "model")])
check(b.returncode == 0, "model build: " + tail(b.stderr))
model_checks = 0
if b.returncode == 0:
    o = sh([os.path.join(tmp, "model")]).stdout.split()
    try:
        check(o[0] == "DIV" and int(o[2]) == 0 and int(o[1]) > 3000000 and int(o[3]) <= 1, "divx model: %s" % o)
        check(o[4] == "GAM" and int(o[6]) == 0 and int(o[5]) == 777, "gamma_of model: %s" % o); model_checks = 2
    except (IndexError, ValueError): check(False, "model output: %s" % o)

# ---- e ------------------------------------------------------------------------------------------------------------------------------------------
consts = os.path.join(tmp, "consts.c")
open(consts, "w").write('''#include <stdio.h>
#include "dk_fmt.h"
int main(void) { DkImage im; DkImageView v; DkSampler s; dkImageViewDefaults(&v, &im); dkSamplerDefaults(&s);
 printf("%d %d %d %d %d %d %d %d %d %d %d %d %d %d %u %u %d %d %d %d %d %d %d %d %d %g %u %u %u\\n", (int)DkImageType_2D, (int)DkImageType_2DArray, (int)DkImageType_3D,
 (int)v.swizzle[0], (int)v.swizzle[1], (int)v.swizzle[2], (int)v.swizzle[3], (int)DkFilter_Nearest, (int)DkMipFilter_None, (int)DkWrapMode_ClampToEdge,
 (int)dk_fmt(1).format, (int)dk_fmt(2).format, (int)DkBarrier_Full, (int)DkPrimitive_Triangles, (unsigned)(DkStageFlag_Vertex | DkStageFlag_Fragment),
 (unsigned)(DkInvalidateFlags_Descriptors), (int)DkStage_Fragment, (int)s.minFilter, (int)s.compareEnable, (int)v.type, (int)v.layerOffset, (int)v.layerCount,
 (int)v.mipLevelOffset, (int)v.mipLevelCount, 0, (double)s.maxAnisotropy, (unsigned)DkInvalidateFlags_Image, (unsigned)(DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code),
 (unsigned)DK_SHADER_CODE_UNUSABLE_SIZE); return 0; }
''')
b = sh(["gcc", "-std=gnu11", "-w"] + INC + [consts, os.path.join(NXD, "dk_fmt.c"), "-o", os.path.join(tmp, "consts")]); check(b.returncode == 0, "consts build: " + tail(b.stderr))
K = sh([os.path.join(tmp, "consts")]).stdout.split() if b.returncode == 0 else ["0"] * 29
(T2D, T2DA, T3D, SW0, SW1, SW2, SW3, NEAR, MIPN, CLAMP, F_RGBA8, F_BGRA8, FULL, TRI, VF_MASK, INV_DESC, STG_FRAG, _, CMP, _, _, _, _, _, _, ANISO, INV_IMG,
 CODE_FLAGS, UNUSABLE) = [float(x) if "." in x else int(x) for x in K]
FSTAGE = STG_FRAG
VIEW_LAYERS = struct.unpack("<2i", b"\0" * 8)  # placeholder to keep the unpack imports honest; the default view layers are read from the double below
def pat(c, i): return ((i * 257 + c * 4099 + (i >> 3) * 31) ^ (c << 9)) & 0xFFFF
def fnv(b):
    h = 2166136261
    for x in b: h = ((h ^ x) * 16777619) & 0xFFFFFFFF
    return h
OFFS = {}; off = 0
for n in ("vs", "fs0", "fs1", "fs2"): OFFS[n] = off; off += (len(PROG[n][1]) + 255) & ~255
CODE_BYTES = off; CODE_SIZE = (off + UNUSABLE + 4095) & ~4095
def fmtf(x): return "%g" % x

class Host:
    def __init__(self, exe):
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"))
    def cmd(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush(); ev = []
        while True:
            out = self.p.stdout.readline()
            if not out: raise RuntimeError("host died: " + tail(self.p.stderr.read(), 6))
            out = out.rstrip("\n")
            if out.startswith("EV "): ev.append(out[3:])
            else: return out, ev
    def call(self, line):   # -> (ret, errors, message, events)
        out, ev = self.cmd(line); m = re.match(r"R ([0-9a-f]+) E (\d+) M (.*)$", out)
        if not m: raise RuntimeError("bad reply to %s: %s" % (line, out))
        return int(m.group(1), 16), int(m.group(2)), m.group(3), ev
    def close(self):
        self.p.stdin.close(); rc = self.p.wait(timeout=60); return rc, self.p.stderr.read()

# images: (id, type, fmt, w, h, samples, mips, layers); type 0 2D, 1 cube, 2 3D; fmt 1 RGBA8, 2 BGRA8, 11 Z24S8, 17 A4R4G4B4
IMGS = [(0, 0, 1, 640, 480, 1, 3, 1), (1, 0, 1, 1280, 720, 1, 1, 1), (2, 1, 1, 256, 256, 1, 1, 6), (3, 2, 1, 64, 64, 1, 1, 1), (4, 0, 11, 64, 64, 1, 1, 1),
        (5, 0, 1, 64, 64, 4, 1, 1), (6, 0, 2, 1024, 768, 1, 1, 1), (7, 0, 1, 16384, 16384, 1, 1, 1), (8, 0, 1, 16384, 16384, 1, 1, 1), (9, 0, 17, 64, 64, 1, 1, 1),
        (10, 0, 1, 64, 64, 1, 1, 1)]
def sub(i, lv, ly, x, y, w, h): return (i, lv, ly, x, y, w, h)
def sstr(s): return ":".join(str(v) for v in s)
def expect(s, d, ramp, ser):
    """The exact recorded sequence of nxdk_scale_point(s -> d)."""
    im = IMGS[s[0]]; kind = {0: 0, 1: 1, 2: 2}[im[1]]; ty = [T2D, T2DA, T3D][kind]
    lay = (0, 6) if kind == 1 else (0, VLAY)
    fmt = 0   # the default view: DkImageFormat_None = the image's own (non-sRGB) format
    sw = "%d%d%d%d" % (SW0, SW1, SW2, SW3)
    ev = ["imgdesc serial=%d im=%d type=%d fmt=%d sw=%s mip=%d,1 lay=%d,%d lo=0 ms=0" % (ser + 1, s[0], ty, fmt, sw, s[1], lay[0], lay[1]),
          "smpdesc serial=%d min=%d mag=%d mip=%d wrap=%d,%d,%d cmp=0 aniso=%s" % (ser + 2, NEAR, NEAR, MIPN, CLAMP, CLAMP, CLAMP, fmtf(ANISO)),
          "query_pause", "target_bind im=%d id=%d level=%d layer=%d rect=%d,%d,%d,%d" % (d[0], d[0], d[1], d[2], d[3], d[4], d[5], d[6]),
          "draw_neutral %d %d" % (max(1, IMGS[d[0]][3] >> d[1]), max(1, IMGS[d[0]][4] >> d[1])),
          "viewport first=0 n=1 %d,%d,%d,%d,0,1" % (d[3], d[4], d[5], d[6]), "scissor first=0 n=1 %d,%d,%d,%d" % (d[3], d[4], d[5], d[6])]
    ctl = lambda n: "%08x" % fnv(PROG[n][0][:struct.unpack_from("<I", PROG[n][0], 8)[0]])
    ev.append("bind_shaders mask=%d n=2 [ctl=%s code=0 stage=0] [ctl=%s code=%d stage=1]" % (VF_MASK, ctl("vs"), ctl("fs%d" % kind), OFFS["fs%d" % kind]))
    ev += ["bind_img_set blk=@ off=1024 n=1 serial=%d" % (ser + 1), "bind_smp_set blk=@ off=1056 n=1 serial=%d" % (ser + 2), "barrier mode=%d flags=%d" % (FULL, INV_DESC),
           "bind_tex stage=%d id=31 n=1 h0=%d" % (FSTAGE, 0), "ubo stage=%d id=15 n=1 blk=@ off=0 size=816 data=@" % FSTAGE,
           "draw prim=%d v=3 i=1 fv=0 fi=0" % TRI, "bind_descriptors_global", "barrier mode=%d flags=%d" % (FULL, INV_DESC | INV_IMG), "query_resume"]
    return ev
def ubo_bytes(s, d, ramp):
    b = struct.pack("<12i", s[3], s[4], s[5], s[6], d[3], d[4], d[5], d[6], 1 if ramp else 0, s[2] if IMGS[s[0]][1] else 0, 0, 0)
    if ramp: b += bytes(pat(k // 256, k % 256) >> 8 for k in range(768))
    else: b += bytes(768)
    return b
VLAY = 0

def scenarios(exe):
    f, n = [], [0]
    def ck(ok, msg):
        n[0] += 1
        if not ok: f.append(msg)
    h = Host(exe)
    for im in IMGS: h.cmd("img %d %d %d %d %d %d %d %d" % im)
    # a scale before init
    r = h.call("scale %s %s none" % (sstr(sub(0, 0, 0, 0, 0, 8, 8)), sstr(sub(1, 0, 0, 0, 0, 8, 8))))
    ck(r[0] == 0 and r[1] == 1 and "not initialised" in r[2] and r[3] == [], "scale before init: one error, nothing recorded: %s" % (r,))
    # init: refusal, success, twice, shutdown, balance
    h.cmd("failblock 1"); r = h.call("init"); h.cmd("failblock 0")
    ck(r[0] != 0 and r[1] == 1 and h.cmd("state")[0] == "S live 0 made 0 staging 0", "init with a refused block: nonzero, one error, nothing live: %s %s" % (r, h.cmd("state")[0]))
    r = h.call("init"); ck(r[0] == 0 and r[1] == 0 and r[3] == [], "init ok: %s" % (r,))
    mb = re.match(r"B size (\d+) flags (\d+) live 1 data (\w*)", h.cmd("block 0 %d" % CODE_BYTES)[0])
    ck(mb and int(mb.group(1)) == CODE_SIZE and int(mb.group(2)) == CODE_FLAGS, "code block: size %d (code + 0x400 rounded to 4 KiB), Code flag: %s" % (CODE_SIZE, mb and mb.groups()[:2]))
    if mb:
        data = bytes.fromhex(mb.group(3)); good = True
        for nme in PROG: good &= data[OFFS[nme]:OFFS[nme] + len(PROG[nme][1])] == PROG[nme][1]
        ck(good, "code block holds the four code sections at their offsets")
    ck(h.cmd("state")[0] == "S live 1 made 1 staging 0", "init makes one block")
    r = h.call("init"); ck(r[0] != 0 and r[1] == 1 and h.cmd("state")[0] == "S live 1 made 1 staging 0", "second init is refused, no second block: %s" % (r,))
    # the sequences
    ser = [0]
    PAIRS = [(sub(0, 0, 0, 0, 0, 640, 480), sub(1, 0, 0, 0, 0, 1280, 720), False), (sub(0, 1, 0, 10, 20, 100, 50), sub(1, 0, 0, 300, 200, 33, 77), True),
             (sub(2, 0, 3, 0, 0, 256, 256), sub(1, 0, 0, 5, 5, 1, 1), False), (sub(3, 0, 5, 1, 2, 3, 4), sub(6, 0, 0, 0, 0, 1000, 700), True),
             (sub(7, 0, 0, 0, 0, 16384, 16384), sub(8, 0, 0, 0, 0, 16384, 16384), False), (sub(0, 2, 0, 0, 0, 160, 120), sub(1, 0, 0, 1279, 719, 1, 1), True),
             (sub(10, 0, 0, 63, 63, 1, 1), sub(1, 0, 0, 0, 0, 1280, 720), True),
             (sub(1, 0, 0, 0, 0, 1280, 720), sub(0, 1, 0, 3, 4, 50, 40), False), (sub(1, 0, 0, 0, 0, 640, 360), sub(0, 2, 0, 0, 0, 160, 120), True)]   # destination levels 1, 2
    for s, d, ramp in PAIRS:
        r = h.call("scale %s %s %s" % (sstr(s), sstr(d), "p" if ramp else "none")); want = expect(s, d, ramp, ser[0]); ser[0] += 2
        got = [re.sub(r"blk=\d+", "blk=@", e) for e in r[3]]; blk = re.search(r"blk=(\d+)", r[3][-5]) if len(r[3]) > 5 else None
        data = None
        if r[3] and r[3][-5].startswith("ubo"): data = re.search(r"data=(\w+)", r[3][-5]).group(1); got[-5] = re.sub(r"data=\w+", "data=@", got[-5])
        ck(r[:2] == (0, 0) and got == want, "sequence %s -> %s ramp %s:\n   got  %s\n   want %s" % (sstr(s), sstr(d), ramp, got, want))
        ck(len({re.search(r"blk=(\d+)", e).group(1) for e in r[3] if "blk=" in e}) == 1, "descriptor sets and the uniform buffer come from one staging block")
        if blk:   # P1: the ramp was poisoned after the call; the staging memory holds the uniforms already
            nb = re.match(r"B size (\d+) flags (\d+) live 1 data (\w*)", h.cmd("block %s 816" % blk.group(1))[0])
            ck(nb and bytes.fromhex(nb.group(3)) == ubo_bytes(s, d, ramp), "staging bytes after the call = uniforms: src, dst, mode / layer, ramp high bytes")
            ck(data is not None and bytes.fromhex(data) == ubo_bytes(s, d, ramp), "uniform data at the bind = %s" % ubo_bytes(s, d, ramp)[:48].hex())
    # refusals: (name, scale args, text); nothing is recorded, ONE error
    R = lambda s, d: "scale %s %s none" % (sstr(s), sstr(d)); D = sub(1, 0, 0, 0, 0, 8, 8)
    REFUSE = [("depth source", R(sub(4, 0, 0, 0, 0, 8, 8), D), "depth"), ("depth destination", R(sub(0, 0, 0, 0, 0, 8, 8), sub(4, 0, 0, 0, 0, 8, 8)), "depth"),
              ("multisampled source", R(sub(5, 0, 0, 0, 0, 8, 8), D), "multisampled"), ("multisampled destination", R(sub(0, 0, 0, 0, 0, 8, 8), sub(5, 0, 0, 0, 0, 8, 8)), "multisampled"),
              ("destination not renderable", R(sub(0, 0, 0, 0, 0, 8, 8), sub(9, 0, 0, 0, 0, 8, 8)), "render target"),
              ("source width 0", R(sub(0, 0, 0, 0, 0, 0, 8), D), "rect"), ("source height 0", R(sub(0, 0, 0, 0, 0, 8, 0), D), "rect"),
              ("destination width 0", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 0, 0, 0, 0, 0, 8)), "rect"), ("destination height 0", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 0, 0, 0, 0, 8, 0)), "rect"),
              ("source past the level (x)", R(sub(0, 0, 0, 1, 0, 640, 8), D), "rect"), ("source past the level (y)", R(sub(0, 0, 0, 0, 473, 8, 8), D), "rect"),
              ("source past level 1", R(sub(0, 1, 0, 0, 0, 321, 8), D), "rect"), ("destination past the image (x)", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 0, 0, 1273, 0, 8, 8)), "rect"),
              ("destination past the image (y)", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 0, 0, 0, 713, 8, 8)), "rect"),
              ("source x + w wraps 32 bits", R(sub(0, 0, 0, 0xFFFFFFFF, 0, 2, 8), D), "rect"), ("source level 3 of 3", R(sub(0, 3, 0, 0, 0, 1, 1), D), "level"),
              ("destination level 1 of 1", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 1, 0, 0, 0, 1, 1)), "level"), ("2D layer 1", R(sub(0, 0, 1, 0, 0, 8, 8), D), "layer"),
              ("cube layer 6", R(sub(2, 0, 6, 0, 0, 8, 8), D), "layer"), ("3D slice 8 of 8", R(sub(3, 0, 8, 0, 0, 8, 8), D), "layer"),
              ("destination layer 1", R(sub(0, 0, 0, 0, 0, 8, 8), sub(1, 0, 1, 0, 0, 8, 8)), "layer")]
    for name, line, text in REFUSE:
        r = h.call(line); ck(r[0] == 0 and r[1] == 1 and text in r[2] and r[3] == [], "refused '%s' with '%s', nothing recorded: %s" % (name, text, r))
    h.cmd("dead 0"); r = h.call(R(sub(0, 0, 0, 0, 0, 8, 8), D)); ck(r[1] == 1 and "live" in r[2] and r[3] == [], "dead source: %s" % (r,)); h.cmd("img 0 0 1 640 480 1 3 1")
    h.cmd("dead 1"); r = h.call(R(sub(0, 0, 0, 0, 0, 8, 8), D)); ck(r[1] == 1 and "live" in r[2] and r[3] == [], "dead destination: %s" % (r,)); h.cmd("img 1 0 1 1280 720 1 1 1")
    r = h.call("scalenull 0:0:0:0:0:8:8"); ck(r[1] == 2 and r[3] == [], "NULL source or destination: one error each, nothing recorded: %s" % (r,))
    h.cmd("failstage 1"); r = h.call(R(sub(0, 0, 0, 0, 0, 8, 8), D)); h.cmd("failstage 0")
    ck(r[0] == 0 and r[1] == 1 and "staging" in r[2] and r[3] == [], "staging refused: the refusal's own error only, nothing recorded: %s" % (r,))
    # shutdown
    h.cmd("stagefree"); r = h.call("shutdown"); ck(r[1] == 0 and h.cmd("state")[0] == "S live 0 made %d staging 0" % int(h.cmd("state")[0].split()[4]), "shutdown frees the code block: %s" % h.cmd("state")[0])
    r = h.call("shutdown"); ck(r[1] == 0 and r[3] == [], "second shutdown is harmless")
    r = h.call(R(sub(0, 0, 0, 0, 0, 8, 8), D)); ck(r[1] == 1 and "not initialised" in r[2], "scale after shutdown: %s" % (r,))
    r = h.call("init"); ck(r[0] == 0 and r[1] == 0, "init again after shutdown"); h.call("shutdown")
    rc, err = h.close(); ck(rc == 0 and not err.strip(), "host exit %d: %s" % (rc, tail(err, 6)))
    return n[0], f

def host_build(src, exe):
    return sh(["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + INC +
              [os.path.join(HERE, "host_main.c"), src, os.path.join(NXD, "dk_fmt.c"), "-o", exe])

def run_host(src, tag):
    exe = os.path.join(tmp, "host_" + tag); b = host_build(src, exe)
    if b.returncode: return None, ["build: " + tail(b.stderr, 6)]
    try: return scenarios(exe)
    except Exception as e: return None, ["host: " + str(e)[:300]]

# the default view layer count of a 2D / 3D source view (from the real header): read once through the double's own init of an untouched view
vl = sh(["gcc", "-std=gnu11", "-w"] + INC + ["-x", "c", "-", "-o", os.path.join(tmp, "vl")], input='#include <stdio.h>\n#include <deko3d.h>\nint main(void){DkImage i;DkImageView v;dkImageViewDefaults(&v,&i);printf("%d\\n",(int)v.layerCount);return 0;}\n')
VLAY = int(sh([os.path.join(tmp, "vl")]).stdout.split()[0]) if vl.returncode == 0 else 0
n, bad = run_host(os.path.join(NXD, "dk_scale.c"), "base")
for b_ in bad or []: check(False, "host run: " + b_)
if n is not None and not bad: print("checks passed %d/%d (ASan + UBSan) + %d model checks (exhaustive)" % (n, n, model_checks))

# ---- f ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ('query pause dropped', '    nxdk_query_pause();\n', ''),
    ('query resume dropped', '    nxdk_query_resume();\n', ''),
    ('draw of 4 vertices', 'DkPrimitive_Triangles, 3, 1, 0, 0', 'DkPrimitive_Triangles, 4, 1, 0, 0'),
    ('ramp byte shift 7', '(uint8_t)(ramp[k] >> 8)', '(uint8_t)(ramp[k] >> 7)'),
    ('ramp flag always on', 'ramp ? 1 : 0,', '1,'),
    ('viewport swaps x / y', '{ (float)d->x, (float)d->y,', '{ (float)d->y, (float)d->x,'),
    ('scissor swaps w / h', 'DkScissor sc = { d->x, d->y, d->w, d->h };', 'DkScissor sc = { d->x, d->y, d->h, d->w };'),
    ('fixed 2D variant', '&sc_sh[1 + kind]', '&sc_sh[1]'),
    ('level bound off by one', 's->level >= im->mips', 's->level > im->mips'),
    ('rect sum wraps in 32 bits', '(uint64_t)s->x + s->w > lw', '(uint32_t)s->x + s->w > lw'),
    ('staging not cleared', '    memset(cpu, 0, SC_STAGE);\n', ''),
    ('last barrier without image flag', 'DkInvalidateFlags_Descriptors | DkInvalidateFlags_Image', 'DkInvalidateFlags_Descriptors'),
    ('descriptors not restored', '    nxdk_bind_descriptors(cmd);\n', ''),
    ('multisample bound', 'im->samples > 1', 'im->samples > 4'),
    ('double init allowed', '    if (sc_code) { nxdk_err("scale: init: already initialised"); return 1; }\n', ''),
    ('shutdown leaks the block', '    dkMemBlockDestroy(sc_code);\n', ''),
    ('render target check off', 'if (dst && !(dk_fmt', 'if (0 && !(dk_fmt'),
    ('3D slice bound ignored', 'im->type == NxImgType_3D ? im->d : im->layers', 'im->layers'),
    ('cube view of one face', 'v.layerCount = im->layers', 'v.layerCount = 1'),
    ('layer not passed', 'kind ? (int32_t)s->layer : 0', '0'),
    ('linear filter', 'smp.minFilter = smp.magFilter = DkFilter_Nearest;', 'smp.minFilter = smp.magFilter = DkFilter_Linear;'),
    ('first barrier without invalidate', 'dkCmdBufBarrier(cmd, DkBarrier_Full, DkInvalidateFlags_Descriptors);', 'dkCmdBufBarrier(cmd, DkBarrier_Full, 0);'),
    ('uniform range cut', 'g, SC_UBO_BYTES);', 'g, 48);'),
    ('neutral size ignores the level', 'nxchk_lvl(d->im->w, d->level), nxchk_lvl(d->im->h, d->level)', 'd->im->w, d->im->h'),
    ('neutral size swaps w / h', 'nxchk_lvl(d->im->w, d->level), nxchk_lvl(d->im->h, d->level)', 'nxchk_lvl(d->im->h, d->level), nxchk_lvl(d->im->w, d->level)'),
    ('neutral size is the sub-rect', 'nxchk_lvl(d->im->w, d->level), nxchk_lvl(d->im->h, d->level)', 'd->w, d->h'),
    ('NULL destination unchecked', 'if (!s || !d)', 'if (!s)'),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None and not bad:
    for i, (name, a, b) in enumerate(MUT):
        if SRC.count(a) != 1: check(False, "mutation '%s': anchor occurs %d times" % (name, SRC.count(a))); continue
        p = os.path.join(tmp, "mut%d.c" % i); open(p, "w").write(SRC.replace(a, b))
        mn, mbad = run_host(p, "m%d" % i)
        if mn is None or mbad: caught += 1; print("  mutation %-40s caught (%s)" % (name, (mbad or ["?"])[0][:70]))
        else: check(False, "mutation '%s' NOT CAUGHT" % name)
    print("f  mutations caught %d/%d" % (caught, len(MUT)))
    check(caught == len(MUT), "a mutation was not caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
