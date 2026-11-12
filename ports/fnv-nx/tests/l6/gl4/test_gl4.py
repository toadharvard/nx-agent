#!/usr/bin/env python3
"""test_gl4.py: tests of render/gl4 (GL 4.5 backend v4, tasks B2a + B2b).  python3 -I tests/l6/gl4/test_gl4.py [--no-mutations]
 a  every NxFormat x {2D, Cube, 3D}: update every level/layer, read back byte-equal
 b  sub-box updates (pitched, offset, BCn blocks, 3D slices, cube faces), rejected updates
 c  Blit: copy, 2x point, linear vs CPU reference (<= 1 LSB), depth, MSAA resolve, conversion, swapchain
 d  GenMips vs CPU references (2D, NPOT, cube, 3D); point = the exact rule
 e  ids reused only after destroy, mem/shader destroy, tables (version 4), malformed stream
 f  v3 ops: draws, UBO, vertex/index buffers, textures with mips, clears, readback
 g  source gates: NXGL4_B2B_PENDING gone, no `default:`, <= 300 lines per file, tables asserted total
 h  mutation checks (single-line breaks in the backend; the part that must catch each is named)
 i  BeginPass4: MRT (2D, cube face of a mip, mip level, 3D slice), depth-only, multisampled, refused passes
 j  vertex layouts: every NxAttrType4 vs the CPU expansion, instancing, 16 streams, refused layouts / binds
 k  queries (occlusion exact, overlapping, event, timestamp, wait 0/1), sampler_update
 l  present: rects, gamma ramp vs CPU, frame files, on_present, refused presents
 m  w[3] bits: MSAA, AALINE, NOCLIP, SAMPLEMASK (multisampled targets) change the output; LASTPIXEL changes nothing (lines draw as GL diamond-exit lines, Wine 9.0); reserved bits
 n  the point rule floor((d + 0.5) * sw / dw) exactly: colour formats, depth-stencil, multisampled source
 q  NXGPU_FN_SHADER_CREATE_D3D: ids of every model class, refusals (hash, size, stage, variant, reserved, L, descriptor, garbage), memo + owners, variants / link sets, every VS x PS pair draws
 p  libnxgl4.so: built by build_gl4.sh, exports nxgl4_ext, leaves nxgl4_guest_ptr / nxgl4_on_present undefined; loaded
    with ctypes next to a preload embedder: clear + present, frame file, hook
Oracle = CPU code in tests/l6/gl4 (patterns, filters, format table, vertex expansion); LP64 host build, identity guest addresses."""
import ctypes, glob, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(preload=True)
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import xl, d3dsyn  # noqa: E402  (the translator build: FNVNX_SHADERS_BUILD is a required input)
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); NXINC = BACK
ENV = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
TESTS = ["t_main.c", "t_parts_ab.c", "t_parts_cd.c", "t_parts_ef.c", "t_parts_i.c", "t_parts_j.c", "t_parts_k.c", "t_parts_l.c", "t_parts_m.c", "t_parts_n.c", "t_parts_o.c", "t_parts_q.c"]
PARTS = "abcdefijklmnoq"
INCDIR = None   # the generated t_d3d.h
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(backend, out, defs=()):
    srcs = sorted(os.path.join(backend, f) for f in os.listdir(backend) if (f.startswith("gl4_") or f == "nxgpu4_check.c") and f.endswith(".c"))
    srcs += [os.path.join(HERE, t) for t in TESTS]
    r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-Werror=switch-enum", "-O1", *defs, "-I", backend, "-I", NXINC, "-I", HERE, "-I", INCDIR, *srcs, *xl.flags(), "-lEGL", "-lGL", "-lm", "-o", out])
    return r.returncode == 0, r.stderr[-1500:]


def run(exe, parts=PARTS):
    r = sh([exe, parts], env=ENV, timeout=500)
    got = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, got


def source_gates():
    n = bad = 0
    srcs = sorted(glob.glob(os.path.join(BACK, "gl4*.[ch]")) + [os.path.join(NXB, "nxgpu4.h")] + glob.glob(os.path.join(NXB, "nxgpu4_check.[ch]")))
    pending = "NXGL4_B2B_" + "PENDING"
    for p in srcs:
        text = open(p).read(); n += 3
        if pending in text: bad += 1; print("gate: %s mentions %s" % (os.path.basename(p), pending))
        if re.search(r"\bdefault\s*:", text): bad += 1; print("gate: %s has a default: label" % os.path.basename(p))
        if text.count("\n") > 300: bad += 1; print("gate: %s is longer than 300 lines" % os.path.basename(p))
    core = open(os.path.join(BACK, "gl4_core.c")).read(); n += 2
    if "(int)N_OPS == (int)NxOp_Count4 && (int)N_FNS == (int)NXGPU_FN_SHADER_CREATE_D3D + 1 && (int)NXGPU_FN_SHADER_CREATE_D3D == (int)NXGPU_FN_COUNT4" not in core: bad += 1; print("gate: tables are not asserted total")
    if "NXGL4_VERSION NXGPU_VERSION4" not in core: bad += 1; print("gate: version is not 4")
    print("PART g (source gates): %d checks, %d failed" % (n, bad))
    if bad: fails.append("source gates")


def ctypes_child(so, pre, outdir):
    os.environ.update(ENV); os.environ["NXGL4_FRAMES"] = outdir
    p = ctypes.CDLL(pre, mode=ctypes.RTLD_GLOBAL)
    p.preload_count.restype = ctypes.c_uint32; p.preload_last.restype = ctypes.c_uint32
    lib = ctypes.CDLL(so)
    lib.nxgl4_create.restype = ctypes.c_void_p; lib.nxgl4_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    lib.nxgl4_ext.restype = ctypes.c_uint64; lib.nxgl4_ext.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32]
    err = ctypes.create_string_buffer(256)
    assert lib.nxgl4_create(16, 16, err, 256), err.value
    def ext(fn, *args):
        a = (ctypes.c_uint64 * max(1, len(args)))(*args)
        return lib.nxgl4_ext(fn, a, len(args))
    addr = lambda o: ctypes.addressof(o)
    info = (ctypes.c_uint32 * 8)()
    assert ext(0, addr(info)) == 0 and info[0] == 0x5550474e and info[1] == 4 and info[6] == 2, list(info)
    class Desc(ctypes.Structure):   # NxImageDesc
        _fields_ = [("w", ctypes.c_uint16), ("h", ctypes.c_uint16), ("fmt", ctypes.c_uint8), ("mips", ctypes.c_uint8), ("flags", ctypes.c_uint8), ("p0", ctypes.c_uint8),
                    ("swz", ctypes.c_uint16), ("p1", ctypes.c_uint16), ("p2", ctypes.c_uint32), ("data", ctypes.c_uint64), ("size", ctypes.c_uint32), ("p3", ctypes.c_uint32)]
    d = Desc(8, 8, 1, 1, 1)   # RGBA8 render target
    img = ext(3, addr(d)); assert 0 < img < 4096
    class Sub(ctypes.Structure):
        _fields_ = [("stream", ctypes.c_uint64), ("words", ctypes.c_uint32), ("frame", ctypes.c_uint32), ("stats", ctypes.c_uint64), ("present", ctypes.c_uint32), ("pad", ctypes.c_uint32)]
    words = (ctypes.c_uint32 * 16)(3 | 8 << 16, img, 0, 1, 0xFFC08040, 0, 0, 8, 8, 4, 0)   # BeginPass: clear colour R40 G80 BC0 AFF; EndPass; End
    sub = Sub(addr(words), 11, 0, 0, 0, 0)
    assert ext(6, addr(sub)) == 0
    class Pres(ctypes.Structure):   # NxPresent
        _fields_ = [("image", ctypes.c_uint32)] + [(n, ctypes.c_int32) for n in ("sx", "sy", "sw", "sh", "dx", "dy", "dw", "dh")] + \
                   [("interval", ctypes.c_uint32), ("gamma", ctypes.c_uint64), ("frame", ctypes.c_uint32), ("pad", ctypes.c_uint32),
                   ("cursor", ctypes.c_uint64), ("cx", ctypes.c_int32), ("cy", ctypes.c_int32), ("cw", ctypes.c_uint32), ("ch", ctypes.c_uint32)]
    assert ctypes.sizeof(Pres) == 80
    ramp = (ctypes.c_uint16 * 768)(*[(255 - (i & 255)) * 257 for i in range(768)])   # inverts every channel
    for frame, gamma, want in ((7, 0, (0x40, 0x80, 0xC0, 0xFF)), (8, addr(ramp), (0xBF, 0x7F, 0x3F, 0xFF))):
        pr = Pres(img, 0, 0, 8, 8, 0, 0, 16, 16, 1, gamma, frame, 0)
        assert ext(17, addr(pr)) == 0
        data = open(os.path.join(outdir, "frame_%06u.rgba" % frame), "rb").read()
        assert data == bytes(want) * 256, (frame, data[:8])
        assert open(os.path.join(outdir, "frame_%06u.txt" % frame)).read() == "16 16\n"
    assert p.preload_count() == 2 and p.preload_last() == 8, (p.preload_count(), p.preload_last())
    bad = Pres(img, 0, 0, 8, 8, 0, 0, 17, 16, 1, 0, 9, 0)
    assert ext(17, addr(bad)) == 0xFFFFFFFFFFFFFF02 and p.preload_count() == 2
    assert ext(99, addr(pr)) == 0xFFFFFFFFFFFFFF02
    lib.nxgl4_destroy()
    print("CTYPES OK")


def lib_test(tmp):
    checks = bad = 0
    so = os.path.join(tmp, "libnxgl4.so"); pre = os.path.join(tmp, "preload.so")
    r = sh(["sh", os.path.join(BACK, "build_gl4.sh"), so]); checks += 1
    if r.returncode: bad += 1; print(r.stderr[-1500:])
    env = {k: v for k, v in os.environ.items() if k != "FNVNX_SHADERS_BUILD"}; no = os.path.join(tmp, "libnone.so"); checks += 3   # the required input: without it nothing is built, exit 2, a message naming it
    r2 = sh(["sh", os.path.join(BACK, "build_gl4.sh"), no], env=env)
    for ok, what in ((r2.returncode == 2, "exit status 2"), ("FNVNX_SHADERS_BUILD" in r2.stderr, "message names FNVNX_SHADERS_BUILD"), (not os.path.exists(no), "no library written")):
        if not ok: bad += 1; print("lib: build without the translator build: not " + what)
    r3 = sh(["sh", os.path.join(BACK, "build_gl4.sh"), "--shaders-build", os.path.join(tmp, "nothing"), no], env=env); checks += 1
    if r3.returncode != 2: bad += 1; print("lib: an unusable --shaders-build directory did not stop the build")
    r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-fPIC", "-shared", os.path.join(HERE, "t_preload.c"), "-o", pre]); checks += 1
    if r.returncode: bad += 1; print(r.stderr[-800:])
    if not bad:
        d = sh(["nm", "-D", "--defined-only", so]).stdout; u = sh(["nm", "-D", "--undefined-only", so]).stdout
        checks += 3
        exp = " T nxgl4_ext" in d and " T nxgl4_create" in d and " T nxgl4_shim_glsl" in d and " T nxgl4_last_error" in d and len(re.findall(r" T ", d)) == 5
        und = re.search(r" U nxgl4_guest_ptr", u) and re.search(r" U nxgl4_on_present", u)
        loc = " gl4_" not in d and " p_gl" not in d
        for ok, what in ((exp, "exports nxgl4_ext / nxgl4_create"), (und, "leaves nxgl4_guest_ptr and nxgl4_on_present undefined"), (loc, "exports nothing internal")):
            if not ok: bad += 1; print("lib: does not satisfy: " + what)
        out = tempfile.mkdtemp(prefix="gl4frames_", dir=tmp); checks += 1
        c = sh([sys.executable, "-I", os.path.abspath(__file__), "--ctypes-child", so, pre, out], env=ENV, timeout=120)
        if c.returncode or "CTYPES OK" not in c.stdout: bad += 1; print((c.stdout + c.stderr)[-1500:])
    print("PART p (libnxgl4.so): %d checks, %d failed" % (checks, bad))
    if bad: fails.append("libnxgl4.so")


MUTS = [  # (part that must catch it, file, old text (exactly once), replacement)
    ("b", "gl4_image.c", "memcpy(tight + (s * rows + r) * rb, src + (s * rows + r) * rowPitch, rb);", "memcpy(tight + (s * rows + r) * rb, src + (s * rows + r) * rb, rb);"),
    ("e", "gl4_image.c", "if (!im || im->isView || id == g4.swapImage)", "if (!im || id == g4.swapImage)"),
    ("a", "gl4_image.c", "GLint zo = im->target == GL_TEXTURE_CUBE_MAP ? (GLint)layer : (GLint)z;", "GLint zo = (GLint)z;"),
    ("f", "gl4_image.c", "data += gl4_row_bytes(&f, box[3]) * gl4_rows(&f, box[4]) * box[5];", "data += gl4_row_bytes(&f, box[3]);"),
    ("e", "gl4_image.c", "memset(&g4.img[v], 0, sizeof g4.img[v]);", "g4.img[v].tex = 0;"),
    ("b", "gl4_image.c", "pitch < rb || need > a[10])", "pitch < rb || need > (1ull << 40))"),
    ("a", "gl4_image.c", "(uint64_t)b[0] + b[3] > lw ||", "(uint64_t)b[0] + b[3] >= lw ||"),
    ("j", "gl4_vtx.c", "word = (word & 0x3FFFFFFFu) | 0x40000000u;", "word = (word & 0x3FFFFFFFu);"),
    ("j", "gl4_vtx.c", "case NxAttr4_D3DColor: return (AttrFmt){GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, 0};", "case NxAttr4_D3DColor: return (AttrFmt){4, GL_UNSIGNED_BYTE, GL_TRUE, 0};"),
    ("j", "gl4_vtx.c", "p_glVertexBindingDivisor(s, (l->l4.instanceStreams >> s & 1) ? l->l4.divisor[s] : 0);", "p_glVertexBindingDivisor(s, (l->l4.instanceStreams >> s & 1) ? 1 : 0);"),
    ("n", "gl4_scale.c", "(ivec2(gl_FragCoord.xy) - dst.xy) + 1) * src.zw)", "(ivec2(gl_FragCoord.xy) - dst.xy)) * src.zw)"),
    ("n", "gl4_scale.c", "p_glClearBufferiv(GL_STENCIL, 0, &zero);", "(void)zero;"),
    ("k", "gl4_query.c", "g4.qry[sl->ids[j]].acc += v;", "g4.qry[sl->ids[j]].acc = v;"),
    ("k", "gl4_query.c", "        if (!wait) return 1;\n", "        if (!wait) return 0;\n"),
    ("k", "gl4_query.c", "        slice_close();\n        q->active = 0; q->issued = 1;", "        q->active = 0; q->issued = 1;"),
    ("i", "nxgpu4_check.c", "else if (tw != *w || th != *h || im.samples != *samples) {", "else if (tw != *w || th != *h) {"),
    ("i", "gl4_pass.c", "p_glNamedFramebufferDrawBuffers(f, (GLsizei)p->nColor, bufs);", "p_glNamedFramebufferDrawBuffers(f, 1, bufs);"),
    ("i", "gl4_pass.c", "for (uint32_t i = 0; i < g4.passN; i++) { p_glColorMaski", "for (uint32_t i = 0; i < 1; i++) { p_glColorMaski"),
    ("l", "gl4_scale.c", "c.r = float(texelFetch(ramp, ivec2(i.r, 0), 0).r >> 8) / 255.0;", "c.r = float(texelFetch(ramp, ivec2(i.r, 0), 0).r >> 7) / 255.0;"),
    ("l", "gl4_present.c", 'frame_%06u.rgba", dir, frame);', 'frame_%06u.rgb", dir, frame);'),
    ("l", "gl4_present.c", "p->interval > 4 ||", "p->interval > 400 ||"),
    ("k", "gl4_sampler.c", "(s->filter & NXSMP_MAG_LINEAR) ? GL_LINEAR : GL_NEAREST);", "GL_NEAREST);"),
    ("m", "gl4_state.c", "if (w3 >> NXRS_M_NOCLIP & 1) p_glEnable(GL_DEPTH_CLAMP);", "if (w3 >> NXRS_M_NOCLIP & 0) p_glEnable(GL_DEPTH_CLAMP);"),
    ("m", "gl4_state.c", "if (w3 >> NXRS_M_MSAA & 1) p_glEnable(GL_MULTISAMPLE); else p_glDisable(GL_MULTISAMPLE);", "p_glEnable(GL_MULTISAMPLE);"),
    ("m", "gl4_state.c", "if (w3 >> NXRS_M_AALINE & 1) p_glEnable(GL_LINE_SMOOTH); else p_glDisable(GL_LINE_SMOOTH);", "p_glDisable(GL_LINE_SMOOTH);"),
    ("m", "gl4_state.c", "if (mask != 0xFFFFFFFFu && g4.passSamples > 1) { p_glEnable(GL_SAMPLE_MASK); p_glSampleMaski(0, mask); } else p_glDisable(GL_SAMPLE_MASK);", "if (mask == 0x12345u && g4.passSamples > 1) { p_glEnable(GL_SAMPLE_MASK); p_glSampleMaski(0, mask); } else p_glDisable(GL_SAMPLE_MASK);"),
    ("o", "gl4_present.c", "d[c] * (255 - a) + 127) / 255);", "d[c] * (255 - a) + 0) / 255);"),
    ("o", "gl4_present.c", "if (ramp) v = ramp[c * 256 + v] >> 8;", "if (0) v = ramp[c * 256 + v] >> 8;"),
    ("o", "gl4_present.c", "if (x1 > sw->w) x1 = sw->w;", "if (x1 > sw->w) x1 = sw->w - 1;"),
    ("o", "gl4_present.c", "uint32_t v = s[2 - c];", "uint32_t v = s[c];"),
    ("o", "gl4_present.c", "p->cw <= 4096 && p->ch <= 4096", "p->cw <= 4097 && p->ch <= 4096"),
    ("o", "gl4_present.c", "uint8_t* z = (uint8_t*)calloc((size_t)sw->w * sw->h, 4);", "uint8_t* z = (uint8_t*)malloc((size_t)sw->w * sw->h * 4); if (z) memset(z, 1, (size_t)sw->w * sw->h * 4);"),
    ("o", "gl4_present.c", "k = ns * 60 / 1000000000ull + 1", "k = ns * 60 / 1000000000ull + 0"),
    ("o", "gl4_present.c", "if (output != 0 || !g4.swapImage)", "if (!g4.swapImage)"),
    ("o", "gl4_read.c", "gl4_img(r->src.image ? r->src.image : g4.swapImage);   /* image 0", "gl4_img(r->src.image);   /* image 0"),
    ("q", "gl4_d3dshader.c", "if (d.variant > 3 || (d.stage == 1 && (d.variant & 1)))", "if (d.variant > 3)"),
    ("q", "gl4_d3dshader.c", "if (d.variant > 3 ||", "if (d.variant > 4 ||"),
    ("q", "gl4_d3dshader.c", "if (memcmp(k, d.key, sizeof k)) {", "if (0) {"),
    ("q", "gl4_d3dshader.c", "if (d.pad[0] || d.pad[1] || d.pad2) return", "if (d.pad[0] || d.pad2) return"),
    ("q", "gl4_d3dshader.c", "if (lp[i] / 16 > 13 || (i && lp[i] <= lp[i - 1])) return", "if (lp[i] / 16 > 13 || (i && lp[i] < lp[i - 1])) return"),
    ("q", "gl4_d3dshader.c", "e->stage == k->stage && e->variant == k->variant && e->nl", "e->stage == k->stage && e->nl"),
    ("q", "gl4_d3dshader.c", "!memcmp(e->l, k->l, k->nl) && !memcmp(e->code", "!memcmp(e->code"),
    ("q", "gl4_d3dshader.c", "!memcmp(e->l, k->l, k->nl) && !memcmp(e->code, code, k->size)", "!memcmp(e->l, k->l, k->nl)"),
    ("q", "gl4_d3dshader.c", "if (id) { free(code); g4.sh[id].d3d->refs++; return id; }", "if (id) { free(code); return id; }"),
    ("q", "gl4_d3dshader.c", "*e = key; e->code = code; e->refs = 1;", "*e = key; e->code = code; e->refs = 2;"),
    ("q", "gl4_d3dshader.c", "(d.stage == 0 && (d.variant & 1) ? FNVNX_SHADER_VS_COMPLETE : 0)", "0"),
    ("q", "gl4_d3dshader.c", "o.link_pairs = key.nl ? key.l : NULL; o.n_link_pairs = key.nl;", "o.link_pairs = NULL; o.n_link_pairs = 0;"),
    ("q", "gl4_d3dshader.c", "const NxShaderD3D* dp = (const NxShaderD3D*)nxgl4_guest_ptr(a, sizeof *dp);\n    if (!dp) return refuse(\"descriptor unreadable\");", "const NxShaderD3D* dp = (const NxShaderD3D*)(uintptr_t)a;"),
    ("q", "gl4_shader.c", "if (g4.sh[id].d3d && --g4.sh[id].d3d->refs) return 0;", "if (g4.sh[id].d3d && g4.sh[id].d3d->refs < 1) return 0;"),
]


def mutations(tmp):
    caught = 0
    for i, (part, fname, old, new) in enumerate(MUTS):
        d = os.path.join(tmp, "mut%d" % i); shutil.copytree(BACK, d)
        p = os.path.join(d, fname); s = open(p).read()
        assert s.count(old) == 1, "mutation %d does not match exactly once in %s: %s" % (i, fname, old)
        open(p, "w").write(s.replace(old, new))
        ok, err = build(d, os.path.join(d, "t"))
        if not ok: print("mutation %d: build failed\n%s" % (i, err)); fails.append("mutation %d build" % i); continue
        r, res = run(os.path.join(d, "t"), part)
        hit = r.returncode != 0 or res.get(part, (0, 0))[1] > 0 or part not in res
        caught += hit
        print("mutation %d (%s, part %s): %s" % (i, fname, part, "caught" if hit else "NOT CAUGHT"))
        if not hit: fails.append("mutation %d not caught" % i)
        shutil.rmtree(d)
    print("PART h: %d mutations, %d caught" % (len(MUTS), caught))


def main():
    global INCDIR
    tmp = tempfile.mkdtemp(prefix="gl4t_")
    try:
        INCDIR = os.path.join(tmp, "inc"); os.makedirs(INCDIR)
        sm, ops = xl.hand(); open(os.path.join(INCDIR, "t_d3d.h"), "w").write(d3dsyn.header(sm, ops))
        exe = os.path.join(tmp, "t_gl4")
        ok, err = build(BACK, exe)
        if not ok:
            print(err); fails.append("build"); return
        r, got = run(exe)
        for p in PARTS:
            if p not in got: fails.append("part " + p + " missing"); print("PART %s: no result" % p); continue
            n, f = got[p]; print("PART %s: %d checks, %d failed" % (p, n, f))
            if f or n == 0: fails.append("part " + p)
        if r.returncode and not fails: fails.append("exit status %d" % r.returncode)
        for l in r.stdout.splitlines()[:200]:
            if l.startswith(("FAIL", " ")): print(l)
        source_gates()
        lib_test(tmp)
        if "--no-mutations" not in sys.argv: mutations(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if len(sys.argv) > 1 and sys.argv[1] == "--ctypes-child":
    ctypes_child(*sys.argv[2:5]); sys.exit(0)
main()
print("ALL OK" if not fails else "FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
