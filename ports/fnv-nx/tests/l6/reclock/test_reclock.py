#!/usr/bin/env python3
"""test_reclock.py: recorder (render/tools/rec) vs lock handling, under Wine. Builds the recorder + lockprobe.c, runs the probe WITHOUT the proxy
(baseline) and WITH it, with a hard timeout (a hang is a failure). Checks: both exit 0 with identical output; the stream decodes fully; for
EVERY lock call of the probe (in call order) the recorded Lock bytes / Unlock bytes have exactly rows(fmt,h)*rowbytes(fmt,w)*depth bytes, computed
here from the level dimensions and the rect printed by the probe, and carry the pattern the probe wrote / read (the pattern is regenerated here).
Also: a D9REC_FLUSH=1 run (stream byte-identical), a D9REC_TRACE=1 run (trace lines appear on stderr, and the stream is byte-identical to the untraced run).
env: RECLOCK_WORK (default /home/user/work/l6/reclock), RECLOCK_TIMEOUT (s, default 180).  Exit 0 iff every check passed."""
import os, re, shutil, subprocess, sys, json, hashlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(HERE, "..", "wrap"), os.path.join(ROOT, "tools", "compare")]
import d9s1_read as R, replay  # noqa: E402
W = os.environ.get("RECLOCK_WORK", "/home/user/work/l6/reclock"); TMO = int(os.environ.get("RECLOCK_TIMEOUT", "180"))
spec = json.load(open(os.path.join(ROOT, "render/d3d9/spec/d3d9_spec.json")))
fails = []; npass = 0


def check(ok, what):
    global npass
    if ok: npass += 1
    else:
        fails.append(what); print("FAIL", what[:300])


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


BLOCK = {"DXT1": 8, "DXT3": 16, "DXT5": 16}; BPP = {"F21": 4, "F50": 1, "F23": 2, "F113": 8}   # A8R8G8B8, L8, R5G6B5, A16B16G16R16F


def extent(lk):
    """bytes of a lock from the texture's creation dims, level and rect: independent of the probe's own arithmetic."""
    lv = lambda d: max(1, d >> lk["L"])
    w, h, d = lv(lk["W"]), lv(lk["H"]), (lv(lk["D"]) if lk["k"] == 2 else 1)
    r = lk["rect"]
    if r[0] != -1:
        w, h = r[2] - r[0], r[3] - r[1]
        if lk["k"] == 2: d = lk["fb"][1] - lk["fb"][0]
    if lk["f"] in BLOCK: return ((w + 3) // 4) * ((h + 3) // 4) * BLOCK[lk["f"]] * d
    return w * BPP[lk["f"]] * h * d


def pat(seed, n): return bytes((seed * 31 + i * 7 + (i >> 8) * 13 + 1) & 255 for i in range(n))


def parse(out):
    lks, cur = [], None
    for ln in out.splitlines():
        if ln.startswith("LK "):
            m = re.match(r"LK (\S+) lock tex=(\d+) k=(\d) f=(\S+) p=(\d) W=(\d+) H=(\d+) D=(\d+) L=(\d+) face=(\d+) rect=(-?\d+),(-?\d+),(-?\d+),(-?\d+) fl=([0-9a-f]+) via=(\d) ok=(\d) rd=(-?\d+)", ln)
            g = m.groups(); cur = dict(tag=g[0], tex=int(g[1]), k=int(g[2]), f=g[3], p=int(g[4]), W=int(g[5]), H=int(g[6]), D=int(g[7]), L=int(g[8]), face=int(g[9]),
                                       rect=tuple(int(x) for x in g[10:14]), fl=int(g[14], 16), via=int(g[15]), ok=int(g[16]), rd=int(g[17]), fb=(0, 0), seed=None); lks.append(cur)
        elif ln.startswith("LKB "): cur["fb"] = tuple(int(x) for x in ln.split()[1:3])
        elif ln.startswith("WR "): cur["seed"] = int(re.search(r"seed=(\d+)", ln).group(1))
    return lks


os.makedirs(W, exist_ok=True)
r = sh([os.path.join(ROOT, "render/tools/rec/build.sh"), W + "/rec"]); check(r.returncode == 0, "build.sh: " + r.stderr[-400:])
r = sh(["i686-w64-mingw32-gcc", "-O1", "-Wall", "-Wno-unused", os.path.join(HERE, "lockprobe.c"), "-o", W + "/lockprobe.exe", "-ld3d9"]); check(r.returncode == 0, "lockprobe build: " + r.stderr[-400:])
if fails: sys.exit(1)
for d in ("plain", "proxy"):
    shutil.rmtree(f"{W}/{d}", ignore_errors=True); os.makedirs(f"{W}/{d}"); shutil.copy(W + "/lockprobe.exe", f"{W}/{d}/")
shutil.copy(W + "/rec/d3d9.dll", W + "/proxy/d3d9.dll")
pfx = W + "/pfx"; lock = replay.prefix_lock(pfx); x = replay.Xvfb(); env = replay.wine_env(pfx, x.display)
try:
    if not os.path.isfile(pfx + "/system.reg"):
        sh(["wineboot", "-i"], env=env, timeout=900); sh(["wineserver", "-w"], env=env, timeout=900)
    penv = dict(env, WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"] + ";d3d9=n,b")

    def run(d, e, tag):
        try: p = sh(["wine", f"{W}/{d}/lockprobe.exe"], env=e, timeout=TMO); return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:   # a crash under Wine starts winedbg and the process never ends: report what stderr said
            sh(["wineserver", "-k"], env=env); er = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            print(f"   {tag}: TIMEOUT after {TMO}s; stderr: " + " | ".join(l for l in er.splitlines() if "Unhandled" in l or "d9rec" in l)[-300:]); return "TIMEOUT", "", er

    rc0, out0, _ = run("plain", env, "plain"); check(rc0 == 0, f"baseline (no proxy) exit {rc0}")
    lks = parse(out0); check(len(lks) > 1000, f"baseline has {len(lks)} locks")
    summ = re.search(r"SUMMARY locks=(\d+) steps=(\d+) fail=(\d+)", out0); print("baseline:", summ.group(0) if summ else "no summary")
    s1 = W + "/proxy.d9s"; s2 = W + "/proxy_trace.d9s"
    for f in (s1, s2):
        if os.path.exists(f): os.remove(f)
    rc1, out1, err1 = run("proxy", dict(penv, D9REC_OUT=replay.wine_path(s1)), "proxy"); check(rc1 == 0, f"proxy run exit {rc1} (TIMEOUT = hang)")
    check(out1 == out0, "proxy output identical to baseline" + ("" if out1 == out0 else f" (first diff line {next((i for i, (a, b) in enumerate(zip(out0.splitlines(), out1.splitlines())) if a != b), min(len(out0.splitlines()), len(out1.splitlines())))})"))
    print(f"proxy: exit {rc1}, stream {os.path.getsize(s1) if os.path.exists(s1) else 'missing'} bytes")
    # access violations / heap damage under the proxy (counts only)
    rc3, _, err3 = run("proxy", dict(penv, D9REC_OUT=replay.wine_path(W + "/proxy_dbg.d9s"), WINEDEBUG="warn+seh,warn+heap,err+all"), "dbg")
    nav = len(re.findall(r"ACCESS_VIOLATION|Unhandled exception|heap_|Heap", err3)); check(rc3 == 0 and nav == 0, f"proxy under WINEDEBUG warn+seh,warn+heap: exit {rc3}, {nav} AV/heap lines")
    # trace run
    rc2, out2, err2 = run("proxy", dict(penv, D9REC_OUT=replay.wine_path(s2), D9REC_TRACE="1"), "trace")
    ntr = len([l for l in err2.splitlines() if l.startswith("d9rec:")])
    check(rc2 == 0 and out2 == out0, f"D9REC_TRACE=1 run exit {rc2}, same output")
    nok = sum(1 for l in lks if l["ok"])
    check(ntr >= 2 * (len(lks) + nok), f"D9REC_TRACE=1: {ntr} trace lines for {len(lks)} locks + {nok} unlocks (need >= 2 per call)")
    check(os.path.exists(s1) and os.path.exists(s2) and open(s1, "rb").read() == open(s2, "rb").read(), "stream with D9REC_TRACE=1 byte-identical to the untraced stream")
    s3 = W + "/proxy_flush.d9s"; rc4, out4, _ = run("proxy", dict(penv, D9REC_OUT=replay.wine_path(s3), D9REC_FLUSH="1"), "flush")
    check(rc4 == 0 and out4 == out0 and os.path.exists(s1) and os.path.exists(s3) and open(s1, "rb").read() == open(s3, "rb").read(), f"D9REC_FLUSH=1 run exit {rc4}: same output, stream byte-identical")
    # decode and compare every lock
    if os.path.exists(s1):
        try: hdr, recs = R.read_stream(open(s1, "rb").read(), spec); check(True, "decode")
        except R.ReadError as e: check(False, f"stream does not decode: {e}"); recs = []
        L = [r for r in recs if r.name in ("LockRect", "LockBox")]; U = [r for r in recs if r.name in ("UnlockRect", "UnlockBox")]
        check(len(L) == len(lks), f"stream has {len(L)} lock records, probe made {len(lks)} locks")
        ul = [l for l in lks if l["ok"]]
        check(len(U) == len(ul), f"stream has {len(U)} unlock records, probe made {len(ul)} successful unlocks")
        nl = nu = nbytes = 0; bad = []
        for lk, rec in zip(lks, L):
            ex = extent(lk); dat = [v for (l, c, rc, v) in rec.items if l.startswith("lock:") and l.endswith(":data")]
            if not lk["ok"]: continue
            if not (lk["fl"] & 0x2000):   # no DISCARD: bytes at Lock return
                v = dat[0] if dat and dat[0] is not None else None
                if v is None or len(v) != ex: bad.append(f"lock #{lks.index(lk)} {lk['tag']} {lk['f']} {lk['W']}x{lk['H']} L{lk['L']}: lock bytes {None if v is None else len(v)} != {ex}")
                elif lk["rd"] >= 0 and v != pat(lk["rd"], ex): bad.append(f"lock #{lks.index(lk)} {lk['tag']} L{lk['L']}: lock bytes differ from the pattern the probe read back")
                nl += 1; nbytes += ex
        # unlock records in call order <-> successful locks in call order (the probe unlocks in lock order)
        for lk, rec in zip(ul, U):
            ex = extent(lk); cl = [(c, rc, v) for (l, c, rc, v) in rec.items if l == "close:data"]
            ro = bool(lk["fl"] & 0x10)
            if ro:
                if cl and cl[0][1]: bad.append(f"unlock of READONLY lock {lk['tag']} L{lk['L']} carries bytes")
                continue
            v = cl[0][2] if cl and cl[0][1] else None
            if v is None or len(v) != ex: bad.append(f"unlock {lk['tag']} {lk['f']} {lk['W']}x{lk['H']} L{lk['L']} via={lk['via']}: unlock bytes {None if v is None else len(v)} != {ex}"); continue
            want = lk["seed"] if lk["seed"] is not None else lk["rd"]
            if want is not None and want >= 0 and v != pat(want, ex): bad.append(f"unlock {lk['tag']} L{lk['L']}: bytes differ from the pattern (seed {want})")
            nu += 1; nbytes += ex
        check(not bad, f"{len(bad)} lock/unlock records wrong; first: {bad[:3]}")
        print(f"checked: {len(lks)} probe locks, {nl} lock byte-runs, {nu} unlock byte-runs, {nbytes} bytes; bad {len(bad)}")
        for b in bad[:6]: print("  ", b[:200])
finally:
    x.close()
print(f"test_reclock: {npass} checks passed, {len(fails)} failed"); sys.exit(1 if fails else 0)
