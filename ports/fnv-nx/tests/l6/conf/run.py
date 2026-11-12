#!/usr/bin/env python3
"""run.py: Wine d3d9 conformance tests (built by build.sh) -> recorded D9S1 streams + counts.
Per test: run plain, run with the recorder twice (A, B). Checks (exit 1 if violated):
 (i) neutrality: harness summary line (pid stripped) and exit code equal plain vs recorded (A and B);
 (ii) both streams decode fully; (iii) determinism: d9cmp A B, reported per test (non-determinism is listed, not a failure).
env: CONF_WORK (default /home/user/work/l6/conf), CONF_TESTS (comma list, default all), CONF_TIMEOUT (s, default 600).
Output: WORK/streams/<t>.d9s (+ .b.d9s), WORK/runs/<t>/*.out, WORK/cmp/<t>.json, conf_summary.tsv, conf_never_called.txt, WORK/nondet.txt."""
import collections, json, os, re, shutil, signal, struct, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CMP = os.path.join(ROOT, "render", "tools", "cmp"); sys.path[:0] = [CMP, os.path.join(ROOT, "tools", "compare")]
import d9s1 as R, replay  # noqa: E402
W = os.environ.get("CONF_WORK", "/home/user/work/l6/conf"); REC = os.environ.get("CONF_REC", "rec_nofree")   # recorder dir under W: rec (as built) or rec_nofree
TAG = "" if REC == "rec_nofree" else "." + REC; TMO = int(os.environ.get("CONF_TIMEOUT", "600"))
SPECF = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json"); spec = json.load(open(SPECF))
TESTS = (os.environ.get("CONF_TESTS") or "stateblock,device,d3d9ex,visual").split(",")
SUM = re.compile(r"(\d+) tests executed \((\d+) marked as todo, (\d+) as flaky, (\d+) failures?\), (\d+) skipped")
fails = []


def check(ok, what):
    if not ok: fails.append(what); print("FAIL", what, flush=True)


def norm(out): return [re.sub(r"^[0-9a-f]{4}:", "", l) for l in out.splitlines()]


def summary(out):
    m = [SUM.search(l) for l in out.splitlines()]; m = [x for x in m if x]
    return tuple(int(v) for v in m[-1].groups()) if m else None


def wine(env, exe, cwd, outf):
    """one run in its own process group; the timeout kills the group and the wineserver. A signal exit at teardown is retried once (flaky Wine)."""
    for _ in range(2):
        t0 = time.time(); p = subprocess.Popen(["wine", exe], cwd=cwd, env=env, stdout=open(outf, "w"), stderr=subprocess.STDOUT, start_new_session=True)
        try: rc = p.wait(TMO)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL); subprocess.run(["wineserver", "-k"], env=env); p.wait(); rc = "timeout"
        subprocess.run(["wineserver", "-k"], env=env); subprocess.run(["wineserver", "-w"], env=env)
        if rc == "timeout" or rc >= 0: break
        print(f"   {exe}: signal exit {rc}, retried", flush=True)
    return rc, time.time() - t0


def failed(r): return r.m["ret_kind"] == "hresult" and any(i.label == "ret" and i.data and int.from_bytes(i.data, "little") & 0x80000000 for i in r.items)


def analyse(path):
    d = open(path, "rb").read() if os.path.isfile(path) else b""
    try: _, recs = R.read_stream(d, spec); err = 0
    except (R.ReadError, KeyError, IndexError, ValueError, struct.error) as ex: print("   decode error:", path, repr(ex)[:200]); recs, err = [], 1
    fr = [r for r in recs if failed(r)]
    return dict(bytes=len(d), records=len(recs), decode_err=err, threads=len({r.thread for r in recs}), depth=max([r.depth for r in recs], default=0),
                methods={(r.iface, r.slot) for r in recs if r.iface}, exports={r.name for r in recs if not r.iface},
                per_iface=collections.Counter(r.iface or "export" for r in recs), failed=len(fr), failed_by=collections.Counter(f"{r.iface}::{r.name}" for r in fr))


allm = {(m["iface"], m["slot"]): m["name"] for m in spec["methods"]}
rows, called, nondet = [], set(), []
pfx = os.environ.get("CONF_PFX", W + "/pfx"); lock = replay.prefix_lock(pfx); x = replay.Xvfb(); env = replay.wine_env(pfx, x.display); env["LP_NUM_THREADS"] = "2"
penv = dict(env, WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"] + ";d3d9=n,b")
try:
    if not os.path.isfile(pfx + "/system.reg"): subprocess.run(["wineboot", "-i"], env=env, timeout=900); subprocess.run(["wineserver", "-w"], env=env)
    for k, v in (("Debugger", "false"), ("Auto", "1")):   # a crashing test must end the process, not wait for winedbg on a headless host
        subprocess.run(["wine", "reg", "add", r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug", "/v", k, "/t", "REG_SZ", "/d", v, "/f"], env=env, capture_output=True)
    for t in TESTS:
        d = f"{W}/runs{TAG}/{t}"; shutil.rmtree(d, ignore_errors=True)
        for n in ("plain", "A", "B"):   # plain: exe alone (no d3d9.dll beside it); A/B: exe + our proxy d3d9.dll
            os.makedirs(f"{d}/{n}"); shutil.copy(f"{W}/out/{t}.exe", f"{d}/{n}/")
            if n != "plain": shutil.copy(f"{W}/{REC}/d3d9.dll", f"{d}/{n}/")
        os.makedirs(W + "/streams" + TAG, exist_ok=True); os.makedirs(W + "/cmp" + TAG, exist_ok=True)
        sp = {"A": f"{W}/streams{TAG}/{t}.d9s", "B": f"{W}/streams{TAG}/{t}.b.d9s"}
        res = {"plain": wine(env, f"{t}.exe", f"{d}/plain", f"{d}/plain.out")}
        for n in ("A", "B"):
            if os.path.exists(sp[n]): os.remove(sp[n])
            res[n] = wine(dict(penv, D9REC_OUT=replay.wine_path(sp[n])), f"{t}.exe", f"{d}/{n}", f"{d}/{n}.out")
        outs = {n: open(f"{d}/{n}.out", errors="replace").read() for n in res}; sm = {n: summary(outs[n]) for n in res}
        print(f"{t}: exit {[res[n][0] for n in res]} summary {sm['plain']} / {sm['A']} / {sm['B']} secs {[round(res[n][1]) for n in res]}", flush=True)
        neutral = "yes"
        for n in ("A", "B"):
            if (res[n][0], sm[n]) != (res["plain"][0], sm["plain"]) or sm[n] is None:
                neutral = "NO"; a_, b_ = norm(outs["plain"]), norm(outs[n])
                first = next((f"plain:{p!r} rec:{q!r}" for p, q in zip(a_, b_) if p != q), f"length {len(a_)} vs {len(b_)}")
                print(f"   NEUTRALITY {t} {n}: exit {res['plain'][0]} vs {res[n][0]}; summary {sm['plain']} vs {sm[n]}; first differing line: {first[:300]}")
                r2 = wine(env, f"{t}.exe", f"{d}/plain", f"{d}/plain2.out"); s2 = summary(open(f"{d}/plain2.out", errors="replace").read())
                print(f"   plain rerun: exit {r2[0]} summary {s2} (plain-vs-plain {'equal' if (r2[0], s2) == (res['plain'][0], sm['plain']) else 'DIFFERS: the test itself is unstable'})")
        check(neutral == "yes", f"(i) neutrality {t}")
        a, b = analyse(sp["A"]), analyse(sp["B"])
        check(a["decode_err"] == 0 and b["decode_err"] == 0 and a["records"] > 0, f"(ii) decode {t}")
        called |= a["methods"] | b["methods"]
        p = subprocess.run([sys.executable, "-I", f"{CMP}/d9cmp.py", sp["A"], sp["B"], "--max", "20", "--json", f"{W}/cmp{TAG}/{t}.json"], capture_output=True, text=True)
        try: j = json.load(open(f"{W}/cmp{TAG}/{t}.json"))
        except (OSError, ValueError): j = {}
        verdict = {0: "equal", 1: "det_mismatch"}.get(p.returncode, f"cmp_error({p.returncode})")
        if p.returncode == 0 and j.get("env_diff"): verdict = "equal_modulo_env"
        print(f"   records {a['records']} bytes {a['bytes']} methods {len(a['methods'])} failed_hr {a['failed']} | {(p.stdout or p.stderr).splitlines()[0][:230]}", flush=True)
        for mm in j.get("mismatches", [])[:20]: nondet.append((t, json.dumps(mm)[:700]))
        for e in j.get("env_diffs", [])[:20]: nondet.append((t, "ENV " + json.dumps(e)[:400]))
        rows.append([t, res["plain"][0], res["A"][0], res["B"][0], sm["plain"], sm["A"], neutral, a["bytes"], a["records"], len(a["methods"]), len(a["exports"]), a["failed"],
                     a["threads"], a["depth"], a["decode_err"] + b["decode_err"], verdict, j.get("det_mismatch", ""), j.get("env_diff", ""), j.get("masked_pix", ""),
                     j.get("interleave_swaps", ""), round(res["A"][1]), ";".join(f"{k}={v}" for k, v in sorted(a["per_iface"].items())),
                     ";".join(f"{k}={v}" for k, v in a["failed_by"].most_common(8))])
finally:
    subprocess.run(["wineserver", "-k"], env=env); x.close()
H = ["test", "exit_plain", "exit_recA", "exit_recB", "summary_plain(exec,todo,flaky,fail,skip)", "summary_rec", "neutral", "stream_bytes", "records", "distinct_methods", "exports_called",
     "failed_hresult_recs", "threads", "max_depth", "decode_errors", "determinism", "det_mismatch", "env_diff", "masked_pix", "interleave_swaps", "secs_rec", "records_per_iface", "top_failed_methods"]
open(HERE + "/conf_summary" + TAG + ".tsv", "w").write("\t".join(H) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows))
never = sorted(f"{i}::{n}" for (i, s), n in allm.items() if (i, s) not in called)
open(HERE + "/conf_never_called" + TAG + ".txt", "w").write("".join(n + "\n" for n in never))
open(W + "/nondet.txt", "w").write("".join(f"{t}\t{m}\n" for t, m in nondet))
print(f"methods called by at least one test: {len(called)} of {len(allm)}; never called {len(never)} -> conf_never_called.txt; nondet details {W}/nondet.txt")
print(f"run.py: {len(fails)} checks failed"); sys.exit(1 if fails else 0)
