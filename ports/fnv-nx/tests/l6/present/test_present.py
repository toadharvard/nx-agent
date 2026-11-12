#!/usr/bin/env python3
"""test_present.py: the presentation hooks of render/d3d9/dev/glue_present.cpp (nxd3d_present, nxd3d_wait_idle, nxd3d_frontbuffer_read, nxd3d_wait_vblank) and the dev_chain.cpp code that calls them, end to end:
the real device (render/d3d9/dev/*.cpp, the real glue) in a -m32 process, the real GL backend (libnxgl4 sources) in an LP64 replay process over llvmpipe (tests/l6/copy harness: RPC + shared arena), a spy on every backend call.
python3 -I tests/l6/present/test_present.py [--no-mutations]
 a Present full / partial rects, scaling, interval 0 / 1, with and without gamma, frame files and GetFrontBufferData equal a CPU model, spy: NxPresent fields, stream submitted before the present   b software cursor at inside / partly outside /
 outside positions, with gamma, hidden, allocation failure; the back buffer never changes   c back buffer rotation for 1, 2, 3 buffers (rule of dev_chain.cpp)   d WaitForVBlank, Reset waits for idle, a chain larger than the swapchain
 h mutations (one rule broken in a copy of the product sources; the named part must fail).
Prints `PART x: n checks, f failed` and `ALL OK`."""
import concurrent.futures as cf, os, re, subprocess, sys, tempfile, shutil
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "gl4")); import xl  # noqa: E402  (the translator build libnxgl4 links: FNVNX_SHADERS_BUILD, required)
D3 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3, "dev"); GEN = os.path.join(D3, "gen"); ENV = os.path.join(D3, "env"); SPEC = os.path.join(D3, "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); BACK = os.path.join(D3, "..", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); FRONT = os.path.join(D3, "front"); COPY = os.path.join(ROOT, "tests", "l6", "copy"); TOT = os.path.join(ROOT, "render", "shaders", "total")
GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp", "env_table.gen.cpp"]
ENVLLVM = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def gen(tmp):
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(DEV, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(DEV, "res_smgen.py"), tmp, os.path.join(TOT, "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")], [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return r.stderr[-800:]
    return None


def cxx(tmp, src, obj, dev=DEV):
    cmd = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", INC, "-I", tmp, "-I", dev, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", COPY, "-I", HERE]
    if os.path.basename(src) == "t_rpc.cpp": cmd += ["-Dnxd3d_gpu_call=t_rpc_gpu_call", "-Dnxd3d_gpu_alloc=t_rpc_gpu_alloc", "-Dnx_env_profile_of=t_rpc_unused_env_profile_of"]   # the harness's RPC / arena / profile become the layer under the spy (t_env.cpp)
    return cmd + ["-c", src, "-o", obj]


def sources(tmp):
    o = {}
    for f in sorted(os.listdir(DEV)):
        if f.endswith(".cpp") and f != "app_unk_guest.cpp" and not f.startswith("ffp_"): o[f] = os.path.join(DEV, f)   # ffp_*: the fixed-function programs need generated GLSL / DKSH headers this harness does not build
    for g in GENS: o[g] = os.path.join(tmp, g)
    for f in sorted(os.listdir(FRONT)):
        if f.endswith(".c"): o["front_" + f] = os.path.join(FRONT, f)
    for t in ("t_env.cpp", "t_parts.cpp"): o[t] = os.path.join(HERE, t)
    o["t_rpc.cpp"] = os.path.join(COPY, "t_rpc.cpp")
    return o


def compile_objs(tmp, objs, od, jobs=3, only=None, dev=DEV):
    os.makedirs(od, exist_ok=True); cmds = []
    for n, src in objs.items():
        if only is not None and n not in only: continue
        obj = os.path.join(od, os.path.splitext(n)[0] + ".o")
        cmds.append((n, ["gcc", "-m32", "-std=c11", "-w", "-I", BACK, "-I", NXB, "-I", FRONT, "-c", src, "-o", obj] if src.endswith(".c") else cxx(tmp, src, obj, dev)))
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(lambda c: sh(c[1]), cmds))
    for (n, c), r in zip(cmds, rs):
        if r.returncode: return n + "\n" + r.stderr[-1500:]
    return None


def link(names, od, exe, repl=None):
    objs = [os.path.join((repl or {}).get(n, od), os.path.splitext(n)[0] + ".o") for n in names]
    r = sh(["g++", "-m32", "-no-pie", "-o", exe] + objs + ["-Wl,--unresolved-symbols=ignore-all"])
    return r.stderr[-800:] if r.returncode else None


def build_server(out, backend=BACK):
    srcs = sorted(os.path.join(backend, f) for f in os.listdir(backend) if (f.startswith("gl4_") or f == "nxgpu4_check.c") and f.endswith(".c")) + [os.path.join(HERE, "server.c")]
    r = sh(["gcc", "-std=gnu11", "-O1", "-w", "-I", backend, *srcs, *xl.flags(), "-lEGL", "-lGL", "-lm", "-o", out])
    return r.stderr[-800:] if r.returncode else None


def run(exe, server, tmp):
    frames = tempfile.mkdtemp(prefix="frames_", dir=tmp); arena = os.path.join(tmp, "arena")
    env = dict(ENVLLVM, NXCOPY_SERVER=server, NXCOPY_ARENA=arena, NXGL4_FRAMES=frames, NXP_W="48", NXP_H="40")
    r = subprocess.run([exe, "."], capture_output=True, text=True, env=env, timeout=600, cwd=tmp)
    shutil.rmtree(frames, ignore_errors=True)
    return r, {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}


# (part that must fail, file, old text (exactly once), replacement)
MUTS = [
    ("a", "glue_present.cpp", "    nxs_flush();                                                                                   // R2d", "    // nxs_flush();                                                                                // R2d"),
    ("a", "glue_present.cpp", "q->gamma = FNVNX_ADDR_(ramp);", "q->gamma = 0;"),
    ("b", "glue_present.cpp", "q->cursor = FNVNX_ADDR_(cur);", "q->cursor = 0;"),
    ("a", "glue_present.cpp", "uint32_t cw = umin(w, info->width), ch = umin(h, info->height)", "uint32_t cw = umin(w, info->width), ch = umin(h, info->height - 1)"),
    ("d", "glue_present.cpp", "for (uint32_t y = 0; y < h; y++) { uint32_t from = y < ch ? cw * 4 : 0; memset(", "for (uint32_t y = h; y < h; y++) { uint32_t from = y < ch ? cw * 4 : 0; memset("),
    ("d", "glue_present.cpp", "uint64_t a[1] = {0}; return result(nxd3d_gpu_call(NXGPU_FN_WAIT_VBLANK", "uint64_t a[1] = {1}; return result(nxd3d_gpu_call(NXGPU_FN_WAIT_VBLANK"),
    ("d", "glue_present.cpp", "void nxd3d_wait_idle(NxObj*) { nxs_finish(); }", "void nxd3d_wait_idle(NxObj*) { nxs_flush(); }"),
    ("b", "glue_present.cpp", "if (cbytes && !(cur = nxd3d_gpu_alloc((cbytes + 4095u) & ~(size_t)4095u))) return NXD3D_HOOK_NOMEM;", "if (cbytes && !(cur = nxd3d_gpu_alloc((cbytes + 4095u) & ~(size_t)4095u))) return 1;"),
    ("b", "dev_chain.cpp", "p.cx = L->cur.x - (int32_t)L->cur.hx - cr.left;", "p.cx = L->cur.x - (int32_t)L->cur.hx;"),
    ("b", "dev_chain.cpp", "if (L->cur.visible && L->cur.bgra) { p.cursor", "if (L->cur.bgra) { p.cursor"),
    ("c", "dev_chain.cpp", "for (uint32_t i = 0; i + 1 < c->nbb; i++) bbimg(c, i)->gpu = bbimg(c, i + 1)->gpu;", "for (uint32_t i = 0; i + 1 < c->nbb; i++) bbimg(c, i)->gpu = bbimg(c, i)->gpu;"),
    ("a", "dev_chain.cpp", "p.interval = (flags & D3DPRESENT_FORCEIMMEDIATE) ? 0 :", "p.interval = (flags & D3DPRESENT_FORCEIMMEDIATE) ? 1 :"),
    ("b", "gl4_present.c", "(uint8_t)((v * a + d[c] * (255 - a) + 127) / 255)", "(uint8_t)((v * a + d[c] * (255 - a)) / 255)"),
    ("a", "gl4_read.c", "gl4_img(r->src.image ? r->src.image : g4.swapImage);   /* image 0", "gl4_img(r->src.image);   /* image 0"),
]


def mutations(tmp, names, objs, od, server):
    caught = 0
    for i, (part, fname, old, new) in enumerate(MUTS):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d); path = next((p for p in (os.path.join(DEV, fname), os.path.join(BACK, fname)) if os.path.exists(p)))
        s = open(path).read(); assert s.count(old) == 1, "mutation %d does not match exactly once in %s: %s" % (i, fname, old)
        src = os.path.join(d, fname); open(src, "w").write(s.replace(old, new)); exe = os.path.join(tmp, "mexe"); srv = server
        if path.startswith(BACK):
            bd = os.path.join(d, "backend"); shutil.copytree(BACK, bd); open(os.path.join(bd, fname), "w").write(s.replace(old, new)); srv = os.path.join(d, "server"); err = build_server(srv, bd)
            if err: print("mutation %d: server build failed\n%s" % (i, err)); fails.append("mutation %d build" % i); continue
        else:
            err = compile_objs(tmp, {fname: src}, d)
            if err or link(names, od, exe, {fname: d}): print("mutation %d: build failed\n%s" % (i, err)); fails.append("mutation %d build" % i); continue
        r, res = run(exe if not path.startswith(BACK) else os.path.join(tmp, "test_present"), srv, tmp)
        hit = r.returncode != 0 or res.get(part, (0, 0))[1] > 0 or part not in res
        if hit: caught += 1
        print("mutation %d (%s, part %s): %s" % (i, fname, part, "caught" if hit else "NOT CAUGHT"))
        if not hit: fails.append("mutation %d not caught" % i)
        shutil.rmtree(d, ignore_errors=True)
    print("PART h: %d mutations, %d caught" % (len(MUTS), caught))


def main():
    tmp = tempfile.mkdtemp(prefix="d9present_"); err = gen(tmp)
    if err: print("GEN FAILED\n" + err); return 1
    objs = sources(tmp); od = os.path.join(tmp, "objs"); err = compile_objs(tmp, objs, od)
    if err: print("BUILD FAILED\n" + err); return 1
    names = list(objs); exe = os.path.join(tmp, "test_present"); err = link(names, od, exe)
    if err: print("LINK FAILED\n" + err); return 1
    server = os.path.join(tmp, "server"); err = build_server(server)
    if err: print("SERVER BUILD FAILED\n" + err); return 1
    r, res = run(exe, server, tmp); sys.stdout.write("".join(l + "\n" for l in r.stdout.split("\n")[-60:] if l and not l.startswith("PART")))
    for p in "abcd":
        if p not in res: fails.append("part " + p + " missing"); print("PART %s: no result" % p); continue
        n, f = res[p]; print("PART %s: %d checks, %d failed" % (p, n, f))
        if f or n == 0: fails.append("part " + p)
    if r.returncode and not fails: fails.append("exit code %d\n%s" % (r.returncode, r.stderr[-600:]))
    if "--no-mutations" not in sys.argv and not fails: mutations(tmp, names, objs, od, server)
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL OK" if not fails else "FAILED: " + ", ".join(fails)); return 1 if fails else 0


if __name__ == "__main__": sys.exit(main())
