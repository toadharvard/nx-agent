#!/usr/bin/env python3
"""test_ffpdev.py: tests of the fixed-function module of the D3D9 device (render/d3d9/dev/ffp_*.cpp, D5b: nx_ffp_vs_program / nx_ffp_ps_program of draw_ffp.h).
python3 -I tests/l6/ffpdev/test_ffpdev.py [--no-mutations] [--no-guest]      (FNVNX_SHADERS_DEPS=<deps dir of render/shaders/build.sh>: uam for the DKSH check and the guest build)
 a  device state -> state-module inputs: a generated sweep of D3D9 device states (every enumerated render state value, every stage state over its domain, lights of all types / counts / sparse indices, 0-8 texture
    stages of every kind, vertex blending, texgen, matrices, random declarations) drives the device through its API; the blocks nx_ffp_vs_program / nx_ffp_ps_program hand out are compared byte for byte with the blocks the
    state modules produce from inputs python built from the same description (model.py), together with the program inputs / texture stages / varyings
 b  the same for every FVF combination (position field x LASTBETA x normal x psize x diffuse x specular x texture count)
 c  end to end on llvmpipe: FFP triangles through DrawPrimitiveUP vs the CPU references (ffp_vs_ref + ffp_ps_ref)
 d  program ids: creation, caching, destruction counts of the backend calls       e  the embedded programs (generated header == the GLSL sources, DKSH by uam) and the guest build before -> after
 f  mutations.   Architecture: the device layer is -m32; the GL backend is LP64 (libnxgl4 in an RPC process, tests/l6/copy/server.c)."""
import concurrent.futures as cf, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "gl4")); import xl  # noqa: E402  (the translator build libnxgl4 links: FNVNX_SHADERS_BUILD, required)
sys.path.insert(0, HERE)
import model, gen  # noqa: E402
D3D9 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3D9, "dev"); FFP = os.path.join(D3D9, "ffp"); GENDIR = os.path.join(D3D9, "gen"); ENV = os.path.join(D3D9, "env"); BACK = os.path.join(D3D9, "backend"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
FRONT = os.path.join(D3D9, "front"); SPEC = os.path.join(D3D9, "spec", "d3d9_spec.json"); INC = os.path.join(ROOT, "shim", "include"); COPY = os.path.join(ROOT, "tests", "l6", "copy")
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]
RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp", "res_fmt.cpp", "res_tex.cpp", "res_surf.cpp", "res_create.cpp"]
GLUE = ["glue_stream.cpp", "glue_image.cpp", "glue_query.cpp", "glue_buf.cpp"]; CP = ["copy_util.cpp", "copy_clear.cpp", "copy_stretch.cpp", "copy_update.cpp", "copy_dc.cpp", "copy_misc.cpp"]
DRAW = ["draw_api.cpp", "draw_exec.cpp", "draw_prim.cpp", "draw_prog.cpp", "draw_layout.cpp", "draw_ids.cpp", "draw_state.cpp", "draw_const.cpp", "draw_tex.cpp", "draw_bind.cpp"]
FFPDEV = ["ffp_dev.cpp", "ffp_map.cpp", "ffp_sets.cpp", "ffp_prog.cpp"]; FFPC = ["ffp_state.c", "ffp_vs_state.c", "ffp_vs_ref.c", "ffp_ps_ref.c"]; TESTS = ["t_rpc.cpp", "t_ffp.cpp"]
ENVLLVM = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def write_cases(tmp):
    counts = {}
    for name, fn in (("a", gen.gen_a), ("b", gen.gen_b), ("c", gen.gen_c)):
        d = fn()
        open(os.path.join(tmp, "cases_%s.txt" % name), "w").write("\n".join(d.cases) + "\n"); open(os.path.join(tmp, "exp_%s.txt" % name), "w").write("\n".join(d.exp) + "\n")
        counts[name] = (d.nq, dict(sorted(d.family.items())))
    return counts


def locations_check():
    """the attribute locations of ffp.vert.glsl are the table the sets rest on (ffp_sets.cpp V1)"""
    got = model.glsl_locations(os.path.join(FFP, "ffp.vert.glsl"))
    return got == model.LOCS, got


def build_server(tmp):
    srcs = sorted(os.path.join(BACK, f) for f in os.listdir(BACK) if f.startswith("gl4_") and f.endswith(".c")) + [os.path.join(NXB, "nxgpu4_check.c")] + [os.path.join(COPY, "server.c")]
    out = os.path.join(tmp, "server"); r = sh(["gcc", "-std=gnu11", "-O1", "-w", "-I", BACK, "-I", NXB, *srcs, *xl.flags(), "-lEGL", "-lGL", "-lm", "-o", out])
    return (out, "") if r.returncode == 0 else (None, r.stderr[-800:])


def gens(tmp, uam=None):
    for cmd in ([sys.executable, "-I", os.path.join(GENDIR, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GENDIR, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(DEV, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(DEV, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")],
                [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp], [sys.executable, "-I", os.path.join(FFP, "gen_glsl.py"), tmp] + (["--uam", uam] if uam else [])):
        r = sh(cmd)
        if r.returncode: return r.stderr[-800:]
    return ""


CXX = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w"]


def flags(tmp): return CXX + ["-I", INC, "-I", tmp, "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]


def src_of(name, tmp):
    for d in (DEV, FFP, HERE, tmp, os.path.join(HERE, "..", "native"), os.path.join(HERE, "ref")):
        if os.path.exists(os.path.join(d, name)): return os.path.join(d, name)
    raise SystemExit("no source " + name)


def build(tmp, jobs=3):
    err = gens(tmp)
    if err: return None, None, err
    srcs = LIB + RES + GLUE + CP + DRAW + FFPDEV + GENS + ["env_table.gen.cpp"] + TESTS + FFPC
    cmds, objs = [], {}
    for s in srcs:
        o = os.path.join(tmp, s + ".o"); objs[s] = o
        if s.endswith(".c"): cmds.append(["gcc", "-m32", "-std=gnu11", "-O1", "-w", "-I", FFP, "-I", os.path.join(HERE, "ref"), "-c", src_of(s, tmp), "-o", o])
        else: cmds.append(flags(tmp) + ["-c", src_of(s, tmp), "-o", o])
    fronts = sorted(f for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    for f in fronts:
        o = os.path.join(tmp, "nx_" + f + ".o"); objs["nx_" + f] = o; cmds.append(["gcc", "-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", os.path.join(FRONT, f), "-o", o])
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    exe = os.path.join(tmp, "test_ffpdev"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + list(objs.values()) + ["-lm", "-Wl,--unresolved-symbols=ignore-all"])
    return (exe, objs, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


def run(exe, data, server, arena, timeout=1500):
    env = dict(ENVLLVM, NXCOPY_SERVER=server, NXCOPY_ARENA=arena); r = subprocess.run([exe, data], capture_output=True, text=True, env=env, timeout=timeout)
    ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, ps


def embedded_check(tmp, uam):
    """e: ffp_glsl.gen.h holds exactly the expanded GLSL sources (both variants); with uam, ffp_dksh.gen.h holds four DKSH images"""
    sys.path.insert(0, FFP); import gen_glsl
    txt = open(os.path.join(tmp, "ffp_glsl.gen.h")).read(); ok = True
    for stage, f, _ in gen_glsl.SOURCES:
        base = gen_glsl.expand(os.path.join(FFP, f)); ok &= "#include" not in base
        for vname, define in gen_glsl.VARIANTS:
            m = re.search(r"nx_ffp_glsl_%s_%s\[\d+\] = \{([0-9,]+)\};" % (stage, vname), txt)
            if not m or bytes(int(x) for x in m.group(1).split(",")) != gen_glsl.variant(base, define).encode(): ok = False
    res = [("embedded GLSL == expanded sources (4 arrays)", ok)]
    if uam:
        d = os.path.join(tmp, "dk"); os.makedirs(d); r = sh([sys.executable, "-I", os.path.join(FFP, "gen_glsl.py"), d, "--uam", uam]); t = open(os.path.join(d, "ffp_dksh.gen.h")).read() if r.returncode == 0 else ""
        sizes = [int(x) for x in re.findall(r"nx_ffp_dksh_\w+_size = (\d+);", t)]
        res.append(("DKSH by uam: 4 images, each > 1 KiB, magic DKSH", r.returncode == 0 and len(sizes) == 4 and min(sizes) > 1024 and all(bytes(int(x) for x in m.split(",")[:4]) == b"DKSH" for m in re.findall(r"nx_ffp_dksh_\w+\[\d+\] = \{([0-9,]+)\};", t))))
    return res


def guest_undefined(root, out_dir):
    tc = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"
    if not os.path.isdir(tc): return None
    r = sh(["bash", os.path.join(root, "render", "d3d9", "build_guest.sh"), out_dir], env=dict(os.environ, FNVNX_TC=tc, JOBS="3"))
    f = os.path.join(out_dir, "undefined.tsv")
    if r.returncode or not os.path.exists(f): return ("build failed: " + (r.stdout + r.stderr)[-400:], 0, [])
    rows = [l.rstrip("\n").split("\t") for l in open(f)][1:]; by = {}
    for c in rows: by[c[2]] = by.get(c[2], 0) + 1
    return by, len(rows), [c[1] for c in rows]


def guest_report(tmp):
    deps = os.environ.get("FNVNX_SHADERS_DEPS"); out = []
    if not deps or not os.path.isdir(deps): return ["guest build: skipped (set FNVNX_SHADERS_DEPS)"], None
    before_root = os.path.join(tmp, "before"); os.makedirs(before_root)
    shutil.copytree(os.path.join(ROOT, "render"), os.path.join(before_root, "render"), ignore=shutil.ignore_patterns("build", "__pycache__")); shutil.copytree(os.path.join(ROOT, "shim"), os.path.join(before_root, "shim"))
    shutil.copytree(os.path.join(ROOT, "tests", "l6", "env"), os.path.join(before_root, "tests", "l6", "env"), ignore=shutil.ignore_patterns("__pycache__"))   # gen_env.py reads its tables
    for f in FFPDEV: os.remove(os.path.join(before_root, "render", "d3d9", "dev", f))
    b = guest_undefined(before_root, os.path.join(tmp, "gb")); a = guest_undefined(ROOT, os.path.join(tmp, "ga"))
    if not b or not a or not isinstance(a[0], dict) or not isinstance(b[0], dict): return ["guest build FAILED: before %s after %s" % (b and b[0], a and a[0])], False
    gone = [n for n in b[2] if "nx_ffp_" in n and n not in a[2]]; still = [n for n in a[2] if "nx_ffp_" in n]
    out.append("guest build undefined symbols: before %d -> after %d; before by owner %s; after by owner %s" % (b[1], a[1], dict(sorted(b[0].items())), dict(sorted(a[0].items()))))
    out.append("  gone: %s; still undefined nx_ffp_*: %s" % (", ".join(gone), still or "none"))
    # the declared input: without the translator build the guest build stops with a message
    env = {k: v for k, v in os.environ.items() if k != "FNVNX_SHADERS_DEPS"}; env["FNVNX_TC"] = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"
    r = sh(["bash", os.path.join(before_root, "render", "d3d9", "build_guest.sh"), os.path.join(tmp, "gn")], env=env)
    miss_ok = r.returncode != 0 and "FNVNX_SHADERS_DEPS" in (r.stdout + r.stderr)
    out.append("  guest build without the translator build: exit %d, message names FNVNX_SHADERS_DEPS: %s" % (r.returncode, miss_ok))
    return out, bool(gone) and not still and len(gone) == 3 and miss_ok


# mutations: (part that must fail, file, old, new)
MUTS = [
    ("a", "dev/ffp_map.cpp", "for (uint16_t i : kVsRs) k->rs[i] = s.rs[i];", "for (uint16_t i : kVsRs) if (i != NX_VS_RS_LIGHTING) k->rs[i] = s.rs[i];"),
    ("a", "dev/ffp_map.cpp", "k->tci[st] = s.tss[st][D3DTSS_TEXCOORDINDEX]; k->ttff[st] = s.tss[st][D3DTSS_TEXTURETRANSFORMFLAGS];", "k->tci[st] = s.tss[st][D3DTSS_TEXTURETRANSFORMFLAGS]; k->ttff[st] = s.tss[st][D3DTSS_TEXCOORDINDEX];"),
    ("a", "dev/ffp_map.cpp", "k->viewport[2] = s.viewport[0].Width; k->viewport[3] = s.viewport[0].Height;", "k->viewport[3] = s.viewport[0].Width; k->viewport[2] = s.viewport[0].Height;"),
    ("a", "dev/ffp_map.cpp", "colour(k->mDiffuse, m.Diffuse); colour(k->mAmbient, m.Ambient);", "colour(k->mDiffuse, m.Ambient); colour(k->mAmbient, m.Diffuse);"),
    ("a", "dev/ffp_map.cpp", "const NxLightEnt& e = d->lights.a[i]; if (!e.enabled) continue;", "const NxLightEnt& e = d->lights.a[i];"),
    ("a", "dev/ffp_map.cpp", "i < d->lights.n && n < NX_FFP_MAX_LIGHTS;", "i < d->lights.n && n < 4;"),
    ("a", "dev/ffp_map.cpp", "case NXK_CUBE: out[n] = NX_FFP_TEX_CUBE; break;", "case NXK_CUBE: out[n] = NX_FFP_TEX_2D; break;"),
    ("a", "dev/ffp_map.cpp", "memcpy(k->view, &s.xf[nx_dxf(D3DTS_VIEW)], 64); memcpy(k->proj, &s.xf[nx_dxf(D3DTS_PROJECTION)], 64);", "memcpy(k->proj, &s.xf[nx_dxf(D3DTS_VIEW)], 64); memcpy(k->view, &s.xf[nx_dxf(D3DTS_PROJECTION)], 64);"),
    ("a", "dev/ffp_sets.cpp", "if (vm == V_TWEEN && dc.hasPos1) add(", "if (dc.hasPos1) add("),
    ("a", "dev/ffp_sets.cpp", "if (!gen && set < 8 && dc.nTex[set] > 0) used[set] = true;", "if (set < 8 && dc.nTex[set] > 0) used[set] = true;"),
    ("a", "dev/ffp_sets.cpp", "if (tss[s][D3DTSS_COLOROP] == D3DTOP_DISABLE) break;", "if (tss[s][D3DTSS_COLOROP] == D3DTOP_DISABLE) continue;"),
    ("a", "dev/ffp_sets.cpp", "out[n++] = FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0);", ""),
    ("a", "dev/ffp_sets.cpp", "bool indexed = k.rs[NX_VS_RS_INDEXEDVERTEXBLENDENABLE] != 0 && (vm == V_WEIGHTS || vm == V_ZERO);", "bool indexed = k.rs[NX_VS_RS_INDEXEDVERTEXBLENDENABLE] != 0 && vm == V_WEIGHTS;"),
    ("a", "dev/ffp_sets.cpp", "if (dc.hasPsize) add(FNVNX_USAGE_PSIZE, 0, 14);", "if (dc.hasPsize) add(FNVNX_USAGE_PSIZE, 0, 13);"),
    ("a", "dev/ffp_prog.cpp", "out->ubo[1] = {NX_FFP_VS_PALETTE_BINDING,", "out->ubo[1] = {NX_FFP_VS_PALETTE_BINDING + 1,"),
    ("a", "dev/ffp_prog.cpp", "if (!f->vs_ok || memcmp(&k, &f->key, sizeof k)) {", "if (!f->vs_ok) {"),
    ("a", "dev/ffp_prog.cpp", "out->tex_mask = nx_ffp_stage_mask(d->cur.tss, tt);", "out->tex_mask = nx_ffp_stage_mask(d->cur.tss, tt) & 0x7F;"),
    ("d", "dev/ffp_prog.cpp", "case D3DSHADE_FLAT: r = 1; break;", "case D3DSHADE_FLAT: r = 0; break;"),
    ("d", "dev/ffp_prog.cpp", "if (!f->ps[flat]) { uint32_t id", "if (true) { uint32_t id"),
    ("d", "dev/ffp_dev.cpp", "nxd3d_gpu_call(NXGPU_FN_SHADER_DESTROY, a, 1); nx_state_forget_shader(id);", "nx_state_forget_shader(id);"),
    ("d", "dev/ffp_dev.cpp", "if (d->getPrivateData(kRec, &c, &sz) == D3D_OK) { c->Release(); return c; }", "if (false) { c->Release(); return c; }"),
    ("c", "dev/ffp_prog.cpp", "return flat ? Blob{nx_ffp_glsl_ps_flat, nx_ffp_glsl_ps_flat_size} : Blob{nx_ffp_glsl_ps_smooth, nx_ffp_glsl_ps_smooth_size};", "return flat ? Blob{nx_ffp_glsl_ps_smooth, nx_ffp_glsl_ps_smooth_size} : Blob{nx_ffp_glsl_ps_flat, nx_ffp_glsl_ps_flat_size};"),   # (the VS qualifier is not observable on GL: the pixel stage's qualifier decides)
    ("c", "ffp/d9_epilogue.glsl", "if (ps_drv_fog.x == 1.0) dk_f = dk_fog_in.x;", "if (ps_drv_fog.x == 1.0) dk_f = 1.0;"),
    ("c", "ffp/ffp.vert.glsl", "gl_Position = vec4(clip.x + posfix.x * clip.w,", "gl_Position = vec4(-clip.x + posfix.x * clip.w,"),
]


def mut_build(base, tmp, rel, old, new, idx):
    mdir = os.path.join(tmp, "m%d" % idx); os.makedirs(mdir, exist_ok=True); path = os.path.join(D3D9, rel); txt = open(path).read()
    mp = os.path.join(mdir, os.path.basename(rel)); open(mp, "w").write(mutsrc.apply(txt, old, new, f"{rel} #{idx}")); objs = dict(base["objs"])
    if rel.startswith("dev/"):
        o = os.path.join(mdir, "x.o"); r = sh(flags(base["tmp"]) + ["-c", mp, "-o", o])
        if r.returncode: return None, "compile: " + r.stderr[-300:]
        objs[os.path.basename(rel)] = o
    else:   # a GLSL source: regenerate the embedded header from a copy of the ffp directory, recompile the file that embeds it
        fd = os.path.join(mdir, "ffp"); shutil.copytree(FFP, fd); shutil.copy(mp, os.path.join(fd, os.path.basename(rel))); g = os.path.join(mdir, "gen"); os.makedirs(g)
        r = sh([sys.executable, "-I", os.path.join(fd, "gen_glsl.py"), g])
        if r.returncode: return None, "gen_glsl: " + r.stderr[-300:]
        o = os.path.join(mdir, "x.o"); r = sh(CXX + ["-I", INC, "-I", g, "-I", base["tmp"], "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE, "-c", os.path.join(DEV, "ffp_prog.cpp"), "-o", o])
        if r.returncode: return None, "compile: " + r.stderr[-300:]
        objs["ffp_prog.cpp"] = o
    exe = os.path.join(mdir, "t"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + list(objs.values()) + ["-lm", "-Wl,--unresolved-symbols=ignore-all"])
    return (exe, "") if r.returncode == 0 else (None, r.stderr[-300:])


def main():
    tmp = tempfile.mkdtemp(prefix="d9ffpdev_"); data = os.path.join(tmp, "data"); os.makedirs(data); arena = os.path.join(tmp, "arena"); uam = None
    deps = os.environ.get("FNVNX_SHADERS_DEPS")
    if deps and os.access(os.path.join(deps, "uam", "build", "uam"), os.X_OK): uam = os.path.join(deps, "uam", "build", "uam")
    ok, got = locations_check(); print("ffp.vert.glsl attribute locations == the table of the sets (%s): %s" % (", ".join("%s %d" % kv for kv in sorted(got.items(), key=lambda kv: kv[1])), ok))
    if not ok: fails.append("locations")
    counts = write_cases(data)
    for k, (n, fam) in counts.items(): print("states %s: %d evaluations %s" % (k, n, fam if k != "b" else ""))
    srv, err = build_server(tmp)
    if not srv: print("SERVER BUILD FAILED\n" + err); return 1
    exe, objs, err = build(tmp)
    if not exe: print("BUILD FAILED\n" + err); return 1
    r, parts = run(exe, data, srv, arena); print("\n".join(l for l in r.stdout.split("\n") if l.startswith(("FAIL", "backend", "  ")))[:3000])
    for p in "abcd":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append("%s: %s" % (p, parts.get(p)))
    print("PARTS: " + ", ".join("PART %s: %d checks, %d failed" % (p, parts[p][0], parts[p][1]) for p in sorted(parts)))
    if r.returncode and not fails: fails.append("exit %d %s" % (r.returncode, r.stderr[-300:]))
    for what, good in embedded_check(tmp, uam):
        print("e: %s: %s" % (what, "ok" if good else "FAILED"));
        if not good: fails.append("e: " + what)
    if uam is None: print("e: DKSH check skipped (no FNVNX_SHADERS_DEPS)")
    if "--no-guest" not in sys.argv:
        lines, good = guest_report(tmp)
        print("\n".join(lines))
        if good is False: fails.append("e: guest build")
    if "--no-mutations" not in sys.argv:
        base = {"objs": objs, "tmp": tmp}
        def one(i):
            part, rel, old, new = MUTS[i]; x, e = mut_build(base, tmp, rel, old, new, i)
            if not x: return i, None, e
            rr, ps = run(x, data, srv, os.path.join(tmp, "arena%d" % i)); return i, ps, rr.returncode
        caught = 0
        with cf.ThreadPoolExecutor(2) as ex:
            for i, ps, rc in ex.map(one, range(len(MUTS))):
                part = MUTS[i][0]; good = ps is not None and ps.get(part, (0, 0))[1] > 0; caught += good
                print("mutation %d (%s: %r -> part %s): %s %s" % (i, MUTS[i][1], MUTS[i][2][:48], part, "caught" if good else "NOT CAUGHT", "" if good else str((ps, rc))[:160]))
                if not good: fails.append("f: mutation %d" % i)
        print("mutations: %d of %d caught" % (caught, len(MUTS)))
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
