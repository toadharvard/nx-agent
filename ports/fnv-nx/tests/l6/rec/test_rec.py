#!/usr/bin/env python3
"""test_rec.py: the Windows-side recorder (render/tools/rec) under Wine. (a) build + export list; (b) nxd9play on synthetic captures with and
without the proxy: exit codes, PNG neutrality, stream decodes fully, call histogram vs the NXD9 capture, two-run determinism after masking
env/pix items (differences listed in $W/nondet.tsv); (d) caps dump -> wine_llvmpipe_env.tsv.
env: D9REC_WORK (work dir, default /home/user/work/l6/rec), D9REC_HOST (render/shaders host tools, default /home/user/work/l6/sm/host),
     D9REC_N (captures to run, default all).  Exit 0 iff every check passed."""
import collections, hashlib, io, json, os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(HERE, "..", "wrap"), HERE, os.path.join(ROOT, "tools", "compare")]
import d9s1_read as R, dump_env, replay, nxd9io  # noqa: E402
W = os.environ.get("D9REC_WORK", "/home/user/work/l6/rec"); HOST = os.environ.get("D9REC_HOST", "/home/user/work/l6/sm/host")
SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json"); spec = json.load(open(SPEC))
fails = []; npass = 0


def check(ok, what):
    global npass
    if ok: npass += 1
    else: fails.append(what); print("FAIL", what)


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---- (a) build, exports
os.makedirs(W, exist_ok=True)
r = sh([os.path.join(ROOT, "render/tools/rec/build.sh"), W + "/rec"]); check(r.returncode == 0, "(a) build.sh: " + r.stderr[-400:])
dll = W + "/rec/d3d9.dll"
exp = []
if os.path.isfile(dll):
    t = sh(["i686-w64-mingw32-objdump", "-p", dll]).stdout.split("[Ordinal/Name Pointer] Table")[-1]
    exp = [l.split("]")[1].strip() for l in t.splitlines() if l.strip().startswith("[") and "]" in l]
want = sorted(e["name"] for e in spec["exports"])
check(len(want) == 14 and sorted(exp) == want, f"(a) exports {len(exp)} == spec's {len(want)}: {sorted(set(want) ^ set(exp))}")
print(f"(a) build {'ok' if not fails else 'FAILED'}; exports {len(exp)}/{len(want)}")
if fails: sys.exit(1)

# ---- (b) captures, nxd9play with / without the proxy
r = sh([os.path.join(ROOT, "tools/compare/d3d9/build.sh"), W + "/cmp"]); check(r.returncode == 0, "(b) tools/compare build.sh: " + r.stderr[-300:])
shutil.rmtree(W + "/caps", ignore_errors=True)
r = sh([sys.executable, "-I", os.path.join(ROOT, "tools/compare/synth.py"), "build", "--asm", HOST + "/s6b_asm", "--sdp2dksh", HOST + "/sdp2dksh", "--out", W + "/caps"])
check(r.returncode == 0, "(b) synth.py build: " + r.stderr[-300:])
caps = sorted(f for f in os.listdir(W + "/caps") if f.endswith(".nxd9"))[:int(os.environ.get("D9REC_N", "999"))]
check(len(caps) >= 3, f"(b) >= 3 synthetic captures ({len(caps)})")
for d in ("plain", "proxy"):
    shutil.rmtree(f"{W}/{d}", ignore_errors=True); os.makedirs(f"{W}/{d}"); shutil.copy(W + "/cmp/nxd9play.exe", f"{W}/{d}/")
shutil.copy(dll, W + "/proxy/d3d9.dll")
pfx = W + "/pfx"; lock = replay.prefix_lock(pfx); x = replay.Xvfb(); env = replay.wine_env(pfx, x.display)
env["LP_NUM_THREADS"] = "1"
if not os.path.isfile(pfx + "/system.reg"):
    sh(["wineboot", "-i"], env=env, timeout=900); sh(["wineserver", "-w"], env=env, timeout=900)
penv = dict(env, WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"] + ";d3d9=n,b")


def run(d, cap, tag, proxy):
    o = f"{W}/runs/{tag}/{cap[:-5]}"; shutil.rmtree(o, ignore_errors=True); os.makedirs(o)
    e = dict(penv, D9REC_OUT=replay.wine_path(o + ".d9s")) if proxy else env
    for _ in range(2):   # a signal exit at Wine teardown is retried once, as replay.py does (seen: -9 on a first run)
        p = sh(["wine", f"{W}/{d}/nxd9play.exe", replay.wine_path(f"{W}/caps/{cap}"), "-o", replay.wine_path(o)], env=e, timeout=600)
        if p.returncode >= 0: break
        print(f"   {cap}: signal exit {p.returncode}, retried")
    return p.returncode, o, p.stderr[-300:]


def pngs(o): return {f: hashlib.sha256(open(f"{o}/{f}", "rb").read()).hexdigest() for f in sorted(os.listdir(o)) if f.endswith(".png")}


def masked(recs):   # record skeletons with env/pix items reduced to (label, cls, rec)
    return [(r.seq, r.thread, r.iface, r.slot, r.this, r.depth, r.new, r.retired, [(l, c, rc, v if c in (None, 0) else None) for (l, c, rc, v) in r.items]) for r in recs]


def rec_spans(d):
    q, o = [], 44
    while o < len(d): n = int.from_bytes(d[o:o + 4], "little"); q.append((o, n)); o += n
    return q


nondet = collections.Counter(); bytes_diff = 0; tot = {"bytes": 0, "recs": 0, "frames": 0}; first_stream = None
try:
    for cap in caps:
        n = cap[:-5]; rc0, o0, e0 = run("plain", cap, "plain", False); rc1, o1, e1 = run("proxy", cap, "p1", True); rc2, o2, e2 = run("proxy", cap, "p2", True)
        check(rc0 == 0 and rc1 == 0 and rc2 == 0, f"(b) {n}: exit codes {rc0} {rc1} {rc2} {e1}")
        a, b, c = pngs(o0), pngs(o1), pngs(o2)
        check(bool(a) and a == b == c, f"(b) {n}: PNGs identical with/without proxy ({len(a)} frames)")
        d1, d2 = (open(f"{o}.d9s", "rb").read() if os.path.isfile(f"{o}.d9s") else b"" for o in (o1, o2))
        try:
            h1, r1 = R.read_stream(d1, spec); h2, r2 = R.read_stream(d2, spec); ok = True
        except (R.ReadError, KeyError, IndexError, ValueError) as ex:
            ok = False; print("   decode error:", n, repr(ex)); r1 = r2 = []
        check(ok and h1["sha"] == R.spec_sha(SPEC), f"(b) {n}: stream decodes ({len(r1)} records, {len(d1)} bytes), spec hash matches")
        if not ok: continue
        # counts vs NXD9
        tags = collections.Counter(t for t, _ in nxd9io.records(f"{W}/caps/{cap}")); h = collections.Counter((r.iface, r.name) for r in r1)
        dev = lambda m: sum(v for (i, k), v in h.items() if k == m and i in ("IDirect3DDevice9", "IDirect3DDevice9Ex"))
        pairs = [("DRAW", dev("DrawPrimitive")), ("DRWI", dev("DrawIndexedPrimitive")), ("PRES", dev("Present")), ("TEX ", dev("CreateTexture") + dev("CreateCubeTexture")),
                 ("VB  ", dev("CreateVertexBuffer")), ("IB  ", dev("CreateIndexBuffer")), ("DECL", dev("CreateVertexDeclaration")), ("VSH ", dev("CreateVertexShader")), ("PSH ", dev("CreatePixelShader"))]
        bad = [(t, tags[t], v) for t, v in pairs if tags[t] != v]
        check(not bad, f"(b) {n}: NXD9 vs stream counts {bad}")
        print(f"   {n}: " + " ".join(f"{t.strip()}={tags[t]}" for t, _ in pairs) + f" | {len(r1)} recs {len(d1)} B {tags['PRES']} frames")
        tot["bytes"] += len(d1); tot["recs"] += len(r1); tot["frames"] += tags["PRES"]
        # determinism
        check(len(r1) == len(r2), f"(b) {n}: two runs have the same record count ({len(r1)} / {len(r2)})")
        if len(d1) == len(d2): bytes_diff += sum(x != y for x, y in zip(d1, d2))
        else: print(f"   {n}: stream lengths differ {len(d1)} {len(d2)}")
        for rr1, rr2 in zip(r1, r2):
            for (l, c1, rc, v1), (_, _, _, v2) in zip(rr1.items, rr2.items):
                if v1 != v2: nondet[(f"{rr1.iface}::{rr1.name}", l, {0: "det", 1: "env", 2: "pix"}.get(c1, "?"))] += 1
        check(masked(r1) == masked(r2), f"(b) {n}: two runs equal after masking env/pix items")
        if first_stream is None: first_stream = d1
    # (c/d) env probe: the questions nxd9play never asks
    r = sh(["i686-w64-mingw32-gcc", "-O1", "-o", W + "/proxy/envprobe.exe", HERE + "/envprobe.c", "-ld3d9", "-luser32", "-static-libgcc"]); check(r.returncode == 0, "(d) envprobe build: " + r.stderr[-300:])
    pr = sh(["wine", W + "/proxy/envprobe.exe"], env=dict(penv, D9REC_OUT=replay.wine_path(W + "/envprobe.d9s")), timeout=300)
    check(pr.returncode == 0, f"(d) envprobe exit {pr.returncode} {pr.stderr[-200:]}")
    probe = open(W + "/envprobe.d9s", "rb").read() if os.path.isfile(W + "/envprobe.d9s") else b""
finally:
    sh(["wineserver", "-k"], env=env); x.close()
open(W + "/nondet.tsv", "w").write("method\titem\tclass\tcount\n" + "".join(f"{m}\t{l}\t{c}\t{k}\n" for (m, l, c), k in sorted(nondet.items())))
print(f"(b) totals {tot}; differing bytes between two proxy runs before masking: {bytes_diff}; differing items: {sum(nondet.values())} -> {W}/nondet.tsv")
print("    nondeterministic items: " + "; ".join(f"{m}.{l}[{c}] x{k}" for (m, l, c), k in sorted(nondet.items())[:12]))
check(all(c in ("env", "pix") for (_, _, c) in nondet), "(b) every differing item is tagged env/pix")

# ---- (d) caps dump: the probe's stream (all env questions) and one nxd9play run
for name, data in (("wine_llvmpipe_env.tsv", probe), ("wine_llvmpipe_env_nxd9play.tsv", first_stream)):
    if not data: check(False, f"(d) no stream for {name}"); continue
    try: R.read_stream(data, spec)
    except Exception as ex: check(False, f"(d) {name}: stream does not decode {ex!r}"); continue
    buf = io.StringIO(); n = dump_env.dump(data, spec, buf); open(f"{HERE}/{name}", "w").write(buf.getvalue()); ms = {l.split("\t")[1].split("::")[1] for l in buf.getvalue().splitlines()[1:]}
    if name == "wine_llvmpipe_env.tsv": need = {"GetDeviceCaps", "GetAdapterIdentifier", "CheckDeviceFormat", "CheckDeviceType", "CheckDepthStencilMatch", "CheckDeviceMultiSampleType", "CheckDeviceFormatConversion", "GetAvailableTextureMem", "EnumAdapterModes", "Reset", "CreateDevice"}; check(need <= ms, f"(d) probe dump lacks {sorted(need - ms)}")
    print(f"(d) {name}: {n} rows; methods {sorted(ms)}")
print(f"test_rec: {npass} checks passed, {len(fails)} failed"); sys.exit(1 if fails else 0)
