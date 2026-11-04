#!/usr/bin/env python3
"""render-veg: scene check of the native vegetation families against Translated (the game's own programs through
S6b's sdp2dksh), on the same synthetic scene, on the host GL (Mesa llvmpipe, EGL surfaceless).

    FNVNX_T019=<sdp2dksh -o DIR --glsl shaderpackage019.sdp> python3 -I tests/s6n/veg_parity.py <native pkg dir> [-v]

<native pkg dir> is render/native/shaders/build.py's output (expanded .glsl). Without FNVNX_T019 the check is skipped
(exit 0): the originals are the player's data and never in the repository; nothing derived from them is written.
Each case draws one grass field (instances of a blade clump, the engine's InstanceData layout: index in TEXCOORD1.x)
with the original program pair and with the native variant, into RGBA32F with depth, and compares per pixel:
coverage (alpha-tested pixels present in one image only) and colour (max |difference|). Tolerance: RENDER_NATIVE §4
(2/255 colour; coverage differences only on pixels whose alpha-test input is within 1/255 of the reference, counted
and reported, limit 0.1 % of the covered pixels). Exit 1 if a case fails, 2 on a setup error.
"""
import ctypes, math, os, re, sys
import numpy as np

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
from OpenGL import EGL, GL  # noqa: E402

W = H = 256
TOL = 2.0 / 255.0
COVER_LIMIT = 0.001


def egl_context():
    dpy = EGL.eglGetPlatformDisplayEXT(0x31DD, EGL.EGL_DEFAULT_DISPLAY, None) if hasattr(EGL, "eglGetPlatformDisplayEXT") \
        else EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
    if not dpy:
        dpy = EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
    maj, mnr = EGL.EGLint(), EGL.EGLint()
    if not EGL.eglInitialize(dpy, ctypes.pointer(maj), ctypes.pointer(mnr)):
        raise SystemExit("EGL init failed")
    EGL.eglBindAPI(EGL.EGL_OPENGL_API)
    attrs = (EGL.EGLint * 3)(EGL.EGL_RENDERABLE_TYPE, EGL.EGL_OPENGL_BIT, EGL.EGL_NONE)
    cfg, n = EGL.EGLConfig(), EGL.EGLint()
    EGL.eglChooseConfig(dpy, attrs, ctypes.pointer(cfg), 1, ctypes.pointer(n))
    cattrs = (EGL.EGLint * 7)(EGL.EGL_CONTEXT_MAJOR_VERSION, 4, EGL.EGL_CONTEXT_MINOR_VERSION, 5,
                              EGL.EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL.EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL.EGL_NONE)
    ctx = EGL.eglCreateContext(dpy, cfg, EGL.EGL_NO_CONTEXT, cattrs)
    if not ctx or not EGL.eglMakeCurrent(dpy, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, ctx):
        raise SystemExit("EGL context failed")
    return dpy, ctx


def compile_prog(vs, fs):
    def sh(kind, src):
        s = GL.glCreateShader(kind)
        GL.glShaderSource(s, src)
        GL.glCompileShader(s)
        if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
            raise SystemExit("compile failed:\n" + GL.glGetShaderInfoLog(s).decode() + "\n" +
                             "\n".join("%4d %s" % (i + 1, l) for i, l in enumerate(src.split("\n"))))
        return s
    p = GL.glCreateProgram()
    GL.glAttachShader(p, sh(GL.GL_VERTEX_SHADER, vs))
    GL.glAttachShader(p, sh(GL.GL_FRAGMENT_SHADER, fs))
    GL.glLinkProgram(p)
    if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
        raise SystemExit("link failed: " + GL.glGetProgramInfoLog(p).decode())
    return p


def native_src(src):
    return src.replace("#version 460", "#version 450 core", 1)


def translated_src(src, ubo_base):
    """sdp2dksh GLSL (per-stage bindings): move the stage's UBO bindings to ubo_base + n (GL bindings are global)"""
    return re.sub(r"layout\(std140, binding = (\d+)\) uniform",
                  lambda m: "layout(std140, binding = %d) uniform" % (ubo_base + int(m.group(1))), src)


def t019_index(d):
    idx = {}
    for line in open(os.path.join(d, "index.tsv")):
        f = line.rstrip("\n").split("\t")
        if f[0] != "hash" and len(f) > 4 and f[4] == "ok":
            idx[f[2]] = f[0]
    return idx


# ---- the synthetic scene -----------------------------------------------------------------------------------------

def perspective_lookat(eye, at, fovy, aspect, zn, zf):
    """D3D-style clip (z in [0, 1]); returns M with clip = M @ (x, y, z, 1) (rows = the engine's ModelViewProj rows)"""
    f = np.array(at, float) - eye; f /= np.linalg.norm(f)
    up = np.array([0, 0, 1.0]); r = np.cross(f, up); r /= np.linalg.norm(r); u = np.cross(r, f)
    V = np.identity(4); V[0, :3] = r; V[1, :3] = u; V[2, :3] = f; V[:3, 3] = -V[:3, :3] @ eye
    t = 1.0 / math.tan(fovy / 2)
    P = np.zeros((4, 4)); P[0, 0] = t / aspect; P[1, 1] = t; P[2, 2] = zf / (zf - zn); P[2, 3] = -zn * zf / (zf - zn); P[3, 2] = 1
    return P @ V


def clump(rng, nblades=6):
    """a grass clump: nblades crossed quads (position, normal, colour, uv); returns (verts [k x 12], indices)"""
    v, ix = [], []
    for b in range(nblades):
        a = rng.uniform(0, math.pi)
        dx, dy = math.cos(a) * 18, math.sin(a) * 18
        ox, oy = rng.uniform(-20, 20, 2)
        h = rng.uniform(30, 55)
        base = len(v)
        for (s, z, uvx, uvy) in ((-1, 0, 0, 1), (1, 0, 1, 1), (1, h, 1, 0), (-1, h, 0, 0)):
            n = np.array([-dy, dx, rng.uniform(0.2, 1.0)]); n /= np.linalg.norm(n)
            top = 1.0 if z > 0 else 0.0
            col = [rng.uniform(0.5, 1), rng.uniform(0.5, 1), rng.uniform(0.4, 0.9), top]   # alpha = sway weight
            v.append([ox + s * dx, oy + s * dy, z, *n, *col, uvx, uvy])
        ix += [base, base + 1, base + 2, base, base + 2, base + 3]
    return np.array(v, np.float32), ix


def field(rng, ninst):
    """instances: xyz position on a gently sloped ground, w = scale percent (integer) + shade (fraction);
    the packed normal is the fraction of xyz (n = (frac - 0.5) x 2), so positions carry it as in the engine"""
    out = []
    for _ in range(ninst):
        x, y = rng.uniform(-600, 600), rng.uniform(150, 1800)
        n = np.array([rng.uniform(-0.35, 0.35), rng.uniform(-0.35, 0.35), 1.0]); n /= np.linalg.norm(n)
        fx, fy, fz = (n * 0.5 + 0.5)
        X = math.floor(x) + min(fx, 0.999); Y = math.floor(y) + min(fy, 0.999); Z = math.floor(0.05 * y) + min(fz, 0.999)
        wv = float(rng.integers(-20, 40)) + rng.uniform(0.0, 0.999)
        out.append([X, Y, Z, wv])
    return np.array(out, np.float32)


def texture_rgba(rng):
    """64x64 RGBA8: green blades with an alpha ramp and holes (the alpha-test input), mipmapped"""
    t = np.zeros((64, 64, 4), np.uint8)
    yy, xx = np.mgrid[0:64, 0:64]
    t[..., 0] = 60 + (xx * 3) % 120; t[..., 1] = 120 + (yy * 2) % 130; t[..., 2] = 40 + ((xx + yy) * 5) % 60
    blade = np.abs(((xx * 5) % 64) - 32) < (32 - yy // 2)
    t[..., 3] = np.where(blade, 255 - yy * 2, (xx * 7 + yy * 3) % 120)
    return t


# ---- programs and constants ----------------------------------------------------------------------------------------

VS_REGS = dict(DiffuseDir=0, DiffuseColor=1, ScaleMask=2, WindData=4, AlphaParam=5, AmbientColor=6, AddlParams=7,
               ModelViewProj=9, FogColor=14, FogParam=15, InstanceData=20)
PS_REGS = dict(AlphaTestRef=3)


def params(rng, mvp):
    ld = np.array([0.4, -0.3, 0.85]); ld /= np.linalg.norm(ld)
    return dict(DiffuseDir=[*ld, 0], DiffuseColor=[1.0, 0.92, 0.8, 1], ScaleMask=[0.6, 0.6, 1.0, 0],
                WindData=[0.7, 0.45, 9.0, rng.uniform(0, 50)], AlphaParam=[0, 0, 1200.0, 450.0],
                AmbientColor=[0.32, 0.36, 0.42, 1], AddlParams=[0.85, 0, 0, 0], FogColor=[0.7, 0.62, 0.55, 1],
                FogParam=[1900.0, 1500.0, 1.6, 0], AlphaTestRef=[0.5, 0, 0, 0], mvp=mvp)


def translated_ubos(p, inst):
    vs = np.zeros((256, 4), np.float32)
    for k, r in VS_REGS.items():
        if k == "ModelViewProj":
            vs[r:r + 4] = p["mvp"]
        elif k == "InstanceData":
            vs[r:r + len(inst)] = inst
        else:
            vs[r] = p[k]
    ps = np.zeros((256, 4), np.float32)
    ps[PS_REGS["AlphaTestRef"]] = p["AlphaTestRef"]
    vdrv = np.zeros((4, 4), np.float32); vdrv[0] = [0, 0, 1, 0]               # posfix: no half pixel, no y flip
    pdrv = np.zeros((20, 4), np.float32)                                      # fogcolor, fog (off), alpha, alpharef
    pdrv[2].view(np.int32)[0] = 5                                             # D3DCMP_GREATER
    pdrv[3] = [p["alpha_ref_d3d"], 0, 0, 0]
    return vs, ps, vdrv, pdrv


def native_ubo_c(lib, p, inst):
    """NxGrassDraw from veg_grass.cpp's nx_grass_fold (FNVNX_VEG_LIB = tests/s6n/veg.mk's libnxveg.so)"""
    F4 = ctypes.c_float * 4
    class Consts(ctypes.Structure):
        _fields_ = [("mvpRows", ctypes.c_float * 16)] + [(k, F4) for k in (
            "diffuseDir", "diffuseColor", "scaleMask", "windData", "alphaParam", "ambientColor", "addlParams",
            "fogColor", "fogParam")] + [("alphaTestRef", ctypes.c_float)]
    c = Consts()
    c.mvpRows[:] = [float(x) for x in np.asarray(p["mvp"], np.float32).reshape(16)]
    for k, ck in (("DiffuseDir", "diffuseDir"), ("DiffuseColor", "diffuseColor"), ("ScaleMask", "scaleMask"),
                  ("WindData", "windData"), ("AlphaParam", "alphaParam"), ("AmbientColor", "ambientColor"),
                  ("AddlParams", "addlParams"), ("FogColor", "fogColor"), ("FogParam", "fogParam")):
        getattr(c, ck)[:] = [float(x) for x in p[k]]
    c.alphaTestRef = p["AlphaTestRef"][0]
    out = np.zeros((13 + 236, 4), np.float32)
    ins = np.ascontiguousarray(inst, np.float32)
    n = lib.nx_grass_fold(ctypes.byref(c), ins.ctypes.data_as(ctypes.c_void_p), len(inst), out.ctypes.data_as(ctypes.c_void_p))
    assert n == 208 + 16 * len(inst), n
    return out


def native_ubo(p, inst, lib=None):
    """NxGrassDraw (n1/veg_families.h): veg_grass.cpp's folding (lib), or the same in numpy"""
    if lib is not None:
        return native_ubo_c(lib, p, inst), native_epi(p)
    head = np.zeros((4 + 10, 4), np.float32)
    head[0:4] = p["mvp"].T                                                    # column-major mat4
    head[4] = p["DiffuseDir"]
    head[5] = np.array(p["DiffuseColor"]) * p["AddlParams"][0]
    head[6] = p["AmbientColor"]
    head[7] = np.array(p["ScaleMask"]) * 0.01
    wd = p["WindData"]; head[8] = [wd[0] * wd[2], wd[1] * wd[2], wd[3], 0]
    ap, fp = p["AlphaParam"], p["FogParam"]
    head[9] = [ap[2], 1.0 / ap[3], fp[0], 1.0 / fp[1]]
    head[10] = [fp[2], 0, 0, 0]
    head[11] = np.clip(p["FogColor"], 0, 1)
    head[12] = [p["AlphaTestRef"][0], 0, 0, 0]
    ubo = np.zeros((13 + 236, 4), np.float32)
    ubo[:13] = head[:13]
    ubo[13:13 + len(inst)] = inst
    return ubo, native_epi(p)


def native_epi(p):
    epi = np.zeros((3, 4), np.float32)                                        # NxEpilogue: fog off, alpha test GREATER
    epi[1] = [0, 0, 0, p["alpha_ref_d3d"]]
    epi[2].view(np.uint32)[:] = [4, 0, 1, 0]
    return epi


# ---- drawing --------------------------------------------------------------------------------------------------------

def make_target():
    fbo = GL.glGenFramebuffers(1); GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    col = GL.glGenTextures(1); GL.glBindTexture(GL.GL_TEXTURE_2D, col)
    GL.glTexStorage2D(GL.GL_TEXTURE_2D, 1, GL.GL_RGBA32F, W, H)
    GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, col, 0)
    dep = GL.glGenRenderbuffers(1); GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, dep)
    GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT32F, W, H)
    GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, dep)
    assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE
    return fbo


def upload_tex(t):
    tex = GL.glGenTextures(1); GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, t.shape[1], t.shape[0], 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, t)
    GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
    return tex


def ubo(binding, arr):
    b = GL.glGenBuffers(1); GL.glBindBuffer(GL.GL_UNIFORM_BUFFER, b)
    data = np.ascontiguousarray(arr, np.float32)
    GL.glBufferData(GL.GL_UNIFORM_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
    GL.glBindBufferBase(GL.GL_UNIFORM_BUFFER, binding, b)
    return b


def geometry(verts, ix, ninst):
    """the engine's batched layout: the clump repeated per instance, TEXCOORD1.x = instance index"""
    k = len(verts)
    allv = np.zeros((k * ninst, 13), np.float32)
    for i in range(ninst):
        allv[i * k:(i + 1) * k, :12] = verts; allv[i * k:(i + 1) * k, 12] = i
    alli = np.concatenate([np.array(ix, np.uint32) + i * k for i in range(ninst)])
    vao = GL.glGenVertexArrays(1); GL.glBindVertexArray(vao)
    vb = GL.glGenBuffers(1); GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vb)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, allv.nbytes, allv, GL.GL_STATIC_DRAW)
    ib = GL.glGenBuffers(1); GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ib)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, alli.nbytes, alli, GL.GL_STATIC_DRAW)
    stride = 13 * 4
    for loc, off, n in ((0, 0, 3), (3, 3, 3), (4, 6, 4), (6, 10, 2), (7, 12, 1)):   # POSITION NORMAL COLOR TEX0 TEX1
        GL.glEnableVertexAttribArray(loc)
        GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(off * 4))
    return vao, len(alli)


def draw(prog, vao, count, ubos, tex):
    GL.glClearColor(0.1, 0.2, 0.3, 0.0); GL.glClearDepth(1.0)
    GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
    GL.glUseProgram(prog)
    for binding, arr in ubos:
        ubo(binding, arr)
    GL.glActiveTexture(GL.GL_TEXTURE0); GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glBindVertexArray(vao)
    GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, None)
    img = GL.glReadPixels(0, 0, W, H, GL.GL_RGBA, GL.GL_FLOAT)
    return np.frombuffer(img, np.float32).reshape(H, W, 4).copy()


# variant cases: (name, original vso, original pso, native vert, native frag, vertex normal used)
CASES = [
    ("grass_base", "GRASS2000.vso", "GRASS2000.pso", "grass.vert", "grass.frag"),
    ("grass_vl", "GRASS2001.vso", "GRASS2000.pso", "grass_vl.vert", "grass.frag"),
    ("grass_slope", "GRASS2002.vso", "GRASS2000.pso", "grass_slope.vert", "grass.frag"),
    ("grass_vlslope", "GRASS2003.vso", "GRASS2000.pso", "grass_vlslope.vert", "grass.frag"),
    ("grass_tms", "GRASS2001.vso", "GRASS2000TMS.pso", "grass_vl.vert", "grass_tms.frag"),
    ("grass_vl_ea", "GRASS2001.vso", "GRASS2000.pso", "grass_vl.vert", "grass__ea.frag"),
]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    t019 = os.environ.get("FNVNX_T019")
    if not t019:
        print("veg_parity: skipped (FNVNX_T019 not set: the originals are the player's data)")
        return 0
    if not args:
        raise SystemExit(__doc__)
    pkg = args[0]
    idx = t019_index(t019)
    lib = None
    if os.environ.get("FNVNX_VEG_LIB"):
        lib = ctypes.CDLL(os.environ["FNVNX_VEG_LIB"])
        lib.nx_grass_fold.restype = ctypes.c_size_t
        lib.nx_grass_fold.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    dpy, ctx = egl_context()
    GL.glEnable(GL.GL_DEPTH_TEST); GL.glDepthFunc(GL.GL_LEQUAL)
    GL.glClipControl(GL.GL_LOWER_LEFT, GL.GL_ZERO_TO_ONE)
    GL.glViewport(0, 0, W, H)
    make_target()
    rng = np.random.default_rng(19)
    verts, ix = clump(rng)
    inst = field(rng, 200)
    vao, count = geometry(verts, ix, len(inst))
    tex = upload_tex(texture_rgba(rng))
    mvp = perspective_lookat(np.array([0, -120.0, 90]), [0, 900, 0], math.radians(65), 1.0, 10, 4000)
    p = params(rng, mvp)
    p["alpha_ref_d3d"] = 0.0                    # the D3D9 alpha test after the shader's own test (ALPHAFUNC GREATER 0)
    fails = 0
    for name, ovs, ops, nvs, nfs in CASES:
        for f in (ovs, ops):
            if f not in idx:
                print("%s: %s missing from FNVNX_T019" % (name, f)); return 2
        tv = translated_src(open(os.path.join(t019, idx[ovs] + ".glsl")).read(), 0)
        if "dk_fog_out" not in tv:   # the PS < 3.0 fog input (location 18) needs a writer in GL; D3D fog is off here
            tv = tv.replace("void main()\n{", "layout(location = 18) out vec4 dk_fog_out;\nvoid main()\n{\n\tdk_fog_out = vec4(1.0);", 1)
        tf = translated_src(open(os.path.join(t019, idx[ops] + ".glsl")).read(), 3)
        tprog = compile_prog(tv, tf)
        vsc, psc, vdrv, pdrv = translated_ubos(p, inst)
        a = draw(tprog, vao, count, [(0, vsc), (2, vdrv), (3, psc), (5, pdrv)], tex)
        nprog = compile_prog(native_src(open(os.path.join(pkg, nvs + ".glsl")).read()),
                             native_src(open(os.path.join(pkg, nfs + ".glsl")).read()))
        nubo, epi = native_ubo(p, inst, lib)
        b = draw(nprog, vao, count, [(1, nubo), (2, epi)], tex)
        ca, cb = a[..., 3] > 0, b[..., 3] > 0
        cov_a = int(ca.sum()); cover = int((ca != cb).sum())
        both = ca & cb
        diff = float(np.abs(a[both] - b[both]).max()) if both.any() else 0.0
        ok = cov_a > 0.02 * W * H and cover <= max(1, COVER_LIMIT * cov_a) and diff <= TOL
        fails += not ok
        print("%-14s %s  covered %6d px, coverage diff %4d px, max |colour diff| %.5f (%.2f/255)%s"
              % (name, "ok  " if ok else "FAIL", cov_a, cover, diff, diff * 255, "" if ok else "  <- %s+%s vs %s+%s" % (ovs, ops, nvs, nfs)))
        if verbose and both.any():
            d = np.abs(a - b).max(axis=2); yx = np.unravel_index(np.argmax(d * both), d.shape)
            print("   worst px", yx, "translated", a[yx], "native", b[yx])
    print("veg_parity: %d cases, %d failed (block from %s)" % (len(CASES), fails, "veg_grass.cpp" if lib else "numpy"))
    EGL.eglTerminate(dpy)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
