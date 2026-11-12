#!/usr/bin/env python3
"""test_env.py: tests of render/d3d9/env + render/d3d9/dev/d3d9_adapter.  python3 -I tests/l6/env/test_env.py [--no-mutations]
 a  REPLAY EQUALITY: our NxDirect3D9 / NxDirect3D9Ex, built g++ -m32 against the generated tables, answers the SAME query list as envprobe2 (both include envrun.h + envq.gen.h, one source
    envq.py) in each profile; every row must equal the row recorded under Wine (env_<profile>.tsv). Rows compared as text: method, canonical argument tuple, return value, out values.
    (Comparison of the answer TSVs, not of two D9S1 streams: our CreateDevice does not exist yet, so the journaling wrapper has no device to journal; make_env.py cross-checks the Wine side
    against the recorder's stream.)
 b  closing rules: every out-of-domain rule has rows (counted), all equal on both sides.   c  gate G-METHOD of tests/l6/dev/test_dev.py with the adapter objects added: missing before -> after
 d  mutation: one table cell / one rule constant broken in a copy of the generated tables -> (a) must fail.   e  ENV_PIN sha256 of the tsv files; the two profiles' tables.
exit 0 iff all pass."""
import concurrent.futures as cf, hashlib, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ENV = os.path.join(ROOT, "render", "d3d9", "env"); DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); INC = os.path.join(ROOT, "shim", "include")
SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
sys.path[:0] = [ENV, HERE, os.path.join(ROOT, "tests", "l6", "dev")]
import gen_env, envq  # noqa: E402
PROFILES = ["handheld", "docked"]; fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(ok, what):
    if not ok: fails.append(what); print("FAIL", what)


def build(tmp, genpy=None, tables_edit=None, tag="host"):
    """generate, compile and link hostenv; returns exe path or None"""
    g = os.path.join(tmp, "gen"); os.makedirs(g, exist_ok=True)
    for cmd in ([sys.executable, "-I", genpy or os.path.join(ENV, "gen_env.py"), g], [sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), g, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), g], [sys.executable, "-I", os.path.join(DEV, "state_model.py"), g, SPEC]):   # state.gen.h: the device classes of d9impl.gen.h need it
        r = sh(cmd)
        if r.returncode: print(r.stdout[-300:], r.stderr[-1500:]); return None
    open(g + "/envq.gen.h", "w").write(envq.emit()); open(g + "/envfmt.gen.h", "w").write(envq.emit_fmt())
    if tables_edit: p = g + "/env_table.gen.cpp"; t = open(p).read(); t2 = tables_edit(t); assert t2 != t, "mutation did not change the tables"; open(p, "w").write(t2)
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O1", "-w", "-I", INC, "-I", g, "-I", DEV, "-I", HERE]
    srcs = [(DEV + "/obj.cpp", "obj"), (os.path.join(ROOT, "tests", "l6", "native") + "/app_unk_native.cpp", "appunk"), (g + "/d9impl_unk.gen.cpp", "unk"), (DEV + "/d3d9_adapter.cpp", "adapter"), (g + "/env_table.gen.cpp", "table"), (HERE + "/hostenv.cpp", "host")]
    cmds = [fl + ["-c", s, "-o", f"{tmp}/{tag}_{n}.o"] for s, n in srcs]
    with cf.ThreadPoolExecutor(5) as ex: rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: print(" ".join(c[-3:]), "\n", r.stderr[-2000:]); return None
    objs = [f"{tmp}/{tag}_{n}.o" for _, n in srcs]
    r = sh(["g++", "-m32", "-no-pie", "-o", f"{tmp}/{tag}.exe"] + objs + ["-Wl,--unresolved-symbols=ignore-all"])   # the other classes' methods (device, resources) are later tasks
    if r.returncode: print(r.stderr[-1500:]); return None
    return f"{tmp}/{tag}.exe"


def rows(path): return [l.rstrip("\n") for l in open(path) if not l.startswith("#")]


def key(l): return l.split("\t", 1)[0]


def tags(name, a):
    """closing rules a query exercises (the out-of-domain argument classes of README.md)"""
    t = []; fm = lambda f: gen_env.cls(f)
    if name not in ("GetAdapterCount", "Direct3DCreate9", "Direct3DCreate9Ex") and a and a[0] != 0: t.append("adapter ordinal out of range")
    fargs = {"CheckDeviceType": (2, 3), "CheckDeviceFormat": (2, 5), "CheckDepthStencilMatch": (2, 3, 4), "CheckDeviceMultiSampleType": (2,), "CheckDeviceFormatConversion": (2, 3), "GetAdapterModeCount": (1,), "EnumAdapterModes": (1,),
             "GetAdapterModeCountEx": (1,), "EnumAdapterModesEx": (1,)}.get(name, ())
    for i in fargs:
        c = fm(a[i])
        if c == gen_env.UNK0: t.append("format outside the table: D3DFMT_UNKNOWN / unmapped value (wined3d UNKNOWN id)")
        if c == gen_env.UNKF: t.append("format outside the table: printable FOURCC unknown to wined3d")
    if name in ("CheckDeviceType", "CheckDeviceFormat", "CheckDepthStencilMatch", "CheckDeviceMultiSampleType", "CheckDeviceFormatConversion", "GetDeviceCaps") and a[1] not in (1, 2, 3, 4): t.append("device type outside D3DDEVTYPE")
    if name == "CheckDeviceFormat":
        if a[2] not in (22, 23, 24): t.append("adapter format outside the three display formats")
        if a[4] not in (1, 2, 3, 4, 5, 6, 7): t.append("resource type outside D3DRESOURCETYPE")
        if a[3] & ~0x103ffff3: t.append("usage bits masked / rejected by the usage word rule")
        if a[3] and a[3] & (a[3] - 1) and a[3] & 0x103ffff3 == a[3]: t.append("usage bit combination (conjunction rule)")
    if name == "CheckDeviceMultiSampleType" and a[4] > 16: t.append("multisample type > 16")
    if name == "CheckDeviceMultiSampleType" and a[4] == 1: t.append("multisample type NONMASKABLE (quality levels out)")
    if name == "CheckDeviceType" and a[2] not in (22, 23, 24, 35): t.append("display format outside the four display formats")
    if name in ("EnumAdapterModes", "EnumAdapterModesEx") and a[-1] >= 1: t.append("mode index >= mode count")
    if name in ("GetAdapterModeCount", "EnumAdapterModes", "GetAdapterModeCountEx", "EnumAdapterModesEx") and a[1] not in (22, 23): t.append("mode format other than X8R8G8B8 / R5G6B5")
    if name in ("GetAdapterModeCountEx", "EnumAdapterModesEx") and a[2] not in (0, 1): t.append("scan-line filter that matches no mode")
    if name == "GetAdapterDisplayModeEx" and a[1] != 24: t.append("D3DDISPLAYMODEEX.Size != 24")
    if name == "GetAdapterIdentifier" and a[1] & 2: t.append("WHQL flag")
    if name in ("Direct3DCreate9", "Direct3DCreate9Ex"): t.append("SDK version (any value accepted)")
    return t


def replay(exe, prof, tmp):
    out = f"{tmp}/{os.path.basename(exe)}_{prof}.tsv"; r = sh([exe, out], env=dict(os.environ, NXENV_PROFILE=prof)); wine = rows(f"{ENV}/env_{prof}.tsv")
    if r.returncode: return None
    host = rows(out); wq = [l for l in wine if not l.startswith("DEV.")]; ndev = len(wine) - len(wq)
    diff = [(a, b) for a, b in zip(wq, host) if a != b]; return dict(queries=len(host), wine_queries=len(wq), equal=len(host) - len(diff), diff=diff, wine_only=ndev, host=host, wine=wq)


def main():
    tmp = tempfile.mkdtemp(prefix="d9env_"); exe = build(tmp)
    if not exe: print("BUILD FAILED"); return 1
    res = {}
    for p in PROFILES:
        r = replay(exe, p, tmp); check(r is not None, f"a: hostenv {p} crashed")
        if r is None: continue
        res[p] = r; check(r["queries"] == r["wine_queries"] and not r["diff"], f"a: {p}: {len(r['diff'])} different, {r['queries']} vs {r['wine_queries']} rows; first: {r['diff'][:2]}")
        print(f"(a) {p}: queries {r['queries']}, equal {r['equal']}, different {len(r['diff'])}; Wine-only rows (device phase, not replayable yet): {r['wine_only']}")
    # b: closing rules
    tally = {}
    for p, r in res.items():
        for w, h in zip(r["wine"], r["host"]):
            m = re.match(r"(\w+)\((.*)\)", key(w)); a = tuple(int(x, 16) for x in m.group(2).split(",")) if m.group(2) else ()
            for t in tags(m.group(1), a): c = tally.setdefault(t, [0, 0]); c[0] += 1; c[1] += (w == h)
    for t, (n, e) in sorted(tally.items()): print(f"(b) rule: {t}: {n} rows, equal {e}"); check(n > 0 and n == e, f"b: rule '{t}' {e}/{n}")
    check(len(tally) >= 17, f"b: {len(tally)} rules exercised")
    # c: gate
    import test_dev
    t2 = os.path.join(tmp, "gate"); exe2, prod = test_dev.build(test_dev.DEV, t2, jobs=8)
    if exe2:
        want, miss0, _ = test_dev.gate(t2, prod); fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", INC, "-I", t2, "-I", DEV]
        sh(["python3", "-I", os.path.join(ENV, "gen_env.py"), t2]); mine = []
        for s, n in ((DEV + "/d3d9_adapter.cpp", "adapter"), (t2 + "/env_table.gen.cpp", "table")):
            r = sh(fl + ["-c", s, "-o", f"{t2}/{n}.o"]); check(r.returncode == 0, "c: " + r.stderr[-300:]); mine.append(f"{t2}/{n}.o")
        _, miss1, unres = test_dev.gate(t2, prod + mine); per = {}
        for m in miss0: per.setdefault(m.split("::")[0], [0, 0])[0] += 1
        for m in miss1: per.setdefault(m.split("::")[0], [0, 0])[1] += 1
        print(f"(c) G-METHOD: methods in spec {len(want)}, missing {len(miss0)} -> {len(miss1)}; IDirect3D9 {per.get('NxDirect3D9', [0, 0])[0]} -> {per.get('NxDirect3D9', [0, 0])[1]}, IDirect3D9Ex {per.get('NxDirect3D9Ex', [0, 0])[0]} -> {per.get('NxDirect3D9Ex', [0, 0])[1]}")
        print("    still missing:", sorted(m for m in miss1 if m.startswith(("NxDirect3D9::", "NxDirect3D9Ex::"))))
        check(per.get("NxDirect3D9", [0, 9])[1] <= 2 and per.get("NxDirect3D9Ex", [0, 9])[1] <= 3, "c: IDirect3D9 / Ex missing counts")
    else: check(False, "c: test_dev build failed")
    # d: mutations
    if "--no-mutations" not in sys.argv:
        def cdt(t): m = re.search(r"static const uint8_t T_CDT_A\[(\d+)\] = \{([^}]*)\}", t); v = m.group(2).split(","); i = v.index("0"); v[i] = "1"; return t[:m.start(2)] + ",".join(v) + t[m.end(2):]
        def cdf(t): m = re.search(r"T_CDF_A\[\d+\] = \{([^}]*)\}", t); v = m.group(1).split(","); j = next(i for i in range(len(v)) if int(v[i]) & 1); v[j] = str(int(v[j]) ^ 4); return t[:m.start(1)] + ",".join(v) + t[m.end(1):]
        muts = [("one CheckDeviceType cell", cdt), ("one CheckDeviceFormat atom bit", cdf), ("usage mask 0x103ffff0 -> 0x103ffff8", lambda t: mutsrc.apply(t, "0x103ffff0u", "0x103ffff8u", "env_rules tables")),
                ("format class rule: printable test", lambda t: mutsrc.apply(t, "b > 0x7eu", "b > 0x7du", "env_rules tables")), ("MSAA type bound 16 -> 17", lambda t: mutsrc.apply(t, "t > 16u", "t > 17u", "env_rules tables")),
                ("WHQL flag bit 2 -> 4", lambda t: mutsrc.apply(t, "flags & 2u", "flags & 4u", "env_rules tables")), ("monitor of out-of-range adapter", lambda t: mutsrc.apply(t, "in_range(p, ad) ? sc(p, SC_MON) : 0", "sc(p, SC_MON)", "env_rules tables"))]
        def one(i):
            d = os.path.join(tmp, f"m{i}"); os.makedirs(d); e = build(d, tables_edit=muts[i][1], tag="mut")
            if not e: return i, None
            return i, replay(e, "handheld", d)
        with cf.ThreadPoolExecutor(3) as ex:
            for i, r in ex.map(one, range(len(muts))):
                nd = None if r is None else len(r["diff"]); print(f"(d) mutation '{muts[i][0]}': {'caught, ' + str(nd) + ' rows differ' if nd else 'NOT CAUGHT'}"); check(bool(nd), f"d: mutation {i} not caught")
    # e: pin
    pin = open(f"{ENV}/ENV_PIN.txt").read()
    for p in PROFILES:
        s = hashlib.sha256(open(f"{ENV}/env_{p}.tsv", "rb").read()).hexdigest(); check(s in pin, f"e: ENV_PIN sha256 of env_{p}.tsv")
    shutil.rmtree(tmp, ignore_errors=True); print("FAILED: " + "; ".join(fails) if fails else "ALL OK"); return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
