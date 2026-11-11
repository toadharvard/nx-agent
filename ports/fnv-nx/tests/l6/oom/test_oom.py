#!/usr/bin/env python3
"""test_oom.py: allocation failure is a result, never a trap (render/d3d9/dev; README "Allocation failure").  python3 -I tests/l6/oom/test_oom.py [--no-mutations] [--no-guest]
 a  the scenario without failures (t_oom.cpp, g++ -m32 build of the layer with a fault-injecting nxd3d_alloc / nxd3d_gpu_alloc and a fake backend whose creations are allocation events too)
 b  one-shot sweep: for every allocation event k = 1..N of the scenario, event k fails: the step that met it returns a documented failure HRESULT, device state / refcounts / registry are unchanged, the allocation balance is zero
 c  sticky sweep: every event from k on fails (cleanup under repeated failure)
 d  the compiler-derived completeness gates: seeded defects of every call shape present in the code must be caught (gcc -fanalyzer + -Werror=unused-result / deprecated, and the clang-AST rule allocscan.py), the real tree must be clean
 e  gate T over the guest objects (build_guest.sh): no reference to a noreturn routine of the toolchain (set derived from the toolchain headers by clang's AST), no brk / udf instruction
 f  mutations: one null check removed in a copy of the tree -> the sweep must fail
Every product file of render/d3d9/dev and render/d3d9/front is scanned and gated, except ffp_*.cpp / m1_*.cpp (other workers, in progress: scanned, findings listed as OTHER, not counted; ffp_* is not in the native build)."""
import concurrent.futures as cf, glob, json, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import allocscan  # noqa: E402
D3 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D3, "dev"); GEN = os.path.join(D3, "gen"); ENV = os.path.join(D3, "env"); SPEC = os.path.join(D3, "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); FRONT = os.path.join(D3, "front"); BACK = os.path.join(D3, "..", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend"))
ALL_DEV = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(DEV, "*.cpp")))
NOT_YET = [n for n in ALL_DEV if n.startswith(("ffp_", "m1_"))]   # other workers, in progress: scanned and listed (OTHER), not gated, not mutated
OWNED = [n for n in ALL_DEV if n not in NOT_YET]
NATIVE = [n for n in ALL_DEV if n not in ("obj", "app_unk_guest", "d3d9_exports") and not n.startswith("ffp_") and (not n.startswith("m1_") or n == "m1_stats")]   # m1_stats defines NxChainCore::presented: linked (the rest of m1_ is in progress and overlaps copy_misc)
FRONT_C = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(FRONT, "front_*.c")))
GENS = ["d9impl_unk", "state", "env_table", "d9bridge"]
CXX = ["-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti"]
fails = []; checks = {}; nfail = {}
TC = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def part(p, ok, what):
    checks[p] = checks.get(p, 0) + 1
    if not ok:
        nfail[p] = nfail.get(p, 0) + 1; fails.append(f"{p}: {what}"); print(f"FAIL {p}: {what}")
    return ok


def generate(tmp):
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(DEV, "state_model.py"), tmp, SPEC],
                [sys.executable, "-I", os.path.join(DEV, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(D3, "spec", "d3d9_tables.py")],
                [sys.executable, "-I", os.path.join(ENV, "gen_env.py"), tmp], [sys.executable, "-I", os.path.join(GEN, "gen_bridge.py"), tmp, SPEC]):
        r = sh(cmd)
        if r.returncode: raise SystemExit("generator: " + " ".join(cmd[-3:]) + "\n" + r.stderr[-600:])


def obj_cpp(text):
    """obj.cpp of the native test build: the platform allocator (the two definitions) is the harness's (t_oom.cpp), so it can inject faults"""
    lines = text.split("\n")
    out = [l for l in lines if not (l.startswith("void* nxd3d_alloc(size_t n)") or l.startswith("void nxd3d_free(void* p)"))]
    assert len(out) == len(lines) - 2, "obj.cpp: the allocator definitions moved"
    return "\n".join(out)


def flags(gen): return CXX + ["-O1", "-w", "-I", INC, "-I", gen, "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT, "-I", HERE]


def build(tmp, jobs=3):
    gen = os.path.join(tmp, "gen"); generate(gen); os.makedirs(os.path.join(tmp, "o"), exist_ok=True)
    jl = [("g++", flags(gen), os.path.join(DEV, n + ".cpp"), n) for n in NATIVE] + [("g++", flags(gen), os.path.join(gen, n + ".gen.cpp"), n) for n in GENS] + [("g++", flags(gen), os.path.join(HERE, "t_oom.cpp"), "t_oom")]
    xo = os.path.join(tmp, "obj_x.cpp"); open(xo, "w").write(obj_cpp(open(os.path.join(DEV, "obj.cpp")).read())); jl.append(("g++", flags(gen), xo, "obj"))
    for f in sorted(glob.glob(os.path.join(FRONT, "front_*.c"))): jl.append(("gcc", ["-m32", "-std=c11", "-O1", "-w", "-I", FRONT, "-I", BACK, "-I", NXB], f, "nx_" + os.path.basename(f)))
    cmds = [[cc] + fl + ["-c", src, "-o", os.path.join(tmp, "o", n + ".o")] for cc, fl, src, n in jl]
    with cf.ThreadPoolExecutor(jobs) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: raise SystemExit("compile: " + c[-3] + "\n" + r.stderr[-1500:])
    return {n: os.path.join(tmp, "o", n + ".o") for _, _, _, n in jl}, gen


def link(objs, out):
    r = sh(["g++", "-m32", "-no-pie", "-o", out] + list(objs.values()) + ["-Wl,--unresolved-symbols=ignore-all"])   # device lifecycle / draw files are other workers': nothing here calls them
    return r.returncode == 0, r.stderr[-500:]


def run(exe, mode=None, timeout=900):
    r = subprocess.run([exe] + ([mode] if mode else []), capture_output=True, text=True, timeout=timeout)
    parts = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    last = [l for l in r.stderr.split("\n") if l.startswith("@")]
    return r, parts, (last[-1] if last else "")


# ---------------------------------------------------------------------------------------------------- d: analyzers
GCC_FLAGS = ["-fanalyzer", "-Wanalyzer-possible-null-dereference", "-Wanalyzer-possible-null-argument", "-Wanalyzer-null-dereference", "-Wanalyzer-null-argument", "-Wunused-result", "-Wdeprecated-declarations",
             "-Werror=analyzer-possible-null-dereference", "-Werror=analyzer-possible-null-argument", "-Werror=analyzer-null-dereference", "-Werror=analyzer-null-argument", "-Werror=unused-result", "-Werror=deprecated-declarations",
             "-Wno-analyzer-malloc-leak", "-Wno-analyzer-mismatching-deallocation", "-Wno-analyzer-deref-before-check", "-Wno-analyzer-use-of-uninitialized-value", "-Wno-analyzer-out-of-bounds", "-Wno-analyzer-allocation-size"]
CATS = ("analyzer-possible-null-dereference", "analyzer-possible-null-argument", "analyzer-null-dereference", "analyzer-null-argument", "unused-result", "deprecated-declarations")
SEED = """#include "d9impl.gen.h"
#include "res_tex.h"
#include "glue_stream.h"
#include <string.h>
struct SeedLife : NxDevLifeBase { int ex; };
void seed_plain(size_t n) { uint8_t* p = (uint8_t*)nxd3d_alloc(n); p[0] = 1; nxd3d_free(p); }   // SEED plain alloc, dereference
void seed_memset(size_t n) { uint8_t* p = (uint8_t*)nxd3d_alloc(n); memset(p, 0, n); nxd3d_free(p); }   // SEED plain alloc, memset argument
void seed_make() { NxVertexBuffer9* b = nx_make<NxVertexBuffer9>(); b->length = 1; }   // SEED nx_make object, base-class field
void seed_make_member(NxObj* dev) { NxVertexBuffer9* b = nx_make<NxVertexBuffer9>(); b->asChild(dev, 0); }   // SEED nx_make object, member call
void seed_make_plain() { NxImg* i = nx_make_plain<NxImg>(); i->kind = 1; }   // SEED nx_make_plain record
void seed_make_life() { SeedLife* l = nx_make_plain<SeedLife>(); l->ex = 1; }   // SEED nx_make_plain, polymorphic lifecycle state
void seed_sparse(NxSparse<NxLightEnt>& s) { NxLightEnt* e = s.add(3); e->idx = 1; }   // SEED NxSparse array growth: add()
void seed_reserve(NxSparse<NxLightEnt>& s) { s.reserve(4); }   // SEED NxSparse reserve() result dropped
void seed_table(NxObj* k) { NxObj** t = (NxObj**)nxd3d_alloc(16); t[0] = k; nxd3d_free(t); }   // SEED pointer table (kids), element store
void seed_gpu() { void* g = nxd3d_gpu_alloc(4096); memset(g, 0, 4096); nxd3d_gpu_free(g); }   // SEED backend memory
void seed_unused() { nxd3d_alloc(8); }   // SEED discarded allocator result
void seed_unused_make() { nx_make<NxVertexBuffer9>(); }   // SEED discarded nx_make result
void seed_unused_member(NxImg* i) { i->upload(); }   // SEED discarded bool result of a fallible member
void seed_new_expr() { NxVertexBuffer9* b = new NxVertexBuffer9; if (b) b->length = 1; }   // SEED new expression of a layer class
void seed_ok_plain(size_t n) { uint8_t* p = (uint8_t*)nxd3d_alloc(n); if (!p) return; p[0] = 1; nxd3d_free(p); }   // CONTROL
void seed_ok_make(NxObj* dev) { NxVertexBuffer9* b = nx_make<NxVertexBuffer9>(); if (!b) return; b->asChild(dev, 0); b->length = 1; }   // CONTROL
void seed_ok_sparse(NxSparse<NxLightEnt>& s) { NxLightEnt* e = s.add(3); if (!e) return; e->idx = 1; if (!s.reserve(2)) return; }   // CONTROL
void seed_ok_gpu() { void* g = nxd3d_gpu_alloc(4096); if (!g) return; memset(g, 0, 4096); nxd3d_gpu_free(g); }   // CONTROL
void seed_ok_image(NxImg* i) { if (!i->upload()) return; }   // CONTROL
"""


def gcc_findings(src, gen, c=False):
    r = sh((["gcc", "-m32", "-std=gnu11"] if c else ["g++"] + CXX) + ["-O0", "-I", INC, "-I", gen, "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT] + GCC_FLAGS + ["-c", src, "-o", os.devnull])
    out = []
    for l in r.stderr.split("\n"):
        m = re.match(r"^(\S+?):(\d+):(\d+): (?:warning|error): (.*?) \[-W(?:error=)?([a-z-]+)", l)
        if m and m.group(5) in CATS: out.append((m.group(1), int(m.group(2)), m.group(5), m.group(4)))
        else:
            m = re.match(r"^(\S+?):(\d+):(\d+): (?:warning|error): (.*deprecated.*)", l)
            if m: out.append((m.group(1), int(m.group(2)), "deprecated-declarations", m.group(4)))
    return out


def ast_args(gen, c=False): return ["--target=i386-linux-gnu"] + (["-std=gnu11"] if c else ["-std=c++17", "-fno-exceptions", "-fno-rtti"]) + ["-I", INC, "-I", gen, "-I", DEV, "-I", BACK, "-I", NXB, "-I", FRONT]


def analyzers(tmp, gen):
    clang = shutil.which("clang++") or "clang++"
    # ---- seeded defects of every call shape of the code
    sd = os.path.join(tmp, "seed"); os.makedirs(sd, exist_ok=True); src = os.path.join(sd, "seed.cpp"); open(src, "w").write(SEED)
    lines = SEED.split("\n"); seeds = {i + 1: l.split("// SEED ")[1] for i, l in enumerate(lines) if "// SEED " in l}; controls = [i + 1 for i, l in enumerate(lines) if "// CONTROL" in l]
    g = gcc_findings(src, gen); gl = {f[1] for f in g if f[0].endswith("seed.cpp")}
    is_main = lambda f: f is not None and (f.endswith("seed.cpp") or f.startswith(DEV) or f.startswith(gen) or f.startswith(FRONT))
    res = allocscan.scan(clang, ast_args(gen), src, is_main)
    part("d", res is not None, "clang AST of the seed file")
    al = {f[1] for f in res[1] if f[0].endswith("seed.cpp")} if res else set()
    gcc_caught = sorted(l for l in seeds if l in gl); ast_caught = sorted(l for l in seeds if l in al); union = sorted(set(gcc_caught) | set(ast_caught))
    print(f"seeded defects ({len(seeds)} shapes, {len(controls)} correct controls): gcc -fanalyzer + -Werror flags catch {len(gcc_caught)}/{len(seeds)}, clang-AST rule (allocscan.py) catches {len(ast_caught)}/{len(seeds)}, together {len(union)}/{len(seeds)}")
    print("  missed by gcc: " + ", ".join(seeds[l] for l in seeds if l not in gl))
    for l in seeds: part("d", l in set(union), f"seed not caught by any tool: {seeds[l]} (seed.cpp:{l})")
    for l in controls: part("d", l not in gl and l not in al, f"false positive on correct control at seed.cpp:{l}")
    # ---- the real tree: owned files must be clean, other workers' files are listed
    owned = [os.path.join(DEV, n + ".cpp") for n in OWNED] + [os.path.join(gen, n + ".gen.cpp") for n in GENS]
    others = [os.path.join(DEV, n + ".cpp") for n in NOT_YET]; fronts = [os.path.join(FRONT, n + ".c") for n in FRONT_C]; owned += fronts
    def one(p):
        c = p.endswith(".c")
        return p, gcc_findings(p, gen, c), allocscan.scan(shutil.which("clang") if c else clang, ast_args(gen, c), p, is_main)
    with cf.ThreadPoolExecutor(3) as ex: rs = list(ex.map(one, owned + others))
    sites = 0; per = {}; hdr = set(); nreal = 0
    for p, gf, sc in rs:
        mine = p in owned; name = os.path.basename(p)
        if sc is None: part("d", mine and False, f"AST of {name}") if mine else print(f"OTHER {name}: AST not available"); continue
        s_sites, s_find = sc; uniq = {(a, b) for a, b, c in s_sites if a == p}; per[name] = len(uniq); sites += len(uniq) if mine else 0
        hdr |= {(os.path.basename(a), b) for a, b, c in s_sites if a.startswith(DEV) and a.endswith(".h")}
        fl = sorted({(os.path.basename(a), b, c) for a, b, c in s_find} | {(os.path.basename(a), b, "%s: %s" % (c, d[:80])) for a, b, c, d in gf})
        if mine: nreal += len(fl); part("d", not fl, f"{name}: {fl[:4]}")
        else:
            for f in fl: print(f"OTHER {f[0]}:{f[1]}: {f[2]}")
    print(f"allocation-function call sites (compiler AST, owned files): {sites} in .cpp files, {len(hdr)} in headers; per file: " + ", ".join(f"{k}={v}" for k, v in sorted(per.items()) if v and k.replace('.cpp', '').replace('.gen', '') in OWNED + GENS))
    print(f"real tree: owned translation units scanned {len(owned)}, findings {nreal}; other workers' units scanned {len(others)}")


# ---------------------------------------------------------------------------------------------------- e: gate T
def probe_noreturn(tmp):
    G = os.path.join(TC, "lib", "gcc", "aarch64-none-elf"); ver = sorted(os.listdir(G))[0]; I = os.path.join(TC, "aarch64-none-elf", "include"); G = os.path.join(G, ver)
    p = os.path.join(tmp, "probe.cpp")
    open(p, "w").write("".join(f"#include <{h}>\n" for h in ["stdlib.h", "assert.h", "setjmp.h", "signal.h", "string.h", "stdio.h", "unistd.h", "pthread.h", "exception", "new", "typeinfo", "stdexcept", "cxxabi.h", "system_error", "functional", "unwind.h"]))
    r = sh(["clang++", "--target=aarch64-none-elf", "-fsyntax-only", "-nostdinc", "-nostdinc++", "-isystem", os.path.join(I, "c++", ver), "-isystem", os.path.join(I, "c++", ver, "aarch64-none-elf"), "-isystem", os.path.join(I, "c++", ver, "backward"),
            "-isystem", os.path.join(G, "include"), "-isystem", os.path.join(G, "include-fixed"), "-isystem", I, "-std=c++17", "-Xclang", "-ast-dump=json", p])
    try: d = json.loads(r.stdout)
    except Exception: return None
    names = set()
    def walk(n):
        if isinstance(n, dict):
            if n.get("kind") in ("FunctionDecl", "CXXMethodDecl") and ("noreturn" in (n.get("type") or {}).get("qualType", "") or any("NoReturn" in (i.get("kind") or "") for i in n.get("inner", []) if isinstance(i, dict))):
                names.add(n.get("mangledName") or n.get("name"))
            for v in n.get("inner", []) if isinstance(n.get("inner"), list) else []: walk(v)
    walk(d); return names


def terminators(objdir, noret):
    nm = os.path.join(TC, "bin", "aarch64-none-elf-nm"); od = os.path.join(TC, "bin", "aarch64-none-elf-objdump"); refs = {}; traps = []
    for o in sorted(glob.glob(os.path.join(objdir, "*.o"))):
        for l in sh([nm, "-u", o]).stdout.split("\n"):
            if l.strip().startswith("U ") and l.split()[1] in noret: refs.setdefault(os.path.basename(o), set()).add(l.split()[1])
        for l in sh([od, "-dr", o]).stdout.split("\n"):
            m = re.match(r"^\s+[0-9a-f]+:\s+R_AARCH64_\w+\s+(\S+?)(?:\+0x[0-9a-f]+)?$", l)
            if m and m.group(1) in noret: refs.setdefault(os.path.basename(o), set()).add(m.group(1))
            if re.match(r"^\s+[0-9a-f]+:\s+[0-9a-f]{8}\s+(brk|udf)\b", l): traps.append((os.path.basename(o), l.strip()))
    return refs, traps


def guest_build(tmp, gen):
    """the guest objects of every product source except ffp_*.cpp (other worker; build_guest.sh needs their uam step at the moment), compiled with build_guest.sh's flags (ARCH + COMMON, -std=c++17 -fno-exceptions -fno-rtti)"""
    gxx = os.path.join(TC, "bin", "aarch64-none-elf-g++"); gcc = os.path.join(TC, "bin", "aarch64-none-elf-gcc"); od = os.path.join(tmp, "guest", "obj"); os.makedirs(od, exist_ok=True)
    arch = ["-mabi=ilp32", "-march=armv8-a+crc+crypto", "-mtune=cortex-a57", "-fno-pie", "-ftls-model=local-exec", "-fsigned-char"]
    common = arch + ["-O2", "-g", "-ffunction-sections", "-fdata-sections", "-frounding-math", "-fshort-wchar", "-Wall"]
    inc = ["-I" + INC, "-I" + gen, "-I" + DEV, "-I" + FRONT, "-I" + BACK, "-I" + NXB]; jobs = []
    for n in ALL_DEV:
        if n in ("app_unk_native",) or n.startswith("ffp_"): continue
        jobs.append(([gxx] + common + ["-std=c++17", "-fno-exceptions", "-fno-rtti", "-DSTDMETHODCALLTYPE="] + inc + ["-c", os.path.join(DEV, n + ".cpp"), "-o", os.path.join(od, "render_d3d9_dev_" + n + ".cpp.o")]))
    for n in GENS: jobs.append(([gxx] + common + ["-std=c++17", "-fno-exceptions", "-fno-rtti", "-DSTDMETHODCALLTYPE="] + inc + ["-c", os.path.join(gen, n + ".gen.cpp"), "-o", os.path.join(od, "gen_" + n + ".gen.cpp.o")]))
    for n in FRONT_C: jobs.append(([gcc] + common + ["-std=gnu11", "-DSTDMETHODCALLTYPE="] + inc + ["-c", os.path.join(FRONT, n + ".c"), "-o", os.path.join(od, "render_d3d9_front_" + n + ".c.o")]))
    with cf.ThreadPoolExecutor(3) as ex: rs = list(ex.map(sh, jobs))
    bad = [r.stderr[-300:] for r in rs if r.returncode]
    return os.path.join(tmp, "guest"), bad


def guest_gate(tmp, gen):
    if not os.path.isdir(TC): print("PART e skipped: no toolchain at", TC); return
    out, bad = guest_build(tmp, gen)
    if not part("e", not bad, "guest compile: " + (bad[0] if bad else "")): return
    noret = probe_noreturn(tmp)
    if not part("e", bool(noret) and "abort" in noret and "__cxa_pure_virtual" in noret and "__assert_func" in noret, "noreturn set derived from the toolchain headers"): return
    refs, traps = terminators(os.path.join(out, "obj"), noret)
    def owned(o):   # object name = source path with / -> _ : render_d3d9_dev_<file>.cpp.o, gen_<file>.o, render_d3d9_front_<file>.c.o
        m = re.match(r"^render_d3d9_dev_(.*)\.cpp\.o$", o); return not m or m.group(1) in OWNED
    mine_refs = {k: v for k, v in refs.items() if owned(k)}; mine_traps = [t for t in traps if owned(t[0])]
    n_obj = len(glob.glob(os.path.join(out, "obj", "*.o")))
    print(f"gate T: noreturn routines declared by the toolchain headers (clang AST): {len(noret)}; guest objects scanned: {n_obj}; owned objects: references {sum(len(v) for v in mine_refs.values())}, brk/udf instructions {len(mine_traps)}")
    for k, v in refs.items():
        if not owned(k): print(f"OTHER {k}: references {sorted(v)}")
    for t in traps:
        if not owned(t[0]): print(f"OTHER {t[0]}: trap instruction {t[1].split(chr(9))[-2:]}")
    part("e", not mine_refs, f"references to terminating routines: {mine_refs}"); part("e", not mine_traps, f"trap instructions: {mine_traps[:3]}")


# ---------------------------------------------------------------------------------------------------- f: mutations
MUTS = [   # (file under dev/, old, new): one null check removed (or its result ignored); the sweep must fail
    ("res_buf.cpp", "nx_make<NxVertexBuffer9>(); if (!b) return D3DERR_OUTOFVIDEOMEMORY;", "nx_make<NxVertexBuffer9>();"),
    ("res_shader.cpp", "if (!code) { nx_destroy(s); return E_OUTOFMEMORY; }", ""),
    ("state_ext.cpp", "NxLightEnt* e = lights.add(index); if (!e) return E_OUTOFMEMORY;", "NxLightEnt* e = lights.add(index);"),
    ("state_rt.cpp", "if (!dst.reserve(st.lights.n)) return E_OUTOFMEMORY;", "nx_ignore(dst.reserve(st.lights.n));"),
    ("res_create.cpp", "if (!k) { to->release(); return E_OUTOFMEMORY; }", "if (!k) { return E_OUTOFMEMORY; }"),
    ("res_tex.cpp", "raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15); if (!raw) return E_OUTOFMEMORY;", "raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15);"),
    ("obj.cpp", "NxPriv* e = (NxPriv*)nxd3d_alloc(sizeof(NxPriv)); if (!e) return E_OUTOFMEMORY;", "NxPriv* e = (NxPriv*)nxd3d_alloc(sizeof(NxPriv));"),
    ("d3d9_adapter.cpp", "if (FAILED(o->setPrivateData(G_NXENV_PROFILE, &p, sizeof p, 0))) { o->release(); return D3DERR_NOTAVAILABLE; }", "nx_ignore(o->setPrivateData(G_NXENV_PROFILE, &p, sizeof p, 0));"),
    ("glue_buf.cpp", "void* gmem = nxd3d_gpu_alloc(gsize); if (!gmem) return 0;", "void* gmem = nxd3d_gpu_alloc(gsize);"),
    ("copy_dc.cpp", "im->raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15); if (!im->raw) return false;", "im->raw = (uint8_t*)nxd3d_alloc((size_t)tot + 15);"),
    ("res_decl.cpp", "nx_make<NxVertexDeclaration9>(); if (!d) return E_OUTOFMEMORY;", "nx_make<NxVertexDeclaration9>();"),
    ("glue_image.cpp", "if (!nxs_reserve_readback(bytes, cb)) return 0;", "nx_ignore(nxs_reserve_readback(bytes, cb));"),
    ("res_query.cpp", "nx_make<NxQuery9>(); if (!q) return E_OUTOFMEMORY;", "nx_make<NxQuery9>();"),
    ("state_ext.cpp", "if (!e) { static_cast<NxObj*>(b)->release(); return E_OUTOFMEMORY; }", "if (!e) { return E_OUTOFMEMORY; }"),
    ("res_tex.cpp", "if (!src || !dst) { nxd3d_free(src); nxd3d_free(dst); return false; }", "if (!src || !dst) { return false; }"),
    ("dev_create.cpp", "C* dev = nx_make<C>(); if (!dev) return E_OUTOFMEMORY;", "C* dev = nx_make<C>();"),
    ("dev_chain.cpp", "NxChainCore** n = (NxChainCore**)nxd3d_alloc(sizeof(void*) * (nchains + 1)); if (!n) return false;", "NxChainCore** n = (NxChainCore**)nxd3d_alloc(sizeof(void*) * (nchains + 1));"),
    ("dev_chain.cpp", "if (hr != D3D_OK) { for (uint32_t k = 0; k < made; k++) { nb[k]->owned = 0; nb[k]->ci->destroy(nb[k]); } nxd3d_free(nb); return hr; }", "if (hr != D3D_OK) { nxd3d_free(nb); return hr; }"),
    ("dev_misc.cpp", "if (!px) { bool w0; nx_img_unlock(im, 0, 0, &w0); return E_OUTOFMEMORY; }", "if (!px) { bool w0; nx_img_unlock(im, 0, 0, &w0); }"),
    ("dev_create.cpp", "if (hr != D3D_OK) { o->owned = 0; o->ci->destroy(o); return hr; }", "if (hr != D3D_OK) { return hr; }"),
]


def mutations(tmp, objs, gen):
    jobs = []
    for i, (f, old, new) in enumerate(MUTS):
        txt = open(os.path.join(DEV, f)).read()
        if not part("f", txt.count(old) == 1, f"mutation pattern not unique in {f}: {old[:50]}"): continue
        mt = txt.replace(old, new, 1)
        if f == "obj.cpp": mt = obj_cpp(mt)
        d = os.path.join(tmp, f"m{i}"); os.makedirs(d, exist_ok=True); src = os.path.join(d, f); open(src, "w").write(mt); jobs.append((i, f, src, d))
    def one(j):
        i, f, src, d = j; o = os.path.join(d, "x.o"); r = sh(["g++"] + flags(gen) + ["-c", src, "-o", o])
        if r.returncode: return i, f, "compile error: " + r.stderr[-200:]
        key = "obj" if f == "obj.cpp" else f[:-4]; ob = dict(objs); ob[key] = o; exe = os.path.join(d, "t"); ok, err = link(ob, exe)
        if not ok: return i, f, "link error"
        try: r, parts, last = run(exe, "one", 300)
        except subprocess.TimeoutExpired: return i, f, "killed: timeout (counts as detected: a hang is a failure)"
        if r.returncode == 0: return i, f, "NOT DETECTED"
        return i, f, "detected: " + ("crash %d at %s" % (r.returncode, last) if r.returncode < 0 else "%d failed checks" % sum(v[1] for v in parts.values()))
    with cf.ThreadPoolExecutor(2) as ex: res = list(ex.map(one, jobs))
    n = 0
    for i, f, msg in res:
        ok = msg.startswith("detected") or msg.startswith("killed"); n += ok
        part("f", ok, f"mutation {i} ({f}): {msg}"); print(f"  mutation {i:2d} {f:18s} {msg}")
    print(f"mutations: {n}/{len(MUTS)} detected")


def main():
    tmp = tempfile.mkdtemp(prefix="oom_")
    try:
        objs, gen = build(tmp)
        ok, err = link(objs, os.path.join(tmp, "t_oom"))
        if not ok: print("LINK FAILED\n" + err); return 1
        r, parts, last = run(os.path.join(tmp, "t_oom"))
        print(r.stdout[-3000:]); sys.stdout.flush()
        if "--keep" in sys.argv: shutil.copy(os.path.join(tmp, "t_oom"), "/tmp/t_oom_keep"); print("kept /tmp/t_oom_keep")
        for p in "abc":
            if p not in parts: part(p, False, f"part {p} missing: exit {r.returncode}, last sentinel {last or 'none'} (a crash ends the sweep)"); continue
            checks[p] = parts[p][0]; nfail[p] = parts[p][1]
            if parts[p][1] or parts[p][0] == 0: fails.append(f"{p}: {parts[p]}")
        if r.returncode != 0 and not fails: fails.append(f"t_oom exit {r.returncode}")
        analyzers(tmp, gen)
        if "--no-guest" not in sys.argv: guest_gate(tmp, gen)
        if "--no-mutations" not in sys.argv: mutations(tmp, objs, gen)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for p in sorted(checks): print(f"PART {p}: {checks[p]} checks, {nfail.get(p, 0)} failed")
    print("ALL OK" if not fails else f"FAILED: {len(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
