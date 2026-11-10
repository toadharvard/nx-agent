"""sm_run.py - encode every universe row, run translator (sdp2dksh --glsl --no-dksh) then uam (sdp2dksh), classify failures.
Rows are encoded by sm_hand (token-stream encoder); rows carrying a ctab go through s6b_asm (it emits the CTAB).  Identical bytecode is run once."""
import concurrent.futures as cf, hashlib, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import sm_hand, sm_classes

import threading
HAND_LOCK = threading.Lock()          # sm_hand keeps the shader major version in a module global

def load_ops(deps):
    return sm_hand.load_opcodes(os.path.join(deps, "mojoshader", "mojoshader_internal.h"))

def encode(row, ops, build, work, i):
    text = row["asm"]
    if row.get("ctab"):
        ln = text.split("\n"); text = "\n".join(ln[:1] + [row["ctab"]] + ln[1:])
        a, b = os.path.join(work, "e%05d.asm" % i), os.path.join(work, "e%05d.bin" % i)
        open(a, "w").write(text)
        r = subprocess.run([os.path.join(build, "s6b_asm"), a, b], capture_output=True, text=True)
        if r.returncode != 0: return None, "asm: " + (r.stderr.strip().splitlines() or ["?"])[0][:160]
        return open(b, "rb").read(), ""
    try:
        with HAND_LOCK: return sm_hand.assemble(text, ops), ""
    except Exception as e: return None, "hand: " + str(e)[:160]

def batch(build, files, outdir, glsl_only, env):
    shutil.rmtree(outdir, ignore_errors=True); os.makedirs(outdir, exist_ok=True)
    cmd = [os.path.join(build, "sdp2dksh"), "-o", outdir, "--glsl"] + (["--no-dksh"] if glsl_only else []) + ["--blob"] + files
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    res = {}
    try: lines = open(os.path.join(outdir, "index.tsv")).read().splitlines()[1:]
    except OSError: return None
    for ln in lines:
        f = ln.split("\t")
        if len(f) >= 10: res[f[2]] = (f[4] == "ok", f[10] if len(f) > 10 else "")
    return res

def run_stage(build, items, work, stage, jobs, env):
    """items: {name: path}; returns {name: (ok, err)}"""
    names = sorted(items); chunks = [names[i:i + 150] for i in range(0, len(names), 150)]
    out = {}
    def go(ci):
        ch = chunks[ci]; od = os.path.join(work, "%s%03d" % (stage, ci))
        r = batch(build, [items[n] for n in ch], od, stage == "t", env)
        if r is None or any(os.path.basename(items[n]) not in r for n in ch):       # crash or dedup: retry per file
            r = r or {}
            for n in ch:
                k = os.path.basename(items[n])
                if k in r: continue
                rr = batch(build, [items[n]], od + "_" + n, stage == "t", env)
                r[k] = rr.get(k, (False, "tool crashed or produced no result")) if rr else (False, "tool crashed or produced no result")
        return {n: r[os.path.basename(items[n])] for n in ch}
    with cf.ThreadPoolExecutor(jobs) as ex:
        for d in ex.map(go, range(len(chunks))): out.update(d)
    return out

def run(rows, build, deps, work, jobs=4):
    shutil.rmtree(work, ignore_errors=True); os.makedirs(work, exist_ok=True)
    env = dict(os.environ); tools = os.path.join(os.path.dirname(build.rstrip("/")), "tools", "bin")
    env["PATH"] = tools + ":" + env.get("PATH", "")
    ops = load_ops(deps); os.makedirs(os.path.join(work, 'dx'), exist_ok=True)
    enc, uniq, byhash = {}, {}, {}
    with cf.ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(lambda t: encode(t[1], ops, build, work, t[0]), enumerate(rows)))
    for i, (bc, err) in enumerate(res):
        if bc is None: enc[i] = ("encode_fail", err); continue
        h = hashlib.sha1(bc).hexdigest(); byhash.setdefault(h, []).append(i)
        if h not in uniq:
            p = os.path.join(work, h + ".bin"); open(p, "wb").write(bc); uniq[h] = p
    t = run_stage(build, {h: p for h, p in uniq.items()}, work, "t", jobs, env)
    u = run_stage(build, {h: p for h, p in uniq.items() if t[h][0]}, work, "u", jobs, env)
    detail = {}                                                                  # GLSL compile errors: re-run each failing bytecode alone, keep the first error line
    for h, (ok, err) in u.items():
        if ok: continue
        p = subprocess.run([os.path.join(build, "sdp2dksh"), "-o", os.path.join(work, "dx"), "--glsl", "--blob", uniq[h]], capture_output=True, text=True, env=env)
        errs = [l.split("error:", 1)[1].strip() for l in p.stderr.splitlines() if "error:" in l]
        detail[h] = "glsl-compile: " + (errs[0] if errs else err)
    os.makedirs(os.path.join(work, "dx"), exist_ok=True)
    out = [None] * len(rows)
    for i, e in enc.items(): out[i] = e
    for h, idx in byhash.items():
        st = ("ok", "") if t[h][0] and u[h][0] else ("translate_fail", t[h][1]) if not t[h][0] else ("uam_fail", detail.get(h, u[h][1]))
        for i in idx: out[i] = st; rows[i]["_bin"] = uniq[h]
    return out
