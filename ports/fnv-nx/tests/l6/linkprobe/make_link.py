#!/usr/bin/env python3
"""make_link.py: the reference table "does Wine 9.0's GLSL link succeed" for every SM form: render/shaders/total/sm_link.tsv.
 1. render/shaders/total/mk_linkprobe.py generates the probe input (rows.tsv, probe_in.tsv, not_expressible.tsv) from sm_table.json / sm_illegal.tsv;
 2. linkprobe.c is built with the pinned mingw-w64 (same flags as envprobe2: -Wl,--no-insert-timestamp);
 3. TWICE, each in a fresh Wine prefix (VideoMemorySize=960 as the env dumps) under Xvfb 1280x720x24 + llvmpipe, linkprobe.exe runs the probe input; Wine's own trace
    (WINEDEBUG=-all,+d3d_shader,err+d3d) is bracketed per item by the probe (@@ROW n / @@END n) and read per item;
 4. the two result tables must be byte-equal (else FAIL: the differing rows are listed).
 Per row: create hr (CreateVertexShader / CreatePixelShader, hex), compile (ok | failed | none), link (ok | failed | none), gl_error, draw hr, set.
   compile = failed iff the item's trace holds a "GL_COMPILE_STATUS: 0" (glsl_shader.c shader_glsl_dump_program_source, printed only when the link status is invalid);
   link = failed iff it holds "link status invalid" (shader_glsl_validate_link); ok iff the shader was compiled ("Compiling shader object", shader_glsl_compile) and neither;
   none = nothing was compiled (create failed, or the draw did not reach GLSL); gl_error = the first err:d3d line of the item, numbers replaced by N, else none.
env: LINKPROBE_WORK (default /home/user/work/l6/linkprobe), MOJO_HDR (mojoshader_internal.h, fnv-nx-build tools/mojoshader).  usage: make_link.py"""
import hashlib, os, re, shutil, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "tools", "compare")]
import replay  # noqa: E402
W = os.environ.get("LINKPROBE_WORK", "/home/user/work/l6/linkprobe"); BIN = os.environ.get("LINKPROBE_BIN", W + "/bin")
TOT = os.path.join(ROOT, "render", "shaders", "total")


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()


def parse_trace(err):
    """{n: [lines]} from the stderr of one run"""
    win, cur = {}, None
    for ln in err.splitlines():
        if ln.startswith("@@ROW "): cur = int(ln[6:]); win[cur] = []; continue
        if ln.startswith("@@END "): cur = None; continue
        if cur is not None: win[cur].append(ln)
    return win


def classify(lines, hc, hd):
    txt = "\n".join(lines)
    compiled = "Compiling shader object" in txt
    cf = "GL_COMPILE_STATUS: 0" in txt; lf = "link status invalid" in txt
    errs = [re.sub(r"0x[0-9a-fA-F]+|\b[0-9]+\b|\b[0-9A-F]{8}\b", "N", re.sub(r"^\S+:err:d3d:\S+ ", "", l)).strip() for l in lines if ":err:d3d:" in l]
    ge = errs[0] if errs else "none"
    if hc != 0: return "na", "na", ge
    comp = "failed" if cf else "ok" if compiled else "none"
    link = "failed" if (lf or cf) else "ok" if compiled else "none"
    return comp, link, ge


def one_run(k, exe, pin):
    work = f"{W}/run{k}"; shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
    os.environ["S16_DISPLAY"] = ":%d" % (150 + os.getpid() % 40)
    x = replay.Xvfb(1280, 720); env = replay.wine_env(work + "/pfx", x.display); lock = replay.prefix_lock(work + "/pfx"); time.sleep(1)
    try:
        sh(["wineboot", "-i"], env=env, timeout=900); sh(["wineserver", "-w"], env=env, timeout=900)
        sh(["wine", "reg", "add", r"HKCU\Software\Wine\Direct3D", "/v", "VideoMemorySize", "/t", "REG_SZ", "/d", "960", "/f"], env=env, timeout=300); sh(["wineserver", "-w"], env=env)
        e = dict(env, WINEDEBUG="-all,+d3d_shader,err+d3d", WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"])
        t0 = time.time()
        with open(work + "/stderr.txt", "wb") as ef:
            r = subprocess.run(["wine", replay.wine_path(exe), replay.wine_path(pin), replay.wine_path(work + "/out.tsv")], env=e, stderr=ef, stdout=subprocess.DEVNULL, timeout=7200)
        sh(["wineserver", "-k"], env=env)
        return r.returncode, time.time() - t0, work
    finally: x.close(); lock.close()


def main():
    os.makedirs(W + "/gen", exist_ok=True); os.makedirs(BIN, exist_ok=True)
    r = sh([sys.executable, "-I", TOT + "/mk_linkprobe.py", "--mojoshader-header", os.environ["MOJO_HDR"], "--out", W + "/gen"]); print(r.stdout.strip(), r.stderr[-400:])
    if r.returncode: return 1
    exe = BIN + "/linkprobe.exe"
    r = sh(["i686-w64-mingw32-gcc", "-O1", "-w", "-Wl,--no-insert-timestamp", "-o", exe, HERE + "/linkprobe.c", "-ld3d9", "-luser32", "-static-libgcc"])
    if r.returncode: print("FAIL build", r.stderr[-500:]); return 1
    rows = [l.split("\t") for l in open(W + "/gen/rows.tsv").read().splitlines()[1:]]
    tabs, times = [], []
    for k in (1, 2):
        rc, dt, work = one_run(k, exe, W + "/gen/probe_in.tsv"); times.append(dt)
        if rc != 0: print(f"FAIL run {k}: exit {rc}"); return 1
        win = parse_trace(open(work + "/stderr.txt", errors="replace").read())
        res = {}
        for ln in open(work + "/out.tsv").read().splitlines():
            n, hc, hp, hd = ln.split("\t"); res[int(n)] = (int(hc, 16), int(hp, 16), int(hd, 16))
        out = ["row\tmodel\tcreate_hr\tcompile\tlink\tgl_error\tdraw_hr\tset"]
        for rid, st, m, n, _ in rows:
            n = int(n)
            if n not in res or n not in win: print("FAIL: no result for item", n); return 1
            hc, hp, hd = res[n]
            if hc == 0 and hp != 0: print("FAIL: partner shader refused, item", n, hex(hp)); return 1
            comp, link, ge = classify(win[n], hc, hd)
            out.append("\t".join([rid, m, "%08x" % hc, comp, link, ge, "na" if hc else "%08x" % hd, st]))
        tabs.append("\n".join(out) + "\n")
    dst = TOT + "/sm_link.tsv"
    same = tabs[0] == tabs[1]
    print("run times: %.0f s, %.0f s; run1 == run2: %s" % (times[0], times[1], same))
    if not same:
        a, b = tabs[0].splitlines(), tabs[1].splitlines()
        for x, y in zip(a, b):
            if x != y: print("DIFF\n ", x, "\n ", y)
        return 1
    open(dst, "w", newline="\n").write(tabs[0])
    c = {}
    for ln in tabs[0].splitlines()[1:]:
        f = ln.split("\t"); k = (f[2] == "00000000", f[3], f[4], f[5]); c[k] = c.get(k, 0) + 1
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]): print(v, "created" if k[0] else "create-failed", "compile", k[1], "link", k[2], "gl", k[3])
    print("sm_link.tsv sha256", sha(dst), "linkprobe.exe sha256", sha(exe), "rows", len(rows)); return 0


if __name__ == "__main__":
    sys.exit(main())
