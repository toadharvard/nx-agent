#!/usr/bin/env python3
"""test_dkmsres.py: tests of render/d3d9/backend/nx/dk_msres.c (deko3d backend: the multisample-resolve primitive with internal DKSH programs).
    python3 -I tests/l6/dkmsres/test_dkmsres.py [--no-mutations]   needs gcc, the devkitA64 cross compiler, libnx / deko3d headers (FNVNX_DK) and uam (UAM)
 a  static: <= 300 lines, <= 160 columns, no stub-class word, no empty function body, no weak symbol, no attribute; the format table has no integer
    renderable format (the proof that dk_msres.c needs no integer branch): every DKFMT_RENDER row is a Unorm / Snorm / Float deko3d format
 b  cross build (aarch64, -Werror) by build_dk.sh; undefined symbols of dk_msres.o are deko3d calls, nxdk / nxchk_err hooks and libc
 c  generated header: build_msres_dksh.sh regenerates dk_msres_dksh.h byte for byte (twice, deterministic); every array is a DKSH image of its sizes
 d  host run of the real product file (+ dk_fmt.c) under ASan + UBSan against a recording double (host_main.c): the exact recorded sequence of every
    (format of dk_fmt.c) x (mode) x (2, 4, 8 samples): view type 2DMS, dsSource, sampler / texture slot, render target, depth / stencil state, one draw
    (eight for the stencil) with its own uniform range, state restore; every refusal; layers of cube destinations; init / shutdown balance; the code block
 e  mutations of a copy of the product file: every one must be caught
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

tmp = tempfile.mkdtemp(prefix="dkmsres_")
SRC = open(os.path.join(NXD, "dk_msres.c")).read()
HDR = open(os.path.join(NXD, "dk_msres_dksh.h")).read()
FMTC = open(os.path.join(NXD, "dk_fmt.c")).read()

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
check(len(SRC.splitlines()) <= 300, "dk_msres.c is longer than 300 lines")
check(max(len(l) for l in SRC.splitlines()) <= 160, "dk_msres.c has a line longer than 160 characters")
m = FORBID.search(SRC); check(not m, "dk_msres.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_msres.c has an empty function body")
check("__attribute__" not in SRC, "dk_msres.c uses an attribute")
rows = re.findall(r"case NxFmt_(\w+): return \(DkFmt\)ROW\((\w+), (\w+), \w+, \d+, ([^)]*)\);", FMTC)
check(len(rows) == 35, "dk_fmt.c has %d rows, the proof is over 35" % len(rows))
render = [r for r in rows if re.search(r"\bCR\b|DKFMT_RENDER", r[3])]
check(len(render) == 23 and all(re.search(r"_(Unorm|Snorm|Float)$", r[1]) and not re.search(r"int", r[1], re.I) for r in render),
      "a DKFMT_RENDER row is not Unorm / Snorm / Float: %s" % [r[1] for r in render if not re.search(r"_(Unorm|Snorm|Float)$", r[1])])
check(not any(re.search(r"Uint|Sint", r[1]) for r in rows), "an integer format exists in dk_fmt.c: dk_msres.c would need an integer branch")

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out]); check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    syms = [s for s in sh([NM, "-u", os.path.join(out, "dk_msres.o")]).stdout.split() if s != "U"]
    check({"nxdk_query_pause", "nxdk_target_bind", "nxdk_stage_alloc", "nxdk_draw_neutral", "nxdk_s", "nxchk_err"} <= set(syms), "dk_msres.o does not use the hooks: " + " ".join(syms))
    check(all(OK_UND.match(s) for s in syms), "unexpected undefined symbols: " + " ".join(s for s in syms if not OK_UND.match(s)))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, os.path.join(out, "dk_msres.o")]).stdout, re.M), "a weak symbol is present")
    dfn = sh([NM, "--defined-only", os.path.join(out, "dk_msres.o")]).stdout
    check({"nxdk_ms_resolve", "nxdk_msres_init", "nxdk_msres_shutdown"} <= set(re.findall(r"\sT\s(\w+)", dfn)), "dk_msres.o does not define the three entry points")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
env = dict(os.environ); env.setdefault("UAM", "/home/user/work/l6a/uam/bin/uam")
for k in (1, 2):
    r = sh(["sh", os.path.join(NXD, "build_msres_dksh.sh"), os.path.join(tmp, "h%d.h" % k)], env=env)
    check(r.returncode == 0, "build_msres_dksh.sh: " + tail(r.stderr + r.stdout))
try:
    h1, h2 = open(os.path.join(tmp, "h1.h")).read(), open(os.path.join(tmp, "h2.h")).read()
    check(h1 == h2, "the DKSH header build is not deterministic"); check(h1 == HDR, "dk_msres_dksh.h differs from a fresh build_msres_dksh.sh run")
except OSError as e: check(False, "no regenerated header: %s" % e)
check(re.match(r"/\* generated by build_msres_dksh.sh: sources [0-9a-f]{16}; do not edit \*/\n", HDR) is not None, "first line of the header is not the sources hash line")
PROG = {}   # name -> (control bytes, code bytes)
for n in ("vs", "avg", "depth", "stencil"):
    ct, cd = (int(x) for x in re.search(r"MR_%s_CTL = (\d+), MR_%s_CODE = (\d+)" % (n, n), HDR).groups())
    body = bytes(int(x) for x in re.search(r"mr_%s\[\] = \{(.*?)\};" % n, HDR, re.S).group(1).replace("\n", "").strip(",").split(","))
    check(len(body) == ct + cd and struct.unpack_from("<II", body, 8) == (ct, cd), "header array of %s is not a DKSH image of its sizes" % n)
    PROG[n] = (body[:ct], body[ct:])

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
CONSTS = [("T2D", "DkImageType_2D"), ("T2DMS", "DkImageType_2DMS"), ("DSD", "DkDsSource_Depth"), ("DSS", "DkDsSource_Stencil"), ("NEAR", "DkFilter_Nearest"),
          ("MIPN", "DkMipFilter_None"), ("CLAMP", "DkWrapMode_ClampToEdge"), ("FULL", "DkBarrier_Full"), ("TRI", "DkPrimitive_Triangles"),
          ("VF_MASK", "(DkStageFlag_Vertex | DkStageFlag_Fragment)"), ("INV_DESC", "DkInvalidateFlags_Descriptors"), ("INV_IMG", "DkInvalidateFlags_Image"),
          ("STG_FRAG", "DkStage_Fragment"), ("FB", "DkFace_FrontAndBack"), ("ALWAYS", "DkCompareOp_Always"), ("KEEP", "DkStencilOp_Keep"),
          ("REPLACE", "DkStencilOp_Replace"), ("CODE_FLAGS", "(DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached | DkMemBlockFlags_Code)"),
          ("UNUSABLE", "DK_SHADER_CODE_UNUSABLE_SIZE"), ("SW0", "DkImageSwizzle_Red"), ("SW1", "DkImageSwizzle_Green"), ("SW2", "DkImageSwizzle_Blue"),
          ("SW3", "DkImageSwizzle_Alpha"), ("FRENDER", "DKFMT_RENDER"), ("FDEPTH", "DKFMT_DEPTH"), ("FSTENCIL", "DKFMT_STENCIL"), ("NUMFMT", "NxFmt_Count"),
          ("DEFLAYERS", "v.layerCount"), ("F_RGBA8", "NxFmt_RGBA8"), ("F_BGRA8", "NxFmt_BGRA8"), ("F_Z24S8", "NxFmt_Z24S8"), ("F_Z16", "NxFmt_Z16")]
consts = os.path.join(tmp, "consts.c")
open(consts, "w").write('#include <stdio.h>\n#include "dk_fmt.h"\nint main(void) { DkImage im; DkImageView v; dkImageViewDefaults(&v, &im);\n' +
    "".join(' printf("%s %%d\\n", (int)(%s));\n' % (a, b) for a, b in CONSTS) +
    ' printf("ANISO %g\\n", ({ DkSampler s; dkSamplerDefaults(&s); (double)s.maxAnisotropy; }));\n'
    ' for (int i = 0; i < NxFmt_Count; i++) printf("F %d %d %d\\n", i, dk_fmt(i).flags, dk_fmt(i).format != DkImageFormat_None);\n return 0; }\n')
b = sh(["gcc", "-std=gnu11", "-w"] + INC + [consts, os.path.join(NXD, "dk_fmt.c"), "-o", os.path.join(tmp, "consts")]); check(b.returncode == 0, "consts build: " + tail(b.stderr))
K = {}; FLAGS = {}
if b.returncode == 0:
    for ln in sh([os.path.join(tmp, "consts")]).stdout.splitlines():
        p = ln.split()
        if p[0] == "F": FLAGS[int(p[1])] = (int(p[2]), int(p[3]))
        else: K[p[0]] = float(p[1]) if "." in p[1] else int(p[1])
class C:   # the constants as attributes
    def __getattr__(self, k): return K[k]
k = C()
def fnv(b):
    h = 2166136261
    for x in b: h = ((h ^ x) * 16777619) & 0xFFFFFFFF
    return h
OFFS = {}; off = 0
for n in ("vs", "avg", "depth", "stencil"): OFFS[n] = off; off += (len(PROG[n][1]) + 255) & ~255
CODE_BYTES = off; CODE_SIZE = (off + K.get("UNUSABLE", 0) + 4095) & ~4095
OFF_IMG, OFF_SMP, RANGE, SSIZE = 2048, 2080, 256, 4096
MODES = ("avg", "depth", "stencil")

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
        self.p.stdin.close(); rc = self.p.wait(timeout=120); return rc, self.p.stderr.read()

def ubo_bytes(samples, b): return struct.pack("<4I4f", samples, 1 << b, 0, 0, 1.0 / samples, 0.0, 0.0, 0.0) + bytes(RANGE - 32)
def expect(si, layer, di, mode, ser):
    """The exact recorded sequence of nxdk_ms_resolve(src si, layer, dst di, mode); the uniform data is checked apart (data=@)."""
    s, d = si, di; w, h = d[3], d[4]; m = MODES[mode]
    ds = k.DSS if mode == 2 else k.DSD
    sw = "%d%d%d%d" % (k.SW0, k.SW1, k.SW2, k.SW3)
    ev = ["imgdesc serial=%d im=%d type=%d fmt=0 sw=%s mip=0,1 lay=0,%d lo=0 ms=0 ds=%d" % (ser + 1, s[0], k.T2DMS, sw, k.DEFLAYERS, ds),
          "smpdesc serial=%d min=%d mag=%d mip=%d wrap=%d,%d,%d cmp=0 aniso=%g" % (ser + 2, k.NEAR, k.NEAR, k.MIPN, k.CLAMP, k.CLAMP, k.CLAMP, K["ANISO"]),
          "query_pause"]
    if mode == 0: ev.append("target_bind im=%d id=0 level=0 layer=%d rect=0,0,%d,%d" % (d[0], layer, w, h))
    else: ev += ["rt n=0 depth im=%d type=%d fmt=0 mip=0,1 lay=%d,%d ds=%d" % (d[0], k.T2D, layer, k.DEFLAYERS, k.DSD),
                 "viewport first=0 n=1 0,0,%d,%d,0,1" % (w, h), "scissor first=0 n=1 0,0,%d,%d" % (w, h)]
    ev.append("draw_neutral")
    if mode == 1: ev.append("dss dt=1 dw=1 st=0 dcmp=%d front=%d,%d,%d,%d back=%d,%d,%d,%d" % ((k.ALWAYS,) + (k.KEEP, k.REPLACE, k.KEEP, k.ALWAYS) * 2))
    if mode == 2:
        ev.append("dss dt=0 dw=0 st=1 dcmp=%d front=%d,%d,%d,%d back=%d,%d,%d,%d" % ((k.ALWAYS,) + (k.KEEP, k.REPLACE, k.KEEP, k.ALWAYS) * 2))
        ev.append("clear_ds cd=0 d=0 smask=255 sval=0")
    ctl = lambda n: "%08x" % fnv(PROG[n][0][:struct.unpack_from("<I", PROG[n][0], 8)[0]])
    ev.append("bind_shaders mask=%d n=2 [ctl=%s code=0 stage=0] [ctl=%s code=%d stage=1]" % (k.VF_MASK, ctl("vs"), ctl(m), OFFS[m]))
    ev += ["bind_img_set blk=@ off=%d n=1 serial=%d" % (OFF_IMG, ser + 1), "bind_smp_set blk=@ off=%d n=1 serial=%d" % (OFF_SMP, ser + 2),
           "barrier mode=%d flags=%d" % (k.FULL, k.INV_DESC | k.INV_IMG), "bind_tex stage=%d id=31 n=1 h0=0" % k.STG_FRAG]
    for b in range(8 if mode == 2 else 1):
        if mode == 2: ev.append("stencil face=%d mask=%d ref=%d fmask=255" % (k.FB, 1 << b, 1 << b))
        ev += ["ubo stage=%d id=15 n=1 blk=@ off=%d size=32 data=@" % (k.STG_FRAG, b * RANGE), "draw prim=%d v=3 i=1 fv=0 fi=0" % k.TRI]
    ev.append("bind_descriptors_global")
    if mode: ev.append("draw_neutral")
    ev += ["barrier mode=%d flags=%d" % (k.FULL, k.INV_DESC | k.INV_IMG), "query_resume"]
    return ev

# images: (id, type, fmt, w, h, samples, mips, layers); type 0 2D, 1 cube, 2 3D; fmt 1 RGBA8, 2 BGRA8, 11 Z24S8 ...
FM = {"RGBA8": K["F_RGBA8"], "BGRA8": K["F_BGRA8"], "Z24S8": K["F_Z24S8"], "Z16": K["F_Z16"]}

def scenarios(exe):
    f, n = [], [0]
    def ck(ok, msg):
        n[0] += 1
        if not ok: f.append(msg)
    h = Host(exe); ser = [0]
    def img(i, ty, fmt, w, hh, s, mips=1, lay=None): h.cmd("img %d %d %d %d %d %d %d %d" % (i, ty, fmt, w, hh, s, mips, lay if lay else (6 if ty == 1 else 1)))
    # a resolve before init
    img(0, 0, 1, 64, 32, 4); img(1, 0, 1, 64, 32, 1)
    r = h.call("resolve 0 0 1 0"); ck(r[0] == 1 and r[1] == 1 and "not initialised" in r[2] and r[3] == [], "resolve before init: one error, nothing recorded: %s" % (r,))
    # init: refusal, success, twice, shutdown, balance
    h.cmd("failblock 1"); r = h.call("init"); h.cmd("failblock 0")
    ck(r[0] != 0 and r[1] == 1 and h.cmd("state")[0] == "S live 0 made 0 staging 0", "init with a refused block: nonzero, one error, nothing live: %s" % (r,))
    r = h.call("init"); ck(r[0] == 0 and r[1] == 0 and r[3] == [], "init ok: %s" % (r,))
    mb = re.match(r"B size (\d+) flags (\d+) live 1 data (\w*)", h.cmd("block 0 %d" % CODE_BYTES)[0])
    ck(mb and int(mb.group(1)) == CODE_SIZE and int(mb.group(2)) == k.CODE_FLAGS, "code block: size %d (code + 0x400 rounded to 4 KiB), Code flag: %s" % (CODE_SIZE, mb and mb.groups()[:2]))
    if mb:
        data = bytes.fromhex(mb.group(3)); good = True
        for nme in PROG: good &= data[OFFS[nme]:OFFS[nme] + len(PROG[nme][1])] == PROG[nme][1]
        ck(good, "code block holds the four code sections at their offsets")
    ck(h.cmd("state")[0] == "S live 1 made 1 staging 0", "init makes one block")
    r = h.call("init"); ck(r[0] != 0 and r[1] == 1 and h.cmd("state")[0] == "S live 1 made 1 staging 0", "second init is refused, no second block: %s" % (r,))
    # the sweep: every format of dk_fmt.c x mode x samples; src id 20, dst id 21 (cube dst id 22 for layers)
    def run(si, layer, di, mode, tag):
        r = h.call("resolve %d %d %d %d" % (si[0], layer, di[0], mode)); h.cmd("stagefree")
        return r
    for fmt in range(k.NUMFMT):
        fl, valid = FLAGS[fmt]
        for samples in (2, 4, 8):
            S = (20, 0, fmt, 64, 32, samples, 1, 1); D = (21, 0, fmt, 64, 32, 1, 1, 1); img(*S); img(*D)
            for mode in (0, 1, 2):
                ok = (mode == 0 and not fl & (k.FDEPTH | k.FSTENCIL) and fl & k.FRENDER) or (mode == 1 and fl & k.FDEPTH) or (mode == 2 and fl & k.FSTENCIL)
                r = h.call("resolve 20 0 21 %d" % mode)
                tag = "fmt %d flags %#x samples %d mode %s" % (fmt, fl, samples, MODES[mode])
                if ok:
                    got = [re.sub(r"blk=\d+", "blk=@", e) for e in r[3]]; ubos = [e for e in r[3] if e.startswith("ubo")]
                    for e in ubos: got[got.index(re.sub(r"blk=\d+", "blk=@", e))] = re.sub(r"data=\w+", "data=@", re.sub(r"blk=\d+", "blk=@", e))
                    want = expect(S, 0, D, mode, ser[0]); ser[0] += 2
                    ck(r[:2] == (0, 0) and got == want, "%s:\n   got  %s\n   want %s" % (tag, got, want))
                    ck(len(ubos) == (8 if mode == 2 else 1) and all(bytes.fromhex(re.search(r"data=(\w+)", e).group(1)) == ubo_bytes(samples, i)[:32] for i, e in enumerate(ubos)),
                       "%s: uniform ranges (samples, 1 << bit, 1 / samples)" % tag)
                    ck(len({re.search(r"blk=(\d+)", e).group(1) for e in r[3] if "blk=" in e}) == 1, "%s: one staging block" % tag)
                    if mode == 2 and samples == 4:   # the staging block: eight ranges at 256-byte steps, zero padding, descriptors at 2048 / 2080
                        bi = re.search(r"blk=(\d+)", ubos[0]).group(1); nb = re.match(r"B size (\d+) flags (\d+) live 1 data (\w*)", h.cmd("block %s 2112" % bi)[0])
                        if nb:
                            sb = bytes.fromhex(nb.group(3)); ck(sb[:2048] == b"".join(ubo_bytes(samples, i) for i in range(8)) and sb[2048:2052] == struct.pack("<I", ser[0] - 1) and
                                                              sb[2080:2084] == struct.pack("<I", ser[0]), "%s: staging bytes (cleared padding, ranges, descriptors)" % tag)
                        else: ck(False, "%s: no staging block" % tag)
                else:
                    text = "depth / stencil" if mode == 0 and fl & (k.FDEPTH | k.FSTENCIL) else "render target" if mode == 0 else "without depth" if mode == 1 else "without stencil"
                    ck(r[0] == 1 and r[1] == 1 and text in r[2] and r[3] == [], "%s refused with '%s', nothing recorded: %s" % (tag, text, r))
                h.cmd("stagefree")
    # layers: a cube destination takes the face as the layer; a 2D destination only layer 0
    z24 = FM["Z24S8"]; z16 = FM["Z16"]; rg = FM["RGBA8"]
    for ty, fmt, lay in ((1, rg, 6), (1, z24, 6), (1, z16, 6), (0, rg, 1), (0, z24, 1)):
        S = (30, 0, fmt, 256, 256, 4, 1, 1); D = (31, ty, fmt, 256, 256, 1, 1, lay); img(*S); img(*D)
        fl = FLAGS[fmt][0]
        for mode in (0, 1, 2):
            if not ((mode == 0 and not fl & (k.FDEPTH | k.FSTENCIL)) or (mode == 1 and fl & k.FDEPTH) or (mode == 2 and fl & k.FSTENCIL)): continue
            for layer in range(lay):
                r = h.call("resolve 30 %d 31 %d" % (layer, mode)); got = [re.sub(r"blk=\d+", "blk=@", re.sub(r"data=\w+", "data=@", e)) for e in r[3]]
                want = expect(S, layer, D, mode, ser[0]); ser[0] += 2
                ck(r[:2] == (0, 0) and got == want, "type %d fmt %d mode %d layer %d:\n   got  %s\n   want %s" % (ty, fmt, mode, layer, got, want)); h.cmd("stagefree")
    # refusals: (name, setup images, resolve line, text); nothing is recorded, ONE error
    RES = [("unknown mode", "resolve 20 0 21 3", "mode"), ("unknown mode 0xFFFFFFFF", "resolve 20 0 21 4294967295", "mode")]
    img(20, 0, rg, 64, 32, 4); img(21, 0, rg, 64, 32, 1)
    bad = [(40, (0, rg, 64, 32, 1), "single-sample source"), (41, (0, rg, 64, 32, 3), "3 samples"), (42, (0, rg, 64, 32, 16), "16 samples"),
           (43, (1, rg, 64, 64, 4), "cube source"), (44, (2, rg, 64, 64, 4), "3D source")]
    for i, (ty, fm, w, hh, s), name in [(b[0], b[1], b[2]) for b in bad]: img(i, ty, fm, w, hh, s, 1, 6 if ty == 1 else 1)
    for i, name in ((40, "single-sample source"), (41, "3 samples"), (42, "16 samples"), (43, "cube source"), (44, "3D source")): RES.append((name, "resolve %d 0 21 0" % i, "2, 4 or 8"))
    img(45, 0, rg, 64, 32, 4); img(46, 2, rg, 64, 32, 1); img(47, 0, FM["BGRA8"], 64, 32, 1); img(48, 0, rg, 65, 32, 1); img(49, 0, rg, 64, 33, 1)
    RES += [("multisampled destination", "resolve 20 0 45 0", "single-sample"), ("3D destination", "resolve 20 0 46 0", "single-sample"),
            ("format differs", "resolve 20 0 47 0", "differs"), ("width differs", "resolve 20 0 48 0", "differs"), ("height differs", "resolve 20 0 49 0", "differs"),
            ("2D layer 1", "resolve 20 1 21 0", "layer"), ("2D layer huge", "resolve 20 4294967295 21 0", "layer"), ("NULL source", "resolve -1 0 21 0", "not live"),
            ("NULL destination", "resolve 20 0 -1 0", "not live")]
    img(50, 1, rg, 64, 32, 1, 1, 6); RES.append(("cube layer 6", "resolve 20 6 50 0", "layer"))
    for name, line, text in RES:
        r = h.call(line); ck(r[0] == 1 and r[1] == 1 and text in r[2] and r[3] == [], "refused '%s' with '%s', nothing recorded: %s" % (name, text, r))
    h.cmd("dead 20"); r = h.call("resolve 20 0 21 0"); ck(r[1] == 1 and "live" in r[2] and r[3] == [], "dead source: %s" % (r,)); img(20, 0, rg, 64, 32, 4)
    h.cmd("dead 21"); r = h.call("resolve 20 0 21 0"); ck(r[1] == 1 and "live" in r[2] and r[3] == [], "dead destination: %s" % (r,)); img(21, 0, rg, 64, 32, 1)
    h.cmd("inpass 1"); r = h.call("resolve 20 0 21 0"); h.cmd("inpass 0"); ck(r[0] == 1 and r[1] == 1 and "open pass" in r[2] and r[3] == [], "inside a pass: %s" % (r,))
    h.cmd("cmd 0"); r = h.call("resolve 20 0 21 0"); h.cmd("cmd 1"); ck(r[0] == 1 and r[1] == 1 and "SUBMIT" in r[2] and r[3] == [], "no recording buffer: %s" % (r,))
    h.cmd("failstage 1"); r = h.call("resolve 20 0 21 0"); h.cmd("failstage 0")
    ck(r[0] == 1 and r[1] == 1 and "staging" in r[2] and r[3] == [], "staging refused: the refusal's own error only, nothing recorded: %s" % (r,))
    r = h.call("resolve 20 0 21 0"); ck(r[0] == 0 and r[1] == 0 and r[3] != [], "still working after the refusals: %s" % (r,))
    # shutdown
    h.cmd("stagefree"); r = h.call("shutdown"); ck(r[1] == 0 and h.cmd("state")[0].startswith("S live 0 "), "shutdown frees the code block: %s" % h.cmd("state")[0])
    r = h.call("shutdown"); ck(r[1] == 0 and r[3] == [], "second shutdown is harmless")
    r = h.call("resolve 20 0 21 0"); ck(r[0] == 1 and r[1] == 1 and "not initialised" in r[2] and r[3] == [], "resolve after shutdown: %s" % (r,))
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

n, bad = run_host(os.path.join(NXD, "dk_msres.c"), "base")
for b_ in (bad or [])[:6]: check(False, "host run: " + b_)
if n is not None and not bad: print("d  checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- e ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ('query pause dropped', '    nxdk_query_pause();\n', ''),
    ('query resume dropped', '    nxdk_query_resume();\n', ''),
    ('draw of 4 vertices', 'DkPrimitive_Triangles, 3, 1, 0, 0', 'DkPrimitive_Triangles, 4, 1, 0, 0'),
    ('source view not 2DMS', 'v.type = DkImageType_2DMS;', 'v.type = DkImageType_2D;'),
    ('source view mip count 2', ' v.mipLevelCount = 1;', ' v.mipLevelCount = 2;'),
    ('dsSource swapped', 'DkDsSource_Depth : DkDsSource_Stencil', 'DkDsSource_Stencil : DkDsSource_Depth'),
    ('dsSource never set', 'if (mode != NXDK_MSRES_AVG) v.dsSource', 'if (0) v.dsSource'),
    ('sample count fixed 4', '{ src->samples, 1u << b, 0, 0 }', '{ 4, 1u << b, 0, 0 }'),
    ('1 / n fixed', '1.0f / (float)src->samples', '1.0f / 4.0f'),
    ('bit mask fixed', '{ src->samples, 1u << b, 0, 0 }', '{ src->samples, 1u, 0, 0 }'),
    ('seven stencil draws', '(mode == NXDK_MSRES_S0_STENCIL ? 8u : 1u)', '(mode == NXDK_MSRES_S0_STENCIL ? 7u : 1u)'),
    ('two draws for avg', '(mode == NXDK_MSRES_S0_STENCIL ? 8u : 1u)', '(mode != NXDK_MSRES_S0_STENCIL ? 2u : 8u)'),
    ('stencil mask != reference', '(uint8_t)(1u << b), (uint8_t)(1u << b), 0xFF', '(uint8_t)(1u << b), 0xFF, 0xFF'),
    ('stencil front face only', 'DkFace_FrontAndBack', 'DkFace_Front'),
    ('stencil state set for depth', 'if (mode == NXDK_MSRES_S0_STENCIL) dkCmdBufSetStencil', 'if (mode != NXDK_MSRES_AVG) dkCmdBufSetStencil'),
    ('stencil clear dropped', 'dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0xFF, 0);', '(void)0;'),
    ('stencil clear value 1', 'dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0xFF, 0);', 'dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0xFF, 1);'),
    ('stencil clear mask 0x7F', 'dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0xFF, 0);', 'dkCmdBufClearDepthStencil(cmd, false, 0.0f, 0x7F, 0);'),
    ('stencil clear also in depth mode', 'if (mode == NXDK_MSRES_S0_STENCIL) dkCmdBufClearDepthStencil', 'if (mode != NXDK_MSRES_AVG) dkCmdBufClearDepthStencil'),
    ('clear depth too', 'dkCmdBufClearDepthStencil(cmd, false,', 'dkCmdBufClearDepthStencil(cmd, true,'),
    ('depth test off', 'ds.depthTestEnable = mode == NXDK_MSRES_S0_DEPTH;', 'ds.depthTestEnable = 0;'),
    ('depth write off', 'ds.depthWriteEnable = mode == NXDK_MSRES_S0_DEPTH;', 'ds.depthWriteEnable = 0;'),
    ('depth write on for stencil', 'ds.depthWriteEnable = mode == NXDK_MSRES_S0_DEPTH;', 'ds.depthWriteEnable = 1;'),
    ('depth compare Less', 'ds.depthCompareOp = DkCompareOp_Always;', 'ds.depthCompareOp = DkCompareOp_Less;'),
    ('stencil test always on', 'ds.stencilTestEnable = mode == NXDK_MSRES_S0_STENCIL;', 'ds.stencilTestEnable = 1;'),
    ('stencil test off', 'ds.stencilTestEnable = mode == NXDK_MSRES_S0_STENCIL;', 'ds.stencilTestEnable = 0;'),
    ('depth / stencil state not reset', 'if (mode != NXDK_MSRES_AVG) nxdk_draw_neutral(cmd);', ''),
    ('descriptors not restored', '    nxdk_bind_descriptors(cmd);\n', ''),
    ('first barrier without image flag', 'DkInvalidateFlags_Descriptors | DkInvalidateFlags_Image);   /* the source', 'DkInvalidateFlags_Descriptors);   /* the source'),
    ('last barrier without image flag', 'DkInvalidateFlags_Image);\n    nxdk_query_resume', 'DkInvalidateFlags_Descriptors);\n    nxdk_query_resume'),
    ('colour layer ignored', 'const NxDkSub t = { dst, 0, 0, layer,', 'const NxDkSub t = { dst, 0, 0, 0,'),
    ('depth layer ignored', 'dv.layerOffset = layer;', 'dv.layerOffset = 0;'),
    ('depth view not 2D', 'dv.type = DkImageType_2D;', 'dv.type = DkImageType_None;'),
    ('depth view mip count 2', 'dv.mipLevelCount = 1;', 'dv.mipLevelCount = 2;'),
    ('viewport swaps w / h', '{ 0.0f, 0.0f, (float)dst->w, (float)dst->h, 0.0f, 1.0f }', '{ 0.0f, 0.0f, (float)dst->h, (float)dst->w, 0.0f, 1.0f }'),
    ('viewport far 2', '(float)dst->h, 0.0f, 1.0f }', '(float)dst->h, 0.0f, 2.0f }'),
    ('scissor swaps w / h', 'const DkScissor sc = { 0, 0, dst->w, dst->h };', 'const DkScissor sc = { 0, 0, dst->h, dst->w };'),
    ('fixed program', '&mr_sh[1 + mode]', '&mr_sh[1]'),
    ('vertex stage skipped', '&mr_sh[0], &mr_sh[1 + mode]', '&mr_sh[1 + mode], &mr_sh[1 + mode]'),
    ('layer bound off by one', 'layer >= d->layers', 'layer > d->layers'),
    ('mode bound', 'mode > NXDK_MSRES_S0_STENCIL', 'mode > 3'),
    ('any sample count', '(s->samples != 2 && s->samples != 4 && s->samples != 8)', '(s->samples < 2)'),
    ('only 2 and 4 samples', 's->samples != 8)', 's->samples != 16)'),
    ('cube source allowed', 's->type != NxImgType_2D || ', ''),
    ('multisampled destination allowed', 'd->samples != 1 || ', ''),
    ('3D destination allowed', 'd->type == NxImgType_3D', '0'),
    ('format may differ', 'd->fmt != s->fmt || ', ''),
    ('only the width compared', 'd->w != s->w || d->h != s->h', 'd->w != s->w'),
    ('avg of depth', 'if (mode == NXDK_MSRES_AVG && (f & (DKFMT_DEPTH', 'if (0 && (f & (DKFMT_DEPTH'),
    ('avg of non-render format', 'if (mode == NXDK_MSRES_AVG && !(f & DKFMT_RENDER))', 'if (0)'),
    ('depth of colour', 'if (mode == NXDK_MSRES_S0_DEPTH && !(f & DKFMT_DEPTH))', 'if (0)'),
    ('stencil of depth-only', 'if (mode == NXDK_MSRES_S0_STENCIL && !(f & DKFMT_STENCIL))', 'if (0)'),
    ('open pass allowed', 'if (nxdk_s.inPass)', 'if (0)'),
    ('no recording buffer allowed', 'if (!nxdk_s.cmd)', 'if (0)'),
    ('NULL images unchecked', '!s || !s->used || !d || !d->used', '!s->used || !d->used'),
    ('dead images allowed', '!s || !s->used || !d || !d->used', '!s || !d'),
    ('uninitialised use allowed', 'if (!mr_code) MR_FAIL("msres: not initialised");', ''),
    ('staging not cleared', '    memset(cpu, 0, MR_STAGE);\n', ''),
    ('uniform range cut', 'g + b * MR_RANGE, 32);', 'g + b * MR_RANGE, 16);'),
    ('uniform ranges overlap', 'g + b * MR_RANGE, 32);', 'g + b * 128, 32);'),
    ('uniform slot 14', 'MR_UBO = 15', 'MR_UBO = 14'),
    ('texture slot 30', 'MR_TEX = 31', 'MR_TEX = 30'),
    ('linear filter', 'smp.minFilter = smp.magFilter = DkFilter_Nearest;', 'smp.minFilter = smp.magFilter = DkFilter_Linear;'),
    ('usesLoadOrStore set', '(DkImageDescriptor*)(cpu + MR_OFF_IMG), &v, false, false', '(DkImageDescriptor*)(cpu + MR_OFF_IMG), &v, true, false'),
    ('decayMS set', '(DkImageDescriptor*)(cpu + MR_OFF_IMG), &v, false, false', '(DkImageDescriptor*)(cpu + MR_OFF_IMG), &v, false, true'),
    ('double init allowed', '    if (mr_code) { nxdk_err("msres: init: already initialised"); return 1; }\n', ''),
    ('shutdown leaks the block', '    dkMemBlockDestroy(mr_code);\n', ''),
    ('code block without Code flag', ' | DkMemBlockFlags_Code', ''),
    ('code sections aligned to 512', '(mr_prog[k].code + 255u) & ~255u', '(mr_prog[k].code + 511u) & ~511u'),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None and not bad:
    for i, (name, a, b) in enumerate(MUT):
        if SRC.count(a) != 1: check(False, "mutation '%s': anchor occurs %d times" % (name, SRC.count(a))); continue
        p = os.path.join(tmp, "mut%d.c" % i); open(p, "w").write(SRC.replace(a, b))
        mn, mbad = run_host(p, "m%d" % i)
        if mn is None or mbad: caught += 1; print("  mutation %-40s caught (%s)" % (name, (mbad or ["?"])[0][:70].replace("\n", " ")))
        else: check(False, "mutation '%s' NOT CAUGHT" % name)
    print("e  mutations caught %d/%d" % (caught, len(MUT)))
    check(caught == len(MUT), "a mutation was not caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
