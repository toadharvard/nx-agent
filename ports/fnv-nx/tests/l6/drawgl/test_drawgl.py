#!/usr/bin/env python3
"""test_drawgl.py: D3D9 shaders end to end on the GL 4.5 backend (task B2c): the REAL device draw path (render/d3d9/dev/draw_*.cpp, glue, ffp module; -m32) -> NXGPU_FN_SHADER_CREATE_D3D of libnxgl4's sources (LP64 replay
process, RPC + shared arena as in tests/l6/copy, llvmpipe) -> GetRenderTargetData, against independent python CPU references of the same shaders.   python3 -I tests/l6/drawgl/test_drawgl.py [--no-mutations] [--only SUBSTR]
Needs FNVNX_SHADERS_BUILD (the translator build, required: the backend links it).  Synthetic shaders only (hand-assembled, render/shaders/total/sm_hand.py).  Parts (one line `PART x: n checks, f failed` each, a check = one pixel
of one render target, plus the API results of the player in part p):
 a constants (float / int / bool, high registers)   b textures (2D address modes, bilinear, cube all faces, volume, sRGB read, sampler slots, ps_1_x type word)   c vertex colour, the unwritten-input rule, non-canonical links
 d fog (ps_2_0 + vs_1_1 without oFog; table / vertex modes; ps_3_0 none)   e alpha test (all 8 functions)   f MRT, vertex buffer, indexed draws   g fixed-function VS + user PS, user VS + fixed-function PS   h clip planes / point size   i storage rule of the CONVERT formats (L6V5U5 / X8L8V8U8 sampled values, L8 / A8 render target textures)   j SHADEMODE = FLAT with every pair kind (COLOR0 takes the first vertex, the rest stays interpolated)   k SM1 texcoord / texcrd reading tN as data   m mutations
Tolerance: <= 1/255 per channel unless a case says so (bilinear filtering: 3/255, fixed-function modulate: 2/255)."""
import concurrent.futures as cf, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "l6", "ffpdev")); sys.path.insert(0, os.path.join(ROOT, "tests", "l6", "gl4")); sys.path.insert(0, HERE)
import test_ffpdev as fd, xl, cases, clib  # noqa: E402
D3D9 = fd.D3D9; DEV = fd.DEV; BACK = fd.BACK; INC = fd.INC; FRONT = fd.FRONT; FFP = fd.FFP
ENVLLVM = fd.ENVLLVM
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def flags(tmp): return fd.CXX + ["-I", HERE, "-I", INC, "-I", tmp, "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT]


def src_of(name, tmp):
    for d in (DEV, FFP, HERE, tmp):
        if os.path.exists(os.path.join(d, name)): return os.path.join(d, name)
    raise SystemExit("no source " + name)


def build(tmp, jobs=3):
    err = fd.gens(tmp)
    if err: return None, None, err
    srcs = fd.LIB + fd.RES + fd.GLUE + fd.CP + fd.DRAW + fd.FFPDEV + fd.GENS + ["env_table.gen.cpp"] + ["t_rpc.cpp", "t_play.cpp"] + fd.FFPC
    cmds, objs = [], {}
    for s in srcs:
        o = os.path.join(tmp, s + ".o"); objs[s] = o
        if s.endswith(".c"): cmds.append(["gcc", "-m32", "-std=gnu11", "-O1", "-w", "-I", FFP, "-c", src_of(s, tmp), "-o", o])
        else: cmds.append(flags(tmp) + ["-c", src_of(s, tmp), "-o", o])
    for f in sorted(f for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c")):
        o = os.path.join(tmp, "nx_" + f + ".o"); objs["nx_" + f] = o; cmds.append(["gcc", "-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", os.path.join(FRONT, f), "-o", o])
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    exe = os.path.join(tmp, "test_drawgl"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + list(objs.values()) + ["-lm", "-Wl,--unresolved-symbols=ignore-all"])
    return (exe, objs, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


def server(out, backend=BACK):
    srcs = sorted(os.path.join(backend, f) for f in os.listdir(backend) if (f.startswith("gl4_") or f == "nxgpu4_check.c") and f.endswith(".c")) + [os.path.join(ROOT, "tests", "l6", "copy", "server.c")]
    r = sh(["gcc", "-std=gnu11", "-O1", "-w", "-I", backend, *srcs, *xl.flags(), "-lEGL", "-lGL", "-lm", "-o", out])
    return r.stderr[-800:] if r.returncode else None


def play(exe, script, data, srv, tag):
    os.makedirs(data, exist_ok=True); env = dict(ENVLLVM, NXCOPY_SERVER=srv, NXCOPY_ARENA=os.path.join(data, "arena" + tag))
    r = subprocess.run([exe, script, data], capture_output=True, text=True, env=env, timeout=900)
    return r


def read_rt(data, case, rt):
    p = os.path.join(data, "o%d_%d.bin" % (case, rt))
    return open(p, "rb").read() if os.path.exists(p) else None


def compare(cs, data):
    """-> {part: [checks, failed]}, lines"""
    res, lines = {}, []
    for k, c in enumerate(cs, 1):
        st = res.setdefault(c.part, [0, 0]); got = [read_rt(data, k, r) for r in range(c.nrt)]
        if any(g is None or len(g) != 4 * clib.W * clib.H for g in got): st[0] += 1; st[1] += 1; lines.append("  FAIL %s: no output" % c.name); continue
        bad = 0; first = None
        for iy in range(clib.H):
            for ix in range(clib.W):
                exp = c.ref(ix, iy)
                for r in range(c.nrt):
                    o = (iy * clib.W + ix) * 4; b, g, rr, a = got[r][o:o + 4]
                    if exp[r] is None: e = (0x40, 0x20, 0x10, 0x80)
                    else: e = tuple(int(clib.clamp01(v) * 255 + 0.5) for v in exp[r])
                    st[0] += 1
                    if max(abs(rr - e[0]), abs(g - e[1]), abs(b - e[2]), abs(a - e[3])) > c.tol:
                        st[1] += 1; bad += 1
                        if first is None: first = (ix, iy, r, (rr, g, b, a), e)
        if bad: lines.append("  FAIL %s: %d pixels differ (tol %d); first (%d,%d) rt%d got %s want %s" % ((c.name, bad, c.tol) + first))
    return res, lines


def script_of(cs): return "\n".join(l for c in cs for l in c.lines) + "\n"


MUTS = [   # (parts that must catch it, file relative to render/d3d9, old (exactly once), new): the draw path's program / constant / texture binding and the backend function
    ("abcdefg", "dev/draw_const.cpp", "w[0] = stage ? 2u : 1u;", "w[0] = stage ? 1u : 2u;"),
    ("abcdefg", "dev/draw_const.cpp", "v[2] = 1.0f; v[FNVNX_DK_VS_PAD_OFFSET / 4]", "v[2] = -1.0f; v[FNVNX_DK_VS_PAD_OFFSET / 4]"),
    ("c", "dev/draw_const.cpp", "pr.ps_version == 0x0300 ? 0.0f : 1.0f", "pr.ps_version == 0x0300 ? 1.0f : 0.0f"),
    ("e", "dev/draw_const.cpp", "p[12] = dyn.alphaRef;", "p[12] = 0.0f;"),
    ("e", "dev/draw_const.cpp", "((sb >> NXSB_ALPHAFUNC & 7) + 1) : 0;", "((sb >> NXSB_ALPHAFUNC & 7) + 2) : 0;"),
    ("d", "dev/draw_const.cpp", "fm == NxFog_Linear ? 4 : 0;", "fm == NxFog_Linear ? 3 : 0;"),
    ("d", "dev/draw_const.cpp", "memcpy(p, dyn.fogColor, 16);", "memcpy(p + 1, dyn.fogColor, 12);"),
    ("a", "dev/draw_const.cpp", "memcpy(ib, s.psi, 256);", "memcpy(ib, s.psi, 128);"),
    ("a", "dev/draw_const.cpp", "((uint32_t*)(ib + 256 + 16 * k))[0] = s.psb[k] != 0; } nx_ubo_emit(1,", "((uint32_t*)(ib + 256 + 16 * k))[0] = s.psb[k] == 1; } nx_ubo_emit(1,"),
    ("a", "dev/draw_const.cpp", "memcpy(f, s.psf, sizeof s.psf);", "memcpy(f, s.psf, sizeof s.psf - 16);"),
    ("a", "dev/draw_const.cpp", "memcpy(f, s.vsf, sizeof f);", "memcpy(f, s.vsf, sizeof f - 16);"),
    ("h", "dev/draw_const.cpp", "memcpy(vs + FNVNX_DK_VS_CLIP_OFFSET + 16 * i, &s.clip[i], 16);", "memcpy(vs + FNVNX_DK_VS_CLIP_OFFSET + 16 * i, &s.clip[(i + 1) % 6], 16);"),
    ("h", "dev/draw_const.cpp", "if (s.rs[D3DRS_CLIPPING]) for", "if (1) for"),
    ("h", "dev/draw_const.cpp", "if (s.rs[D3DRS_CLIPPLANEENABLE] >> i & 1) memcpy", "if (1) memcpy"),
    ("h", "dev/draw_const.cpp", "q[1] = fbits(s.rs[D3DRS_POINTSIZE_MIN]); q[2] = fbits(s.rs[D3DRS_POINTSIZE_MAX]);", "q[1] = fbits(s.rs[D3DRS_POINTSIZE_MAX]); q[2] = fbits(s.rs[D3DRS_POINTSIZE_MIN]);"),
    ("h", "dev/draw_const.cpp", "q[0] = fbits(s.rs[D3DRS_POINTSIZE]);", "q[0] = fbits(s.rs[D3DRS_POINTSIZE_MIN]);"),
    ("b", "dev/draw_const.cpp", "im && im->kind == NXK_CUBE ? FNVNX_DK_STYPE_CUBE", "im && im->kind == NXK_CUBE ? FNVNX_DK_STYPE_2D"),
    ("c", "dev/draw_prog.cpp", "fnvnx_dk_vs_needs_complete(vw, (int)nvw, pr, (int)npr) | flat << 1, l, nl, &hr)", "0, l, nl, &hr)"),
    ("c", "dev/draw_prog.cpp", "p->ps = shader_prog(psc, 1, flat << 1, l, nl, &hr);", "p->ps = shader_prog(psc, 1, flat << 1, l, 0, &hr);"),
    ("c", "dev/draw_prog.cpp", "p->vs = shader_prog(vsc, 0, fnvnx_dk_vs_needs_complete(vw, (int)nvw, pr, (int)npr) | flat << 1, l, nl, &hr);", "p->vs = shader_prog(vsc, 0, fnvnx_dk_vs_needs_complete(vw, (int)nvw, pr, (int)npr) | flat << 1, l, 0, &hr);"),
    ("c", "dev/draw_prog.cpp", "if (fnvnx_dk_varying_location(d[i] / 16, d[i] % 16) < 0) canon = false;", "if (false) canon = false;"),
    ("b", "dev/draw_tex.cpp", "put(plan, 1, n, handle_of(d, n, &dm, &hr));", "put(plan, 1, n, handle_of(d, 0, &dm, &hr));"),
    ("b", "dev/draw_tex.cpp", "((ss.filter & NXSMP_SRGB) ? 1u : 0u)", "0u"),
    ("b", "dev/draw_tex.cpp", "put(plan, 1, dm == 1 ? 8 + n : dm == 2 ? 16 + n : n, h);", "put(plan, 1, dm == 1 ? 9 + n : dm == 2 ? 16 + n : n, h);"),
    ("f", "dev/draw_exec.cpp", "w[4] = (uint32_t)r.base; w[5] = 0; }", "w[4] = 0; w[5] = 0; }"),
    ("f", "dev/draw_exec.cpp", "w[3] = r.first; w[4] = 0; }", "w[3] = 0; w[4] = 0; }"),
    ("c", "backend/gl4_d3dshader.c", "o.link_pairs = key.nl ? key.l : NULL; o.n_link_pairs = key.nl;", "o.link_pairs = NULL; o.n_link_pairs = 0;"),
    ("c", "backend/gl4_d3dshader.c", "(d.stage == 0 && (d.variant & 1) ? FNVNX_SHADER_VS_COMPLETE : 0)", "0"),
    ("i", "dev/draw_state.cpp", "((uint64_t)m << NXRS_B_MASK(i)); }", "((uint64_t)15 << NXRS_B_MASK(i)); }"),
    ("j", "backend/gl4_d3dshader.c", "((d.variant & 2) ? FNVNX_SHADER_FLAT_COLOR : 0)", "0"),
    ("j", "backend/gl4_d3dshader.c", "if (d.variant > 3 ||", "if (d.variant > 1 ||"),
    ("j", "dev/draw_prog.cpp", "shader_prog(psc, 1, flat << 1, l, nl, &hr); if (!p->ps) return hr; }", "shader_prog(psc, 1, 0, l, nl, &hr); if (!p->ps) return hr; }"),
    ("j", "dev/draw_prog.cpp", "if ((vsc || psc) && !nx_ffp_flat(d, &flat))", "if (false && !nx_ffp_flat(d, &flat))"),
    ("f", "backend/gl4_shader.c", "if (g4.sh[id].d3d && --g4.sh[id].d3d->refs) return 0;", "if (0) return 0;"),
]


def mutated(base, tmp, rel, old, new, idx):
    d = os.path.join(tmp, "m%d" % idx); os.makedirs(d, exist_ok=True); path = os.path.join(D3D9, rel); txt = open(path).read()
    if txt.count(old) != 1: return None, None, "pattern matches %d times" % txt.count(old)
    if rel.startswith("backend/"):
        bd = os.path.join(d, "backend"); shutil.copytree(BACK, bd); open(os.path.join(bd, os.path.basename(rel)), "w").write(txt.replace(old, new))
        srv = os.path.join(d, "server"); err = server(srv, bd)
        return (base["exe"], srv, "") if not err else (None, None, err)
    mp = os.path.join(d, os.path.basename(rel)); open(mp, "w").write(txt.replace(old, new)); objs = dict(base["objs"]); o = os.path.join(d, "x.o")
    r = sh(flags(base["tmp"]) + ["-c", mp, "-o", o])
    if r.returncode: return None, None, "compile: " + r.stderr[-300:]
    objs[os.path.basename(rel)] = o; exe = os.path.join(d, "t"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + list(objs.values()) + ["-lm", "-Wl,--unresolved-symbols=ignore-all"])
    return (exe, base["srv"], "") if r.returncode == 0 else (None, None, r.stderr[-300:])


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    xl.flags()
    sm, ops = xl.hand(); cs = cases.make(lambda t: sm.assemble(t, ops))
    if only: cs = [c for c in cs if only in c.name]
    tmp = tempfile.mkdtemp(prefix="d9drawgl_"); data = os.path.join(tmp, "data"); script = os.path.join(tmp, "script.txt"); open(script, "w").write(script_of(cs))
    err = server(os.path.join(tmp, "server"))
    if err: print("SERVER BUILD FAILED\n" + err); return 1
    exe, objs, err = build(tmp)
    if not exe: print("BUILD FAILED\n" + err); return 1
    srv = os.path.join(tmp, "server")
    def run_all(e, s, tag):
        shutil.rmtree(data, ignore_errors=True); r = play(e, script, data, s, tag); res, lines = compare(cs, data); return r, res, lines
    r, res, lines = run_all(exe, srv, "0")
    print("\n".join(l for l in r.stdout.split("\n") if l.startswith(("FAIL", "backend")))[:2500]); print("\n".join(lines)[:4000])
    m = re.search(r"PART p: (\d+) checks, (\d+) failed", r.stdout); pp = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
    if r.returncode or not m or pp[1]: fails.append("player %s rc=%d %s" % (pp, r.returncode, r.stderr[-300:]))
    print("PART p (player API results): %d checks, %d failed" % pp)
    for p in sorted(res):
        n, f = res[p]; print("PART %s: %d checks, %d failed" % (p, n, f))
        if f or not n: fails.append("part " + p)
    print("cases: %d (%s)" % (len(cs), ", ".join("%s %d" % (p, sum(c.part == p for c in cs)) for p in sorted(res))))
    if "--no-mutations" not in sys.argv and not only:
        base = {"exe": exe, "objs": objs, "tmp": tmp, "srv": srv}; caught = 0
        def one(i):
            parts, rel, old, new = MUTS[i]; x, s, e = mutated(base, tmp, rel, old, new, i)
            if not x: return i, None, e
            dd = os.path.join(tmp, "md%d" % i); os.makedirs(dd, exist_ok=True); r2 = play(x, script, dd, s, "m"); rs2, _ = compare(cs, dd)
            return i, (r2, rs2), ""
        with cf.ThreadPoolExecutor(2) as ex:
            for i, out, e in ex.map(one, range(len(MUTS))):
                parts = MUTS[i][0]
                if out is None: print("mutation %d: build failed: %s" % (i, e)); fails.append("mutation %d build" % i); continue
                r2, rs2 = out; hit = r2.returncode != 0 or any(rs2.get(p, (0, 0))[1] > 0 for p in parts)
                caught += hit; print("mutation %d (%s: %r -> parts %s): %s" % (i, MUTS[i][1], MUTS[i][2][:50], parts, "caught" if hit else "NOT CAUGHT"))
                if not hit: fails.append("mutation %d not caught" % i)
        print("PART m: %d mutations, %d caught" % (len(MUTS), caught))
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL OK" if not fails else "FAILED: " + "; ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
