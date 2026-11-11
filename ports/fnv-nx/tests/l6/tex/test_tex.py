#!/usr/bin/env python3
"""test_tex.py: tests of render/d3d9/dev/res_{fmt,tex,surf,create}.cpp (D4: textures, surfaces, volumes).  python3 -I tests/l6/tex/test_tex.py [--no-mutations]
 a creation legality over the whole (type x format x usage x pool x levels x device x profile) domain + surfaces (RT / DS / plain x format x multisample x pool), against the python oracle below
 b level chains / descs / identity / refcounts / LOD   c pitches, lock flags, rect validation, dirty regions -> recorded backend updates   d AUTOGENMIPMAP / GenerateMipSubLevels / filter type
 e volumes and cubes   h format table completeness (env table -> layout + NxFormat)   f gate G-METHOD   g mutation checks.
Oracle = independent python: the env tsv (render/d3d9/env/env_<profile>.tsv, CheckDeviceFormat / CheckDeviceMultiSampleType / GetDeviceCaps rows) + the stated Wine 9.0 rules (res_create.cpp header) + its own
format layout table from wined3d utils.c (bpp, block info, MAPPABLE, BROKEN_PITCH, YV12 / NV12 height scale) + the NxFormat set parsed from render/native/front/d3d9_state.c (case labels of nx_d3d9_format)."""
import concurrent.futures as cf, importlib.util, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); ENV = os.path.join(ROOT, "render", "d3d9", "env"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); NXF = os.path.join(ROOT, "render", "native"); DEVT = os.path.join(ROOT, "tests", "l6", "dev"); BACK = os.path.join(ROOT, "render", "d3d9", "backend")
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]
RES = ["res_buf.cpp", "res_fvf.cpp", "res_decl.cpp", "res_shader.cpp", "res_smcheck.cpp", "res_query.cpp"]; TEX = ["res_fmt.cpp", "res_tex.cpp", "res_surf.cpp", "res_create.cpp"]
TESTS = ["t_env.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp", "t_e.cpp", "t_h.cpp"]
fails = []
INV, NA, OOM, NOTIMPL, OOVM = 0x8876086C, 0x8876086A, 0x8007000E, 0x80004001, 0x8876017C


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fcc(s): return sum(ord(c) << (8 * i) for i, c in enumerate(s))


# ---------------------------------------------------------------- oracle
PLAIN = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2, 27: 1, 28: 1, 29: 2, 30: 2, 31: 4, 32: 4, 33: 4, 34: 4, 35: 4, 36: 8, 40: 2, 41: 1, 50: 1, 51: 2, 52: 1, 60: 2, 61: 2, 62: 4, 63: 4, 64: 4, 67: 4,
         70: 2, 71: 4, 73: 2, 75: 4, 77: 4, 79: 4, 80: 2, 81: 2, 82: 4, 83: 4, 101: 2, 102: 4, 110: 8, 111: 2, 112: 4, 113: 8, 114: 4, 115: 8, 116: 16, 117: 2}
MAPPABLE = {70, fcc("ATI1"), fcc("ATI2"), fcc("INTZ")}


def layout(f):
    """(bytes, bw, bh, bb, blocks, noverify, broken, hs) or None (size 0)"""
    if f in PLAIN: return (PLAIN[f], 1, 1, PLAIN[f], False, False, False, (1, 1))
    t = {"UYVY": (2, 2, 1, 4, True, True, False, (1, 1)), "YUY2": (2, 2, 1, 4, True, True, False, (1, 1)), "YV12": (1, 1, 1, 1, False, False, False, (3, 2)), "NV12": (1, 1, 1, 1, False, False, False, (3, 2)),
         "DXT1": (1, 4, 4, 8, True, False, False, (1, 1)), "DXT2": (1, 4, 4, 16, True, False, False, (1, 1)), "DXT3": (1, 4, 4, 16, True, False, False, (1, 1)), "DXT4": (1, 4, 4, 16, True, False, False, (1, 1)),
         "DXT5": (1, 4, 4, 16, True, False, False, (1, 1)), "MET1": (1, 1, 1, 1, False, False, False, (1, 1)), "GRGB": (1, 1, 1, 1, False, False, False, (1, 1)), "RGBG": (1, 1, 1, 1, False, False, False, (1, 1)),
         "ATI1": (1, 4, 4, 8, True, True, True, (1, 1)), "ATI2": (1, 4, 4, 16, True, True, True, (1, 1)), "NVHU": (2, 1, 1, 2, False, False, False, (1, 1)), "NVHS": (2, 1, 1, 2, False, False, False, (1, 1)),
         "INTZ": (4, 1, 1, 4, False, False, False, (1, 1)), "NULL": (4, 1, 1, 4, False, False, False, (1, 1)), " R16": (2, 1, 1, 2, False, False, False, (1, 1)), "AL16": (4, 1, 1, 4, False, False, False, (1, 1))}
    for k, v in t.items():
        if fcc(k) == f: return v
    return None


def pitch(L, w, h):
    by, bw, bh, bb, blk, nv, brk, hs = L
    if blk: row = (-(-w // bw) * bb + 3) & ~3; sl = row * -(-h // bh)
    else: row = (by * w + 3) & ~3; sl = row * h
    return row, sl * hs[0] // hs[1]


def mappitch(L, w, h):
    if L[6]: return w * L[0], w * L[0] * h
    return pitch(L, w, h)


def size(L, w, h, d):
    return w * h * d * L[0] if L[6] else pitch(L, w, h)[1] * d


def dim(v, l): return max(1, v >> l)
def chain(*d): return max(d).bit_length()


class Env:
    def __init__(self, prof):
        self.cdf, self.ms, self.caps, self.vmem = {}, {}, {}, None
        for l in open(os.path.join(ENV, f"env_{prof}.tsv")):
            if l.startswith("CheckDeviceFormat(0,1,16,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; self.cdf[tuple(a[3:])] = int(r, 16)
            elif l.startswith("CheckDeviceMultiSampleType(0,1,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]
                m = re.match(r"q=([0-9a-f]+)", o); self.ms[(a[2], a[3], a[4])] = (int(r, 16), int(m.group(1), 16) if m else 1)
            elif l.startswith("GetDeviceCaps(0,1)"):
                d = dict(t.split("=", 1) for t in l.rstrip("\n").split("\t")[2].split(" ") if "=" in t); self.caps = {k: int(d[k], 16) for k in ("MaxTextureWidth", "MaxTextureHeight")}
            elif l.startswith("DEV.GetAvailableTextureMem") and self.vmem is None: self.vmem = int(l.split("\t")[1], 16)

    def ok(self, usage, rtype, fmt): return self.cdf.get((usage, rtype, fmt)) == 0

    def ms_ok(self, fmt, t, q):
        if t == 0: return True
        if t > 16: return False
        r = self.ms.get((fmt, 0, t))
        if r is None or r[0] != 0: return False
        return q < r[1] if t == 1 else q in (0, 0xFFFFFFFF)


def nx_set():
    src = open(os.path.join(NXF, "front", "d3d9_state.c")).read(); a = src.index("NxFormatInfo nx_d3d9_format"); b = src.index("/* ---- defaults")
    s = set(int(m) for m in re.findall(r"case (\d+):", src[a:b]))
    for m in re.findall(r"NXD3D_FOURCC\('(.)', '(.)', '(.)', '(.)'\)", src[a:b]): s.add(fcc("".join(m)))
    return s


NX = nx_set()
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
    return (0, gpu, lock, gpu)


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
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(dev, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")],
                [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return None, None, r.stderr[-800:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", HERE, "-I", INC, "-I", tmp, "-I", dev, "-I", BACK, "-I", os.path.join(NXF, "include"), "-I", os.path.join(NXF, "front")]
    jobs_ = [(os.path.join(dev, s), s) for s in LIB + RES + TEX] + [(os.path.join(tmp, s), s) for s in GENS + ["env_table.gen.cpp"]] + [(os.path.join(HERE, s), s) for s in TESTS]
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in jobs_]
    cmds.append(["gcc", "-m32", "-std=c11", "-w", "-I", os.path.join(NXF, "include"), "-c", os.path.join(NXF, "front", "d3d9_state.c"), "-o", os.path.join(tmp, "nxstate.o")])
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    prod = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + RES + TEX]
    link = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS + TEX + TESTS + ["env_table.gen.cpp"]] + [os.path.join(tmp, "nxstate.o")]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_tex")] + link + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_tex"), prod, "") if r.returncode == 0 else (None, None, r.stderr[-800:])


def run(exe, data):
    r = sh([exe, data]); return r, {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}


def main():
    tmp = tempfile.mkdtemp(prefix="d9tex_"); data = os.path.join(tmp, "data"); os.makedirs(data)
    n = write_data(data); print(f"oracle: {n} creation cases; NxFormat set {len(NX)} formats")
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
                ("d", "res_tex.cpp", "img->autogen && img->mip_dirty) img->sync(true)", "img->autogen) img->sync(true)"), ("d", "res_tex.cpp", "filter == D3DTEXF_POINT ? 0 : 1", "1"),
                ("e", "res_tex.cpp", "if (u > o.k)", "if (u > o.k)") if False else ("e", "res_tex.cpp", "if (n.k > o.k) o.k = n.k;", ""), ("c", "res_surf.cpp", "if (s->gpu_newer) { if (!discard", "if (s->gpu_newer) { if (false")]
        def one(i):
            part, f, old, new = muts[i]; root = os.path.join(tmp, f"m{i}_root", "d3d9"); mdev = os.path.join(root, "dev"); os.makedirs(root)
            shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); os.symlink(os.path.join(ROOT, "render", "d3d9", "ffp"), os.path.join(root, "ffp"))
            txt = open(os.path.join(mdev, f)).read()
            if old not in txt: return i, None, "pattern not found"
            open(os.path.join(mdev, f), "w").write(txt.replace(old, new, 1))
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
