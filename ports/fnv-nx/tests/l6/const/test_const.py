#!/usr/bin/env python3
"""test_const.py: the constant-result d3d9 bodies "as Wine 9.0" (task CONST).  python3 -I tests/l6/const/test_const.py [--no-mutations]
 a  render/d3d9/dev/const_fixme.tsv: L4 row format, ids 41.., no clash with the other fixme tables, every id has an enum value and a call site in the product sources (NX_CF_*), every row names a Wine 9.0 function
 b  behaviour through the real entries (export, validator object, both device classes, odd arguments): result equals the row's value, the exact number of fnvnx_wine_fixme lines per call is 1 with the row's id
    (kind void/u32), bodies without an id (DebugSetMute, validator AddRef / Release, BeginEvent / EndEvent: Wine logs no FIXME) log 0 lines; the event level is the real state; Wine's results by case below
 c  contract_consts.tsv: every row is named in this test's table (no row without a check), and no row's recorded value disagrees with the behaviour
 d  mutations: each one breaks one body or one id; part b must fail
Product objects = render/d3d9/dev/*.cpp minus app_unk_guest.cpp, glue_*.cpp, ffp_*.cpp + generated sources + render/d3d9/front/*.c (as tests/l6/m1), with the real fnvnx_wine_fixme of app_unk_native.cpp (one stderr line per call)."""
import concurrent.futures as cf, importlib.util, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); M1 = os.path.join(ROOT, "tests", "l6", "m1")
sp = importlib.util.spec_from_file_location("test_m1", os.path.join(M1, "test_m1.py")); tm = importlib.util.module_from_spec(sp); sp.loader.exec_module(tm)
fails = []; counts = {}
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(p, ok, msg=""):
    counts[p] = counts.get(p, 0) + 1
    if not ok: fails.append(f"{p}: {msg}"); print(f"FAIL {p}: {msg}")
    return ok

# case -> (id or None, expected result (None = any), kind). Wine 9.0 text per row: const_fixme.tsv wine_ref; no-id rows: contract_consts.tsv.
IDS = {"D3DPERF_GetStatus": 41, "D3DPERF_QueryRepeatFrame": 42, "D3DPERF_SetMarker": 43, "D3DPERF_SetOptions": 44, "D3DPERF_SetRegion": 45, "SetDialogBoxMode": 46, "NxDevice9Ex::ComposeRects": 47,
       "DrawRectPatch": 48, "DrawTriPatch": 49, "DeletePatch": 50, "RegisterSoftwareDevice": 51, "DebugSetMute": 52, "NxShaderValidator9::AddRef": 53, "NxShaderValidator9::Release": 54}
NOID = {"D3DPERF_BeginEvent", "D3DPERF_EndEvent"}
OTHER = {"Direct3DShaderValidatorCreate9": 20, "probe.GetClipStatus": 3}   # ids of other tables (x1_validator.cpp, pv_exit.cpp): a call after / next to ours still logs exactly its own line

def read_tsv(p):
    rows = []
    for l in open(p).read().split("\n"):
        if not l or l.startswith("#"): continue
        rows.append(l.split("\t"))
    return rows

def link(tmp, base_objs, tod, repl_objs):
    names = sorted(f for f in os.listdir(base_objs) if f.endswith(".o"))
    objs = [repl_objs.get(f, os.path.join(base_objs, f)) for f in names] + [os.path.join(tod, "t_env.o"), os.path.join(tod, "t_const.o")]
    exe = os.path.join(tmp, "t_const"); r = sh(["g++", "-m32", "-no-pie", "-o", exe] + objs + ["-Wl,--unresolved-symbols=ignore-all"])
    return (exe, "") if r.returncode == 0 else (None, r.stderr[-800:])

def run(exe):
    r = sh([exe]); cases = []   # [(case, n, id list)]
    ids = {}; cur = None
    for l in r.stderr.split("\n"):
        m = re.match(r"@@ (\S+) (\d+)$", l)
        if m: cur = (m.group(1), int(m.group(2))); ids[cur] = []; continue
        m = re.match(r"fnvnx-wine-fixme: (\d+)$", l)
        if m and cur: ids[cur].append(int(m.group(1)))
    res = {}
    for l in r.stdout.split("\n"):
        m = re.match(r"R (\S+) (\d+) ([0-9a-f]{8})", l)
        if m: res[(m.group(1), int(m.group(2)))] = int(m.group(3), 16)
    return r, ids, res

def judge(ids, res, rows, quiet=False):
    """returns the list of failures of part b"""
    f = []; byid = {int(r[0]): r for r in rows}
    perf_levels = {}; seen = set()
    for (case, n), got in sorted(ids.items(), key=lambda x: x[0][1]):
        base = case.split(".")[0]   # "<name>.<class tag>" for a body called on both device classes
        seen.add(base)
        if case.startswith("probe."): base = "probe.GetClipStatus"   # (the case name has a dot of its own)
        r = res.get((case, n))
        if r is None: f.append(f"{case}#{n}: no result line"); continue
        if base in IDS:
            row = byid.get(IDS[base])
            if not row: f.append(f"{case}: no row {IDS[base]}"); continue
            want_ids = [IDS[base]]
            if row[3] == "u32" and r != int(row[4], 0): f.append(f"{case}#{n}: result {r:#x}, row {row[4]}")
            if row[3] == "void" and r != 0: f.append(f"{case}#{n}: void body result harness {r:#x}")
        elif base in OTHER: want_ids = [OTHER[base]]
        elif base in NOID:
            want_ids = []
        else: f.append(f"{case}#{n}: unknown case"); continue
        if ids[(case, n)] != want_ids: f.append(f"{case}#{n}: fixme lines {ids[(case, n)]}, want {want_ids}")
    ev = [res[k] for k in sorted(res, key=lambda k: k[1]) if k[0] in ("D3DPERF_BeginEvent", "D3DPERF_EndEvent")]
    if ev != [0, 1, 1, 0]: f.append(f"event levels {ev}, want [0, 1, 1, 0] (Begin returns the level before; End the level after)")
    need = set(IDS) | NOID | {"Direct3DShaderValidatorCreate9"}
    for c in sorted(need - seen): f.append(f"case {c} never ran")
    cnt = {}
    for (case, n) in ids: cnt[case.split(".")[0]] = cnt.get(case.split(".")[0], 0) + 1
    exp = {"D3DPERF_GetStatus": 3, "D3DPERF_QueryRepeatFrame": 3, "D3DPERF_SetMarker": 3, "D3DPERF_SetOptions": 3, "D3DPERF_SetRegion": 3, "SetDialogBoxMode": 12, "NxDevice9Ex::ComposeRects": 4, "DebugSetMute": 2, "NxShaderValidator9::AddRef": 3, "NxShaderValidator9::Release": 3,
           "DrawRectPatch": 4, "DrawTriPatch": 4, "DeletePatch": 4, "RegisterSoftwareDevice": 4}
    for k, v in exp.items():
        if cnt.get(k) != v: f.append(f"calls of {k}: {cnt.get(k)}, want {v}")
    return f

def main():
    tmp = tempfile.mkdtemp(prefix="d9const_")
    # ---- a
    rows = read_tsv(os.path.join(DEV, "const_fixme.tsv"))
    check("a", len(rows) == 7 and [int(r[0]) for r in rows] == list(range(41, 55)), f"ids {[r[0] for r in rows]}")
    other_ids = set()
    for f in ("pvw_fixme.tsv", "d5a_fixme.tsv"): other_ids |= {int(r[0]) for r in read_tsv(os.path.join(DEV, f)) if r[0].isdigit()}
    other_ids |= {2, 3, 20, 22, 23, 24}   # verify/wine_fixme.tsv rows of x1_validator.cpp / pv_exit.cpp
    check("a", not other_ids & {int(r[0]) for r in rows}, "id clash")
    hdr = open(os.path.join(DEV, "const_fixme.h")).read(); src = "".join(open(os.path.join(DEV, f)).read() for f in os.listdir(DEV) if f.endswith(".cpp"))
    for r in rows:
        check("a", len(r) == 5, str(r))
        check("a", bool(re.fullmatch(r"wine-9\.0:dlls/(d3d9|wined3d)/[a-z0-9_]+\.c:[A-Za-z_][A-Za-z0-9_]*", r[2])), r[2])
        check("a", (r[3] == "void" and r[4] == "") or (r[3] == "u32" and re.fullmatch(r"(0x[0-9a-fA-F]+|[0-9]+)", r[4]) is not None), str(r))
        name = {"D3DPERF_GetStatus": "NX_CF_D3DPERF_GetStatus", "D3DPERF_QueryRepeatFrame": "NX_CF_D3DPERF_QueryRepeatFrame", "D3DPERF_SetMarker": "NX_CF_D3DPERF_SetMarker", "D3DPERF_SetOptions": "NX_CF_D3DPERF_SetOptions",
                "D3DPERF_SetRegion": "NX_CF_D3DPERF_SetRegion", "IDirect3DDevice9::SetDialogBoxMode": "NX_CF_SetDialogBoxMode", "IDirect3DDevice9Ex::ComposeRects": "NX_CF_ComposeRects",
                "IDirect3DDevice9::DrawRectPatch": "NX_CF_DrawRectPatch", "IDirect3DDevice9::DrawTriPatch": "NX_CF_DrawTriPatch", "IDirect3DDevice9::DeletePatch": "NX_CF_DeletePatch",
                "IDirect3D9::RegisterSoftwareDevice": "NX_CF_RegisterSoftwareDevice", "DebugSetMute": "NX_CF_DebugSetMute", "IDirect3DShaderValidator9::AddRef": "NX_CF_ShaderValidatorAddRef",
                "IDirect3DShaderValidator9::Release": "NX_CF_ShaderValidatorRelease"}[r[1]]
        check("a", re.search(name + r" = " + r[0] + r"\b", hdr) is not None, f"enum {name} = {r[0]}")
        check("a", len(re.findall(r"(?:fnvnx_wine_fixme|NX_WINE_FIXME_VOID)\(" + name + r"\)", src)) >= 1, f"call site of {name}")
    # Wine function text exists in the downloaded tree when it is there (optional evidence, never a decision)
    wine = os.environ.get("FNVNX_WINE_SRC", "/home/user/work/l6/wine")
    wf = {"d3d9/d3d9_main.c": "d3d9_d3d9_main.c", "wined3d/device.c": "wined3d_device.c", "d3d9/device.c": "d3d9_device.c"}
    if os.path.isdir(wine):
        for r in rows:
            m = re.fullmatch(r"wine-9\.0:dlls/(.+):(\w+)", r[2]); p = os.path.join(wine, wf.get(m.group(1), "-"))
            if os.path.exists(p):
                t = open(p).read(); i = t.find(m.group(2) + "("); j = t.find("\n}\n", i)
                body = t[i:j] if i >= 0 else ""
                check("a", i >= 0 and "return" in body or r[3] == "void", f"Wine function {m.group(2)} not found")
                if r[1].endswith("ComposeRects"): check("a", "E_NOTIMPL" in body and "FIXME" in body, "Wine ComposeRects text")
                if r[1].startswith("D3DPERF"): check("a", "FIXME" in body, "Wine " + m.group(2) + " has no FIXME")
    # ---- build + b
    prod_err = tm.gen(tmp, DEV)
    if prod_err: print("GEN FAILED\n" + prod_err); return 1
    prod = tm.sources(tmp, DEV); err = tm.compile_all(tmp, DEV, prod)
    if err: print("BUILD FAILED\n" + err); return 1
    od = os.path.join(tmp, "objs"); tod = os.path.join(tmp, "tobjs"); os.makedirs(tod)
    fl = tm.fl(tmp, DEV)
    for n, s, extra in (("t_env", os.path.join(M1, "t_env.cpp"), ["-Dmain=m1_main"]), ("t_const", os.path.join(HERE, "t_const.cpp"), [])):
        r = sh(fl + extra + ["-c", s, "-o", os.path.join(tod, n + ".o")])
        if r.returncode: print("BUILD FAILED " + n + "\n" + r.stderr[-1500:]); return 1
    exe, err = link(tmp, od, tod, {})
    if not exe: print("LINK FAILED\n" + err); return 1
    r, ids, res = run(exe); print("driver:", r.stdout.strip().split("\n")[-1], "| cases", len(ids))
    check("b", r.returncode == 0 and "DONE" in r.stdout, "driver rc %d %s" % (r.returncode, r.stdout[-300:]))
    f = judge(ids, res, rows)
    for x in f[:20]: print("FAIL b:", x)
    counts["b"] = counts.get("b", 0) + len(ids) * 2; fails.extend("b: " + x for x in f)
    # ---- c
    cc = [l.split("\t") for l in open(os.path.join(DEV, "contract_consts.tsv")).read().split("\n") if l]
    known = {"D3DPERF_GetStatus", "D3DPERF_QueryRepeatFrame", "D3DPERF_SetMarker", "D3DPERF_SetOptions", "D3DPERF_SetRegion", "NxDevice9::SetDialogBoxMode", "NxDevice9Ex::SetDialogBoxMode", "NxDevice9Ex::ComposeRects",
             "DebugSetMute", "NxShaderValidator9::AddRef", "NxShaderValidator9::Release"}
    check("c", {c[0] for c in cc} == known, f"contract_consts rows {sorted({c[0] for c in cc} ^ known)} are not covered by this test")
    for c in cc:
        if c[1] == "u32" and c[0].endswith("ComposeRects"): check("c", int(c[2]) == 0x80004001, "ComposeRects row value")
        if c[0] in ("D3DPERF_GetStatus", "D3DPERF_QueryRepeatFrame", "NxDevice9::SetDialogBoxMode"): check("c", c[2] == "0", c[0])
        if c[0].startswith(("D3DPERF_Get", "D3DPERF_Query", "D3DPERF_Set", "NxDevice9")) and not c[0].endswith("ComposeRects"): check("c", "fnvnx_wine_fixme" in c[3], c[0] + " row does not name its fixme id")
    # ---- d mutations: (name, file in dev, old, new, consumer files to recompile)
    H = "const_fixme.h"; EX = ["d3d9_exports.cpp", "dev_misc.cpp", "m1_misc.cpp", "copy_misc.cpp", "d3d9_adapter.cpp", "x1_validator.cpp"]
    muts = [("GetStatus no fixme", "d3d9_exports.cpp", "{ fnvnx_wine_fixme(NX_CF_D3DPERF_GetStatus); return 0; }", "{ return 0; }"),
            ("GetStatus result", "d3d9_exports.cpp", "fnvnx_wine_fixme(NX_CF_D3DPERF_GetStatus); return 0;", "fnvnx_wine_fixme(NX_CF_D3DPERF_GetStatus); return 1;"),
            ("QueryRepeatFrame result", "d3d9_exports.cpp", "NX_CF_D3DPERF_QueryRepeatFrame); return FALSE;", "NX_CF_D3DPERF_QueryRepeatFrame); return TRUE;"),
            ("SetMarker two lines", "d3d9_exports.cpp", "{ NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetMarker); }", "{ NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetMarker); NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetMarker); }"),
            ("SetOptions wrong id", "d3d9_exports.cpp", "NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetOptions)", "NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetRegion)"),
            ("SetRegion no fixme", "d3d9_exports.cpp", "{ NX_WINE_FIXME_VOID(NX_CF_D3DPERF_SetRegion); }", "{ }"),
            ("SetDialogBoxMode result", "dev_misc.cpp", "fnvnx_wine_fixme(NX_CF_SetDialogBoxMode); return D3D_OK;", "fnvnx_wine_fixme(NX_CF_SetDialogBoxMode); return D3DERR_INVALIDCALL;"),
            ("SetDialogBoxMode conditional line", "dev_misc.cpp", "C::SetDialogBoxMode(BOOL) { fnvnx_wine_fixme(NX_CF_SetDialogBoxMode); return D3D_OK; }", "C::SetDialogBoxMode(BOOL b) { if (!b) fnvnx_wine_fixme(NX_CF_SetDialogBoxMode); return D3D_OK; }"),
            ("ComposeRects old result", "m1_misc.cpp", "fnvnx_wine_fixme(NX_CF_ComposeRects); return E_NOTIMPL;", "fnvnx_wine_fixme(NX_CF_ComposeRects); return D3DERR_INVALIDCALL;"),
            ("ComposeRects no fixme", "m1_misc.cpp", "{ fnvnx_wine_fixme(NX_CF_ComposeRects); return E_NOTIMPL; }", "{ return E_NOTIMPL; }"),
            ("id value in header", H, "NX_CF_ComposeRects = 47", "NX_CF_ComposeRects = 46"),
            ("Release result", "x1_validator.cpp", "fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Release); return 1;", "fnvnx_wine_fixme(NX_WINE_FIXME_ShaderValidator_Release); return 0;"),
            ("DrawRectPatch result", "copy_misc.cpp", "fnvnx_wine_fixme(NX_CF_DrawRectPatch); return D3D_OK;", "fnvnx_wine_fixme(NX_CF_DrawRectPatch); return D3DERR_INVALIDCALL;"),
            ("DeletePatch no fixme", "copy_misc.cpp", "fnvnx_wine_fixme(NX_CF_DeletePatch); return D3DERR_INVALIDCALL;", "return D3DERR_INVALIDCALL;"),
            ("RegisterSoftwareDevice no fixme", "d3d9_adapter.cpp", "{ fnvnx_wine_fixme(NX_CF_RegisterSoftwareDevice); return D3D_OK; }", "{ return D3D_OK; }"),
            ("EndEvent level", "d3d9_exports.cpp", "fetch_sub(1, std::memory_order_relaxed) - 1", "fetch_sub(1, std::memory_order_relaxed)")]
    if "--no-mutations" not in sys.argv:
        def one(i):
            n, fn, old, new = muts[i]; d = os.path.join(tmp, f"m{i}"); shutil.copytree(DEV, os.path.join(d, "dev"), ignore=shutil.ignore_patterns("__pycache__")); mdev = os.path.join(d, "dev")
            f = os.path.join(mdev, fn); t = open(f).read()
            open(f, "w").write(mutsrc.apply(t, old, new, f"{fn} #{i}")); repl = {}; os.makedirs(os.path.join(d, "o"))
            for c in (EX if fn == H else [fn]):
                o = os.path.join(d, "o", c[:-4] + ".o"); r = sh(tm.fl(tmp, mdev) + ["-c", os.path.join(mdev, c), "-o", o])
                if r.returncode: return i, "build"
                repl[c[:-4] + ".o"] = o
            x, e = link(d, od, tod, repl)
            if not x: return i, "link"
            rr, ii, rs = run(x); return i, bool(judge(ii, rs, rows)) or rr.returncode != 0
        with cf.ThreadPoolExecutor(3) as pool:
            for i, res_ in pool.map(one, range(len(muts))):
                ok = res_ is True; print(f"mutation {i} ({muts[i][0]}): {'caught' if ok else 'NOT CAUGHT ' + str(res_)}")
                check("d", ok, f"mutation {muts[i][0]} {res_}")
    for p in sorted(counts): print(f"PART {p}: {counts[p]} checks, {sum(1 for f_ in fails if f_.startswith(p + ':'))} failed")
    shutil.rmtree(tmp, ignore_errors=True); print("FAILED: " + "; ".join(fails) if fails else "ALL OK"); return 1 if fails else 0
if __name__ == "__main__": sys.exit(main())
