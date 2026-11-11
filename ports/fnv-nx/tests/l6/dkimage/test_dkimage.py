#!/usr/bin/env python3
"""test_dkimage.py: tests of the image, transfer, sampler and read files of the deko3d backend (render/d3d9/backend/nx/dk_image.c, dk_xfer.c, dk_sampler.c, dk_read.c).
    python3 -I tests/l6/dkimage/test_dkimage.py [--no-mutations]   needs gcc, g++, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK, default
                                                                   /home/user/work/l6/dk) and the translator build for libnxgl4.so (FNVNX_SHADERS_BUILD,
                                                                   default /home/user/work/l6/build/host)
 a  static: each .c <= 300 lines; no stub-class words, no empty function body, no attribute in the product files
 b  cross build (aarch64, -Werror) by build_dk.sh; the undefined symbols are the handlers of files not written yet, the embedder hooks and library calls
 c  host selftest (host_dk.c) of the real product files under ASan + UBSan against a functional double of deko3d (dk_double.h): recorded call arguments,
    upload -> read round trips, error cases, cleanup, the X cases, the descriptor sets: every check 1, the exact number of checks, the named checks present
 d  the oracle: the same generated scripts (35 formats x {2D, cube, 3D} x mips / layers / samples / swizzle / flags, id sequences, capacity, the read and readback
    error matrices, pattern uploads read back in every face and level, samplers) run on the real product (host_dk) and on libnxgl4.so (llvmpipe) through the
    ABI; every output line must be equal. A difference is allowed only where deko3d cannot express what GL accepts (X1 .. X6 below, each decided from the
    generated case parameters and the format table before any run, never from the result): the dk side must then refuse loudly and the GL side accept
 e  mutations of copies of the product files: every one must be caught (build error, sanitizer, a failing or a missing check, a difference to GL)
Host driver limits: the GL side runs on llvmpipe, whose 8 samples and 3D depth above 2048 GL refuses; those cases (dk accepts them by the Maxwell limits) are covered
    by the host selftest only (lay_ms8, ms8_read_bad, x2_limit_ok) and are not in the generated scripts
X1  samples > 1 of a format that is neither colour-renderable nor depth (GL makes a multisampled texture of anything the driver allows)
X2  an extent above 16384 (the 2D / 3D engine and image limit of Maxwell)
X3  an image of more than 4 GiB - 4 KiB (a deko3d memory block size is 32 bit); covered by host_dk selftest x3_block_size only (GL needs the memory)
X4  image_read / readback of a multisampled depth or stencil image (the 2D engine cannot resolve them)
X5  image_read / readback of a multisampled colour image with 8 samples or of a format without the 2D engine (resolve goes through the 2D engine)
G1  GL-side only: the GL context keeps the ids 1 and 2 for its swapchain image (the script ids are the dk ids, shifted by 2 for GL), so GL has two ids fewer
    than the 4096 of the ABI; the two last ids (R of idx 4094, 4095; d 4095, 4096 of the capacity block) are accepted by dk and refused by GL
X6  a sampler wrap value 5..7 or a mip filter 3 (GL logs it and substitutes a clamped value, dk rejects before anything changes)
Output: counts, differences per X case, mutations caught, ALL OK."""
import collections, ctypes, glob, json, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
sys.path.append(os.path.join(ROOT, "tests", "l6"))
import san
san.install(preload=True)
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
SHB = os.environ.get("FNVNX_SHADERS_BUILD", "/home/user/work/l6/build/host")
ENV = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
PRODUCT = ["dk_image.c", "dk_xfer.c", "dk_sampler.c", "dk_read.c", "dk_fmt.c"]
ALLC = PRODUCT + ["dk_core.c", "dk_swap.c"]
EXPECT_CHECKS = 108
MUST_NAMES = ["lay_2d", "lay_cube", "lay_3d", "lay_ms4", "srgb_pair", "srgb_desc", "destroy_twice_bad", "capacity", "swizzle_desc", "x1_rule_all_formats", "x2_limit_ok",
              "x2_extent_bad", "x3_block_size", "create_fail_clean", "upload_fail_clean", "v3_data_short_bad", "upload_cube_args", "roundtrip_cube_faces", "roundtrip_3d",
              "roundtrip_bc1", "bc1_unaligned_bad", "rot8_roundtrip", "bgrx_x_byte", "view_reads_base", "read_errors", "readback_rgba8", "readback_depth32f",
              "readback_stencil", "ms4_resolve_read", "ms8_read_bad", "ms_depth_read_bad", "smp_wrap_table", "smp_capacity", "smp_update_wrap5_bad", "bind_sets",
              "tex_handle", "balanced_layouts", "balanced_ids", "balanced_cleanup", "balanced_xfer", "balanced_read", "balanced_ms", "balanced_sampler"]
PENDING = {"nxdk_shader_create", "nxdk_shader_destroy", "nxdk_shader_create_d3d", "nxdk_submit", "nxdk_wait_frame", "nxdk_query_result", "nxdk_present_compose"}
EMBEDDER = {"nxdk_guest_ptr", "nxdk_on_present"}
HANDLERS = {"nxdk_mem_create", "nxdk_mem_gpu_addr", "nxdk_mem_destroy", "nxdk_present", "nxdk_wait_vblank", "nxdk_shutdown", "nxdk_image_create", "nxdk_image_create4",
            "nxdk_image_destroy", "nxdk_image_read", "nxdk_readback", "nxdk_sampler_create", "nxdk_sampler_update", "nxdk_img"}
LIBC = {"memset", "memcpy", "snprintf", "vsnprintf", "malloc", "free", "calloc", "memcmp", "memmove"}
LIBCALL = re.compile(r"^(dk|nwindow|vi|event)[A-Z]\w*$")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
BAD = 0xFFFFFFFFFFFFFF02
MAXDIM = 16384
NIMG = 4096
NSMP = 256
RB_BPP = [4, 16, 4, 1]

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])
def lvl(v, l): return max(1, v >> l)
def fnv(b):
    h = 2166136261
    for c in b: h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return h
def pattern(n, seed, f=0):
    b = bytearray((i * 131 + seed * 17 + 7 + (i >> 8) * 29) & 255 for i in range(n))
    # a float depth texel is a depth value (GL clamps / canonicalises anything else on upload: driver behaviour, not a contract):
    # exponent 0x70..0x7E (a value in [2^-15, 1), the range GL keeps), sign clear; the 32-bit word that follows a Z32FS8 depth holds the stencil in its low byte only
    if f in (12, 34):
        for w in range(0, n - 3, 4):
            if f == 34 and (w >> 2) & 1: b[w + 1] = b[w + 2] = b[w + 3] = 0; continue
            v = int.from_bytes(b[w:w + 4], "little"); v = (v & 0x807FFFFF & 0x007FFFFF) | ((0x70 + ((v >> 23) % 15)) << 23)
            b[w:w + 4] = v.to_bytes(4, "little")
    return bytes(b)

# ---- the GL child: runs a script through libnxgl4.so ---------------------------------------------------------------------------------------------------
def gl_child(so, pre, scriptfile, ftab):
    os.environ.update(ENV)
    ctypes.CDLL(pre, mode=ctypes.RTLD_GLOBAL)
    lib = ctypes.CDLL(so)
    lib.nxgl4_create.restype = ctypes.c_void_p; lib.nxgl4_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    lib.nxgl4_ext.restype = ctypes.c_uint64; lib.nxgl4_ext.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32]
    err = ctypes.create_string_buffer(256)
    assert lib.nxgl4_create(16, 16, err, 256), err.value
    def ext(fn, *a):
        arr = (ctypes.c_uint64 * max(1, len(a)))(*a)
        return lib.nxgl4_ext(fn, arr, len(a))
    u8, u16, u32, u64 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64
    class Desc(ctypes.Structure): _fields_ = [("width", u16), ("height", u16), ("format", u8), ("mips", u8), ("flags", u8), ("pad0", u8), ("swizzle", u16), ("pad1", u16), ("pad2", u32), ("data", u64), ("data_size", u32), ("pad3", u32)]
    class Desc4(ctypes.Structure): _fields_ = [("width", u16), ("height", u16), ("depth", u16), ("type", u8), ("format", u8), ("mips", u8), ("samples", u8), ("flags", u8), ("pad0", u8), ("swizzle", u16), ("pad1", u16)]
    class Rd(ctypes.Structure): _fields_ = [("image", u32), ("level", u16), ("layer", u16), ("x", u32), ("y", u32), ("z", u32), ("w", u32), ("h", u32), ("d", u32), ("dst", u64), ("dst_size", u32), ("pitch", u32)]
    class Rb(ctypes.Structure): _fields_ = [("image", u32), ("x", u32), ("y", u32), ("w", u32), ("h", u32), ("format", u32), ("dst", u64), ("dst_size", u32), ("pad", u32)]
    class Smp(ctypes.Structure): _fields_ = [("filter", u8), ("maxAniso", u8), ("wrap", u16), ("minLod", u8), ("maxLod", u8), ("pad", u8 * 2), ("lodBias", ctypes.c_float), ("border", u32)]
    assert [ctypes.sizeof(t) for t in (Desc, Desc4, Rd, Rb, Smp)] == [32, 16, 48, 40, 16]
    ft = {int(k): v for k, v in json.load(open(ftab)).items()}
    def rows(f, h): return (h + 3) // 4 if ft[f][2] else h
    def rowb(f, w): return ((w + 3) // 4 if ft[f][2] else w) * ft[f][3]
    def blob_bytes(f, w, h, mips, faces): return faces * sum(rowb(f, lvl(w, l)) * rows(f, lvl(h, l)) for l in range(mips))
    def mksmp(a): return Smp(a[0], a[1], a[2], a[3], a[4], (u8 * 2)(0, 0), ctypes.c_float(a[5] / 8.0).value, a[6])
    def out(s): sys.stdout.write(s + "\n")
    GLOFF = 2   # the swapchain image of the GL context (an sRGB-viewable RGBA8 image) holds the ids 1 and 2; the script ids are the dk ids
    def gid(x): return x + GLOFF if 1 <= x <= 4096 else x
    def sid(x): return x - GLOFF if x else 0
    for line in open(scriptfile).read().splitlines():
        if not line: continue
        if line[0] == "#": out(line); continue
        t = line.split(); c = t[0]; a = [int(x, 0) for x in t[1:]] + [0] * 16
        if c in "ck":
            d = Desc4(a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[9], 0, a[8], 0)
            i = ext(10, ctypes.addressof(d)); out("R %d %d" % (a[0], sid(i)))
            if i and c == "c": out("D %d %d" % (a[0], ext(11, i)))
        elif c in "vm":
            idx, f, cube, w, h, mips, seed = a[:7]; mips = mips or 1; faces = 6 if cube else 1
            sz = blob_bytes(f, w, h, mips, faces); blob = ctypes.create_string_buffer(pattern(sz + 16, seed, f), sz + 16)
            d = Desc(w, h, f, mips, 4 if cube else 0, 0, 0, 0, 0, ctypes.addressof(blob), sz, 0)
            i = ext(3, ctypes.addressof(d)); out("R %d %d" % (idx, sid(i)))
            if c == "m": continue
            for face in range(faces if i else 0):
                for l in range(mips):
                    lw, lh = lvl(w, l), lvl(h, l); rb = rowb(f, lw); size = rb * rows(f, lh)
                    o = ctypes.create_string_buffer(b"\xcd" * size, size)
                    r = Rd(i, l, face, 0, 0, 0, lw, lh, 1, ctypes.addressof(o), size, rb)
                    out("H %d %d %d %d %d" % (idx, l, face, ext(15, ctypes.addressof(r)), fnv(o.raw[:size])))
            if i: out("D %d %d" % (idx, ext(11, i)))
        elif c == "w":
            idx, f, w, h, seed = a[:5]
            sz = blob_bytes(f, w, h, 1, 1); blob = ctypes.create_string_buffer(pattern(sz + 16, seed, f), sz + 16)
            d = Desc(w, h, f, 1, 0, 0, 0, 0, 0, ctypes.addressof(blob), sz, 0)
            i = ext(3, ctypes.addressof(d)); out("R %d %d" % (idx, sid(i)))
            for k in range(4 if i else 0):
                size = w * h * RB_BPP[k]; o = ctypes.create_string_buffer(b"\xcd" * size, size)
                r = Rb(i, 0, 0, w, h, k, ctypes.addressof(o), size, 0)
                rc = ext(8, ctypes.addressof(r)); out("B %d %d %d %s" % (idx, k, rc, ("GFD"[k] + o.raw[:size].hex()) if k < 3 else fnv(o.raw[:size])))
            if i: ext(11, i)
        elif c == "d": out("E %d" % ext(11, gid(a[0])))
        elif c == "r":
            o = ctypes.create_string_buffer(a[9] + 16)
            r = Rd(gid(a[0]), a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], 0 if a[11] else ctypes.addressof(o), a[9], a[10])
            out("E %d" % ext(15, ctypes.addressof(r)))
        elif c == "b":
            o = ctypes.create_string_buffer(a[6] + 16)
            r = Rb(gid(a[0]), a[1], a[2], a[3], a[4], a[5], ctypes.addressof(o), a[6], 0)
            out("E %d" % ext(8, ctypes.addressof(r)))
        elif c == "s": s = mksmp(a); out("S %d" % ext(4, ctypes.addressof(s)))
        elif c == "u": s = mksmp(a[1:]); out("E %d" % ext(14, a[0], ctypes.addressof(s)))
        else: raise SystemExit("gl child: unknown command " + c)
    sys.stdout.flush()
    lib.nxgl4_destroy()

if len(sys.argv) > 1 and sys.argv[1] == "--gl-child":
    gl_child(*sys.argv[2:6]); sys.exit(0)

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------------
for f in ALLC + ["dk.h"]:
    p = os.path.join(NXD, f)
    if not os.path.exists(p): check(False, "missing product file " + f); continue
    txt = open(p).read()
    if f.endswith(".c"): check(len(txt.splitlines()) <= 300, f + " is longer than 300 lines")
    m = FORBID.search(txt); check(not m, f + " contains the forbidden word " + (m.group(0) if m else ""))
    check(not re.search(r"\)\s*\{\s*\}", txt), f + " has an empty function body")
    check(not re.search(r"nxdk_guest_ptr\s*\([^;{]*\)\s*\{", txt) and "__attribute__" not in txt, f + " defines nxdk_guest_ptr or uses an attribute")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

tmp = tempfile.mkdtemp(prefix="dkimage_")
# ---- b ------------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    und, dfn = set(), set()
    for line in open(os.path.join(out, "undefined.txt")).read().splitlines():
        t = line.split()
        if len(t) == 2 and t[0] == "U": und.add(t[1])
    for line in open(os.path.join(out, "defined.txt")).read().splitlines():
        t = line.split()
        if len(t) == 3 and t[1] in "TtDdBbRrCc": dfn.add(t[2])
    ext_ = {s for s in und if s not in dfn}
    nxs = {s for s in ext_ if s.startswith("nxdk")}
    check(nxs == PENDING | EMBEDDER, "undefined nxdk symbols differ: extra %s missing %s" % (sorted(nxs - PENDING - EMBEDDER), sorted(PENDING | EMBEDDER - nxs)))
    bad = {s for s in ext_ if not s.startswith("nxdk") and not (LIBCALL.match(s) or s in LIBC)}
    check(not bad, "unexpected undefined symbols: %s" % sorted(bad))
    check(HANDLERS <= dfn and "nxdk_guest_ptr" not in dfn and "nxdk_on_present" not in dfn, "defined handlers wrong")
    objs = [os.path.join(out, o[:-2] + ".o") for o in PRODUCT]
    nw = sh([NM, "-C"] + [o for o in objs if os.path.exists(o)]).stdout
    check(not re.search(r"^\S*\s+[Ww]\s", nw, re.M), "a weak symbol is present")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- the host harness -------------------------------------------------------------------------------------------------------------------------------------
fmtcpp = glob.glob(DK + "/deko3d/**/image_formats.cpp", recursive=True)
check(len(fmtcpp) == 1, "deko3d image_formats.cpp not found under " + DK)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
imgfmt = os.path.join(tmp, "imgfmt.o")
r = sh(["g++", "-std=gnu++17", "-c", "-O1", "-I", os.path.join(HERE, "shim"), "-I", os.path.join(DK, "dkp", "libnx", "include"), "-I", os.path.join(DK, "deko3d", "include"),
        "-I", os.path.dirname(os.path.dirname(os.path.dirname(fmtcpp[0]))), fmtcpp[0], "-o", imgfmt])
check(r.returncode == 0, "deko3d image_formats.cpp: " + tail(r.stderr, 6))
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

def build_host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + srcdir, "-I" + BACK,
           "-I" + os.path.join(ROOT, "tests", "l6"), "-I" + HERE]
    cmd = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc + \
          [os.path.join(HERE, "host_dk.c")] + [os.path.join(srcdir, f) for f in PRODUCT] + [imgfmt, "-lstdc++", "-o", exe]
    return sh(cmd)

HENV = dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1")
def run_exe(exe, arg, stdin=None):
    r = sh([exe] + arg, env=HENV, input=stdin, timeout=600)
    return r.returncode, r.stdout, r.stderr

def selftest(exe):
    rc, o, e = run_exe(exe, ["selftest"])
    if rc: return None, "exit %d: %s" % (rc, tail(e, 6))
    chk = {}; n = 0
    for line in o.splitlines():
        m = re.match(r"CHK (\S+) ([01])$", line)
        if m: chk[m.group(1)] = chk.get(m.group(1), 1) & int(m.group(2))
        m = re.match(r"NCHK (\d+)$", line)
        if m: n = int(m.group(1))
    bad = [k for k, v in chk.items() if not v]
    if bad: return None, "failed checks: " + " ".join(bad[:8])
    if n != EXPECT_CHECKS or len(chk) < EXPECT_CHECKS - 40: return None, "check count %d (%d names), expected %d" % (n, len(chk), EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

base_exe = os.path.join(tmp, "host_base")
b = build_host(NXD, base_exe)
check(b.returncode == 0, "host build: " + tail(b.stderr, 8))
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
n, why = selftest(base_exe)
check(n is not None, "selftest: " + why)
if n is not None: print("selftest checks passed %d/%d (ASan + UBSan)" % (n, n))
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# the format table of the backend
rc, o, e = run_exe(base_exe, [], "f\n")
F = {}
for line in o.splitlines():
    t = line.split()
    if t and t[0] == "F": F[int(t[1])] = tuple(int(x) for x in t[2:])   # render depth compressed bpb usage2d srgb
NF = len(F)
check(NF == 35 and sorted(F) == list(range(1, 36)), "format table has %d entries" % NF)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
ftab = os.path.join(tmp, "ftab.json"); json.dump({str(k): v for k, v in F.items()}, open(ftab, "w"))
def rend(f): return F[f][0] or F[f][1]
def depth(f): return F[f][1]
def comp(f): return F[f][2]
def u2d(f): return F[f][4]
def has_srgb(f): return F[f][5]
FM = list(range(1, NF + 1))

# ---- the generated scripts --------------------------------------------------------------------------------------------------------------------------------
class Blk:
    def __init__(self, title, allow, cmds): self.title, self.allow, self.cmds = title, set(allow), cmds

def create_allow(w, h, d, f, samples):
    t = set()
    if max(w, h, d) > MAXDIM: t.add("X2")
    if samples > 1 and not rend(f): t.add("X1")
    return t

def g_main():
    B = []
    # creation matrix: 35 formats x {2D, cube, 3D} x variants; every case create + destroy ("c"), so the id sequence stays 1
    var2 = [(8, 8, 1, 0, 1, 1, 0, 0), (8, 8, 1, 0, 4, 1, 0, 0), (8, 8, 1, 0, 5, 1, 0, 0), (8, 8, 1, 0, 0, 1, 0, 0), (5, 3, 1, 0, 3, 1, 0, 0), (5, 3, 1, 0, 4, 1, 0, 0),
            (1, 1, 1, 0, 1, 1, 0, 0), (1, 1, 1, 0, 2, 1, 0, 0), (8, 8, 2, 0, 1, 1, 0, 0), (8, 8, 0, 0, 1, 1, 0, 0), (8, 8, 1, 3, 1, 1, 0, 0), (8, 8, 1, 255, 1, 1, 0, 0),
            (0, 8, 1, 0, 1, 1, 0, 0), (8, 0, 1, 0, 1, 1, 0, 0)]
    var2 += [(8, 8, 1, 0, 1, s, 0, 0) for s in (0, 2, 3, 4, 5, 9, 16, 255)] + [(8, 4, 1, 0, 1, 4, 0, 0), (8, 8, 1, 0, 2, 4, 0, 0), (8, 8, 2, 0, 1, 4, 0, 0)]
    var2 += [(8, 8, 1, 0, 1, 1, s, 0) for s in (0x3210, 0x0123, 0x5555, 0x0006, 0x6000, 0x00F0, 0xFFFF, 0x4321)] + [(8, 8, 1, 0, 1, 1, 0, fl) for fl in (1, 2, 4, 7, 255)]
    var2 += [(MAXDIM, 1, 1, 0, 1, 1, 0, 0), (1, MAXDIM, 1, 0, 1, 1, 0, 0), (MAXDIM + 1, 1, 1, 0, 1, 1, 0, 0), (1, MAXDIM + 1, 1, 0, 1, 1, 0, 0), (65535, 1, 1, 0, 1, 1, 0, 0)]
    varc = [(8, 8, 1, 1, 1, 1, 0, 0), (8, 8, 1, 1, 4, 1, 0, 0), (8, 8, 1, 1, 5, 1, 0, 0), (8, 8, 1, 1, 0, 1, 0, 0), (6, 6, 1, 1, 3, 1, 0, 0), (8, 4, 1, 1, 1, 1, 0, 0),
            (4, 8, 1, 1, 1, 1, 0, 0), (8, 8, 2, 1, 1, 1, 0, 0), (8, 8, 1, 1, 1, 4, 0, 0), (8, 8, 1, 1, 1, 1, 0x3210, 0), (8, 8, 1, 1, 1, 1, 0x7000, 0), (1, 1, 1, 1, 1, 1, 0, 0),
            (MAXDIM, MAXDIM, 1, 1, 1, 1, 0, 0) if False else (MAXDIM + 1, MAXDIM + 1, 1, 1, 1, 1, 0, 0)]
    var3 = [(8, 8, 4, 2, 1, 1, 0, 0), (8, 8, 4, 2, 4, 1, 0, 0), (8, 8, 4, 2, 5, 1, 0, 0), (8, 8, 4, 2, 0, 1, 0, 0), (8, 8, 0, 2, 1, 1, 0, 0), (8, 8, 4, 2, 1, 4, 0, 0),
            (5, 3, 7, 2, 3, 1, 0, 0), (5, 3, 7, 2, 4, 1, 0, 0), (8, 8, 4, 2, 1, 1, 0x3210, 0), (2, 2, 16, 2, 5, 1, 0, 0), (2, 2, 16, 2, 6, 1, 0, 0), (1, 1, 1, 2, 1, 1, 0, 0),
            (1, 1, 1024, 2, 1, 1, 0, 0), (1, 1, MAXDIM + 1, 2, 1, 1, 0, 0), (1, 1, 1, 2, 1, 2, 0, 0)]
    for f in FM:
        for v in var2 + varc + var3:
            w, h, d, ty, mips, smp, swz, fl = v
            B.append(Blk("create f%d %s" % (f, v), create_allow(w, h, d, f, smp), ["c 0 %d %d %d %d %d %d %d %d %d" % (w, h, d, ty, f, mips, smp, swz, fl)]))
    for f in (0, NF + 1, 36, 255):
        B.append(Blk("create bad format %d" % f, [], ["c 0 8 8 1 0 %d 1 1 0 0" % f, "c 1 8 8 8 2 %d 1 1 0 0" % f]))
    # id sequences: sRGB pairs, reuse of holes, destroy of every kind of bad id
    sr = [f for f in FM if has_srgb(f)][0]; pl = [f for f in FM if not has_srgb(f) and not comp(f) and not depth(f)][0]
    seq = ["k 0 8 8 1 0 %d 1 1 0 0" % sr, "k 1 8 8 1 0 %d 1 1 0 0" % pl, "k 2 8 8 1 0 %d 1 1 0 0" % sr, "d 2", "d 1", "d 1", "k 3 8 8 1 0 %d 1 1 0 0" % sr, "d 0", "d 4097", "d 4096",
           "d 65536", "d 4294967295", "d 4294967297", "k 4 8 8 1 0 %d 1 1 0 0" % pl, "d 3", "k 5 8 8 1 0 %d 1 1 0 0" % pl, "k 6 8 8 1 0 %d 1 1 0 0" % sr, "d 5", "d 4", "k 7 8 8 1 0 %d 1 1 0 0" % sr,
           "k 8 8 8 1 0 %d 1 1 0 0" % pl, "d 8", "d 8"]
    seq += ["d %d" % i for i in range(1, 16)]
    B.append(Blk("id sequence", [], seq))
    # the pattern uploads (v3 create, every face and level read back): all formats x {2D, cube} x shapes
    shapes = [(8, 8, 1), (8, 8, 4), (5, 3, 3), (6, 6, 3), (4, 4, 3), (1, 1, 1), (16, 4, 5)]
    for f in FM:
        for cube in (0, 1):
            for (w, h, mips) in shapes:
                if cube and w != h: continue
                B.append(Blk("pattern f%d cube%d %dx%d m%d" % (f, cube, w, h, mips), [], ["v 0 %d %d %d %d %d %d" % (f, cube, w, h, mips, 3 + f)]))
    for f in FM:
        for (w, h) in ((8, 8), (5, 3), (1, 1)): B.append(Blk("readback f%d %dx%d" % (f, w, h), [], ["w 0 %d %d %d %d" % (f, w, h, 5 + f)]))
    # the read and readback error matrices (image id 1 is the only image alive)
    def img_blocks(title, mk, f, ty, w, h, d, mips, smp, cube=0):
        lay = 6 if ty == 1 else 1
        allow = set()
        if smp > 1 and depth(f): allow.add("X4")
        if smp > 1 and not depth(f) and (smp == 8 or not u2d(f)): allow.add("X5")
        cmds = ["k 0 %d %d %d %d %d %d %d 0 0" % (w, h, d, ty, f, mips, smp)]
        bx = lambda lw, lh, ld: [(0, 0, 0, lw, lh, 1), (0, 0, 0, 1, 1, 1), (1, 1, 0, max(lw - 1, 1), max(lh - 1, 1), 1), (0, 0, 0, 0, 0, 1), (0, 0, 0, lw + 1, lh, 1), (lw, 0, 0, 1, 1, 1),
                                 (0, 0, 1, lw, lh, 1), (0, 0, 0, lw, lh, 2), (0, 0, 0, lw, lh, 0), (2, 2, 0, 2, 2, 1), (1, 0, 0, 3, 4, 1), (0xFFFFFFFF, 0, 0, 2, 1, 1), (0, 0, 0, lw, lh, ld)]
        for lv in (0, 1, 2, 3, 4, 0xFFFF):
            for ly in range(0, 8) if ty == 1 else (0, 1, 2, 0xFFFF):
                lw, lh, ld = lvl(w, lv), lvl(h, lv), lvl(d, lv)
                for (x, y, z, bw, bh, bd) in bx(lw, lh, ld):
                    rb = (((bw + 3) // 4) if comp(f) else bw) * F[f][3]; rs = ((bh + 3) // 4) if comp(f) else bh
                    cmds.append("r 1 %d %d %d %d %d %d %d %d %d %d 0" % (lv, ly, x, y, z, bw, bh, bd, rb * rs * max(bd, 1), rb))
        lw, lh = w, h; rb = (((lw + 3) // 4) if comp(f) else lw) * F[f][3]; rs = ((lh + 3) // 4) if comp(f) else lh; nd = max(d, 1); need = rb * rs * nd
        for (sz, pitch, nodst) in ((need, rb, 0), (need - 1, rb, 0), (need + 64, rb, 0), (need, rb - 1, 0), (need, rb + 3, 0), (need + 3 * rs, rb + 3, 0), (need, 0, 0), (need, rb, 1), (0, rb, 0), (need, 0xFFFFFFFF, 0)):
            cmds.append("r 1 0 0 0 0 0 %d %d %d %d %d %d" % (lw, lh, nd if d > 1 else 1, sz, pitch, nodst))
        cmds += ["r 2 0 0 0 0 0 1 1 1 16 4 0", "r 4097 0 0 0 0 0 1 1 1 16 4 0"]   # image 0 (the swapchain image) is covered by the selftest
        for k in (0, 1, 2, 3, 4, 9, 255):
            for (x, y, bw, bh, sz) in ((0, 0, w, h, w * h * 16), (0, 0, w, h, 0), (0, 0, w, h, w * h * RB_BPP[k % 4] - 1), (1, 1, max(w - 1, 1), max(h - 1, 1), w * h * 16), (0, 0, 0, 0, 16), (0, 0, w + 1, h, w * h * 16 + 64),
                                       (w, h, 1, 1, 16), (0xFFFFFFFF, 0, 2, 1, 64)):
                cmds.append("b 1 %d %d %d %d %d %d" % (x, y, bw, bh, k, sz))
        cmds += ["b 3 0 0 1 1 0 16", "d 1"]
        B.append(Blk(title, allow, cmds))
    rgba = 1; z32 = [f for f in FM if depth(f)]
    img_blocks("reads 2D rgba8", None, rgba, 0, 8, 8, 1, 3, 1)
    img_blocks("reads 2D non-pow2 rgba8", None, rgba, 0, 5, 3, 1, 3, 1)
    img_blocks("reads cube rgba8", None, rgba, 1, 4, 4, 1, 2, 1)
    img_blocks("reads 3D rgba8", None, rgba, 2, 4, 4, 4, 3, 1)
    for f in FM: img_blocks("reads 2D f%d" % f, None, f, 0, 8, 8, 1, 2, 1)
    for s in (2, 4):
        for f in [x for x in FM if rend(x)]: img_blocks("reads ms%d f%d" % (s, f), None, f, 0, 8, 8, 1, 1, s)
    return B

def g_imgcap():
    cmds = ["k %d 1 1 1 0 6 1 1 0 0" % i for i in range(NIMG + 2)] + ["d %d" % i for i in range(1, NIMG + 3)] + ["k 0 1 1 1 0 6 1 1 0 0", "d 1"]
    return [Blk("image capacity", ["G1"], cmds)]

def smp_args(filt, aniso, wrap, lo, hi, bias, border): return "%d %d %d %d %d %d %d" % (filt, aniso, wrap, lo, hi, bias, border)
def wr(a, b, c): return a | b << 3 | c << 6

def g_smp():
    B = []
    W = [wr(0, 0, 0), wr(1, 1, 1), wr(2, 2, 2), wr(3, 3, 3), wr(4, 4, 4), wr(1, 2, 3), wr(4, 3, 2), wr(2, 0, 4), wr(0, 4, 1)]
    n = 0
    for mip in range(3):
        for ml in range(4):
            for ex in (0, 0x10, 0x20, 0x30):
                filt = ml | mip << 2 | ex
                B.append(Blk("smp filter %x" % filt, [], ["s %s" % smp_args(filt, 1, W[n % len(W)], 0, 13, 0, 0)])); n += 1
    for w in W: B.append(Blk("smp wrap %x" % w, [], ["s %s" % smp_args(3, 0, w, 0, 0, 0, 0)]))
    for an in (0, 1, 2, 8, 16, 17, 255): B.append(Blk("smp aniso %d" % an, [], ["s %s" % smp_args(0x1b, an, 0, 0, 0, 0, 0)]))
    for lo, hi in ((0, 0), (0, 13), (3, 15), (255, 255), (1, 0)): B.append(Blk("smp lod", [], ["s %s" % smp_args(1, 1, 0, lo, hi, 0, 0)]))
    for bias in (-128, -8, 0, 5, 127): B.append(Blk("smp bias", [], ["s %s" % smp_args(1, 1, 0, 0, 13, bias, 0)]))
    for bd in (0, 0xFFFFFFFF, 0x80402010, 0x01000000): B.append(Blk("smp border", [], ["s %s" % smp_args(1, 1, 3, 0, 13, 0, bd)]))
    ups = []
    for i, w in enumerate(W): ups.append("u %d %s" % (1 + i, smp_args(0x1b, 4, w, 1, 9, 3, 0x11223344)))
    ups += ["u 0 %s" % smp_args(1, 1, 0, 0, 0, 0, 0), "u 9999 %s" % smp_args(1, 1, 0, 0, 0, 0, 0), "u 255 %s" % smp_args(1, 1, 0, 0, 0, 0, 0), "u 256 %s" % smp_args(1, 1, 0, 0, 0, 0, 0)]
    B.append(Blk("smp updates", [], ups))
    # X6 updates (the id exists in GL and dk; the old sampler stays on the dk side)
    B.append(Blk("smp X6 update", ["X6"], ["u 1 %s" % smp_args(1, 1, w, 0, 0, 0, 0) for w in (5, 6, 7, wr(0, 5, 0), wr(0, 0, 7), wr(7, 7, 7))] + ["u 2 %s" % smp_args(1 | 3 << 2, 1, 0, 0, 0, 0, 0), "u 2 %s" % smp_args(0x3f, 1, 0, 0, 0, 0, 0)]))
    return B

def g_smpcap():
    return [Blk("sampler capacity", [], ["s %s" % smp_args(1, 1, 0, 0, i & 15, i, i) for i in range(NSMP + 4)] + ["u 255 %s" % smp_args(2, 1, 1, 0, 0, 0, 0), "u 256 %s" % smp_args(2, 1, 1, 0, 0, 0, 0)])]

def g_smpx6():
    B = []
    for w in (5, 6, 7, wr(0, 5, 0), wr(0, 0, 6), wr(7, 7, 7)): B.append(Blk("smp X6 create wrap %x" % w, ["X6"], ["s %s" % smp_args(1, 1, w, 0, 0, 0, 0), "s %s" % smp_args(1, 1, 0, 0, 0, 0, 0)]))
    for filt in (3 << 2, 0x3f, 0x0f): B.append(Blk("smp X6 create mip %x" % filt, ["X6"], ["s %s" % smp_args(filt, 1, 0, 0, 0, 0, 0), "s %s" % smp_args(1, 1, 0, 0, 0, 0, 0)]))
    return B

GROUPS = [("main", g_main, False), ("imgcap", g_imgcap, False), ("smp", g_smp, False), ("smpcap", g_smpcap, False), ("smpx6", g_smpx6, True)]
GEN = {}
for name, g, loose in GROUPS:
    blocks = g()
    path = os.path.join(tmp, name + ".txt")
    with open(path, "w") as fh:
        for i, bk in enumerate(blocks): fh.write("# %d %s\n" % (i, bk.title)); fh.write("\n".join(bk.cmds) + "\n")
    GEN[name] = (blocks, path, loose)
print("generated %d blocks, %d commands" % (sum(len(v[0]) for v in GEN.values()), sum(len(b.cmds) for v in GEN.values() for b in v[0])))

def split_blocks(text):
    d, cur = {}, None
    for line in text.splitlines():
        if line.startswith("#"): cur = int(line.split()[1]); d[cur] = []
        elif cur is not None and not line.startswith("LIVE"): d[cur].append(line)
    return d

# ---- the GL oracle (run once, reused by every mutation) -----------------------------------------------------------------------------------------------
so = os.path.join(tmp, "libnxgl4.so"); pre = os.path.join(tmp, "preload.so")
genv = dict(os.environ, FNVNX_SHADERS_BUILD=SHB)
r = sh(["sh", os.path.join(BACK, "build_gl4.sh"), so], env=genv)
check(r.returncode == 0, "build_gl4.sh: " + tail(r.stderr + r.stdout, 8))
r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-fPIC", "-shared", os.path.join(ROOT, "tests", "l6", "gl4", "t_preload.c"), "-o", pre])
check(r.returncode == 0, "t_preload.c: " + tail(r.stderr, 6))
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
GL = {}
for name, (blocks, path, loose) in GEN.items():
    c = sh([sys.executable, "-I", os.path.abspath(__file__), "--gl-child", so, pre, path, ftab], env=san.env(ENV), timeout=900)
    if c.returncode: check(False, "GL child %s exit %d: %s" % (name, c.returncode, tail(c.stderr, 8))); continue
    GL[name] = split_blocks(c.stdout)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- the comparison -----------------------------------------------------------------------------------------------------------------------------------
KIND_TAGS = {"R": {"X1", "X2"}, "H": {"X4", "X5"}, "B": {"X4", "X5"}, "E": {"X4", "X5", "X6"}, "S": {"X6"}}
def reject_pair(k, dl, gl):
    """(dk, gl) lines: dk refused loudly where GL accepted"""
    a, b = dl.split(), gl.split()
    if not a or not b or a[0] != b[0] or a[0] not in KIND_TAGS: return False
    if k == "R": return a[1] == b[1] and a[2] == "0" and b[2] != "0"
    if k in "HB": return a[:3 if k == "B" else 4] == b[:3 if k == "B" else 4] and int(a[3 if k == "B" else 4]) == BAD and int(b[3 if k == "B" else 4]) == 0
    if k == "E": return int(a[1]) == BAD and int(b[1]) == 0
    return a[1] == "0" and b[1] != "0"

PACKED = (8, 15, 16, 17, 18, 35)   # formats with a channel of 1 .. 6 or 10 bits: R5G6B5, A1R5G5B5, X1R5G5B5, A4R4G4B4, RGB10A2, BGR10A2
def f32_close(a, b, fmt):
    """U1: a readback converts the texels on the driver. GL 4.5 defines c / (2^n - 1) (and, to bytes, round(c * 255)) and leaves the rounding of the
    result to the implementation (llvmpipe multiplies by a reciprocal, and widens 5 / 6 / 4 bit channels by bit replication), so the two sides agree within
    1 ulp per RGBA32F component (the sign, rc and every NaN / infinity must be exactly equal; U1) and, for the packed formats only, within 1 per RGBA8 byte
    (U2); the Depth32F readback of a normalised depth (Z16, Z24) is a division as well (1 ulp, U1). Every other format (8-bit, 16-bit, float, snorm,
    Z32F, Z32FS8) must be equal."""
    p, q = a.split(), b.split()
    if len(p) != 5 or p[:4] != q[:4] or p[4][0] != q[4][0] or len(p[4]) != len(q[4]) or p[4][0] not in "FGD": return None
    x, y = bytes.fromhex(p[4][1:]), bytes.fromhex(q[4][1:])
    if p[4][0] == "G": return "U2" if fmt in PACKED and all(abs(u - v) <= 1 for u, v in zip(x, y)) else None
    if p[4][0] == "D" and fmt in (12, 34): return None   # a float depth is copied: equal
    for o in range(0, len(x), 4):
        u, v = int.from_bytes(x[o:o + 4], "little"), int.from_bytes(y[o:o + 4], "little")
        if u != v and (abs(u - v) > 1 or (u ^ v) >> 31 or (u >> 23 & 255) == 255): return None
    return "U1"

def compare(name, dk_out, tags):
    blocks, _, loose = GEN[name]
    dkb, glb = split_blocks(dk_out), GL[name]
    errs = []
    if sorted(dkb) != sorted(glb): return ["block sets differ in " + name]
    for i in sorted(glb):
        dl, gl, bk = dkb[i], glb[i], blocks[i]
        if loose:   # the id sequences differ by the objects GL makes out of X6 values; only accept / reject is compared
            dl = [("S %d" % (l.split()[1] != "0")) if l[0] == "S" else l for l in dl]; gl2 = [("S %d" % (l.split()[1] != "0")) if l[0] == "S" else l for l in gl]
            for p, q, (a, b) in zip(dl, gl2, zip(dkb[i], gl)):
                if p != q:
                    if reject_pair("S", a, b) and "X6" in bk.allow: tags["X6"] += 1
                    else: errs.append("%s block %d (%s): dk '%s' GL '%s'" % (name, i, bk.title, a, b))
            if len(dl) != len(gl2): errs.append("%s block %d: line counts %d / %d" % (name, i, len(dl), len(gl2)))
            continue
        if dl == gl: continue
        if "G1" in bk.allow:   # G1: the GL context keeps ids 1, 2 for its swapchain image, so it has NIMG - 2 ids for the script: dk accepts, GL refuses, at those ids only
            for j in range(len(dl)):
                if dl[j] == gl[j]: continue
                p, q = dl[j].split(), gl[j].split()
                last = (p[0] == "R" and NIMG - 2 <= int(p[1]) < NIMG and p[2] != "0" and q[2] == "0") or (p[0] == "E" and int(p[1]) == 0 and int(q[1]) == BAD and j >= 2 * NIMG)
                if not last: errs.append("%s block %d line %d: dk '%s' GL '%s'" % (name, i, j, dl[j], gl[j]))
                else: tags["G1"] += 1
            if len(dl) != len(gl): errs.append("%s block %d: line counts" % (name, i))
            continue
        stop = False
        for j in range(min(len(dl), len(gl))):
            if dl[j] == gl[j]: continue
            k = dl[j][0]
            if k == "B" and bk.title.startswith("readback"):
                u = f32_close(dl[j], gl[j], int(bk.title.split()[1][1:]))
                if u: tags[u] += 1; continue
            ok = reject_pair(k, dl[j], gl[j]) and (KIND_TAGS[k] & bk.allow)
            if not ok: errs.append("%s block %d (%s) line %d: dk '%s' GL '%s'" % (name, i, bk.title, j, dl[j], gl[j])); stop = True; break
            tags[sorted(KIND_TAGS[k] & bk.allow)[0]] += 1
            if k == "R": stop = True; break
        if not stop and len(dl) != len(gl): errs.append("%s block %d (%s): %d lines dk, %d lines GL" % (name, i, bk.title, len(dl), len(gl)))
    return errs

def run_scripts(exe, tags):
    errs = []
    for name, (blocks, path, loose) in GEN.items():
        rc, o, e = run_exe(exe, [], open(path).read())
        if rc: errs.append("%s: host exit %d %s" % (name, rc, tail(e, 4))); continue
        lm = re.search(r"^LIVE (\d+)$", o, re.M)
        if not lm or int(lm.group(1)) != 0: errs.append("%s: memory blocks alive after shutdown: %s" % (name, lm.group(1) if lm else "no LIVE line"))
        errs += compare(name, o, tags)
    return errs

tags = collections.Counter()
errs = run_scripts(base_exe, tags)
for e in errs[:25]: print("DIFF", e)
check(not errs, "the product differs from GL in %d places" % len(errs))
print("oracle: %d blocks equal to GL; allowed differences (dk refuses loudly, GL accepts): %s" % (sum(len(v[0]) for v in GEN.values()), dict(tags) or "none"))
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- e ------------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("mips above the maximum accepted", "dk_image.c", "m->mips > max_mips(m)", "m->mips > max_mips(m) + 1"),
    ("3D depth not counted in the mip limit", "dk_image.c", "if (m->type == NxImgType_3D && m->d > e) e = m->d;", ""),
    ("view id destroyable", "dk_image.c", "|| nxdk.img[id].isView)", ")"),
    ("depth of a 2D image accepted", "dk_image.c", "m->type != NxImgType_3D && m->d != 1", "0"),
    ("swizzle range off by one", "dk_image.c", "> NxSwz_1)", "> NxSwz_1 + 1)"),
    ("sRGB pair takes one id", "dk_image.c", "alloc_ids(srgb ? 2 : 1)", "alloc_ids(1)"),
    ("short data accepted", "dk_image.c", "need * layers > data_size", "need * layers > data_size + 1"),
    ("X1 rule dropped", "dk_image.c", "im->samples > 1 && !renderable", "0"),
    ("X2 limit 16385", "dk_image.c", "im->w > NXDK_MAX_DIM ||", "im->w > NXDK_MAX_DIM + 1 ||"),
    ("3D BC4 accepted", "dk_image.c", " || m->fmt == NxFmt_BC4 || m->fmt == NxFmt_BC5", ""),
    ("cube need not be square", "dk_image.c", "m->type == NxImgType_Cube && m->w != m->h", "0"),
    ("multisampled mips accepted", "dk_image.c", "|| m->mips != 1 ||", "||"),
    ("destroy without idle wait", "dk_image.c", "    dkQueueWaitIdle(nxdk.q);   /* no queued", "    /* no queued"),
    ("sampler wrap 5 accepted", "dk_sampler.c", "    nxdk_err(\"sampler: wrap %u\", w);\n    return 1;", "    *out = DkWrapMode_Repeat; return 0;"),
    ("sampler mip filter 3 accepted", "dk_sampler.c", "    nxdk_err(\"sampler: mip filter %u\", m);\n    return 1;", "    *out = DkMipFilter_None; return 0;"),
    ("sampler wrap mirror table", "dk_sampler.c", "case NxWrap_Mirror: *out = DkWrapMode_MirroredRepeat;", "case NxWrap_Mirror: *out = DkWrapMode_Repeat;"),
    ("anisotropy dropped", "dk_sampler.c", "s->maxAniso > 1 ? (float)s->maxAniso : 1.0f", "1.0f"),
    ("sampler capacity off by one", "dk_sampler.c", "nxdk.nSmp + 1 >= NX_MAX_SAMPLERS", "nxdk.nSmp + 1 > NX_MAX_SAMPLERS"),
    ("update of a bad id keeps going", "dk_sampler.c", "if (!s || !id || id > nxdk.nSmp)", "if (!s)"),
    ("mag filter from the min bit", "dk_sampler.c", "d->magFilter = (s->filter & NXSMP_MAG_LINEAR)", "d->magFilter = (s->filter & NXSMP_MIN_LINEAR)"),
]
caught = 0
if "--no-mutations" not in sys.argv:
    for i, (name, f, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        for g in os.listdir(NXD):
            if g.endswith((".c", ".h")): shutil.copy(os.path.join(NXD, g), d)
        txt = open(os.path.join(d, f)).read()
        if txt.count(a) != 1: check(False, "mutation '%s': anchor occurs %d times" % (name, txt.count(a))); continue
        open(os.path.join(d, f), "w").write(txt.replace(a, b))
        exe = os.path.join(tmp, "host_m%d" % i)
        bm = build_host(d, exe)
        if bm.returncode: caught += 1; print("  caught: %s (build: %s)" % (name, tail(bm.stderr, 1)[:80])); continue
        m, why = selftest(exe)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90])); continue
        e2 = run_scripts(exe, collections.Counter())
        if e2: caught += 1; print("  caught: %s (%s)" % (name, e2[0][:90]))
        else: check(False, "mutation not caught: " + name)
        shutil.rmtree(d, ignore_errors=True); os.remove(exe)
    print("mutations caught %d/%d" % (caught, len(MUT)))
    check(caught >= 10, "fewer than 10 mutations caught")
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
