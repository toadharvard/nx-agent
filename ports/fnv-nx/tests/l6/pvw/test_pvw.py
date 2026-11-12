#!/usr/bin/env python3
"""test_pvw.py: tests of render/d3d9/dev/pvw_*.cpp (ProcessVertices exactly as Wine 9.0 does it, both device classes).  python3 -I tests/l6/pvw/test_pvw.py [--no-mutations] [--no-guest] [--seed N]
 a  the generated sweep (cases_a: matrices x viewports x source layouts x destination FVFs x lighting / fog / material sources x ranges): every destination byte bit-equal with oracle.py (numpy binary32 model written from Wine's arithmetic)
 b  every return-code path and every fixme id (exactly once per call, captured from stderr "fnvnx-wine-fixme: <id>"), the pointer rules P1 / P2, the memory-unsafe rules M1 / M2, exact-fit buffers
 c  state / refcounts / source buffers unchanged, dirty range of the destination per pool, in-place processing (destination = source stream)  (these checks also run inside a and b)
 d  the not-todo_wine row of Wine's own d3d9 test (visual.c test_sysmem_draw) on both device classes
 e  guest build: defined nxb rows, "L6 method without body", os_exit_process no longer referenced   f  the oom scan (tests/l6/oom --no-mutations)   g  mutation checks (one rule broken in a copy of dev/, the named part must fail)
Product objects = render/d3d9/dev/*.cpp minus app_unk_guest.cpp and glue_*.cpp / ffp_*.cpp + generated sources + front/*.c; hooks recorded by t_env.cpp (as tests/l6/m1)."""
import concurrent.futures as cf, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D3 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3, "dev"); GEN = os.path.join(D3, "gen"); ENV = os.path.join(D3, "env"); SPEC = os.path.join(D3, "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); BACK = os.path.join(D3, "..", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); FRONT = os.path.join(D3, "front"); TOT = os.path.join(ROOT, "render", "shaders", "total")
TESTS = ["t_env.cpp", "t_run.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp", "env_table.gen.cpp"]
TC = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"; UAM_DEPS = os.environ.get("FNVNX_SHADERS_DEPS") or "/home/user/work/l6/s2a/build/deps"
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fl(tmp, dev): return ["g++", "-m32", "-msse2", "-mfpmath=sse", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", dev, "-I", INC, "-I", tmp, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE] + san.flags()


def gen(tmp, dev=DEV):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(TOT, "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")], [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return r.stderr[-800:]
    return None


def compile_all(tmp, dev, objs, jobs=3, outdir=None):
    od = outdir or os.path.join(tmp, "objs"); os.makedirs(od, exist_ok=True); cmds = []
    for n, src in objs.items():
        cmds.append((n, (["gcc", "-m32", "-std=c11", "-w", "-I", BACK, "-I", NXB, "-I", FRONT] + san.flags() if src.endswith(".c") else fl(tmp, dev)) + ["-c", src, "-o", os.path.join(od, os.path.splitext(n)[0] + ".o")]))
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(lambda c: sh(c[1]), cmds))
    for (n, c), r in zip(cmds, rs):
        if r.returncode: return n + "\n" + r.stderr[-1500:]
    return None


def sources(tmp, dev):
    o = {}
    for f in sorted(os.listdir(dev)):
        if f.endswith(".cpp") and f != "app_unk_guest.cpp" and not f.startswith(("glue_", "ffp_")): o[f] = os.path.join(dev, f)
    for g in GENS: o[g] = os.path.join(tmp, g)
    for f in sorted(os.listdir(FRONT)):
        if f.endswith(".c"): o["front_" + f] = os.path.join(FRONT, f)
    return o


def link(names, od, test_od, exe, swap=None):
    bn = lambda d, n: os.path.join(swap[1] if swap and n == swap[0] else d, os.path.splitext(n)[0] + ".o")
    r = sh(["g++", "-m32", "-no-pie", "-o", exe] + [bn(od, n) for n in names] + [os.path.join(test_od, os.path.splitext(n)[0] + ".o") for n in TESTS] + san.flags() + ["-lm", "-Wl,--unresolved-symbols=ignore-all"])
    return r.stderr[-800:] if r.returncode else None


def run(exe, data, parts="abcd"):
    r = sh([exe, data, parts], env=dict(os.environ, **san.env())) if hasattr(san, "env") else sh([exe, data, parts]); ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, ps


def guest(src_root, out):
    env = dict(os.environ, FNVNX_TC=TC, FNVNX_SHADERS_DEPS=UAM_DEPS, JOBS="3"); r = subprocess.run(["bash", os.path.join(src_root, "render", "d3d9", "build_guest.sh"), out], capture_output=True, text=True, env=env)
    return r


def main():
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 1
    import cases, gen as G
    tmp = tempfile.mkdtemp(prefix="d9pvw_"); data = os.path.join(tmp, "data"); os.makedirs(data); counts = {}
    for name, f in (("a", cases.gen_a), ("b", cases.gen_b), ("c", cases.gen_c)):
        L = f(seed); n_inv = 0
        with open(os.path.join(data, f"cases_{name}.txt"), "w") as o:
            for c in L: G.run_model(c); n_inv += c["hr"] != 0; o.write(G.encode(c))
        counts[name] = (len(L), n_inv)
    print("oracle: " + ", ".join(f"{k}: {v[0]} cases ({v[1]} INVALIDCALL)" for k, v in sorted(counts.items())))
    err = gen(tmp)
    if err: print("GEN FAILED\n" + err); return 1
    prod = sources(tmp, DEV); tsrc = {t: os.path.join(HERE, t) for t in TESTS}
    err = compile_all(tmp, DEV, {**prod, **tsrc})
    if err: print("BUILD FAILED\n" + err); return 1
    od = os.path.join(tmp, "objs"); exe = os.path.join(tmp, "test_pvw"); err = link(list(prod), od, od, exe)
    if err: print("LINK FAILED\n" + err); return 1
    r, parts = run(exe, data); print("\n".join(l for l in r.stdout.split("\n") if l.startswith(("FAIL", "  line", "a:", "b:", "c:")))[:3500])
    for p in "abcd":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
        if p in parts: print(f"PART {p}: {parts[p][0]} checks, {parts[p][1]} failed")
    # ---- e: guest build
    if "--no-guest" not in sys.argv:
        gout = os.path.join(tmp, "guest"); r = guest(ROOT, gout)
        if r.returncode: fails.append("e: guest build failed"); print("guest build failed:", (r.stdout + r.stderr)[-800:])
        else:
            nxb = len([l for l in open(os.path.join(gout, "defined_nxb.txt")).read().split("\n") if l.strip() and not l.startswith("#")]); und = open(os.path.join(gout, "undefined.tsv")).read().split("\n")
            nobody = [l for l in und if "L6 method without body" in l]; exitref = [l for l in und if "os_exit_process" in l]; pv = [l for l in nobody if "ProcessVertices" in l]
            print(f"PART e: guest build: defined nxb {nxb}, L6 method without body {len(nobody)}, ProcessVertices without body {len(pv)}, os_exit_process references {len(exitref)}")
            if nobody or exitref: fails.append(f"e: {len(nobody)} methods without body, {len(exitref)} os_exit_process references")
    # ---- f: oom scan
    if "--no-oom" not in sys.argv:
        r = sh([sys.executable, "-I", os.path.join(HERE, "..", "oom", "test_oom.py"), "--no-mutations"], env=dict(os.environ, FNVNX_TC=TC, FNVNX_SHADERS_DEPS=UAM_DEPS)); tail = [l for l in r.stdout.split("\n") if l.strip()][-6:]
        print("PART f: oom: " + " | ".join(tail)[-600:]); ok = r.returncode == 0 and "ALL OK" in r.stdout
        if not ok: fails.append("f: oom scan not clean")
    # ---- g: mutations
    if "--no-mutations" not in sys.argv:
        P = lambda f, old, new: (f, old, new)
        muts = [("a", *P("pvw_math.cpp", "t.m[r][c] = ((a + b) + e) + f;", "t.m[r][c] = a + (b + (e + f));")), ("a", *P("pvw_core.cpp", "y *= -1;", "")), ("a", *P("pvw_core.cpp", "rhw = 1 / rhw;", "")),
                ("a", *P("pvw_core.cpp", "max_z = v.MaxZ > v.MinZ + 0.001f ? v.MaxZ : v.MinZ + 0.001f", "max_z = v.MaxZ")), ("a", *P("pvw_core.cpp", "x += vw / 2 + vx;", "x += vw / 2;")),
                ("a", *P("pvw_core.cpp", "wr(c, dp + 12, &o[3], 4, pos == D3DFVF_XYZ);", "")), ("a", *P("pvw_core.cpp", "if (fvf & D3DFVF_PSIZE) dp += 4;", "")), ("a", *P("pvw_core.cpp", "v * 255.0f + 0.5f", "v * 255.0f")),
                ("a", *P("pvw_core.cpp", "o->r = o->g = o->b = o->a = mcs == 1 ? 1.0f : 0.0f;", "o->r = o->g = o->b = o->a = 0.0f;")), ("a", *P("pvw_light.cpp", "{-p.Direction.x, -p.Direction.y, -p.Direction.z, 0.0f}", "{p.Direction.x, p.Direction.y, p.Direction.z, 0.0f}")),
                ("a", *P("pvw_light.cpp", "s.rs[D3DRS_RANGEFOGENABLE] ? 3 : 2", "s.rs[D3DRS_RANGEFOGENABLE] ? 2 : 3")), ("a", *P("pvw_light.cpp", "att = 1.0f / att;", "")), ("a", *P("pvw_light.cpp", "att /= t;", "att *= t;")),
                ("b", *P("pvw_api.cpp", "if (decl) fnvnx_wine_fixme(NXW_d3d9_device_ProcessVertices);", "")), ("b", *P("pvw_core.cpp", "if (!(si.use_map >> PVW_FFP_POSITION & 1)) return D3DERR_INVALIDCALL;", "if (false) return D3DERR_INVALIDCALL;")),
                ("a", *P("pvw_stream.cpp", "o.buf = static_cast", "if (si->use_map >> slot & 1) continue; o.buf = static_cast")), ("a", *P("pvw_core.cpp", "dst->lock((uint32_t)c.left", "dst->lock((uint32_t)c.left + 1")),
                ("b", *P("pvw_core.cpp", "if (d->cur.rs[D3DRS_CLIPPING]) fnvnx_wine_fixme(NXW_wined3d_device_process_vertices);", "")), ("b", *P("pvw_core.cpp", "if (off + n > e.buf->length) {", "if (off + n > e.buf->length + 1) {")),
                ("a", *P("pvw_math.cpp", "if (fabsf(r[3][0]) > fabsf(r[2][0])) swp(&r[3], &r[2]);", "")), ("b", *P("pvw_api.cpp", "if (!dst || !nx_chk_obj(dst) || !nx_chk_obj(decl)) return D3DERR_INVALIDCALL;", "if (!dst) return D3DERR_INVALIDCALL;")),
                ("a", *P("pvw_core.cpp", "if (!c.dry && !fx_normal) { fx_normal = true; fnvnx_wine_fixme(NXW_process_vertices_strided); }", "")), ("a", *P("pvw_light.cpp", "ls->fog_mode = (uint8_t)(s.rs[D3DRS_FOGVERTEXMODE] & 3);", "ls->fog_mode = (uint8_t)(s.rs[D3DRS_FOGVERTEXMODE] & 1);"))]
        muts += [("a", *P("pvw_light.cpp", "i < ls->n_par; i++, index++", "i < 0; i++, index++")), ("b", *P("pvw_light.cpp", "if (log_fixme) fnvnx_wine_fixme(NXW_init_transformed_lights);", "")),
                 ("b", *P("pvw_core.cpp", "si.e[k].buf->pool == (uint8_t)D3DPOOL_DEFAULT) return D3DERR_INVALIDCALL;", "si.e[k].buf->pool == (uint8_t)D3DPOOL_DEFAULT) continue;")),
                 ("b", *P("pvw_light.cpp", "if (log_fixme) fnvnx_wine_fixme(NXW_init_transformed_lights);", "fnvnx_wine_fixme(NXW_init_transformed_lights);"))]
        def one(i):
            part, f, old, new = muts[i]; mdev = os.path.join(tmp, f"m{i}_dev"); shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}")); mt = os.path.join(tmp, f"m{i}"); mo = os.path.join(mt, "objs"); os.makedirs(mo, exist_ok=True)
            e2 = compile_all(tmp, mdev, {f: os.path.join(mdev, f)}, outdir=mo, jobs=1)
            if e2: return i, None, e2
            e3 = link(list(prod), od, od, os.path.join(mt, "t"), swap=(f, mo))
            if e3: return i, None, e3
            x, ps = run(os.path.join(mt, "t"), data, "ab" if part in "ab" else part); shutil.rmtree(mt, ignore_errors=True); shutil.rmtree(mdev, ignore_errors=True); return i, ps, x.returncode
        with cf.ThreadPoolExecutor(2) as ex:
            for i, ps, rc in ex.map(one, range(len(muts))):
                part = muts[i][0]; ok = ps is not None and (ps.get(part, (0, 0))[1] > 0 or (rc != 0 and part not in ps))
                print(f"mutation {i} ({muts[i][1]}: {muts[i][2][:48]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else str((ps, rc))[:300]}")
                if not ok: fails.append(f"g: mutation {i} not caught ({ps} {rc})")
        print(f"PART g: {len(muts)} mutations")
    if os.environ.get("KEEP"): print("kept", tmp)
    else: shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
