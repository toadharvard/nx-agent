#!/usr/bin/env python3
"""test_draw.py: tests of render/d3d9/dev/draw_*.cpp (D5a: the draw path of the D3D9 device). python3 -I tests/l6/draw/test_draw.py [--no-mutations] [--no-guest]
 a validation of the four draw calls over their parameter domain vs the python oracle (oracle.py: Wine 9.0 device.c + the Windows rows of d3d9/tests/device.c)   b primitive / vertex / index formulas, instancing
 c vertex layouts: declarations x vertex shader signatures (hand assembled) x frequencies   d state flush: constants and driver blocks byte-exact vs the python model of the layout contract, every dirty group
 e program pairs: link set L and variant bit vs a python reimplementation, lazy creation / caching / destruction   f UP draws   g method gate and guest undefined count of the current tree vs the numbers it records (g_record.json; --record rewrites it)   h mutations.
The device is the product code (obj, state, res_*, glue, draw_*) built -m32 over a RECORDING backend (t_rec.cpp: it decodes every submitted stream and logs every backend call)."""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "dev"))
import oracle  # noqa: E402
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); ENV = os.path.join(ROOT, "render", "d3d9", "env"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); FRONT = os.path.join(ROOT, "render", "d3d9", "front"); BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]; NATIVE = os.path.join(ROOT, "tests", "l6", "native")
RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp", "res_fmt.cpp", "res_tex.cpp", "res_surf.cpp", "res_create.cpp"]
GLUE = ["glue_stream.cpp", "glue_image.cpp", "glue_query.cpp", "glue_buf.cpp"]
DRAW = ["draw_api.cpp", "draw_exec.cpp", "draw_prim.cpp", "draw_prog.cpp", "draw_layout.cpp", "draw_ids.cpp", "draw_state.cpp", "draw_const.cpp", "draw_tex.cpp", "draw_bind.cpp"]
TESTS = ["t_rec.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp", "t_e.cpp", "t_f.cpp"]
REC = os.path.join(HERE, "g_record.json")
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def flags(dev, tmp): return ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w", "-I", INC, "-I", tmp, "-I", dev, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]


def build(dev, tmp, jobs=8, base=None, only=None):
    """compile every source of the device in `dev` (+ generated, front, tests) into tmp; with base (a finished build dir) only the sources in `only` are recompiled; returns (exe, product objects) or (None, error)"""
    os.makedirs(tmp, exist_ok=True)
    if base is None:
        for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                    [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")],
                    [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
            r = sh(cmd)
            if r.returncode: return None, r.stderr[-800:]
    gdir = tmp if base is None else base
    fl = flags(dev, gdir)
    srcs = [(os.path.join(NATIVE if s == "app_unk_native.cpp" else dev, s), s) for s in LIB + RES + GLUE + DRAW] + [(os.path.join(gdir, s), s) for s in GENS + ["env_table.gen.cpp"]] + [(os.path.join(HERE, s), s) for s in TESTS]
    fronts = sorted(f for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in srcs if only is None or n in only]
    if base is None: cmds += [["gcc", "-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", os.path.join(FRONT, f), "-o", os.path.join(tmp, "nx_" + f + ".o")] for f in fronts]
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    objdir = tmp
    def obj(n): p = os.path.join(tmp, n + ".o"); return p if os.path.exists(p) else os.path.join(base, n + ".o")
    prod = [obj(n) for _, n in srcs if n in LIB + GENS + RES + GLUE + DRAW]
    link = [obj(n) for _, n in srcs] + [os.path.join(tmp if base is None else base, "nx_" + f + ".o") for f in fronts]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_draw")] + link + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_draw"), prod) if r.returncode == 0 else (None, r.stderr[-800:])


def run(exe, data):
    r = subprocess.run([exe, data], capture_output=True, text=True, timeout=900)
    return r, {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}


# ---- g: gates
def guest_undefined(root, out):
    tc = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"
    if not os.path.isdir(tc): return None
    r = subprocess.run(["bash", os.path.join(root, "render", "d3d9", "build_guest.sh"), out], capture_output=True, text=True, env=dict(os.environ, FNVNX_TC=tc, JOBS="3"), timeout=900)
    if r.returncode: return ("build failed: " + (r.stdout + r.stderr)[-300:], None)
    rows = [l.split("\t") for l in open(os.path.join(out, "undefined.tsv")).read().split("\n")[1:] if l]
    return [r[0] for r in rows if r[2] != "toolchain library"]   # libc symbols (memcmp, ...) are the toolchain's, not missing implementations


MUTS = [   # (part that must fail, file, old text, new text)
    ("d", "draw_state.cpp", "fnvnx_wine_fixme(NXW_wined3d_device_apply_stateblock);", "(void)0;"),
    ("d", "draw_state.cpp", "!rs[D3DRS_LASTPIXEL] && !b.fx_lastpixel", "!b.fx_lastpixel"),
    ("b", "draw_prim.cpp", "case D3DPT_TRIANGLELIST: return n * 3;", "case D3DPT_TRIANGLELIST: return n * 2;"),
    ("b", "draw_prim.cpp", "case D3DPT_TRIANGLEFAN: return n + 2;", "case D3DPT_TRIANGLEFAN: return n + 1;"),
    ("b", "draw_prim.cpp", "return s.freq[0] & NXD_FREQ_MASK;", "return s.freq[0] & 0x7FFFF;"),
    ("b", "draw_exec.cpp", "w[2] = nx_instances(s);", "w[2] = 1;"),
    ("c", "draw_layout.cpp", "FNVNX_DK_PAIR(c.Usage, c.UsageIndex) == sig.pair[k] && c.Usage <= 13 && c.UsageIndex <= 15) e = &c;", "FNVNX_DK_PAIR(c.Usage, c.UsageIndex) == sig.pair[k] && c.Usage <= 13 && c.UsageIndex <= 15 && !e) e = &c;"),
    ("c", "draw_layout.cpp", "a.type = NxAttr4_Float4; a.location = sig.loc[unb[j]];", "a.type = NxAttr4_Float3; a.location = sig.loc[unb[j]];"),
    ("c", "draw_layout.cpp", "L.divisor[s] = freq[s] & NXD_FREQ_MASK ? freq[s] & NXD_FREQ_MASK : 1;", "L.divisor[s] = 1;"),
    ("d", "draw_const.cpp", "v[2] = 1.0f;", "v[2] = -1.0f;"),
    ("d", "draw_const.cpp", "pr.ps_version == 0x0300 ? 0.0f : 1.0f", "pr.ps_version == 0x0200 ? 0.0f : 1.0f"),
    ("d", "draw_const.cpp", "fm == NxFog_Vertex ? 1 : fm == NxFog_Exp ? 2", "fm == NxFog_Vertex ? 4 : fm == NxFog_Exp ? 2"),
    ("d", "draw_const.cpp", "e[4] = fbits(t[D3DTSS_BUMPENVLSCALE]); e[5] = fbits(t[D3DTSS_BUMPENVLOFFSET]);", "e[4] = fbits(t[D3DTSS_BUMPENVLOFFSET]); e[5] = fbits(t[D3DTSS_BUMPENVLSCALE]);"),
    ("d", "draw_state.cpp", "(rs[D3DRS_CLIPPING] == 0 ? 1ull << NXRS_M_NOCLIP : 0)", "(rs[D3DRS_CLIPPING] != 0 ? 1ull << NXRS_M_NOCLIP : 0)"),
    ("d", "draw_tex.cpp", "NX_TEX_HANDLE(im->gpu + ((ss.filter & NXSMP_SRGB) ? 1u : 0u), sid)", "NX_TEX_HANDLE(im->gpu, sid)"),
    ("d", "draw_state.cpp", "if (!b.vp_ok || memcmp(&b.vp, &dyn.viewport, sizeof b.vp)) emit_vp(dyn.viewport);", "emit_vp(dyn.viewport);"),
    ("e", "draw_prog.cpp", "if (canon) { *nl = 0; return 0; }", "if (false) { *nl = 0; return 0; }"),
    ("e", "draw_prog.cpp", "fnvnx_dk_vs_needs_complete(vw, (int)nvw, pr, (int)npr)", "0"),
    ("e", "res_smcheck.cpp", "else if (cl == NXC_oFog) add_pair(a, 11, 0);", "else if (cl == NXC_oFog) add_pair(a, 11, 1);"),
    ("a", "draw_api.cpp", "if (!d->cur.ib[0]) return D3D_OK;", "if (!d->cur.ib[0]) return D3DERR_INVALIDCALL;"),
    ("a", "draw_api.cpp", "    if (indexed) { IDirect3DIndexBuffer9* nul = nullptr; d->put_direct(NX_S_ib, &nul); }\n    return hr;", "    return hr;"),
    ("f", "draw_bind.cpp", "bind_vtx(0, blk, off, r.up->vbytes, r.up->vstride, false);", "bind_vtx(0, blk, off, r.up->vbytes - 1, r.up->vstride, false);"),
    ("f", "draw_api.cpp", "(const uint8_t*)vdata + (uint64_t)minv * stride", "(const uint8_t*)vdata"),
]


def main():
    keep = "--keep" in sys.argv; tmp = tempfile.mkdtemp(prefix="d9draw_"); data = os.path.join(tmp, "data"); os.makedirs(data)
    oracle.write_all(data)
    exe, prod = build(DEV, tmp)
    if not exe: print("BUILD FAILED\n" + prod); return 1
    r, parts = run(exe, data); print(r.stdout[-2500:], r.stderr[-300:])
    for p in "abcdef":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    import test_dev  # noqa: E402  (tests/l6/dev: gate() over the product objects)
    want, miss, unres = test_dev.gate(tmp, prod)
    draw_names = [w for w in want if w.split("::")[1] in ("DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP")]
    draw_miss = sorted(set(draw_names) & set(miss)); defined = len(want) - len(miss)
    rec = json.load(open(REC)) if os.path.exists(REC) else None
    print(f"g: method gate over the draw test's product set (spec {len(want)}): defined {defined}, draw methods {len(draw_names) - len(draw_miss)}/{len(draw_names)}; recorded {rec}")
    if len(draw_names) != 8 or draw_miss: fails.append(f"g: draw methods not all defined: {draw_miss}")
    if "--no-guest" not in sys.argv:
        ga = guest_undefined(ROOT, os.path.join(tmp, "guest_after"))
        if ga is None: print("g: guest toolchain absent: not run"); fails.append("g: guest toolchain absent")
        elif isinstance(ga, tuple): print("g: guest build failed", ga); fails.append("g: guest build failed")
        else:
            dsyms = [g for g in ga if re.search(r"Draw(Indexed)?Primitive|nx_draw_|nx_ffp_[pv]s_program", g)]
            print(f"g: guest undefined (HEAD sources as they are) {len(ga)}; draw-path / fixed-function symbols among them: {dsyms}")
            if dsyms: fails.append(f"g: draw path symbols undefined in the guest build: {dsyms}")
            if "--record" in sys.argv: json.dump({"defined": defined, "guest_undefined": len(ga)}, open(REC, "w")); print("g: recorded", REC)
            elif rec is None: fails.append("g: no record (run with --record once)")
            else:
                if defined < rec["defined"]: fails.append(f"g: defined methods fell {rec['defined']} -> {defined}")
                if len(ga) > rec["guest_undefined"]: fails.append(f"g: guest undefined rose {rec['guest_undefined']} -> {len(ga)}")
    if "--no-mutations" not in sys.argv:
        def one(i):
            part, f, old, new = MUTS[i]; mdev = os.path.join(tmp, f"m{i}_dev"); shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__"))
            txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}"))
            x, pr = build(mdev, os.path.join(tmp, f"m{i}"), jobs=2, base=tmp, only={f})
            if not x: return i, None, pr
            rr, ps = run(x, data); return i, ps, rr.returncode
        with cf.ThreadPoolExecutor(3) as ex:
            for i, ps, rc in ex.map(one, range(len(MUTS))):
                part = MUTS[i][0]; ok = ps is not None and (ps.get(part, (0, 0))[1] > 0 or rc != 0 and part not in ps)
                print(f"h: mutation {i} ({MUTS[i][1]}: {MUTS[i][2][:44]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else (ps, rc)}")
                if not ok: fails.append(f"h: mutation {i} not caught ({ps} {rc})")
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    if not keep: shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
