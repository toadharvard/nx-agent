#!/usr/bin/env python3
"""test_x1.py: X1 = DebugSetMute + Direct3DShaderValidatorCreate9 + IDirect3DShaderValidator9, exactly Wine 9.0 d3d9_main.c (+ one fixme line per stub body).  python3 -I tests/l6/x1/test_x1.py [--no-mutations]
 a  exports / table / bridge rows: 514 rows, the 8 new ones exact (6 methods of the interface, 2 exports); the table: interface 6 slots, iid null, signatures; spec / bridge / exports / wrap / cmp tests green
 b  behaviour: Create = the one static object + log id 20; Begin / Instruction / End = S_OK, ids 22 / 23 / 24, arguments not looked at; DebugSetMute log id 52
 c  the callback of Begin is never called   d  IUnknown lattice: AddRef 2 / Release 1, QueryInterface E_NOINTERFACE (no log line) for every iid, layer-private IID_NxObj
 e  no allocation: the object file references no allocator (nm)   f  guest build: defined nxb 514/514, undefined set unchanged   g  mutations"""
import json, os, re, shutil, subprocess, sys, tempfile, concurrent.futures as cf
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D9 = os.path.join(ROOT, "render", "d3d9"); DEV = os.path.join(D9, "dev"); GEN = os.path.join(D9, "gen"); SPEC = os.path.join(D9, "spec", "d3d9_spec.json"); INC = os.path.join(ROOT, "shim", "include")
PY = [sys.executable, "-I"]; fails = []; counts = {}
TC = os.environ.get("FNVNX_TC") or "/home/user/work/l6/toolchain/toolchain-1fc1f7b1ef44/toolchain/prefix"
DEPS = os.environ.get("FNVNX_SHADERS_DEPS") or "/home/user/work/l6/s2a/build/deps"
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(p, ok, msg=""):
    counts[p] = counts.get(p, 0) + 1
    if not ok: fails.append(f"{p}: {msg}"); print(f"FAIL {p}: {msg}")
    return ok

def build(dev, tmp):
    os.makedirs(tmp, exist_ok=True)
    for cmd in (PY + [os.path.join(GEN, "gen_impl.py"), tmp, SPEC], PY + [os.path.join(dev, "state_model.py"), tmp, SPEC],
                PY + [os.path.join(dev, "res_smgen.py"), tmp, os.path.join(ROOT, "render", "shaders", "total", "sm_table.json"), os.path.join(D9, "spec", "d3d9_tables.py")], PY + [os.path.join(D9, "env", "gen_env.py"), tmp]):
        r = sh(cmd)
        if r.returncode: return None, r.stderr[-600:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", INC, "-I", tmp, "-I", dev]
    srcs = [os.path.join(dev, "obj.cpp"), os.path.join(dev, "x1_validator.cpp"), os.path.join(tmp, "d9impl_unk.gen.cpp"), os.path.join(HERE, "t_x1.cpp"), os.path.join(dev, "d3d9_exports.cpp")]
    r = sh(fl + srcs + ["-o", os.path.join(tmp, "t_x1"), "-Wl,--unresolved-symbols=ignore-all"])
    if r.returncode: return None, r.stderr[-1500:]
    r = sh(fl + ["-c", os.path.join(dev, "x1_validator.cpp"), "-o", os.path.join(tmp, "x1.o")])
    return os.path.join(tmp, "t_x1"), ""

def run(exe):
    r = sh([exe]); return r, {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}

def main():
    tmp = tempfile.mkdtemp(prefix="d9x1_"); spec = json.load(open(SPEC))
    # ---- a
    I = spec["interfaces"].get("IDirect3DShaderValidator9"); check("a", I == {"base": "IUnknown", "iid": None, "slots": 6}, str(I))
    ms = sorted((m for m in spec["methods"] if m["iface"] == "IDirect3DShaderValidator9"), key=lambda m: m["slot"])
    check("a", [m["name"] for m in ms] == ["QueryInterface", "AddRef", "Release", "Begin", "Instruction", "End"], "methods")
    ex = {e["name"]: e for e in spec["exports"]}
    check("a", "undocumented" not in ex["DebugSetMute"] and ex["DebugSetMute"]["params"] == [] and ex["DebugSetMute"]["ret_kind"] == "void", "DebugSetMute")
    check("a", ex["Direct3DShaderValidatorCreate9"]["params"] == [] and ex["Direct3DShaderValidatorCreate9"]["ret_kind"] == "handle" and ex["Direct3DShaderValidatorCreate9"]["iface"] == "IDirect3DShaderValidator9", "Create")
    r = sh(PY + [os.path.join(GEN, "gen_bridge.py"), tmp, SPEC]); check("a", r.returncode == 0, r.stderr[-300:])
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(tmp, "d9bridge_rows.tsv"))]
    new = [r for r in rows if r[0] == "!IDirect3DShaderValidator9" or r[1] in ("DebugSetMute", "Direct3DShaderValidatorCreate9")]
    want = [["!IDirect3DShaderValidator9", n, str(i), "stdcall", rv, a, "nxb_IDirect3DShaderValidator9_" + n, "sym"] for i, (n, rv, a) in enumerate(
        [("QueryInterface", "i", "iii"), ("AddRef", "i", "i"), ("Release", "i", "i"), ("Begin", "i", "iiii"), ("Instruction", "i", "iiiii"), ("End", "i", "i")])] + \
        [["d3d9.dll", "DebugSetMute", "", "stdcall", "v", "", "nxb_DebugSetMute", "sym"], ["d3d9.dll", "Direct3DShaderValidatorCreate9", "", "stdcall", "i", "", "nxb_Direct3DShaderValidatorCreate9", "sym"]]
    check("a", len(rows) == 514, f"rows {len(rows)}"); check("a", sorted(new) == sorted(want), f"new rows {new}")
    sub = [("spec", "test_spec.py"), ("bridge", "bridge/test_bridge.py"), ("exports", "exports/test_exports.py"), ("wrap", "wrap/test_wrap.py"), ("cmp", "cmp/test_cmp.py")]
    with cf.ThreadPoolExecutor(2) as pool: rs = list(pool.map(lambda t: sh(PY + [os.path.join(ROOT, "tests", "l6", t[1])]), sub))
    for (n, _), r in zip(sub, rs): check("a", r.returncode == 0, f"{n} test red: {r.stdout[-300:]}")
    # ---- b c d
    exe, err = build(DEV, os.path.join(tmp, "n"))
    if not exe: print("BUILD FAILED\n" + err); return 1
    r, parts = run(exe); print(r.stdout[-800:], end="")
    for p in "bcd":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    # ---- e
    und = sh(["nm", "-u", os.path.join(tmp, "n", "x1.o")]).stdout
    check("e", not re.search(r"nxd3d_alloc|malloc|_Znw|_Zna|calloc|nx_make|nx_alloc", und), und)
    # ---- g mutations
    muts = [("fixme Create", "fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidatorCreate9);", ""), ("QI foreign iid", "*out = nullptr; return E_NOINTERFACE;", "*out = nullptr; return S_OK;"),
            ("AddRef", "NX_WINE_FIXME_ShaderValidator_AddRef); return 2; }", "NX_WINE_FIXME_ShaderValidator_AddRef); return 1; }"),
            ("Release", "NX_WINE_FIXME_ShaderValidator_Release); return 1; }", "NX_WINE_FIXME_ShaderValidator_Release); return 0; }"),
            ("QI IUnknown", "*out = nullptr; return E_NOINTERFACE;", "*out = nullptr; return E_NOINTERFACE;" .replace("E_NOINTERFACE;", "riid.Data1 == 0 ? S_OK : E_NOINTERFACE;")),
            ("Begin result", "fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Begin); return S_OK;", "fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Begin); return E_FAIL;"),
            ("Instruction result", "NX_WINE_FIXME_ShaderValidator_Instruction); return S_OK;", "NX_WINE_FIXME_ShaderValidator_Instruction); return E_FAIL;"),
            ("End id", "ShaderValidator_End = 24", "ShaderValidator_End = 23"), ("object behind face", "return &g_mem.obj;", "return (IDirect3DShaderValidator9*)&g_mem;"),
            ("QI out null", "*out = nullptr; return E_NOINTERFACE;", "return E_NOINTERFACE;")]
    if "--no-mutations" not in sys.argv:
        def one(i):
            n, old, new_ = muts[i]; d = os.path.join(tmp, f"m{i}"); shutil.copytree(DEV, os.path.join(d, "dev"), ignore=shutil.ignore_patterns("__pycache__"))
            f = os.path.join(d, "dev", "x1_validator.cpp"); t = open(f).read()
            open(f, "w").write(mutsrc.apply(t, old, new_, f"x1_validator.cpp #{i}")); x, e = build(os.path.join(d, "dev"), os.path.join(d, "b"))
            if not x: return i, "build"
            r, ps = run(x); return i, (r.returncode != 0 or any(v[1] for v in ps.values()) or not all(p in ps for p in "bcd"))
        with cf.ThreadPoolExecutor(3) as pool:
            for i, res in pool.map(one, range(len(muts))):
                ok = res is True or res == "build"; print(f"mutation {i} ({muts[i][0]}): {'caught' if ok else 'NOT CAUGHT ' + str(res)}")
                check("g", ok, f"mutation {muts[i][0]} {res}")
    # ---- f guest build
    if os.path.isdir(TC):
        out = os.path.join(tmp, "guest"); r = sh(["bash", os.path.join(D9, "build_guest.sh"), out], env={**os.environ, "FNVNX_TC": TC, "FNVNX_SHADERS_DEPS": DEPS})
        print(r.stdout[-500:])
        m = re.search(r"defined nxb: (\d+)/(\d+) expected, (\d+) unexpected, (\d+) missing", r.stdout)
        check("f", r.returncode == 0 and m and m.group(1) == m.group(2) == "514" and m.group(3) == m.group(4) == "0", (r.stderr or r.stdout)[-400:])
        if r.returncode == 0:
            u = open(os.path.join(out, "undefined.tsv")).read(); check("f", "NxShaderValidator9" not in u and "fnvnx_wine_fixme" in u or "fnvnx_wine_fixme" not in u, "undefined")
            print("undefined fixme/validator lines:", [l.split("\t")[0] for l in u.split("\n") if "Validator" in l or "wine_fixme" in l])
    else: check("f", False, "no toolchain")
    for p in sorted(set(counts) | set(parts)): print(f"PART {p}: {counts.get(p, 0) + parts.get(p, (0, 0))[0]} checks, {sum(1 for f in fails if f.startswith(p + ':'))} failed")
    shutil.rmtree(tmp, ignore_errors=True); print("FAILED: " + "; ".join(fails) if fails else "ALL OK"); return 1 if fails else 0
if __name__ == "__main__": sys.exit(main())
