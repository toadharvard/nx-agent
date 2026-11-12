#!/usr/bin/env python3
"""test_wrap.py: self-test of the D3D9 journaling wrapper generator (RD1, task T2).  python3 -I tests/l6/wrap/test_wrap.py
 1 generator: runs gen_wrap.py (and checks that it FAILS, naming the item, on a spec with an unknown kind);
 2 builds (g++ -m32) wrapper + stream runtime + a generated ECHO implementation + DRIVER (gen_echo.py) that calls every method of every interface;
 3 runs it twice (determinism), decodes the stream with d9s1_read.py and compares every record/item with the plan's expected stream (enc.py);
 4 mutation checks: one input / one echo output byte / one handle changed -> the comparison must fail.   Exit != 0 on any failure."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, concurrent.futures as cf
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GEN = os.path.join(ROOT, "render", "d3d9", "gen"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json"); INC = os.path.join(ROOT, "shim", "include")
sys.path.insert(0, HERE)
import plan as P, enc, gen_echo, d9s1_read as R                      # noqa: E402
fails = []; counts = {}; TMP = None


def check(name, ok, msg=""):
    counts[name] = counts.get(name, 0) + 1
    if not ok: fails.append(f"{name}: {msg}")
    return ok


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def compile_(src, out):
    r = sh(["g++", "-m32", "-std=c++17", "-O0", "-w", "-I", INC, "-I", GEN, "-I", os.path.dirname(src), "-I", os.path.join(ROOT, "render", "tools", "replay"), "-c", src, "-o", out])
    return r.returncode == 0, r.stderr[-1500:]


def compare(exp, act, spec, label):
    """item-by-item comparison; returns the number of differing records"""
    bad = 0
    if len(exp) != len(act): check("record count", False, f"{label}: expected {len(exp)} records, got {len(act)}"); bad += 1
    for e, a in zip(exp, act):
        hd = (e.iface, e.slot, e.this, e.depth, e.thread, e.new, e.retired)
        if hd != (a.iface, a.slot, a.this, a.depth, a.thread, a.new, a.retired):
            bad += 1; check("header/ids/retire", False, f"{label}: seq {e.seq} {e.name}: header {hd} != {(a.iface, a.slot, a.this, a.depth, a.thread, a.new, a.retired)}"); continue
        check("header/ids/retire", True)
        if e.items != a.items:
            bad += 1
            for x, y in zip(e.items, a.items):
                if x != y: check("items", False, f"{label}: seq {e.seq} {e.name} item {x[0]}: expected {x[1:]} got {y[1:]}"); break
            else: check("items", False, f"{label}: seq {e.seq} {e.name}: item count {len(e.items)} != {len(a.items)}")
        else:
            for it in e.items: counts["items"] = counts.get("items", 0) + 1
            for lab, kind in (("in", "inputs"), ("out", "outputs"), ("ret", "returns"), ("lock", "lock bytes/pitches"), ("close", "unlock bytes")):
                counts[kind] = counts.get(kind, 0) + sum(1 for it in e.items if it[0].split(":")[0] == lab)
            counts["buffer lengths"] = counts.get("buffer lengths", 0) + sum(1 for it in e.items if it[0].endswith(":data") or (it[0].startswith(("in:", "out:")) and isinstance(it[3], bytes) and len(it[3]) > 4))
    return bad


def main():
    raw = open(SPEC, "rb").read(); spec = json.loads(raw); tmp = tempfile.mkdtemp(prefix="d9wrap_"); global TMP; TMP = tmp
    # 1 generator + fail-closed check
    r = sh([sys.executable, "-I", os.path.join(GEN, "gen_wrap.py"), tmp]); check("generator runs", r.returncode == 0, r.stderr[-300:])
    bad = json.loads(raw); bad["methods"][5]["params"] = bad["methods"][5]["params"] + [{"name": "zz", "kind": "bogus", "dir": "in", "byref": False, "ctype": "int"}]
    open(os.path.join(tmp, "bad.json"), "w").write(json.dumps(bad))
    r = sh([sys.executable, "-I", os.path.join(GEN, "gen_wrap.py"), os.path.join(tmp, "bad_out"), os.path.join(tmp, "bad.json")])
    check("generator fails on unknown kind", r.returncode != 0 and "bogus" in r.stderr, r.stderr[-200:])
    man = open(os.path.join(tmp, "d9wrap.manifest.txt")).read().split("\n")
    wrapped_m = sum(1 for l in man if l.startswith("M ")); wrapped_e = sum(1 for l in man if l.startswith("E ")); unw = [l for l in man if l.startswith("U ")]
    print(f"generated {sum(1 for _ in open(os.path.join(tmp, 'd9wrap.gen.cpp')))} + {sum(1 for _ in open(os.path.join(tmp, 'd9wrap.gen.h')))} lines; methods {wrapped_m}/{len(spec['methods'])}, exports {wrapped_e}/{len(spec['exports'])}")
    # 2 plan, expected stream, echo + driver
    pl = P.Plan(spec); pl.build(); sha = hashlib.sha256(raw).digest(); expected = enc.Enc(pl, sha).run()
    variants = {"base": None}
    nonprof = [c for c in pl.calls[pl.cover:] if c["hr"] == 0]
    mi = next(c["k"] for c in nonprof if c["iface"] and any(p["kind"] == "value" and not p["byref"] and p["name"] not in P.refs(c["m"]) for p in c["m"]["params"]))
    mo = next(c["k"] for c in nonprof if c["iface"] and (c["iface"], c["m"]["name"]) not in P.PROF and any(p["dir"] == "out" and p["kind"] in ("value", "struct") for p in c["m"]["params"]))
    mh = next(c["k"] for c in nonprof if c["iface"] and any(p["kind"] == "handle" and p["dir"] == "in" for p in c["m"]["params"]))
    variants.update({"mut_input": ("in", mi), "mut_output": ("out", mo), "mut_handle": ("handle", mh)})
    objs = {}
    with cf.ThreadPoolExecutor(4) as ex:
        fut = {"gen": ex.submit(compile_, os.path.join(tmp, "d9wrap.gen.cpp"), os.path.join(tmp, "gen.o")), "st": ex.submit(compile_, os.path.join(ROOT, "render", "tools", "replay", "d9stream.cpp"), os.path.join(tmp, "st.o"))}
        for v, mut in variants.items():
            src = os.path.join(tmp, f"echo_{v}.cpp"); open(src, "w").write(gen_echo.Echo(pl, spec, mut).render()); fut[v] = ex.submit(compile_, src, os.path.join(tmp, f"echo_{v}.o"))
        for k, f in fut.items():
            ok, err = f.result(); check(f"compile {k}", ok, err)
    if fails: return finish()
    streams = {}
    for v in variants:
        exe = os.path.join(tmp, f"exe_{v}")
        r = sh(["g++", "-m32", "-pthread", "-o", exe] + [os.path.join(tmp, x) for x in ("gen.o", "st.o", f"echo_{v}.o")]); check(f"link {v}", r.returncode == 0, r.stderr[-300:])
        for run in (1, 2):
            if v != "base" and run == 2: continue
            out = os.path.join(tmp, f"{v}_{run}.d9s"); r = sh([exe, out]); check(f"run {v}", r.returncode == 0, f"rc {r.returncode} {r.stderr[-200:]}")
            streams[(v, run)] = open(out, "rb").read() if r.returncode == 0 else b""
            if v == "base" and run == 1: print(r.stdout.strip()); drv = r.stdout
    if fails: return finish()
    check("driver ident/ret/forward counters", all(f"{k}=0" in drv for k in ("ident_fail", "ret_fail", "fwd_fail")), drv.strip())
    check("determinism: two runs byte-identical", streams[("base", 1)] == streams[("base", 2)], "streams differ")
    print(f"stream {len(streams[('base', 1)])} bytes, expected {len(expected)} bytes")
    h, exp = R.read_stream(expected, spec); h2, act = R.read_stream(streams[("base", 1)], spec)
    check("header sha256", h2["sha"] == sha, "sha mismatch")
    compare(exp, act, spec, "base")
    seen = {(r.iface, r.slot) for r in act if r.iface}; mexp = {(m["iface"], m["slot"]) for m in spec["methods"]}
    sx = {r.slot for r in act if not r.iface}; nx = wrapped_e
    print(f"coverage: methods {len(seen & mexp)}/{len(mexp)}, exports {len(sx)}/{len(spec['exports'])} ({len(unw)} exports have no signature in the spec: {', '.join(l.split()[2] for l in unw)})")
    check("coverage methods 503/503", len(seen & mexp) == len(mexp) == 503, f"{len(seen & mexp)}/{len(mexp)}")
    check("coverage wrappable exports", len(sx) == nx, f"{len(sx)}/{nx}")
    nret = sum(len(r.retired) for r in act); check("retired ids", nret == sum(len(r.retired) for r in exp) and nret > 0, f"{nret}")
    # 4 mutation checks
    for v in ("mut_input", "mut_output", "mut_handle"):
        try: _, mact = R.read_stream(streams[(v, 1)], spec)
        except R.ReadError as ex: check(f"mutation {v} detected", True); continue
        sv = list(fails); n = compare(exp, mact, spec, v); del fails[len(sv):]; counts.pop("header/ids/retire", None) if False else None
        check(f"mutation {v} detected", n > 0, "mutated run is indistinguishable from the expected stream")
    finish()


def finish():
    if TMP and not os.environ.get("D9WRAP_KEEP"): shutil.rmtree(TMP, ignore_errors=True)
    print("checks: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for f in fails[:40]: print("FAIL", f)
    print(f"RESULT: {'FAIL' if fails else 'PASS'} ({len(fails)} failures)"); sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
