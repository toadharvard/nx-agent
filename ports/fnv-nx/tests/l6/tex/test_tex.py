#!/usr/bin/env python3
"""test_tex.py: tests of render/d3d9/dev/res_{fmt,tex,surf,create}.cpp (D4: textures, surfaces, volumes).  python3 -I tests/l6/tex/test_tex.py [--no-mutations]
 a creation legality over the whole (type x format x usage x pool x levels x device x profile) domain + surfaces (RT / DS / plain x format x multisample x pool), against the python oracle below
 b level chains / descs / identity / refcounts / LOD   c pitches, lock flags, rect validation, dirty regions -> recorded backend updates   d AUTOGENMIPMAP / GenerateMipSubLevels / filter type
 e volumes and cubes   h format table completeness (env table -> layout + NxFormat)   f gate G-METHOD   g mutation checks.
Oracle = independent python: the env tsv (render/d3d9/env/env_<profile>.tsv, CheckDeviceFormat / CheckDeviceMultiSampleType / GetDeviceCaps rows) + the stated Wine 9.0 rules (res_create.cpp header) + its own
format layout table from wined3d utils.c (bpp, block info, MAPPABLE, BROKEN_PITCH, YV12 / NV12 height scale) + the NxFormat set parsed from render/native/front/d3d9_state.c (case labels of nx_d3d9_format)."""
import concurrent.futures as cf, importlib.util, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); ENV = os.path.join(ROOT, "render", "d3d9", "env"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); NXF = os.path.join(ROOT, "render", "native"); DEVT = os.path.join(ROOT, "tests", "l6", "dev"); BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]; NATIVE = os.path.join(ROOT, "tests", "l6", "native")
RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp"]; TEX = ["res_fmt.cpp", "res_tex.cpp", "res_surf.cpp", "res_create.cpp"]
TESTS = ["t_env.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp", "t_e.cpp", "t_h.cpp"]
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


sys.path.insert(0, HERE)
from texoracle import *  # noqa: E402,F401  (fcc, PLAIN, MAPPABLE, layout, pitch, mappitch, size, dim, chain, Env, result codes)


FRONT = os.path.join(ROOT, "render", "d3d9", "front"); sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "render", "d3d9", "env")); import envq  # noqa: E402
NX = set(); NXI = {}   # NX: D3DFORMAT values with a native format (calling the compiled front); NXI: value -> (format, flags)
DRIVER = r"""#include <stdio.h>
#include "d3d9_state.h"
int main(void) { uint32_t v; while (scanf("%u", &v) == 1) { NxFormatInfo i = nx_d3d9_format(v); printf("%u %u %u\n", v, i.format, i.flags); } return 0; }
"""


def nx_table(tmp, fmts):
    """the format table by calling the compiled function: a tiny C driver over render/d3d9/front/front_*.c (-I front -I backend)"""
    d = os.path.join(tmp, "drv"); os.makedirs(d, exist_ok=True); c = os.path.join(d, "drv.c"); open(c, "w").write(DRIVER)
    srcs = sorted(os.path.join(FRONT, f) for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    r = sh(["gcc", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, c, *srcs, "-o", os.path.join(d, "drv")])
    if r.returncode: raise SystemExit("format driver build failed:\n" + r.stderr[-800:])
    o = subprocess.run([os.path.join(d, "drv")], input="\n".join(str(f) for f in fmts), capture_output=True, text=True).stdout
    t = {}
    for l in o.split("\n"):
        if l: v, f, fl = (int(x) for x in l.split()); t[v] = (f, fl)
    return t
FMTS = sorted(PLAIN) + [fcc(x) for x in ("UYVY", "YUY2", "YV12", "NV12", "DXT1", "DXT2", "DXT3", "DXT4", "DXT5", "MET1", "GRGB", "RGBG", "ATI1", "ATI2", "INST", "NVDB", "NVHU", "NVHS", "INTZ", "RESZ", "NULL", " R16", "AL16", "ATOC", "ZZZZ")] + [0, 19, 999, 0x7FFFFFFF]


def tex_oracle(E, kind, w, h, d, lv, usage, fmt, pool, ex):
    """-> (hr, nlevels, gpu, lock, zero_updates, preload_updates)"""
    Z = (INV, 0, 0, 0, 0, 0)
    if pool > 3 or (pool == 1 and ex) or usage & 8: return Z
    if kind == 2 and usage & 3: return Z
    ag = False
    if usage & 0x400:
        if kind == 2 or pool == 2 or lv > 1: return Z
        ag = E.ok(0x400, 3, fmt); lv = 1
    if not (w and h and d) or fmt == 0: return Z
    if usage & 0x200 and pool in (1, 3): return Z
    if pool in (0, 1) and (w > E.caps["MaxTextureWidth"] or h > E.caps["MaxTextureHeight"]): return (NA, 0, 0, 0, 0, 0)
    L = layout(fmt)
    if L is None or lv > 32: return Z
    bind = usage & 3; rt = {0: 3, 1: 5, 2: 4}[kind]
    if bind and (pool != 0 or usage & 0x200): return Z
    if pool != 3 and (not E.ok(0, rt, fmt) or (bind & 1 and not E.ok(1, rt, fmt)) or (bind & 2 and not E.ok(2, rt, fmt))): return Z
    if L[4] and not L[5] and (w % L[1] or h % L[2]): return Z
    n = (1 if ag else lv) or chain(w, h, d); nlev = chain(w, h, d) if ag else n; faces = 6 if kind == 1 else 1
    gpu = int(pool in (0, 1) and fmt in NX); lock = int(pool != 0 or bool(usage & 0x200) or fmt in MAPPABLE)
    zero = nlev * faces if pool == 0 else (nlev - n) * faces if pool == 1 else 0   # DEFAULT: every level; MANAGED: only the GPU-only levels of the AUTOGENMIPMAP chain (the exposed ones are dirty at creation)
    return (0, n, gpu, lock, zero if gpu else 0, n * faces if gpu and pool == 1 else 0)


def surf_oracle(E, what, w, h, fmt, ms, q, flag, pool, ex, usage):
    Z = (INV, 0, 0, 0)
    if what == 2 and (pool == 1 or pool > 3): return Z
    if usage != 0xFFFFFFFF and usage & 3: return Z
    L = layout(fmt)
    if not (w and h) or fmt == 0 or L is None: return Z
    bind = (1, 2, 0)[what]
    if what != 2:
        if not E.ms_ok(fmt, ms, q) or not E.ok(bind, 1, fmt): return Z
        if what == 0 and flag and ms: return Z
    if L[4] and not L[5] and (w % L[1] or h % L[2]): return Z
    pl = pool if what == 2 else 0
    gpu = int(pl == 0 and fmt in NX); lock = int(what == 2 or (what == 0 and flag) or fmt in MAPPABLE)
    return (0, gpu, lock, int(gpu and ms == 0))   # a multisampled image takes no zero ImageUpdate (res_tex.cpp)


def write_data(d):
    n = 0
    with open(os.path.join(d, "create.txt"), "w") as f:
        for prof, name in ((0, "handheld"), (1, "docked")):
            E = Env(name); dims = [(4, 4, 4), (6, 6, 3), (1, 1, 1)] if prof == 0 else [(4, 4, 4)]
            for kind in (0, 1, 2):
                for fmt in FMTS:
                    for usage in (0, 1, 2, 3, 0x200, 0x400, 8, 0x201, 0x402, 0x600):
                        for pool in (0, 1, 2, 3, 4):
                            for ex in (0, 1):
                                for (w, h, dd) in dims:
                                    for lv in ((0, 1, 2, 33) if w > 1 else (0,)):
                                        if kind != 2: dd = 1
                                        if kind == 1: h = w
                                        r = tex_oracle(E, kind, w, h, dd, lv, usage, fmt, pool, ex)
                                        f.write("T %d %d %d %d %d %d %d %d %d %d %x %d %d %d %d %d\n" % ((kind, w, h, dd, lv, usage, fmt, pool, ex, prof) + r)); n += 1
            for what in (0, 1, 2):
                for fmt in FMTS:
                    for (ms, q) in (((0, 0), (1, 0), (1, 1), (2, 0), (4, 0), (4, 1), (4, 0xFFFFFFFF), (8, 0), (17, 0)) if what != 2 else ((0, 0),)):
                        for flag in ((0, 1) if what != 2 else (0,)):
                            for pool in ((0,) if what != 2 else (0, 1, 2, 3, 4)):
                                for ex, usage in ((0, 0xFFFFFFFF), (1, 0xFFFFFFFF), (1, 0), (1, 1), (1, 0x200)):
                                    if what == 2 and usage != 0xFFFFFFFF: continue
                                    for (w, h) in (((4, 4), (6, 6), (0, 4)) if prof == 0 else ((4, 4),)):
                                        r = surf_oracle(E, what, w, h, fmt, ms, q, flag, pool, ex, usage)
                                        f.write("S %d %d %d %d %d %d %d %d %d %u %d %x %d %d %d\n" % (what, w, h, fmt, ms, q, flag, pool, ex, usage, prof, r[0], r[1], r[2], r[3])); n += 1
            if prof == 0: E0 = E
    E = E0
    with open(os.path.join(d, "chain.txt"), "w") as f:
        for kind in (0, 1, 2):
            for fmt in (21, fcc("DXT1"), 50, 116, fcc("YUY2")):
                for (w, h, dd) in ((16, 16, 4), (15, 7, 3), (1, 1, 1), (32, 4, 8), (5, 5, 2), (8, 8, 8)):
                    if fmt == fcc("DXT1") and (w % 4 or h % 4): continue
                    if kind == 1: h = w
                    if kind != 2: dd = 1
                    for lv in (0, 1, 3, 5):
                        c = chain(w, h, dd) if lv == 0 else lv
                        f.write("C %d %d %d %d %d %d %d %s\n" % (kind, w, h, dd, lv, fmt, c, " ".join("%d,%d,%d" % (dim(w, i), dim(h, i), dim(dd, i)) for i in range(c))))
    with open(os.path.join(d, "pitch.txt"), "w") as f:
        for fmt in FMTS:
            L = layout(fmt)
            if L is None: continue
            for (w, h) in ((8, 8), (5, 3), (16, 4), (1, 1), (7, 9)):
                if L[4] and not L[5] and (w % L[1] or h % L[2]): continue
                for lv in range(min(3, chain(w, h))):
                    lw, lh = dim(w, lv), dim(h, lv); r, s = mappitch(L, lw, lh)
                    f.write("P %d %d %d %d %d %d %d\n" % (fmt, w, h, lv, r, s, size(L, lw, lh, 1)))
    with open(os.path.join(d, "fmts.txt"), "w") as f:
        for fmt in FMTS:
            if layout(fmt) is None: f.write("Z %d\n" % fmt)
        for rt in (1, 3, 4, 5):
            for us in (0, 1, 2):
                for fmt in FMTS:
                    if layout(fmt) is not None and E.ok(us, rt, fmt): f.write("%s %d %d %d\n" % ("L" if rt == 1 and us == 0 else "F", rt, us, fmt))
    with open(os.path.join(d, "ag.txt"), "w") as f:
        for fmt in FMTS:
            if layout(fmt) is not None and fmt in NX and E.ok(0, 3, fmt) and tex_oracle(E, 0, 16, 16, 1, 0, 0, fmt, 1, 0)[0] == 0: f.write("G %d %d\n" % (fmt, int(E.ok(0x400, 3, fmt))))
    return n


def build(dev, tmp, jobs=8, with_res=True):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")],
                [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return None, None, r.stderr[-800:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", HERE, "-I", INC, "-I", tmp, "-I", dev, "-I", BACK, "-I", NXB, "-I", FRONT]
    jobs_ = [(os.path.join(NATIVE if s == "app_unk_native.cpp" else dev, s), s) for s in LIB + RES + TEX] + [(os.path.join(tmp, s), s) for s in GENS + ["env_table.gen.cpp"]] + [(os.path.join(HERE, s), s) for s in TESTS]
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in jobs_]
    fronts = sorted(f for f in os.listdir(FRONT) if f.startswith("front_") and f.endswith(".c"))
    for f in fronts: cmds.append(["gcc", "-m32", "-std=c11", "-w", "-I", FRONT, "-I", BACK, "-I", NXB, "-c", os.path.join(FRONT, f), "-o", os.path.join(tmp, "nx_" + f + ".o")])
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    prod = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + RES + TEX]
    link = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + TEX + TESTS + ["env_table.gen.cpp"]] + [os.path.join(tmp, "nx_" + f + ".o") for f in fronts]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_tex")] + link + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_tex"), prod, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


def run(exe, data):
    r = sh([exe, data]); ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    for m in re.finditer(r"^FAIL (\w):", r.stdout, re.M): ps.setdefault(m.group(1), (1, 0)); ps[m.group(1)] = (ps[m.group(1)][0], max(ps[m.group(1)][1], 1)) if m.group(1) not in ps or ps[m.group(1)][1] == 0 else ps[m.group(1)]   # a crash after FAIL lines still names the part
    return r, ps


def main():
    tmp = tempfile.mkdtemp(prefix="d9tex_"); data = os.path.join(tmp, "data"); os.makedirs(data)
    NXI.update(nx_table(tmp, sorted(set(FMTS) | set(envq.FMT)))); NX.update(v for v, (f, fl) in NXI.items() if f); n = write_data(data); print(f"oracle: {n} creation cases; NxFormat set {len(NX)} formats")
    exe, prod, err = build(DEV, tmp)
    if not exe: print("BUILD FAILED\n" + err); return 1
    r, parts = run(exe, data); ls = r.stdout.split('\n'); print('\n'.join(l for l in ls if l.startswith('FAIL') )[:3000]); print('\n'.join([l for l in ls if l.startswith('  case')][:6]))
    if os.environ.get('KEEP'): print('kept', tmp); return 1
    for p in "abcdeh":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    print("parts: " + ", ".join(f"{p} {parts[p][0]} checks {parts[p][1]} failed" for p in sorted(parts)))
    sp = importlib.util.spec_from_file_location("test_dev", os.path.join(DEVT, "test_dev.py")); td = importlib.util.module_from_spec(sp); sp.loader.exec_module(td)
    want, miss, unres = td.gate(tmp, prod); before = td.gate(tmp, [o for o in prod if os.path.basename(o)[:-2] not in TEX])
    print(f"G-METHOD before (dev + res): missing {len(before[1])};  after (+ tex): missing {len(miss)} of {len(want)}")
    cls = lambda ms: {}
    for ifc in ("Texture9", "CubeTexture9", "VolumeTexture9", "BaseTexture9", "Surface9", "Volume9"):
        print(f"  Nx{ifc}: before {sum(1 for m in before[1] if m.split('::')[0] == 'Nx' + ifc)} -> after {sum(1 for m in miss if m.split('::')[0] == 'Nx' + ifc)}")
    print("  still missing: " + ", ".join(m for m in miss if m.split("::")[0] in ("NxTexture9", "NxCubeTexture9", "NxVolumeTexture9", "NxBaseTexture9", "NxSurface9", "NxVolume9") or m.split("::")[1].startswith(("CreateTexture", "CreateVolumeTexture", "CreateCubeTexture", "CreateRenderTarget", "CreateDepthStencil", "CreateOffscreen"))))
    if not len(miss) < len(before[1]): fails.append("f: gate did not improve")
    if "--no-mutations" not in sys.argv:
        muts = [("a", "res_create.cpp", "|| (usage & D3DUSAGE_WRITEONLY)) return D3DERR_INVALIDCALL;", ") return D3DERR_INVALIDCALL;"), ("a", "res_create.cpp", "((bind & 1) && !cdf(p, 1, rt, fmt))", "false"),
                ("a", "res_create.cpp", "if (pool != D3DPOOL_SCRATCH && (!cdf(", "if (pool != D3DPOOL_MANAGED && (!cdf("), ("a", "res_create.cpp", "(q && q != 0xFFFFFFFFu)", "false"),
                ("a", "res_create.cpp", "if (what == 0 && flag && ms != 0) return D3DERR_INVALIDCALL;", ""), ("a", "res_create.cpp", "(usage & D3DUSAGE_DYNAMIC) && (pool == D3DPOOL_MANAGED || pool == D3DPOOL_SCRATCH)", "false"),
                ("c", "res_surf.cpp", "if (s->locked) return D3DERR_INVALIDCALL;", ""), ("c", "res_surf.cpp", "if (!im->rec_dirty || level > 0)", "if (!im->rec_dirty)"),
                ("c", "res_surf.cpp", "(b->Left & wm) ||", ""), ("c", "res_surf.cpp", "return container && container->ci->rtype == D3DRTYPE_TEXTURE ? D3D_OK : D3DERR_INVALIDCALL;", "return D3D_OK;"),
                ("c", "res_fmt.cpp", "{FC('D', 'X', 'T', '1'), 1, 4, 4, 8, BL", "{FC('D', 'X', 'T', '1'), 1, 4, 4, 16, BL"), ("c", "res_surf.cpp", "else if (!(flags & D3DLOCK_NO_DIRTY_UPDATE))", "else"),
                ("b", "res_tex.cpp", "if (!managed()) return 0; DWORD o", "DWORD o"), ("b", "res_tex.cpp", "k->addref(); return D3D_OK;", "return D3D_OK;"),
                ("d", "res_tex.cpp", "1); mip_dirty = false; }", "1); }"), ("d", "res_tex.cpp", "filter == D3DTEXF_POINT ? 0 : 1", "1"),
                ("e", "res_tex.cpp", "if (u > o.k)", "if (u > o.k)") if False else ("e", "res_tex.cpp", "if (n.k > o.k) o.k = n.k;", ""), ("c", "res_surf.cpp", "if (s->gpu_newer) { if (!discard", "if (s->gpu_newer) { if (false")]
        def one(i):
            part, f, old, new = muts[i]; root = os.path.join(tmp, f"m{i}_root", "d3d9"); mdev = os.path.join(root, "dev"); os.makedirs(root)
            shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); os.symlink(os.path.join(ROOT, "render", "d3d9", "ffp"), os.path.join(root, "ffp"))
            txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}"))
            x, pr, e = build(mdev, os.path.join(tmp, f"m{i}"), jobs=2)
            if not x: return i, None, e
            rr, ps = run(x, data); return i, ps, rr.returncode
        with cf.ThreadPoolExecutor(2) as ex:
            for i, ps, rc in ex.map(one, range(len(muts))):
                part = muts[i][0]; ok = ps is not None and ps.get(part, (0, 0))[1] > 0
                print(f"mutation {i} ({muts[i][1]}: {muts[i][2][:48]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else str((ps, rc))[:300]}")
                if not ok: fails.append(f"g: mutation {i} not caught ({ps} {rc})")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
