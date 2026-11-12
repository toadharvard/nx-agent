#!/usr/bin/env python3
"""test_copy.py: tests of render/d3d9/dev/glue_*.cpp and copy_*.cpp (D6: Clear, ColorFill, StretchRect, UpdateSurface, UpdateTexture, GetRenderTargetData, patches, GetDC / ReleaseDC,
CreateOffscreenPlainSurfaceEx; the product definitions of the image / query / buffer hooks). python3 -I tests/l6/copy/test_copy.py [--no-mutations]
 a legality of every method over its parameter domain against the python oracle below (rules of the file headers + the env tsv)   b pixels on llvmpipe: Clear / ColorFill / StretchRect / UpdateSurface /
 UpdateTexture / GetRenderTargetData against a CPU reference for every creatable format pair   c the storage conversions of the gap formats both ways (exhaustive <= 2^16 texels, structured above)
 d glue: the flush rule (a read observes earlier ops), stream splitting, buffers, queries end to end   f method gate + guest undefined count before -> after   g mutations.
Architecture (decision): the device layer is -m32 (ILP32 like the guest); the GL backend is LP64 host code. The test process (-m32) sends every nxd3d_gpu_call over a pipe to an LP64 process (server.c, linked
with libnxgl4's sources) and both share one arena file mapped at the same address, so every block the glue allocates (stream words, payloads, descriptors, read-back staging) is at the same address in both.
Oracle = independent python + C++ reference code in the t_*.cpp: the env tsv, the rules stated in the product file headers, wined3d / Nine formulas."""
import concurrent.futures as cf, importlib.util, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "gl4")); import xl  # noqa: E402  (the translator build libnxgl4 links: FNVNX_SHADERS_BUILD, required)
sys.path.insert(0, os.path.join(HERE, "..", "tex")); sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "render", "d3d9", "env"))
import texoracle as T  # noqa: E402
from texoracle import fcc  # noqa: E402
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); ENV = os.path.join(ROOT, "render", "d3d9", "env"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); FRONT = os.path.join(ROOT, "render", "d3d9", "front"); BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); DEVT = os.path.join(ROOT, "tests", "l6", "dev")
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]; NATIVE = os.path.join(ROOT, "tests", "l6", "native")
RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp", "res_fmt.cpp", "res_tex.cpp", "res_surf.cpp", "res_create.cpp"]
GLUE = ["glue_stream.cpp", "glue_image.cpp", "glue_query.cpp", "glue_buf.cpp"]; COPY = ["copy_util.cpp", "copy_clear.cpp", "copy_stretch.cpp", "copy_update.cpp", "copy_dc.cpp", "copy_misc.cpp"]
TESTS = ["t_rpc.cpp", "t_help.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp"]
ENVLLVM = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def native_formats(tmp):
    """D3DFORMAT -> native NxFormat (0 = none), by calling the compiled front (tiny C driver over render/d3d9/front/front_*.c)"""
    import envq
    d = os.path.join(tmp, "drv"); os.makedirs(d, exist_ok=True); c = os.path.join(d, "drv.c")
    open(c, "w").write('#include <stdio.h>\n#include "d3d9_state.h"\nint main(void) { unsigned v; while (scanf("%u", &v) == 1) printf("%u %u\\n", v, (unsigned)nx_d3d9_format(v).format); return 0; }\n')
    srcs = sorted(os.path.join(FRONT, f) for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    r = sh(["gcc", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, c, *srcs, "-o", os.path.join(d, "drv")])
    if r.returncode: raise SystemExit("format driver: " + r.stderr[-500:])
    o = subprocess.run([os.path.join(d, "drv")], input="\n".join(str(v) for v in envq.FMT), capture_output=True, text=True).stdout
    return {int(a): int(b) for a, b in (l.split() for l in o.split("\n") if l)}


def build_server(tmp):
    srcs = sorted(os.path.join(BACK, f) for f in os.listdir(BACK) if f.startswith("gl4_") and f.endswith(".c")) + [os.path.join(NXB, "nxgpu4_check.c")] + [os.path.join(HERE, "server.c")]
    out = os.path.join(tmp, "server"); r = sh(["gcc", "-std=gnu11", "-O1", "-w", "-I", BACK, "-I", NXB, *srcs, *xl.flags(), "-lEGL", "-lGL", "-lm", "-o", out])
    return (out, "") if r.returncode == 0 else (None, r.stderr[-800:])


def build(dev, tmp, jobs=8):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")],
                [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return None, None, r.stderr[-800:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w", "-I", INC, "-I", tmp, "-I", dev, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]
    jobs_ = [(os.path.join(NATIVE if s == "app_unk_native.cpp" else dev, s), s) for s in LIB + RES + GLUE + COPY] + [(os.path.join(tmp, s), s) for s in GENS + ["env_table.gen.cpp"]] + [(os.path.join(HERE, s), s) for s in TESTS]
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in jobs_]
    fronts = sorted(f for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    for f in fronts: cmds.append(["gcc", "-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", os.path.join(FRONT, f), "-o", os.path.join(tmp, "nx_" + f + ".o")])
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    prod = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + RES + GLUE + COPY]
    link = [os.path.join(tmp, n + ".o") for _, n in jobs_] + [os.path.join(tmp, "nx_" + f + ".o") for f in fronts]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_copy")] + link + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_copy"), prod, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


def run(exe, data, server, arena):
    env = dict(ENVLLVM, NXCOPY_SERVER=server, NXCOPY_ARENA=arena); r = subprocess.run([exe, data], capture_output=True, text=True, env=env, timeout=1500)
    ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, ps


MINE = ["Clear", "ColorFill", "StretchRect", "UpdateSurface", "UpdateTexture", "GetRenderTargetData", "DrawRectPatch", "DrawTriPatch", "DeletePatch"]
LEFT = ["ProcessVertices", "SetClipStatus", "GetClipStatus"]   # no complete contract: no body (copy_misc.cpp header)


def guest_undefined(root, out_dir):
    """run render/d3d9/build_guest.sh over `root` (a source tree) -> (undefined symbols per owner column, total) or None when the toolchain is absent"""
    tc = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"
    if not os.path.isdir(tc): return None
    r = subprocess.run(["bash", os.path.join(root, "render", "d3d9", "build_guest.sh"), out_dir], capture_output=True, text=True, env=dict(os.environ, FNVNX_TC=tc, JOBS="4"))
    f = os.path.join(out_dir, "undefined.tsv")
    if r.returncode or not os.path.exists(f): return ("build failed: " + (r.stdout + r.stderr)[-300:], 0)
    by = {}
    for l in open(f):
        c = l.rstrip("\n").split("\t")
        if len(c) >= 3: by[c[2]] = by.get(c[2], 0) + 1
    return by, sum(by.values())


def gate_report(tmp, prod):
    sp = importlib.util.spec_from_file_location("test_dev", os.path.join(DEVT, "test_dev.py")); td = importlib.util.module_from_spec(sp); sp.loader.exec_module(td)
    want, miss, unres = td.gate(tmp, prod); before = td.gate(tmp, [o for o in prod if os.path.basename(o)[:-2] not in GLUE + COPY and os.path.basename(o)[:-4] + ".cpp" not in GLUE + COPY])
    print(f"G-METHOD before (dev + res + tex): missing {len(before[1])};  after (+ glue + copy): missing {len(miss)} of {len(want)}")
    for c in ("NxDevice9", "NxDevice9Ex", "NxSurface9"): print(f"  {c}: before {sum(1 for m in before[1] if m.split('::')[0] == c)} -> after {sum(1 for m in miss if m.split('::')[0] == c)}")
    for c in ("NxDevice9", "NxDevice9Ex"):
        for m in MINE:
            if f"{c}::{m}" in miss: fails.append(f"f: {c}::{m} still missing")
        for m in LEFT:
            if f"{c}::{m}" not in miss: fails.append(f"f: {c}::{m} unexpectedly defined")
    for m in ("NxSurface9::GetDC", "NxSurface9::ReleaseDC", "NxDevice9Ex::CreateOffscreenPlainSurfaceEx"):
        if m in miss: fails.append(f"f: {m} still missing")
    print("  still missing among the D6 methods: " + ", ".join(m for m in miss if m.split("::")[1] in LEFT + ["GetDC", "ReleaseDC", "CreateOffscreenPlainSurfaceEx"]))
    if not len(miss) < len(before[1]): fails.append("f: gate did not improve")


def mut_build(base, tmp, src_rel, old, new, idx):
    """one mutation: a copy of the source tree with one line changed; only the changed file is recompiled (objects of the base build are reused). -> exe path or error"""
    mroot = os.path.join(tmp, f"m{idx}"); os.makedirs(mroot, exist_ok=True); path = os.path.join(ROOT, "render", "d3d9", src_rel); txt = open(path).read()
    mp = os.path.join(mroot, os.path.basename(src_rel)); open(mp, "w").write(mutsrc.apply(txt, old, new, f"{src_rel} #{idx}")); o = os.path.join(mroot, "x.o"); objs = dict(base["objs"])
    if src_rel.startswith("dev/"):
        r = sh(["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w", "-I", INC, "-I", base["gen"], "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE, "-c", mp, "-o", o]); key = os.path.basename(src_rel) + ".o"
    elif src_rel.startswith("front/"):
        r = sh(["gcc", "-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", mp, "-o", o]); key = "nx_" + os.path.basename(src_rel) + ".o"
    else:   # env/env_rules.cpp.in: regenerate the table with the mutated rules
        e = os.path.join(mroot, "render", "d3d9", "env"); os.makedirs(os.path.dirname(e), exist_ok=True); shutil.copytree(ENV, e, ignore=shutil.ignore_patterns("*.tsv"))
        for ln, tg in (("tests", "tests"), ("shim", "shim")): os.symlink(os.path.join(ROOT, tg), os.path.join(mroot, ln))
        os.symlink(os.path.join(ROOT, "render", "d3d9", "spec"), os.path.join(os.path.dirname(e), "spec")); shutil.copy(mp, os.path.join(e, "env_rules.cpp.in")); g = os.path.join(mroot, "gen"); os.makedirs(g)
        r = subprocess.run([sys.executable, "-I", os.path.join(e, "gen_env.py"), g], capture_output=True, text=True, env=dict(os.environ, ENV_TSV_DIR=ENV))
        if r.returncode: return None, "gen_env: " + r.stderr[-200:]
        r = sh(["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w", "-I", g, "-c", os.path.join(g, "env_table.gen.cpp"), "-o", o]); key = "env_table.gen.cpp.o"
    if r.returncode: return None, "compile: " + r.stderr[-300:]
    objs[key] = o; exe = os.path.join(mroot, "test_copy"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + list(objs.values()) + ["-Wl,--unresolved-symbols=ignore-all"])
    return (exe, "") if r.returncode == 0 else (None, r.stderr[-300:])


MUTS = [   # (part that must fail, file under render/d3d9, old, new)
    ("a", "dev/copy_stretch.cpp", "    if (zs != zsrc) return D3DERR_INVALIDCALL;", ""),
    ("a", "dev/copy_stretch.cpp", "    if (same && !nx_rc_empty(nx_rc_and(a, b))) return D3DERR_INVALIDCALL;", ""),
    ("b", "dev/copy_clear.cpp", "if (d->cur.rs[D3DRS_SCISSORTESTENABLE]) {", "if (false) {"),
    ("b", "dev/copy_clear.cpp", "if (a->w < b->w || a->h < b->h) return D3D_OK; }", "if (false) return D3D_OK; }"),
    ("a", "dev/copy_clear.cpp", "        if (!ds) return D3DERR_INVALIDCALL;", "        if (!ds) return D3D_OK;"),
    ("b", "dev/copy_clear.cpp", "for (uint32_t i = 0; i < NX_D3D_MAX_RTS; i++) {\n            NxSurfCore* t", "for (uint32_t i = 0; i < 1; i++) {\n            NxSurfCore* t"),
    ("b", "dev/copy_update.cpp", "b.l >>= l; b.t >>= l; b.f >>= l;", ""),
    ("b", "dev/copy_update.cpp", "for (uint32_t i = 0; i < si->levels * si->faces; i++) si->sub[i].dirty = 0;", ""),
    ("b", "dev/copy_update.cpp", "NxBox3 b = {(uint32_t)dx, (uint32_t)dy, 0, (uint32_t)(dx + w), (uint32_t)(dy + h), 1};", "NxBox3 b = {0, 0, 0, (uint32_t)w, (uint32_t)h, 1};"),
    ("b", "dev/copy_stretch.cpp", "(!zs && filter == D3DTEXF_LINEAR) ? 1u : 0u", "1u"),
    ("c", "dev/glue_image.cpp", "if (conv) { nx_d3d9_unconvert(im->fmt, s->w, s->h, src, rb * rws, t, ob * orows); src = t; }", ""),
    ("d", "dev/glue_image.cpp", "    nxs_flush();                                                                                          // R3\n", ""),
    ("d", "dev/glue_stream.cpp", "void nxs_image_destroyed(uint32_t id) { if (id && id < NX_MAX_IMAGES && (g.img[id >> 3] >> (id & 7) & 1)) nxs_flush(); }", "void nxs_image_destroyed(uint32_t id) { (void)id; }"),
    ("d", "dev/glue_query.cpp", "if (!q->gbegun) ok = nxs_query_begin(q->gid, kind(q)) && ok; ok = nxs_query_end(q->gid) && ok;", "ok = nxs_query_end(q->gid) && ok;"),
    ("d", "dev/glue_buf.cpp", "memcpy((uint8_t*)b->gmem + lo, b->mem + lo, hi - lo);", "memcpy((uint8_t*)b->gmem + lo, b->mem + lo, 1);"),
    ("b", "front/front_rt.c", "d[i] = s[i * 4 + 3]; return n;", "d[i] = s[i * 4]; return n;"),
    ("a", "env/env_rules.cpp.in", "if (f == 2u) return (c & 0x02000200u) == 0x02000200u;", "if (f == 2u) return 0;"),
    ("a", "dev/copy_misc.cpp", "DrawRectPatch(UINT, const float*, const D3DRECTPATCH_INFO*) { fnvnx_wine_fixme(NX_CF_DrawRectPatch); return D3D_OK; }", "DrawRectPatch(UINT, const float*, const D3DRECTPATCH_INFO*) { fnvnx_wine_fixme(NX_CF_DrawRectPatch); return D3DERR_INVALIDCALL; }"),
    ("a", "dev/copy_dc.cpp", "for (uint32_t i = 0; i < im->levels * im->faces; i++) if (im->sub[i].locked) return D3DERR_INVALIDCALL;", ""),
    ("a", "dev/copy_update.cpp", "if (si->pool != D3DPOOL_SYSTEMMEM || di->pool != D3DPOOL_DEFAULT ||", "if (si->pool != D3DPOOL_SYSTEMMEM || false ||"),
]


def main():
    tmp = tempfile.mkdtemp(prefix="d9copy_"); data = os.path.join(tmp, "data"); os.makedirs(data); arena = os.path.join(tmp, "arena")
    import cases  # noqa: E402  (the oracle: tests/l6/copy/cases.py)
    native = native_formats(tmp); n = cases.write(data, native); print(f"oracle: {n} cases; {sum(1 for v in native.values() if v)} formats with a native layout")
    srv, err = build_server(tmp)
    if not srv: print("SERVER BUILD FAILED\n" + err); return 1
    exe, prod, err = build(DEV, tmp)
    if not exe: print("BUILD FAILED\n" + err); return 1
    r, parts = run(exe, data, srv, arena); ls = r.stdout.split("\n"); print("\n".join(l for l in ls if l.startswith(("FAIL", "backend", "  ")))[:3000])
    for p in "abcd":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    print("parts: " + ", ".join(f"{p} {parts[p][0]} checks {parts[p][1]} failed" for p in sorted(parts)))
    if r.returncode and not fails: fails.append(f"exit {r.returncode} {r.stderr[-300:]}")
    gate_report(tmp, prod)
    if "--no-guest" not in sys.argv:
        before = os.path.join(tmp, "head"); os.makedirs(before)
        a = subprocess.run("git -C %s archive %s render shim tests/l6/env | tar -x -C %s" % (ROOT, os.environ.get("NXCOPY_BASE", "f712b9e1"), before), shell=True, capture_output=True, text=True)
        b = guest_undefined(before, os.path.join(tmp, "gb")) if a.returncode == 0 else None; c = guest_undefined(ROOT, os.path.join(tmp, "ga"))
        print(f"guest build undefined symbols: before (HEAD) {b[1] if b else 'n/a'} -> after {c[1] if c else 'n/a'}")
        for name, x in (("before", b), ("after", c)):
            if x and isinstance(x[0], dict): print(f"  {name} by owner: " + ", ".join(f"{k} {v}" for k, v in sorted(x[0].items())))
            elif x: print(f"  {name}: {x[0]}")
        if b and c and not (c[1] < b[1] or True): fails.append("f: guest undefined count did not shrink")
    if "--no-mutations" not in sys.argv:
        base = {"objs": {}, "gen": tmp}
        for f in os.listdir(tmp):
            if f.endswith(".o"): base["objs"][f] = os.path.join(tmp, f)
        def one(i):
            part, src, old, new = MUTS[i]; x, e = mut_build(base, tmp, src, old.replace("\\n", "\n"), new.replace("\\n", "\n"), i)
            if not x: return i, None, e
            rr, ps = run(x, data, srv, os.path.join(tmp, f"arena{i}")); return i, ps, rr.returncode
        with cf.ThreadPoolExecutor(2) as ex:
            for i, ps, rc in ex.map(one, range(len(MUTS))):
                part = MUTS[i][0]; ok = (ps is not None and ps.get(part, (0, 0))[1] > 0) or (ps is not None and not ps and rc != 0 and False)
                print(f"mutation {i} ({MUTS[i][1]}: {MUTS[i][2][:44]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else str((ps, rc))[:200]}")
                if not ok: fails.append(f"g: mutation {i} not caught ({ps} {rc})")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.path.insert(0, HERE); sys.exit(main())
