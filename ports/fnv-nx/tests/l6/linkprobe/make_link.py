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
TEXTS (pair key): wined3d 9.0 links vs + ps (+ a reorder shader for every vs below 4.0, glsl_shader.c shader_glsl_generate_vs3_rasterizer_input_setup), so the link
 result belongs to the pair. Per item the runner records the sha256 of the GLSL text of the three roles, read from wined3d's own trace: every "shader_glsl_compile" event carries
 object id + text; "set_glsl_shader_program ... Attaching GLSL shader object X to program P" lists the program's objects in wined3d's order: 3 attaches = vs, reorder, ps;
 2 attaches = vs, ps (no vs shader: the fixed-function vs has no reorder shader). A role's text is the latest compile of that object id before the attach.
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


CMP_RE = re.compile(r"^\w+:trace:d3d_shader:shader_glsl_compile (.*)$"); OBJ_RE = re.compile(r"^Compiling shader object (\d+)\.$")
ATT_RE = re.compile(r"set_glsl_shader_program Attaching GLSL shader object (\d+) to program (\d+)\.")
TXT = {}      # object id -> sha256 of its latest compiled text (global over the run, in trace order)


def roles(lines):
    """-> [(vs_sha, reorder_sha or '-', ps_sha)] one per program created in the item's lines; updates TXT from the compile events in order"""
    out, cur, atts, i = [], None, {}, 0
    n = len(lines)
    while i < n:
        ln = lines[i]; m = CMP_RE.match(ln)
        if m and OBJ_RE.match(m.group(1)):
            oid = int(OBJ_RE.match(m.group(1)).group(1)); buf = []; i += 1
            while i < n:
                m2 = CMP_RE.match(lines[i])
                if not m2 or not m2.group(1).startswith("    "): break
                buf.append(m2.group(1)[4:]); i += 1
            TXT[oid] = hashlib.sha256("\n".join(buf).encode()).hexdigest(); continue
        a = ATT_RE.search(ln)
        if a: atts.setdefault(int(a.group(2)), []).append(TXT.get(int(a.group(1)), "?"))
        i += 1
    for pid, L in atts.items():
        if len(L) == 3: out.append((L[0], L[1], L[2]))
        elif len(L) == 2: out.append((L[0], "-", L[1]))
        else: out.append(("?", "?", "?"))
    return out


def classify(lines, hc, hd):
    txt = "\n".join(lines)
    compiled = "Compiling shader object" in txt
    cf = "GL_COMPILE_STATUS: 0" in txt; lf = "link status invalid" in txt
    errs = [re.sub(r"0x[0-9a-fA-F]+|\b[0-9]+\b|\b[0-9A-F]{8}\b", "N", re.sub(r"^\S+:err:d3d:\S+ ", "", l)).strip() for l in lines if ":err:d3d:" in l]
    ge = errs[0] if errs else "none"
    up = "not_linked" if re.search(r"glUseProgram\(program \d+ not linked\)", txt) else "none"      # Mesa's GL error after wined3d's glUseProgram (debug callback, err:d3d)
    if hc != 0: return "na", "na", ge, "na"
    comp = "failed" if cf else "ok" if compiled else "none"
    link = "failed" if (lf or cf) else "ok" if compiled else "none"
    return comp, link, ge, up


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
            r = subprocess.run(["wine", replay.wine_path(exe), replay.wine_path(pin), replay.wine_path(work + "/out.tsv")], env=e, stderr=ef, stdout=subprocess.DEVNULL, timeout=14400)
        sh(["wineserver", "-k"], env=env)
        return r.returncode, time.time() - t0, work
    finally: x.close(); lock.close()


def items_of(work):
    """{n: 'create_hr compile link gl_error draw_hr vs ps reorder'} for every item of one run, in item order (TXT is filled in trace order)"""
    TXT.clear(); win = parse_trace(open(work + "/stderr.txt", errors="replace").read()); res = {}
    for ln in open(work + "/out.tsv").read().splitlines():
        n, hc, hp, hd = ln.split("\t"); res[int(n)] = (int(hc, 16), int(hp, 16), int(hd, 16))
    out, nlink = {}, 0
    for n in sorted(res):
        hc, hp, hd = res[n]
        if hc == 0 and hp != 0: print("FAIL: partner shader refused, item", n, hex(hp)); sys.exit(1)
        L = win[n]; rl = roles(L); nlink += sum("Linking GLSL shader program" in x for x in L)
        comp, link, ge, up = classify(L, hc, hd)
        vs, ro, ps = rl[0] if len(rl) == 1 else ("-", "-", "-") if not rl else ("?", "?", "?")
        out[n] = "\t".join(["%08x" % hc, comp, link, ge, "na" if hc else "%08x" % hd, vs, ps, ro, up])
    return out, nlink


def table(rows, items):
    out = ["row\tmodel\tset\tcreate_hr\t" + "\t".join(f"{g}_{c}" for g in ("min", "ffp") for c in ("compile", "link", "gl_error", "draw_hr", "vs_sha", "ps_sha", "reorder_sha", "use_program"))]
    for rid, st, m, n, nf, _ in rows:
        a, b = items[int(n)].split("\t"), items[int(nf)].split("\t")
        out.append("\t".join([rid, m, st, a[0]] + a[1:] + b[1:]))
    return "\n".join(out) + "\n"


def main():
    os.makedirs(W + "/gen", exist_ok=True); os.makedirs(BIN, exist_ok=True)
    cmd = [sys.executable, "-I", TOT + "/mk_linkprobe.py", "--mojoshader-header", os.environ["MOJO_HDR"], "--out", W + "/gen"]
    if os.environ.get("SDP"): cmd += ["--sdp", os.environ["SDP"], "--private-out", W + "/genp"]
    r = sh(cmd); print(r.stdout.strip(), r.stderr[-400:])
    if r.returncode: return 1
    exe = BIN + "/linkprobe.exe"
    r = sh(["i686-w64-mingw32-gcc", "-O1", "-w", "-Wl,--no-insert-timestamp", "-o", exe, HERE + "/linkprobe.c", "-ld3d9", "-luser32", "-static-libgcc"])
    if r.returncode: print("FAIL build", r.stderr[-500:]); return 1
    pin = W + "/gen/all_in.tsv"
    with open(pin, "w", newline="\n") as o:
        o.write(open(W + "/gen/probe_in.tsv").read())
        if os.environ.get("SDP"): o.write(open(W + "/genp/probe_in_shipped.tsv").read())
    rd = lambda p: [l.split("\t") for l in open(p).read().splitlines()[1:]]
    rows = rd(W + "/gen/rows.tsv"); prow = rd(W + "/genp/rows_shipped.tsv") if os.environ.get("SDP") else []
    runs, times, nlinks = [], [], []
    for k in (1, 2):
        rc, dt, work = one_run(k, exe, pin); times.append(dt)
        if rc != 0: print(f"FAIL run {k}: exit {rc}"); return 1
        it, nl = items_of(work); runs.append(it); nlinks.append(nl)
    same = runs[0] == runs[1]
    print("run times: %.0f s, %.0f s; programs linked %d, %d; items %d; run1 == run2: %s" % (times[0], times[1], nlinks[0], nlinks[1], len(runs[0]), same))
    if not same:
        bad = [n for n in runs[0] if runs[0][n] != runs[1].get(n)]
        print("DIFF items:", len(bad)); [print(" ", n, runs[0][n], "|", runs[1].get(n)) for n in bad[:20]]
        return 1
    it = runs[0]
    pub = table(rows, it); open(TOT + "/sm_link.tsv", "w", newline="\n").write(pub)
    if prow: open(W + "/genp/sm_link_shipped.tsv", "w", newline="\n").write(table(prow, it))
    allrows = rows + prow
    V, P, R, pairs = set(), set(), set(), set()
    for n, v in it.items():
        f = v.split("\t"); vs, ps, ro = f[5], f[6], f[7]
        if vs != "-": V.add(vs)
        if ps != "-": P.add(ps)
        if ro != "-": R.add(ro)
        pairs.add((vs, ps, ro))
    print("V (distinct vs texts) %d, P (distinct ps texts) %d, V x P = %d, distinct reorder texts %d, distinct (vs,ps,reorder) triples drawn %d" % (len(V), len(P), len(V) * len(P), len(R), len(pairs)))
    print("time per program (generate + compile + link + draw, Wine 9.0 + Mesa llvmpipe, 1 thread): %.1f ms" % (1000 * times[0] / max(1, nlinks[0])))
    c = {}
    for ln in pub.splitlines()[1:]:
        f = ln.split("\t"); k = (f[3] == "00000000", f[4], f[5], f[6]); c[k] = c.get(k, 0) + 1
    for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:12]: print(v, "created" if k[0] else "create-failed", "compile", k[1], "link", k[2], "gl", k[3][:90])
    print("sm_link.tsv sha256", sha(TOT + "/sm_link.tsv"), "linkprobe.exe sha256", sha(exe), "public rows", len(rows), "shipped rows", len(prow)); return 0


if __name__ == "__main__":
    sys.exit(main())
