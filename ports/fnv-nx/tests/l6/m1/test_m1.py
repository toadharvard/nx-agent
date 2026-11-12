#!/usr/bin/env python3
"""test_m1.py: tests of render/d3d9/dev/m1_*.cpp (M1: ValidateDevice, GetPresentStats, ComposeRects, SetConvolutionMonoKernel + the three rechecked lifecycle decisions).
python3 -I tests/l6/m1/test_m1.py [--no-mutations] [--no-guest]
 a ValidateDevice over a generated state sweep vs the python oracle below (rule table written from the MS page, Wine 9.0 wined3d_device_validate_device / d3d9 tests test_filter, Mesa Nine, and the env table's own caps image and
   CheckDeviceFormat rows), nx_m1_draw_legal equality, draw rule D1 equality
 b GetPresentStats: the V(c) = floor(c * R / f) clock rule over a domain of (f, c) vs python bigints, counters, error domain, both kinds of chain
 c ComposeRects: no D3DFMT_A1 resource can be made through any creator (format x pool x usage), every call = D3DERR_INVALIDCALL
 d SetConvolutionMonoKernel: validation rows, stored state, state blocks, Reset   e the three rechecked lifecycle decisions (Reset count, GetLastPresentCount, GetFrontBufferData formats)
 f method gate before -> after (tests/l6/dev/test_dev.py gate()) and the guest build's undefined symbols before -> after   g mutation checks (one rule broken in a copy of dev/, the named part must fail).
Product objects = render/d3d9/dev/*.cpp minus app_unk_guest.cpp and glue_*.cpp + generated sources + render/d3d9/front/*.c; the hooks of dev_hooks.h / res_tex.h are recorded by t_env.cpp (as tests/l6/devlife)."""
import concurrent.futures as cf, importlib.util, os, random, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D3 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3, "dev"); GEN = os.path.join(D3, "gen"); ENV = os.path.join(D3, "env"); SPEC = os.path.join(D3, "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); BACK = os.path.join(D3, "..", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); FRONT = os.path.join(D3, "front"); DEVT = os.path.join(ROOT, "tests", "l6", "dev"); TOT = os.path.join(ROOT, "render", "shaders", "total")
TESTS = ["t_env.cpp", "t_a.cpp", "t_b.cpp", "t_c.cpp", "t_d.cpp", "t_e.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp", "env_table.gen.cpp"]
M1 = ["m1_validate.cpp", "m1_stats.cpp", "m1_misc.cpp"]
TC = os.environ.get("FNVNX_TC", "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"); UAM_DEPS = os.environ.get("FNVNX_SHADERS_DEPS", "/home/user/work/l6/s2a/build/deps")
fails = []
HR = dict(OK=0, INV=0x8876086C, LOST=0x88760868, WRONGFMT=0x88760818, COLOROP=0x88760819, COLORARG=0x8876081A, ALPHAOP=0x8876081B, ALPHAARG=0x8876081C, TOOMANY=0x8876081D, FILTER=0x88760822, CONFLICTRS=0x88760821, EFAIL=0x80004005)


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- oracle (independent of the C++): the rule table of m1_validate.cpp's header over the env tsv
TEXOP_CAPS = {1: 0x1, 2: 0x2, 3: 0x4, 4: 0x8, 5: 0x10, 6: 0x20, 7: 0x40, 8: 0x80, 9: 0x100, 10: 0x200, 11: 0x400, 12: 0x800, 13: 0x1000, 14: 0x2000, 15: 0x4000, 16: 0x8000, 17: 0x10000, 18: 0x20000, 19: 0x40000, 20: 0x80000,
              21: 0x100000, 22: 0x200000, 23: 0x400000, 24: 0x800000, 25: 0x1000000, 26: 0x2000000}   # D3DTEXOPCAPS_* by D3DTEXTUREOP (MS D3DCAPS9 page)
MINF = {1: 0x100, 2: 0x200, 3: 0x400, 6: 0x800, 7: 0x1000, 8: 0x40000}; MAGF = {1: 0x1000000, 2: 0x2000000, 3: 0x4000000, 6: 0x8000000, 7: 0x10000000, 8: 0x40000}; MIPF = {0: 0, 1: 0x10000, 2: 0x20000}   # D3DPTFILTERCAPS_*
TEX = {1: ("2d", "A8"), 2: ("cube", "A8"), 3: ("vol", "A8"), 4: ("2d", "FA"), 5: ("cube", "FA"), 6: ("2d", "FB"), 7: ("vol", "FA")}; RTYPE = {"2d": 3, "cube": 5, "vol": 4}
RTS = {0: (640, 480, 1), 1: (64, 64, 1), 2: (32, 32, 1), 3: (64, 64, 4)}; DSS = {0: (640, 480, 1), 2: (64, 64, 1), 3: (32, 32, 1), 4: (64, 64, 4)}   # render target / depth ids of t_a.cpp (0 = the implicit buffers, ds 1 = NULL)
STAGE0 = dict(cop=4, c1=2, c2=1, c0=1, aop=2, a1=2, a2=1, a0=1, res=1); STAGEN = dict(STAGE0, cop=1, aop=1)


class Env:
    def __init__(self, prof):
        self.cdf = {}; self.caps = None; self.rr = 0
        for l in open(os.path.join(ENV, f"env_{prof}.tsv")):
            if l.startswith("CheckDeviceFormat(0,1,16,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; self.cdf[tuple(a[3:])] = int(r, 16)
            elif l.startswith("GetDeviceCaps(0,1)"): self.caps = {k: int(v, 16) for k, v in re.findall(r"([\w.]+)=([0-9a-fA-F]+)", l.rstrip("\n").split("\t")[2])}
            elif l.startswith("GetAdapterDisplayMode(0,0"):
                m = re.search(r"RefreshRate=(\w+)", l)
                if m: self.rr = int(m.group(1), 16)
        assert self.caps and self.cdf
        fm = sorted({k[2] for k in self.cdf}); self.nonfilter = [f for f in fm if self.cdf.get((0, 3, f)) == 0 and self.cdf.get((0x20000, 3, f), 0) != 0 and self.cdf.get((0, 5, f)) == 0 and self.cdf.get((0, 4, f)) == 0]
        self.bump = [f for f in fm if self.cdf.get((0, 3, f)) == 0 and self.cdf.get((0x8000, 3, f)) == 0 and self.cdf.get((0x20000, 3, f)) == 0]
        self.fmt = {"A8": 21, "FA": self.nonfilter[0], "FB": self.bump[0]}

    def filterable(self, tid): k, f = TEX[tid]; return self.cdf[(0x20000, RTYPE[k], self.fmt[f])] == 0
    def bumpable(self, tid): k, f = TEX[tid]; return self.cdf[(0x8000, RTYPE[k], self.fmt[f])] == 0
    def refresh(self): return self.rr or 60


def reads(op):
    """argument slots an operation reads (ffp/README): -> (arg0, arg1, arg2)"""
    if op in (25, 26): return (1, 1, 1)
    if op in (2, 17): return (0, 1, 0)
    if op == 3: return (0, 0, 1)
    return (0, 1, 1) if 4 <= op <= 24 else (0, 0, 0)


def arg_ok(a, misc): return (a >> 6) == 0 and (a & 15) <= 6 and not ((a & 15) == 5 and not misc & 0x400) and not ((a & 15) == 6 and not misc & 0x8000)


def oracle(env, c):
    cp = env.caps
    if not c["ex"] and c["lost"]: return HR["LOST"]
    if not c["decl"]: return HR["INV"]
    smp = dict(((s, (a, b, m)) for s, a, b, m in c["s"])); tx = dict(c["t"])
    for slot in list(range(16)) + [17, 18, 19, 20]:
        mn, mg, mp = smp.get(slot, (1, 1, 0)); tid = tx.get(slot, 0)
        w = cp["VertexTextureFilterCaps"] if slot >= 17 else (cp[{"2d": "TextureFilterCaps", "cube": "CubeTextureFilterCaps", "vol": "VolumeTextureFilterCaps"}[TEX[tid][0]]] if tid else cp["TextureFilterCaps"])
        if mn == 0 or mg == 0 or not (MINF.get(mn, 0) & w) or not (MAGF.get(mg, 0) & w) or not (mp == 0 or (MIPF.get(mp, 0) & w)): return HR["FILTER"]
        if tid and not env.filterable(tid) and (mg != 1 or mn != 1 or mp not in (0, 1)): return HR["EFAIL"]
    if not c["ps"]:
        st = dict(c["g"]); act = tex = 0
        for i in range(8):
            g = st.get(i, STAGE0 if i == 0 else STAGEN)
            if g["cop"] == 1: break
            act += 1
            if act > cp["MaxTextureBlendStages"]: return HR["TOOMANY"]
            ao = g["aop"] != 1; rc = reads(g["cop"]); ra = reads(g["aop"]) if ao else (0, 0, 0)
            cu = any(r and (g[k] & 15) == 2 for r, k in zip(rc, ("c0", "c1", "c2"))) or g["cop"] in (13, 15, 22, 23)
            au = ao and (any(r and (g[k] & 15) == 2 for r, k in zip(ra, ("a0", "a1", "a2"))) or g["aop"] in (13, 15, 22, 23))
            if cu or au:
                tex += 1
                if tex > cp["MaxSimultaneousTextures"]: return HR["TOOMANY"]
            if not TEXOP_CAPS.get(g["cop"], 0) & cp["TextureOpCaps"]: return HR["COLOROP"]
            if ao and not TEXOP_CAPS.get(g["aop"], 0) & cp["TextureOpCaps"]: return HR["ALPHAOP"]
            if any(r and not arg_ok(g[k], cp["PrimitiveMiscCaps"]) for r, k in zip(rc, ("c0", "c1", "c2"))): return HR["COLORARG"]
            if any(r and not arg_ok(g[k], cp["PrimitiveMiscCaps"]) for r, k in zip(ra, ("a0", "a1", "a2"))): return HR["ALPHAARG"]
            if g["res"] != 1 and not (g["res"] == 5 and cp["PrimitiveMiscCaps"] & 0x400): return HR["COLORARG"]
            if g["cop"] in (22, 23) and not (tx.get(i, 0) and env.bumpable(tx[i])): return HR["WRONGFMT"]
    rts = [RTS[c["rt"][0]]] + ([RTS[c["rt"][1]]] if c["rt"][1] else [])
    if any(r != rts[0] for r in rts): return HR["CONFLICTRS"]
    if c["ds"] != 1 and any(c["z"]) and DSS[c["ds"]] != rts[0]: return HR["CONFLICTRS"]
    return 0


def fmt_case(c, exp):
    z = ",".join(map(str, c["z"])); s = ";".join(f"{a}:{b},{d},{m}" for a, b, d, m in c["s"]); t = ";".join(f"{a}:{b}" for a, b in c["t"])
    g = ";".join(f"{i}:{x['cop']},{x['c1']},{x['c2']},{x['c0']},{x['aop']},{x['a1']},{x['a2']},{x['a0']},{x['res']}" for i, x in c["g"])
    return f"C ex={c['ex']} lost={c['lost']} decl={c['decl']} ps={c['ps']} z={z} rt={c['rt'][0]},{c['rt'][1]} ds={c['ds']} s={s} t={t} g={g} e={exp:x}\n"


def write_data(d):
    env = Env("handheld"); rnd = random.Random(20261111); out = []
    base = dict(ex=0, lost=0, decl=1, ps=0, z=(1, 1, 0), rt=(0, 0), ds=0, s=[], t=[], g=[])
    cases = []
    def add(**kw): cases.append(dict(base, **kw))
    # A samplers: slot 0 over the whole filter x texture domain, the other slots over a subset
    for mn in list(range(10)) + [255]:
        for mg in list(range(10)) + [255]:
            for mp in (0, 1, 2, 3, 9):
                for tid in range(8):
                    if rnd.random() < 0.32 or (mn in (0, 1, 2) and mg in (0, 1, 2) and mp < 3): add(s=[(0, mn, mg, mp)], t=[(0, tid)] if tid else [])
    for slot in (1, 3, 15, 17, 18, 20):
        for mn in (0, 1, 2, 3, 6, 7, 8):
            for mg in (0, 1, 2, 3, 6, 7, 8):
                for mp in (0, 1, 2, 3):
                    for tid in (0, 1, 2, 3, 4, 5, 6, 7):
                        if rnd.random() < 0.025: add(s=[(slot, mn, mg, mp)], t=[(slot, tid)] if tid else [])
    # B fixed-function stages: operations, arguments, chains
    for cop in range(0, 29):
        for aop in range(0, 29): add(g=[(0, dict(STAGE0, cop=cop, aop=aop))], t=[(0, 1)])
    for a in range(0, 0x80):
        for k in ("c1", "c2", "c0", "a1", "a2", "a0", "res"):
            for op in ((4, 25) if k in ("c0", "a0") else (4,)): add(g=[(0, dict(STAGE0, cop=op, aop=op, **{k: a}))], t=[(0, 1)])
    for a in (0x100, 0x1234, 0xffffffff, 0x40, 0x80000000): add(g=[(0, dict(STAGE0, c1=a))]); add(g=[(0, dict(STAGE0, a1=a))])
    for _ in range(2500):
        g = []
        for i in range(rnd.randint(1, 8)):
            x = dict(STAGE0 if i == 0 else STAGEN)
            if rnd.random() < 0.85: x["cop"] = rnd.choice([1, 2, 3, 4, 7, 13, 17, 22, 23, 24, 25, 26, rnd.randint(0, 30)]) if i or rnd.random() < 0.5 else 4
            for k in ("c1", "c2", "c0", "a1", "a2", "a0", "res"):
                if rnd.random() < 0.3: x[k] = rnd.choice([0, 1, 2, 3, 4, 5, 6, 7, 15, 0x12, 0x22, 0x40, rnd.randint(0, 0x7f)])
            if rnd.random() < 0.5: x["aop"] = rnd.choice([1, 2, 3, 4, 13, 15, 22, 25, rnd.randint(0, 30)])
            g.append((i, x))
        add(g=g, t=[(i, rnd.choice((0, 1, 2, 3, 4, 6))) for i in range(rnd.randint(0, 4))], ps=int(rnd.random() < 0.15))
    for st in range(0, 4):
        for tid in range(8):
            for op in (22, 23): add(g=[(i, dict(STAGE0 if i == 0 else STAGEN, cop=op if i == st else 4)) for i in range(st + 1)], t=[(st, tid)] if tid else [])
    # C targets: every combination
    for r0 in range(4):
        for r1 in range(4):
            for ds in range(5):
                for z in [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]: add(rt=(r0, r1), ds=ds, z=z)
    # D device kind / lost / declaration / pixel shader, alone and with a failing state
    for ex in (0, 1):
        for lost in (0, 1):
            for decl in (0, 1):
                for ps in (0, 1):
                    for bad in (0, 1): add(ex=ex, lost=lost, decl=decl, ps=ps, s=[(2, 0, 1, 0)] if bad else [], rt=(1, 0) if bad else (0, 0))
    # E mixtures
    for _ in range(1500):
        add(ex=rnd.randint(0, 1), lost=int(rnd.random() < 0.05), decl=int(rnd.random() < 0.9), ps=int(rnd.random() < 0.3), z=(rnd.randint(0, 1), rnd.randint(0, 1), rnd.randint(0, 1)), rt=(rnd.randint(0, 3), rnd.choice((0, 0, 1, 2, 3))), ds=rnd.randint(0, 4),
            s=[(rnd.choice((0, 5, 17)), rnd.choice((1, 1, 2, 3, 0)), rnd.choice((1, 1, 2, 3)), rnd.choice((0, 0, 1, 2)))], t=[(rnd.choice((0, 5, 17)), rnd.randint(1, 7))] if rnd.random() < 0.6 else [])
    codes = {}
    for c in cases:
        e = oracle(env, c); codes[e] = codes.get(e, 0) + 1; out.append(fmt_case(c, e))
    open(os.path.join(d, "val.txt"), "w").write(f"F {env.fmt['FA']} {env.fmt['FB']}\n" + "".join(out))
    need = {HR["OK"], HR["INV"], HR["LOST"], HR["FILTER"], HR["EFAIL"], HR["COLOROP"], HR["ALPHAOP"], HR["COLORARG"], HR["ALPHAARG"], HR["WRONGFMT"], HR["CONFLICTRS"]}
    assert need <= set(codes), f"oracle never produces {sorted(need - set(codes))}"   # every reachable row is exercised
    # stats: V(c) = floor(c * R / f) mod 2^32 and V(c + f)
    R = env.refresh(); lines = []
    for f in (1000000, 10000000, 3, 1000, 19200000, 1 << 40, 1):
        for c in [0, 1, f - 1, f, f + 1, 123456789, (1 << 62), (1 << 63) - (1 << 20)] + [rnd.randint(0, 1 << 60) for _ in range(12)]:
            if c + f >= 1 << 64: continue
            lines.append(f"{f} {c} {(c * R // f) & 0xffffffff}\n{((c + f) * R // f) & 0xffffffff}\n")
    open(os.path.join(d, "stats.txt"), "w").writelines(lines)
    k = []
    for w in list(range(10)) + [0xffffffff, 0x80000000]:
        for h in list(range(10)) + [0xffffffff, 0x80000000]:
            for r in (0, 1):
                for c in (0, 1): k.append(f"{w} {h} {r} {c} {0 if (1 <= w <= 7 and 1 <= h <= 7 and not r and not c) else HR['INV']:x}\n")
    open(os.path.join(d, "kern.txt"), "w").writelines(k)
    return len(cases), codes


# ---------------------------------------------------------------- build (as tests/l6/devlife)
def fl(tmp, dev): return ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", dev, "-I", INC, "-I", tmp, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]


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
        cmds.append((n, (["gcc", "-m32", "-std=c11", "-w", "-I", BACK, "-I", NXB, "-I", FRONT] if src.endswith(".c") else fl(tmp, dev)) + ["-c", src, "-o", os.path.join(od, os.path.splitext(n)[0] + ".o")]))
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


def link(names, od, test_od, exe):
    bn = lambda d, n: os.path.join(d, os.path.splitext(n)[0] + ".o")
    r = sh(["g++", "-m32", "-no-pie", "-o", exe] + [bn(od, n) for n in names] + [bn(test_od, n) for n in TESTS] + ["-Wl,--unresolved-symbols=ignore-all"])
    return r.stderr[-800:] if r.returncode else None


def run(exe, data):
    r = sh([exe, data]); ps = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, ps


def guest(src_root, out, extra_env=None):
    """build_guest.sh over src_root/render/d3d9 -> (number of undefined symbols, set of names)"""
    env = dict(os.environ, FNVNX_TC=TC, FNVNX_SHADERS_DEPS=UAM_DEPS, JOBS="3"); r = subprocess.run(["bash", os.path.join(src_root, "render", "d3d9", "build_guest.sh"), out], capture_output=True, text=True, env=env)
    p = os.path.join(out, "undefined.tsv")
    if r.returncode or not os.path.exists(p): return None, (r.stdout + r.stderr)[-600:]
    names = {l.split("\t")[1] if l.count("\t") > 1 else l.split("\t")[0] for l in open(p).read().splitlines() if l.strip() and not l.startswith("#")}
    return len(names), names


def main():
    tmp = tempfile.mkdtemp(prefix="d9m1_"); data = os.path.join(tmp, "data"); os.makedirs(data); n, codes = write_data(data); print(f"oracle: {n} ValidateDevice cases, results {{" + ", ".join(f"{k:x}: {v}" for k, v in sorted(codes.items())) + "}")
    err = gen(tmp)
    if err: print("GEN FAILED\n" + err); return 1
    prod = sources(tmp, DEV); tsrc = {t: os.path.join(HERE, t) for t in TESTS}
    err = compile_all(tmp, DEV, {**prod, **tsrc})
    if err: print("BUILD FAILED\n" + err); return 1
    od = os.path.join(tmp, "objs"); exe = os.path.join(tmp, "test_m1"); err = link(list(prod), od, od, exe)
    if err: print("LINK FAILED\n" + err); return 1
    r, parts = run(exe, data); ls = r.stdout.split("\n"); print("\n".join(l for l in ls if l.startswith(("FAIL", "a:", "c:", "  case")))[:3500])
    for p in "abcde":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    for p in "abcde":
        if p in parts: print(f"PART {p}: {parts[p][0]} checks, {parts[p][1]} failed")
    # ---- f: gates
    sp = importlib.util.spec_from_file_location("test_dev", os.path.join(DEVT, "test_dev.py")); td = importlib.util.module_from_spec(sp); sp.loader.exec_module(td)
    objs = lambda names: [os.path.join(od, os.path.splitext(x)[0] + ".o") for x in names]
    want, miss, unres = td.gate(tmp, objs(list(prod))); before = td.gate(tmp, objs([x for x in prod if x not in M1]))
    mine = {"NxDevice9::ValidateDevice", "NxDevice9Ex::ValidateDevice", "NxDevice9Ex::SetConvolutionMonoKernel", "NxDevice9Ex::ComposeRects", "NxSwapChain9Ex::GetPresentStats"}
    print(f"PART f: G-METHOD missing before (without m1_*.cpp) {len(before[1])} -> after {len(miss)} of {len(want)}; my 5 methods missing before {len(mine & set(before[1]))} -> after {len(mine & set(miss))}")
    if not mine <= set(before[1]) or mine & set(miss): fails.append(f"f: gate rows {sorted(mine - set(before[1]))} / {sorted(mine & set(miss))}")
    if len(before[1]) - len(miss) != 5: fails.append(f"f: gate improved by {len(before[1]) - len(miss)}, expected 5")
    if "--no-guest" not in sys.argv:
        a, an = guest(ROOT, os.path.join(tmp, "guest_after"))
        if a is None: fails.append("f: guest build after failed: " + str(an)); print("guest build (after) failed:", an)
        else:   # before = the same tree without the M1 files and without their hooks (a scratch copy; the repository is never touched)
            sroot = os.path.join(tmp, "tree"); os.makedirs(os.path.join(sroot, "render")); os.symlink(os.path.join(ROOT, "shim"), os.path.join(sroot, "shim")); os.symlink(os.path.join(ROOT, "tests"), os.path.join(sroot, "tests"))
            for e in os.listdir(os.path.join(ROOT, "render")):
                if e != "d3d9": os.symlink(os.path.join(ROOT, "render", e), os.path.join(sroot, "render", e))
            shutil.copytree(D3, os.path.join(sroot, "render", "d3d9"), ignore=shutil.ignore_patterns("__pycache__")); cd = os.path.join(sroot, "render", "d3d9", "dev")
            for m in M1: os.remove(os.path.join(cd, m))
            t = open(os.path.join(cd, "dev_chain.cpp")).read().replace("presented();", "pcount++;"); open(os.path.join(cd, "dev_chain.cpp"), "w").write(t)
            t = open(os.path.join(cd, "state_model.py")).read(); t = "\n".join(l for l in t.split("\n") if 'name="convk"' not in l); open(os.path.join(cd, "state_model.py"), "w").write(t)
            b, bn = guest(sroot, os.path.join(tmp, "guest_before"))
            if b is None: fails.append("f: guest build before failed: " + str(bn)); print("guest build failed:", bn)
            else: print(f"PART f: guest undefined symbols before {b} -> after {a}; resolved {sorted(bn - an)}; new {sorted(an - bn)}")
    # ---- g: mutations
    if "--no-mutations" not in sys.argv:
        P = lambda f, old, new: (f, old, new)
        muts = [("a", *P("m1_validate.cpp", "return v == D3DTEXF_NONE || (v == D3DTEXF_POINT", "return (v == D3DTEXF_POINT")), ("a", *P("m1_validate.cpp", "(mp != D3DTEXF_NONE && mp != D3DTEXF_POINT)", "(mp != D3DTEXF_NONE)")),
                ("a", *P("m1_validate.cpp", "sel <= D3DTA_CONSTANT &&", "sel <= 7 &&")), ("a", *P("m1_validate.cpp", "(caps >> (op - 1) & 1)", "(caps >> op & 1)")),
                ("a", *P("m1_validate.cpp", "b->w != w || b->h != h || n != sm) return", "b->w != w || b->h != h) return")), ("a", *P("m1_validate.cpp", "if (!L->ex && L->state != NX_DEV_OK)", "if (L->state != NX_DEV_OK)")),
                ("a", *P("m1_validate.cpp", "if (!d->cur.vdecl[0]) return D3DERR_INVALIDCALL;  ", "if (false) return D3DERR_INVALIDCALL;  ")), ("a", *P("m1_validate.cpp", "(op >= 2 && op != 3 && op <= 26)", "(op >= 2 && op <= 26)")),
                ("a", *P("m1_validate.cpp", "i >= 16 ? c.VertexTextureFilterCaps :", "")), ("a", *P("m1_validate.cpp", "if (hr == D3D_OK) *n = 1;", "if (hr == D3D_OK) *n = 2;")),
                ("a", *P("m1_validate.cpp", "s.rs[D3DRS_ZENABLE] || s.rs[D3DRS_ZWRITEENABLE] ||", "s.rs[D3DRS_ZENABLE] ||")), ("a", *P("m1_validate.cpp", "if (ao && !op_ok(aop, c.TextureOpCaps))", "if (false)")),
                ("b", *P("m1_stats.cpp", "+ (qpc % f) * r / f", "")), ("b", *P("m1_stats.cpp", "s->PresentCount = pcount;", "s->PresentCount = pcount + 1;")), ("b", *P("m1_stats.cpp", "s->SyncQPCTime.QuadPart = (LONGLONG)c;", "s->SyncQPCTime.QuadPart = 0;")),
                ("b", *P("m1_stats.cpp", "pcount++; pres_vb = vblank(this, c, f);", "pcount++;")), ("c", *P("m1_misc.cpp", "return E_NOTIMPL; }\n", "return D3D_OK; }\n")),
                ("d", *P("m1_misc.cpp", "w > M1_KERNEL_MAX", "w > M1_KERNEL_MAX + 1")), ("d", *P("m1_misc.cpp", "put(NX_S_convk + 1, &k[1]);", "")), ("d", *P("m1_misc.cpp", "|| rows || cols)", ")")),
                ("e", *P("dev_chain.cpp", "if (im->fmt != D3DFMT_A8R8G8B8) return INV;", "if (im->fmt != D3DFMT_A8R8G8B8 && im->fmt != D3DFMT_X8R8G8B8) return INV;")), ("e", *P("dev_chain.cpp", "rotate(this); presented(); return D3D_OK;\n}", "rotate(this); return D3D_OK;\n}")),
                ("e", *P("dev_reset.cpp", "c->pp.BackBufferCount = e.count;", "c->pp.BackBufferCount = c->pp.BackBufferCount;")),
                ("d", *P("state_model.py", 'default="1", blocks="P", group="MISC"', 'default="1", blocks="V", group="MISC"'))]
        def one(i):
            part, f, old, new = muts[i]; mdev = os.path.join(tmp, f"m{i}_dev"); shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__")); txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}")); mt = os.path.join(tmp, f"m{i}"); mo = os.path.join(mt, "objs"); os.makedirs(mo, exist_ok=True)
            if f.endswith(".py"):   # a generated table changes: regenerate and recompile everything
                e1 = gen(mt, mdev)
                if e1: return i, None, e1
                e2 = compile_all(mt, mdev, {**sources(mt, mdev)}, outdir=mo)
                if e2: return i, None, e2
                e3 = link(list(prod), mo, od, os.path.join(mt, "t"))
            else:
                e2 = compile_all(tmp, mdev, {f: os.path.join(mdev, f)}, outdir=mo)
                if e2: return i, None, e2
                rr = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(mt, "t")] + [os.path.join(mo if n == f else od, os.path.splitext(n)[0] + ".o") for n in prod] + [os.path.join(od, os.path.splitext(t)[0] + ".o") for t in TESTS] + ["-Wl,--unresolved-symbols=ignore-all"])
                e3 = rr.stderr[-500:] if rr.returncode else None
            if e3: return i, None, e3
            x, ps = run(os.path.join(mt, "t"), data); shutil.rmtree(mt, ignore_errors=True); shutil.rmtree(mdev, ignore_errors=True); return i, ps, x.returncode
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
