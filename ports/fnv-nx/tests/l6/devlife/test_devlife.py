#!/usr/bin/env python3
"""test_devlife.py: tests of render/d3d9/dev/dev_*.cpp (D7: device lifecycle, swap chains, presentation, the non-drawing device methods).  python3 -I tests/l6/devlife/test_devlife.py [--no-mutations]
 a CreateDevice / CreateDeviceEx over the whole parameter domain against the python oracle below (env tsv + the rules stated in dev_params.cpp / dev_create.cpp)   b refcounts + identity of the implicit objects
 c Reset / ResetEx   d swap chains, back buffer rotation, recorded present calls   e gamma / cursor / dialog box / raster status / texture memory (rows DEV.Mem* of the env table) / eviction   f Ex methods
 g gate G-METHOD before -> after (tests/l6/dev/test_dev.py gate())   h mutation checks (one rule broken in a copy of dev/, the named part must fail).
Product objects = render/d3d9/dev/*.cpp minus app_unk_guest.cpp + the generated sources + render/d3d9/front/*.c; the platform / backend hooks of dev_hooks.h and res_tex.h are recorded by t_env.cpp."""
import concurrent.futures as cf, importlib.util, os, random, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D3 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3, "dev"); GEN = os.path.join(D3, "gen"); ENV = os.path.join(D3, "env"); SPEC = os.path.join(D3, "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); BACK = os.path.join(D3, "..", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); FRONT = os.path.join(D3, "front"); DEVT = os.path.join(ROOT, "tests", "l6", "dev"); TOT = os.path.join(ROOT, "render", "shaders", "total")
TESTS = ["t_env.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp", "t_e.cpp", "t_f.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp", "env_table.gen.cpp"]
MINE = ["dev_params.cpp", "dev_chain.cpp", "dev_create.cpp", "dev_mem.cpp", "dev_reset.cpp", "dev_misc.cpp", "dev_ex.cpp"]
INV, NA, OOVM, EFAIL, OK = 0x8876086C, 0x8876086A, 0x8876017C, 0x80004005, 0
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fcc(s): return sum(ord(c) << (8 * i) for i, c in enumerate(s))


# ---------------------------------------------------------------- oracle (independent of the C++): the stated Wine rules over the env tsv
PLAIN = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2, 27: 1, 28: 1, 29: 2, 30: 2, 31: 4, 32: 4, 33: 4, 34: 4, 35: 4, 36: 8, 40: 2, 41: 1, 50: 1, 51: 2, 52: 1, 60: 2, 61: 2, 62: 4, 63: 4, 64: 4, 67: 4,
         70: 2, 71: 4, 73: 2, 75: 4, 77: 4, 79: 4, 80: 2, 81: 2, 82: 4, 83: 4, 101: 2, 102: 4, 110: 8, 111: 2, 112: 4, 113: 8, 114: 4, 115: 8, 116: 16, 117: 2}
FOURCC = {"UYVY": (2, False), "YUY2": (2, False), "YV12": (1, False), "NV12": (1, False), "DXT1": (8, True), "DXT2": (16, True), "DXT3": (16, True), "DXT4": (16, True), "DXT5": (16, True), "MET1": (1, False),
          "GRGB": (1, False), "RGBG": (1, False), "ATI1": (8, True), "ATI2": (16, True), "NVHU": (2, False), "NVHS": (2, False), "INTZ": (4, False), "NULL": (4, False), " R16": (2, False), "AL16": (4, False)}
NOVERIFY = {"UYVY", "YUY2", "ATI1", "ATI2"}   # wined3d BLOCKS_NO_VERIFY: no block alignment check


def printable4(f): return all(0x20 <= (f >> (8 * i)) & 255 <= 0x7e for i in range(4))
def unk(f): return not printable4(f) and f not in PLAIN
def rt(f): return 0 if unk(f) else f   # d3dformat_from_wined3dformat(wined3dformat_from_d3dformat(f))


def layout(f):
    """-> (bytes per pixel / block, blocks, noverify) or None (size 0)"""
    if f in PLAIN: return (PLAIN[f], False, False)
    for k, (b, blk) in FOURCC.items():
        if fcc(k) == f: return (b, blk, k in NOVERIFY)
    return None


def block_ok(f, w, h):
    L = layout(f)
    return not (L[1] and not L[2] and (w % 4 or h % 4))


CLIENT = {0x100: (800, 600), 0x200: (0, 0), 0x400: (100, 50)}
VALID = {0x100, 0x200, 0x300, 0x400}


class Env:
    def __init__(self, prof):
        self.cdf, self.ms, self.dm, self.fmts = {}, {}, None, set()
        for l in open(os.path.join(ENV, f"env_{prof}.tsv")):
            if l.startswith("CheckDeviceFormat(0,1,16,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; self.cdf[tuple(a[3:])] = int(r, 16)
            elif l.startswith("CheckDeviceMultiSampleType(0,1,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]
                m = re.match(r"q=([0-9a-f]+)", o); self.ms[(a[2], a[3], a[4])] = (int(r, 16), int(m.group(1), 16) if m else 1); self.fmts.add(a[2])
            elif l.startswith("GetAdapterDisplayMode(0)"):
                d = dict(t.split("=", 1) for t in l.rstrip("\n").split("\t")[2].split(" ")); self.dm = (int(d["Width"], 16), int(d["Height"], 16), int(d["Format"], 16))

    def ok(self, usage, fmt): return self.cdf.get((usage, 1, fmt)) == 0

    def ms_check(self, fmt, t, q):
        """wined3d_texture_create: (hr, samples)"""
        if t == 0: return OK, 1
        r = self.ms.get((fmt, 0, t if t <= 16 else 16)) if t <= 16 else None
        if t == 1:
            if r is None or r[0] != 0 or q >= r[1]: return NA, 1
            n = 0
            for tt in range(2, 17):
                e = self.ms.get((fmt, 0, tt))
                if e is not None and e[0] == 0:
                    if n == q: return OK, tt
                    n += 1
            return NA, 1
        if t > 16 or r is None or r[0] != 0 or (q and q != 0xFFFFFFFF): return NA, 1
        return OK, t


def tex_check(E, w, h, fmt, bind):
    if not w or not h or unk(fmt) or layout(fmt) is None: return INV
    if bind and not E.ok(bind, fmt): return INV
    if not block_ok(fmt, w, h): return INV
    L = layout(fmt); size = (-(-w // 4) * L[0] * -(-h // 4)) if L[1] else ((L[0] * w + 3) & ~3) * h
    return OOVM if size > (1 << 30) else OK


def oracle(E, ex, c):
    """-> (hr, bw, bh, bf, bc, flags, dsfmt, nimg, samples, ds_samples)"""
    Z = lambda hr: (hr, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    if c["ad"] != 0: return Z(INV)
    win = c["win"]
    if not win and (c["focus"] or c["hdw"]) not in VALID: return Z(EFAIL)
    se, cnt, iv = c["swap"], c["count"], c["iv"]
    if se == 0 or se > (5 if ex else 3): return Z(INV)
    if cnt > (30 if ex else 3) or (se == 3 and cnt > 1): return Z(INV)
    if iv not in (0, 1, 2, 4, 8, 0x80000000): return Z(INV)
    window = c["hdw"] or c["focus"]; w, h, fmt = c["w"], c["h"], c["fmt"]
    if win:
        cw, ch = CLIENT.get(window, E.dm[:2]) if window == 0x300 else CLIENT.get(window, (0, 0))
        w = w or cw or 8; h = h or ch or 8
        if unk(fmt): fmt = E.dm[2]
    hr, sm = E.ms_check(fmt, c["ms"], c["msq"])
    if hr: return Z(hr)
    hr = tex_check(E, w, h, fmt, 0)
    if hr: return Z(hr)
    if not win:
        bits = layout(fmt)[0] * 8
        if (w, h) != E.dm[:2] or bits not in (16, 32): return Z(NA)
    hr = tex_check(E, w, h, fmt, 1)
    if hr: return Z(hr)
    dsm = 1
    if c["dsen"]:
        hr, dsm = E.ms_check(c["dsfmt"], c["ms"], c["msq"])
        if not hr: hr = tex_check(E, w, h, c["dsfmt"], 2)
        if hr: return Z(hr)
    return (OK, w, h, fmt, cnt or 1, c["flags"] & 0xfff, rt(c["dsfmt"]), (0 if fmt == fcc("NULL") else (cnt or 1)) + (1 if c["dsen"] else 0), sm, dsm)


def line(prof, obj, c, r):
    return "C %d %d %d %x %x %x %d %d %d %x %d %d %d %d %d %d %d %x %d %d | %x %d %d %d %d %x %d %d %d %d\n" % (obj, c["ad"], c["dt"], c["beh"], c["focus"], c["hdw"], c["win"], c["swap"], c["count"], c["iv"], c["w"], c["h"], c["fmt"], c["ms"], c["msq"],
        c["dsen"], c["dsfmt"], c["flags"], c["refresh"], prof, *r)


def write_data(d):
    n = 0; rnd = random.Random(7)
    with open(os.path.join(d, "create.txt"), "w") as f:
        for prof, name in ((0, "handheld"), (1, "docked")):
            E = Env(name); dw, dh, dfmt = E.dm; fm = sorted(E.fmts)
            base = dict(ad=0, dt=1, beh=0x20, focus=0x100, hdw=0x100, win=1, swap=1, count=1, iv=0, w=640, h=480, fmt=22, ms=0, msq=0, dsen=0, dsfmt=0, flags=0, refresh=0)
            def emit(obj, c):
                nonlocal n
                f.write(line(prof, obj, c, oracle(E, obj != 0, c))); n += 1
            for obj in (0, 1, 2, 3):   # G1: the d3d9-level rules
                for win in (0, 1):
                    for swap in range(7):
                        for count in (0, 1, 3, 4, 30, 31):
                            for iv in (0, 1, 2, 3, 4, 8, 0x10, 0x80000000):
                                c = dict(base, win=win, swap=swap, count=count, iv=iv); c["w"], c["h"] = (dw, dh) if not win else (640, 480); emit(obj, c)
            for obj in (0, 2):   # G2: size / format / window
                for win in (0, 1):
                    for (w, h) in ((0, 0), (640, 480), (dw, dh), (1, 1), (dw, 0), (0, dh), (20000, 20000), (5, 3)):
                        for fmt in fm:
                            for hdw, focus in ((0x100, 0x100), (0, 0x100), (0x200, 0x100), (0x999, 0), (0x300, 0x300), (0x400, 0)):
                                if (rnd.random() > 0.15 and hdw not in (0x100,)) or (fmt not in (22, 21, 23) and rnd.random() > 0.5): continue
                                emit(obj, dict(base, win=win, w=w, h=h, fmt=fmt, hdw=hdw, focus=focus))
            for win in (0, 1):   # G3: multisample + depth stencil
                for ms in (0, 1, 2, 4, 8, 16, 17, 33):
                    for msq in (0, 1, 2, 0xffffffff):
                        for dsen in (0, 1):
                            for dsfmt in fm[::4]:
                                for fmt in (22, 21, 23):
                                    if not dsen and dsfmt != fm[0]: continue
                                    emit(0, dict(base, win=win, ms=ms, msq=msq, dsen=dsen, dsfmt=dsfmt, fmt=fmt, w=dw if not win else 640, h=dh if not win else 480))
            for _ in range(2000):   # G4: adapter / device type / behaviour / focus / flags / refresh
                emit(rnd.choice((0, 1, 2)), dict(base, ad=rnd.choice((0, 0, 0, 1, 0xffffffff)), dt=rnd.choice((0, 1, 2, 3, 4, 5, 0x7fffffff)), beh=rnd.choice((0, 0x40, 0x20, 0x10, 0xffffffff, 0x80000)),
                    focus=rnd.choice((0x100, 0, 0x999, 0x300)), hdw=rnd.choice((0x100, 0, 0x999, 0x400)), win=rnd.choice((0, 1)), flags=rnd.choice((0, 1, 0xfff, 0xf000, 0xffffffff)), refresh=rnd.choice((0, 60, 0xffffffff)), swap=rnd.choice((1, 2, 3)),
                    w=rnd.choice((dw, 640)), h=rnd.choice((dh, 480)), fmt=rnd.choice((22, 21)), dsen=rnd.choice((0, 1)), dsfmt=rnd.choice((75, 80, 22, 0))))
    # memory rows: DEV.Mem* of the env table (Wine measurements), deduplicated
    rows = set()
    for l in open(os.path.join(ENV, "env_handheld.tsv")):
        if not l.startswith("DEV.Mem"): continue
        q, r, o = l.rstrip("\n").split("\t"); m = re.match(r"(DEV\.\w+)\((\w+),(\w+),(\w+)\)", q); kind, id_, idx = m.group(1), int(m.group(2), 16), int(m.group(3), 16); ex = int(id_ >= 250)
        if "m0" in o:
            fl = {a: int(b, 16) for a, b in (t.split("=") for t in o.split())}; delta = fl["m0"] - fl["m1"]
            if kind == "DEV.MemTexture": rows.add("T %d %d %d %d %d %d\n" % (idx // 16, (idx % 16) // 4, (idx % 4) // 2, idx % 2, ex, delta))
            elif kind in ("DEV.MemVB", "DEV.MemIB", "DEV.MemSurface"): rows.add("%s %d %d %d\n" % ({"DEV.MemVB": "V", "DEV.MemIB": "I", "DEV.MemSurface": "S"}[kind], idx, ex, delta))
            elif kind == "DEV.MemRT": rows.add("R %d\n" % ex)
            elif kind == "DEV.MemDS": rows.add("D %d\n" % ex)
        elif kind == "DEV.MemTexture": rows.add("T %d %d %d %d %d F %x\n" % (idx // 16, (idx % 16) // 4, (idx % 4) // 2, idx % 2, ex, int(o.split("=")[1], 16)))
    open(os.path.join(d, "mem.txt"), "w").writelines(sorted(rows))
    return n


# ---------------------------------------------------------------- build
def fl(tmp, dev): return ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", dev, "-I", INC, "-I", tmp, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]


def gen(tmp):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(DEV, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(DEV, "res_smgen.py"), tmp, os.path.join(TOT, "sm_table.json"), os.path.join(os.path.dirname(SPEC), "d3d9_tables.py")], [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return r.stderr[-800:]
    return None


def compile_all(tmp, dev, objs, jobs=4, only=None, outdir=None):
    """objs: {name: source}; compiles the ones in `only` (all when None) into tmp/objs/name.o"""
    od = outdir or os.path.join(tmp, "objs"); os.makedirs(od, exist_ok=True); cmds = []
    for n, src in objs.items():
        if only is not None and n not in only: continue
        cmds.append((n, (["gcc", "-m32", "-std=c11", "-w", "-I", BACK, "-I", NXB, "-I", FRONT] if src.endswith(".c") else fl(tmp, dev)) + ["-c", src, "-o", os.path.join(od, os.path.splitext(n)[0] + ".o")]))
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(lambda c: sh(c[1]), cmds))
    for (n, c), r in zip(cmds, rs):
        if r.returncode: return n + "\n" + r.stderr[-1500:]
    return None


def sources(tmp, dev):
    o = {}
    for f in sorted(os.listdir(dev)):
        if f.endswith(".cpp") and f != "app_unk_guest.cpp" and not f.startswith(("glue_", "ffp_")): o[f] = os.path.join(dev, f)   # glue_*.cpp = the product definitions of the backend hooks (nxg calls): the test records them instead
    for g in GENS: o[g] = os.path.join(tmp, g)
    for f in sorted(os.listdir(FRONT)):
        if f.endswith(".c"): o["front_" + f] = os.path.join(FRONT, f)
    return o


def link(tmp, prod_names, test_names, exe, objdir=None):
    od = objdir or os.path.join(tmp, "objs"); bn = lambda n: os.path.join(od, os.path.splitext(n)[0] + ".o")
    r = sh(["g++", "-m32", "-no-pie", "-o", exe] + [bn(n) for n in prod_names] + [os.path.join(tmp, "objs", os.path.splitext(n)[0] + ".o") for n in test_names] + ["-Wl,--unresolved-symbols=ignore-all"])
    return r.stderr[-800:] if r.returncode else None


def run(exe, data):
    r = sh([exe, data]); ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, ps


def main():
    tmp = tempfile.mkdtemp(prefix="d9life_"); data = os.path.join(tmp, "data"); os.makedirs(data); n = write_data(data); print(f"oracle: {n} CreateDevice cases")
    err = gen(tmp)
    if err: print("GEN FAILED\n" + err); return 1
    prod = sources(tmp, DEV); tsrc = {t: os.path.join(HERE, t) for t in TESTS}
    err = compile_all(tmp, DEV, {**prod, **tsrc})
    if err: print("BUILD FAILED\n" + err); return 1
    exe = os.path.join(tmp, "test_devlife"); err = link(tmp, list(prod), TESTS, exe)
    if err: print("LINK FAILED\n" + err); return 1
    r, parts = run(exe, data); ls = r.stdout.split("\n"); print("\n".join(l for l in ls if l.startswith(("FAIL", "  case", "  bad")))[:3000])
    for p in "abcdef":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    print("parts: " + ", ".join(f"{p} {parts[p][0]} checks {parts[p][1]} failed" for p in sorted(parts)))
    sp = importlib.util.spec_from_file_location("test_dev", os.path.join(DEVT, "test_dev.py")); td = importlib.util.module_from_spec(sp); sp.loader.exec_module(td)
    objs = lambda names: [os.path.join(tmp, "objs", os.path.splitext(x)[0] + ".o") for x in names]
    want, miss, unres = td.gate(tmp, objs(list(prod))); before = td.gate(tmp, objs([x for x in prod if x not in MINE]))
    cls = lambda m: m.split("::")[0]; mine = ("NxDevice9", "NxDevice9Ex", "NxSwapChain9", "NxSwapChain9Ex", "NxDirect3D9", "NxDirect3D9Ex")
    print(f"g: G-METHOD missing before (without dev_*.cpp) {len(before[1])} -> after {len(miss)} of {len(want)}")
    for c in mine: print(f"   {c}: {sum(1 for m in before[1] if cls(m) == c)} -> {sum(1 for m in miss if cls(m) == c)}")
    LEFT = set()   # GetPresentStats, SetConvolutionMonoKernel, ComposeRects have bodies since M1 (dev/m1_*.cpp, tests/l6/m1)
    other = {"UpdateSurface", "UpdateTexture", "GetRenderTargetData", "StretchRect", "ColorFill", "Clear", "SetClipStatus", "GetClipStatus", "ValidateDevice", "DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP", "ProcessVertices", "DrawRectPatch", "DrawTriPatch", "DeletePatch"}
    mm = [m for m in miss if cls(m) in mine and m.split("::")[1] not in other]; print("   my methods without a body: " + ", ".join(mm))
    if set(mm) != LEFT: fails.append(f"g: methods without a body {sorted(set(mm) ^ LEFT)}")
    if not len(miss) < len(before[1]): fails.append("g: gate did not improve")
    if "--no-mutations" not in sys.argv:
        P = lambda f, old, new: (f, old, new)
        muts = [("a", *P("dev_params.cpp", "cnt > (ex ? 30u : 3u)", "cnt > (ex ? 31u : 3u)")), ("a", *P("dev_params.cpp", "(se == D3DSWAPEFFECT_COPY && cnt > 1)", "(false)")), ("a", *P("dev_params.cpp", "w = c ? c : 8;", "w = c ? c : 9;")),
                ("a", *P("dev_params.cpp", "(bits != 32 && bits != 16) || (bits == 32", "(bits != 32) || (bits == 32")), ("a", *P("dev_params.cpp", "if (FAILED(hr = ms_ok(p, fmt, pp->MultiSampleType, pp->MultiSampleQuality, &sm))) return hr;", "")),
                ("a", *P("dev_create.cpp", "L->cp.BehaviorFlags = flags;", "L->cp.BehaviorFlags = 0;")), ("a", *P("dev_create.cpp", "pp->Flags &= 0xfffu;", "")), ("d", *P("dev_chain.cpp", "bbimg(c, i)->gpu = bbimg(c, i + 1)->gpu;", "bbimg(c, i)->gpu = bbimg(c, i)->gpu;")),
                ("d", *P("dev_chain.cpp", "p.frame = ++L->frame;", "p.frame = L->frame;")), ("d", *P("dev_chain.cpp", "p.gamma = ramp_set ? (uint64_t)(uintptr_t)&ramp : 0;", "p.gamma = (uint64_t)(uintptr_t)&ramp;")),
                ("d", *P("dev_chain.cpp", "dst.right > cw || dst.bottom > ch", "false")), ("d", *P("dev_misc.cpp", "for (uint32_t i = 0; i < L->nchains; i++) if (!L->chains[i]->pp.Windowed) return INV;", "")),
                ("c", *P("dev_reset.cpp", "if (nx_dev_default_pool_blocks(dev)) { L->state = NX_DEV_NOTRESET;", "if (false) { L->state = NX_DEV_NOTRESET;")), ("c", *P("dev_reset.cpp", "d->init(d->cp);  ", ";  ")),
                ("c", *P("dev_reset.cpp", "if (!pp->Windowed == !mode) return D3DERR_INVALIDCALL;", "")), ("e", *P("dev_mem.cpp", "if (nx_life(dev)->ex) return 0;", "")), ("e", *P("dev_mem.cpp", "t += (l + 15) & ~15ull;", "t += l;")),
                ("e", *P("dev_misc.cpp", "if (c.bgra) c.visible = show != 0;", "c.visible = show != 0;")), ("e", *P("obj.cpp", "if (this->pool != 0xFF) { rnext", "if (false) { rnext")), ("f", *P("dev_ex.cpp", "if (n > 30) return INV;", "if (n > 31) return INV;")),
                ("e", *P("dev_misc.cpp", "nxd3d_os_cursor_pos(&c.x, &c.y);", ";"))]
        def one(i):
            part, f, old, new = muts[i]; mdev = os.path.join(tmp, f"m{i}_dev"); shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}")); mt = os.path.join(tmp, f"m{i}"); os.makedirs(os.path.join(mt, "objs"), exist_ok=True)
            e2 = compile_all(tmp, mdev, {f: os.path.join(mdev, f)}, outdir=os.path.join(mt, 'objs'))
            if e2: return i, None, e2
            names = list(prod); od = os.path.join(mt, "objs"); mexe = os.path.join(mt, "t")
            rr = sh(["g++", "-m32", "-no-pie", "-o", mexe] + [os.path.join(od if n == f else os.path.join(tmp, "objs"), os.path.splitext(n)[0] + ".o") if n == f else os.path.join(tmp, "objs", os.path.splitext(n)[0] + ".o") for n in names]
                    + [os.path.join(tmp, "objs", os.path.splitext(t)[0] + ".o") for t in TESTS] + ["-Wl,--unresolved-symbols=ignore-all"])
            if rr.returncode: return i, None, rr.stderr[-500:]
            x, ps = run(mexe, data); return i, ps, x.returncode
        with cf.ThreadPoolExecutor(2) as ex:
            for i, ps, rc in ex.map(one, range(len(muts))):
                part = muts[i][0]; ok = ps is not None and (ps.get(part, (0, 0))[1] > 0 or (rc != 0 and part not in ps))
                print(f"mutation {i} ({muts[i][1]}: {muts[i][2][:48]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {'' if ok else str((ps, rc))[:300]}")
                if not ok: fails.append(f"h: mutation {i} not caught ({ps} {rc})")
    if os.environ.get("KEEP"): print("kept", tmp)
    else: shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
