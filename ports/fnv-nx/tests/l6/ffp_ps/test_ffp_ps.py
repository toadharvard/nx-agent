#!/usr/bin/env python3
"""T-FFP-PS: D3D9 fixed-function pixel pipeline (render/d3d9/ffp). Run: python3 test_ffp_ps.py [--no-gl] [--quick]
 (a) CPU reference (ffp_ps_ref.c) vs the independent oracle (oracle.py): full cross product on a 2-stage cascade
     (op 26 x arg source 7 x modifier 4 for every slot the op reads, colour and alpha, RESULTARG 2, stage-0 result 2),
     directed anchors with hand-computed values, and random general states (all 8 stages, bump/projective/PM/invalid).
 (b) ffp.frag.glsl through glslangValidator and uam (each reported if present).
 (c) GLSL on llvmpipe (EGL surfaceless, 1x1 target) vs the CPU reference, >= 2000 states, tolerance 1/255."""
import multiprocessing, os, random, shutil, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oracle
FFP = os.path.join(HERE, "../../../render/d3d9/ffp")
OUT = os.environ.get("FFP_OUT", "/tmp/claude-0/ffp")
os.makedirs(OUT, exist_ok=True)
UAM = os.environ.get("UAM", "/home/user/work/l6/sm/deps/uam/build/uam")
TEX = [[0.90, 0.20, 0.50, 0.60], [0.30, 0.75, 0.45, 0.85]]
def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]
def fbits(x): return struct.unpack("<I", struct.pack("<f", x))[0]
USED = {op: set(oracle.slots(op)) for op in range(1, 27)}
Q = {"a": 0}

def build(name, srcs, extra=()):
    exe = os.path.join(OUT, name)
    cmd = ["gcc", "-std=c99", "-O1", "-Wall", "-o", exe] + [os.path.join(HERE, s) for s in srcs] + \
          [os.path.join(FFP, "ffp_ps_ref.c"), os.path.join(FFP, "ffp_state.c")] + list(extra) + ["-lm"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print(r.stderr); sys.exit("build failed: " + name)
    return exe

def arg28(i): return i % 7 | (0x10 if (i // 7) & 1 else 0) | (0x20 if (i // 7) & 2 else 0)

def sweep_cases():
    for s0 in range(2):
        for chan in range(2):
            for op in range(1, 27):
                u = USED[op]
                n0, n1, n2 = (28 if 0 in u else 1), (28 if 1 in u else 1), (28 if 2 in u else 1)
                for i0 in range(n0):
                    for i1 in range(n1):
                        for i2 in range(n2):
                            for res in range(2):
                                a = (arg28(i0) if 0 in u else 0x13, arg28(i1) if 1 in u else 0x13, arg28(i2) if 2 in u else 0x13)
                                yield (s0, chan, op) + a + (res,), sweep_words(s0, chan, op, a, res)

def sweep_words(s0, chan, op, a, res):
    w = []
    for s in range(8): w += [1, 1, 0, 1, 0x13, 0x13, 0x13, 0, 0x13, 0x13, 0x13, 0x40CC7F19 if s == 0 else 0xB35A8D2F, 0, 0, 0, 0, 0, 0]
    p0, p1 = 0, 18
    w[p0 + 0], w[p0 + 4], w[p0 + 5], w[p0 + 6] = 8, 0, 2, 0
    w[p0 + 1], w[p0 + 9], w[p0 + 2] = 2, 2, 5 if s0 else 1
    w[p1 + 2] = 5 if res else 1
    if chan == 0:
        w[p1 + 0], w[p1 + 4], w[p1 + 5], w[p1 + 6] = op, a[0], a[1], a[2]
        w[p1 + 1], w[p1 + 9], w[p1 + 10] = 4, 1, 2
    else:
        w[p1 + 1], w[p1 + 8], w[p1 + 9], w[p1 + 10] = op, a[0], a[1], a[2]
        w[p1 + 0], w[p1 + 5], w[p1 + 6] = 4, 1, 2
    d, sp = [f32(v) for v in (0.8, 0.3, 0.6, 0.7)], [f32(v) for v in (0.1, 0.55, 0.2, 0.4)]
    return w + [0x66CC3399, 1] + [fbits(v) for v in d + sp] + [0] * 32

def const_sampler(s, t, c): return TEX[s & 1]

def sweep_chunk(rng):
    lo, hi, data = rng
    bad, first = 0, None
    for i, (key, w) in enumerate(sweep_cases()):
        if i < lo: continue
        if i >= hi: break
        k = struct.unpack_from("<7I4f", data, i * 44)
        if k[:7] != key: return (1, "key mismatch at %d: %s vs %s" % (i, k[:7], key))
        e = oracle.run(w, const_sampler)
        if max(abs(e[j] - k[7 + j]) for j in range(4)) > 2e-5:
            bad += 1
            if first is None: first = (i, key, e, k[7:])
    return (bad, first)

def test_sweep(drv):
    data = subprocess.run([drv, "sweep"], capture_output=True).stdout
    n = len(data) // 44
    step = 20000
    ranges = [(lo, min(lo + step, n), data) for lo in range(0, n, step)]
    with multiprocessing.Pool() as pool:
        res = pool.map(sweep_chunk, ranges)
    bad = sum(r[0] for r in res)
    for r in res:
        if r[1]: print("  first fail:", r[1]); break
    print("(a) sweep: cases %d failed %d" % (n, bad)); return bad

def rec(stages, tf=0x80FF8040, spec=1, diffuse=(0.8, 0.3, 0.6, 0.7), specular=(0.1, 0.5, 0.2, 0.4), tcs=None):
    w = []
    for s in range(8):
        st = stages[s] if s < len(stages) else dict(op=1)
        w += [st.get("op", 1), st.get("aop", 1), st.get("res", 1), st.get("tt", 1), st.get("c0", 0), st.get("c1", 0), st.get("c2", 0),
              st.get("ttff", 0), st.get("a0", 0), st.get("a1", 0), st.get("a2", 0), st.get("cst", 0xFF808080)] + \
             [fbits(v) for v in st.get("bump", (0, 0, 0, 0))] + [fbits(v) for v in st.get("lum", (1, 0))]
    tcs = tcs or [[0.3 + 0.1 * s, 0.6, 0.2, 1.0] for s in range(8)]
    return w + [tf, spec] + [fbits(v) for v in list(diffuse) + list(specular)] + [fbits(v) for t in tcs for v in t]

def run_general(drv, recs):
    blob = b"".join(struct.pack("<186I", *r) for r in recs)
    out = subprocess.run([drv, "general", write(blob)], capture_output=True).stdout
    return [struct.unpack_from("<4f", out, 16 * i) for i in range(len(recs))]

def write(blob):
    p = os.path.join(OUT, "general.bin"); open(p, "wb").write(blob); return p

def anchors():
    """(name, record, expected rgba) - values computed by hand from the documentation formulas."""
    A = []
    A.append(("stage0 DISABLE = diffuse (+specular)", rec([], spec=0), [0.8, 0.3, 0.6, 0.7]))
    A.append(("specular added to RGB only, clamped", rec([]), [0.9, 0.8, 0.8, 0.7]))
    # MODULATE tex0 x diffuse; alpha SELECTARG1 diffuse; tex0 colour from texel() is not hand-friendly -> no texture bound
    A.append(("MODULATE diffuse x tfactor, alpha MODULATE", rec([dict(op=4, aop=4, tt=0, c1=0, c2=3, a1=0, a2=3)], spec=0, tf=0x80FF8040),
              [0.8 * 1.0, 0.3 * 128 / 255, 0.6 * 64 / 255, 0.7 * 128 / 255]))
    A.append(("DOTPRODUCT3 replicates to alpha", rec([dict(op=24, aop=2, tt=0, c1=6, c2=6, cst=0xFFC0C0C0)], spec=0),
              [4 * 3 * (192 / 255 - 0.5) ** 2] * 4))
    A.append(("TEXTURE with no texture bound -> SELECTARG1 CURRENT", rec([dict(op=4, aop=2, tt=0, c1=2, c2=0, a1=0)], spec=0),
              [0.8, 0.3, 0.6, 0.7]))
    A.append(("out-of-range op 99 = NOP, alpha op still runs", rec([dict(op=99, aop=2, tt=0, a1=3)], spec=0, tf=0x33FF0000),
              [0.8, 0.3, 0.6, 0.2]))
    A.append(("RESULTARG TEMP leaves CURRENT, next stage reads TEMP", rec([dict(op=2, aop=2, res=5, tt=0, c1=3, a1=3),
              dict(op=2, aop=2, tt=0, c1=5, a1=5)], spec=0, tf=0xFF336699), [0.2, 0.4, 0.6, 1.0]))
    A.append(("ALPHAOP DISABLE keeps CURRENT alpha", rec([dict(op=2, aop=1, tt=0, c1=3)], spec=0, tf=0x10FF0000), [1.0, 0.0, 0.0, 0.7]))
    k = 0x80 / 255
    A.append(("LERP = (Arg1-Arg2)*Arg0+Arg2 (Arg0 constant 0x80)", rec([dict(op=26, aop=2, tt=0, c0=6, c1=3, c2=0, a1=0, cst=0xFF808080)], spec=0, tf=0xFFFFFFFF),
              [(1 - 0.8) * k + 0.8, (1 - 0.3) * k + 0.3, (1 - 0.6) * k + 0.6, 0.7]))
    return A

def rand_word_op(r):
    return r.choice([r.randint(1, 26)] * 12 + [0, 27, 99, 255, 0xFFFFFFFF, r.randint(0, 2 ** 32 - 1)])

def rand_arg(r):
    v = r.choice([r.randint(0, 6)] * 6 + [r.randint(7, 15)]) | r.choice([0, 0, 0x10, 0x20, 0x30])
    return v | (r.choice([0, 0, 0, 0x40, 0xFFFFFF00]) if r.random() < 0.2 else 0)

def rand_case(r):
    n = r.choice([0, 1, 2, 3, 4, 8, 8, 5, 6, 7]); stages = []
    for s in range(n):
        stages.append(dict(op=rand_word_op(r), aop=rand_word_op(r), res=r.choice([1, 1, 5, 5, 0, 7, 0x15]),
                           tt=r.choice([0, 1, 1, 1, 2, 3, 4, 255]), c0=rand_arg(r), c1=rand_arg(r), c2=rand_arg(r),
                           a0=rand_arg(r), a1=rand_arg(r), a2=rand_arg(r),
                           ttff=r.choice([0, 0, 2, 3, 4, 1, 256 | 2, 256 | 3, 256 | 4, 256 | 1, 256, 5, 256 | 255]),
                           cst=r.randint(0, 2 ** 32 - 1), bump=[r.uniform(-.5, .5) for _ in range(4)], lum=(r.uniform(.3, 2), r.uniform(-.5, .5))))
    for s in stages:                                   # make bump / lum / premodulate / blends common
        if r.random() < 0.25: s["op"] = r.choice([22, 23, 17, 13, 15, 12, 24])
    tcs = [[r.uniform(.25, 1.5) for _ in range(4)] for _ in range(8)]
    if r.random() < 0.1: tcs[r.randrange(8)][r.randrange(4)] = 0.0
    return rec(stages, r.randint(0, 2 ** 32 - 1), r.choice([0, 1, 7]), [r.random() for _ in range(4)], [r.random() for _ in range(4)], tcs)

def test_general(drv):
    r = random.Random(20261110)
    recs = [x[1] for x in anchors()] + [rand_case(r) for _ in range(20000)]
    got = run_general(drv, recs)
    bad = 0
    for i, (w, g) in enumerate(zip(recs, got)):
        e = oracle.run(w, oracle.texel)
        if max(abs(e[j] - g[j]) for j in range(4)) > 3e-4:
            bad += 1
            if bad <= 3: print("  general fail #%d (%s): oracle %s ref %s" % (i, anchors()[i][0] if i < len(anchors()) else "random", e, g))
    nab = 0
    for (name, w, exp), g in zip(anchors(), got):
        if max(abs(exp[j] - g[j]) for j in range(4)) > 1e-5:
            nab += 1; print("  anchor FAIL:", name, exp, g)
    print("(a) general: cases %d failed %d; hand-computed anchors %d failed %d" % (len(recs), bad, len(anchors()), nab))
    return bad + nab

def test_compilers():
    ok = 0
    for var, defs in (("plain", []), ("flat", ["NX_FFP_FLAT"])):
        flat = os.path.join(OUT, "ffp_%s.frag" % var)
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "flatten.py"), os.path.join(FFP, "ffp.frag.glsl"), flat] + defs, check=True)
        for name, cmd in (("glslangValidator", ["glslangValidator", "-S", "frag", flat]), ("uam", [UAM, "-s", "frag", "-o", flat + ".dksh", flat])):
            if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]):
                print("(b) %-16s %-5s NOT AVAILABLE" % (name, var)); continue
            r = subprocess.run(cmd, capture_output=True, text=True)
            print("(b) %-16s %-5s %s" % (name, var, "accepted" if r.returncode == 0 else "REJECTED\n" + (r.stdout + r.stderr)[:600]))
            ok += r.returncode == 0
    return 0

def test_gl():
    src = os.path.join(HERE, "gl_driver.c")
    r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-o", os.path.join(OUT, "gl_driver"), src, os.path.join(FFP, "ffp_ps_ref.c"),
                        os.path.join(FFP, "ffp_state.c"), "-lEGL", "-lGL", "-lm"], capture_output=True, text=True)
    if r.returncode: print("(c) GL: build failed\n" + r.stderr[:800]); return 1
    r = random.Random(77)
    recs = []
    for op in range(1, 27):                            # stratified: every op in the colour and in the alpha slot of stage 0
        for chan in range(2):
            for _ in range(46):
                w = rand_case(r)
                if chan == 0: w[0] = op
                else: w[0], w[1] = r.randint(2, 26), op
                recs.append(w)
    p = os.path.join(OUT, "gl_cases.bin"); open(p, "wb").write(b"".join(struct.pack("<186I", *w) for w in recs))
    res = subprocess.run([os.path.join(OUT, "gl_driver"), p, os.path.join(OUT, "ffp_plain.frag")], capture_output=True, text=True)
    sys.stdout.write(res.stdout[-1500:])
    if res.returncode >= 2: print("(c) GL: could not run (rc %d): %s" % (res.returncode, res.stderr[-600:])); return 1
    print("    " + res.stderr.strip().splitlines()[0] if res.stderr.strip() else "")
    return res.returncode

if __name__ == "__main__":
    fails = 0
    drv = build("ffp_driver", ["ffp_driver.c"])
    if "--quick" not in sys.argv: fails += test_sweep(drv)
    fails += test_general(drv)
    fails += test_compilers()
    if "--no-gl" not in sys.argv: fails += test_gl()
    print("RESULT:", "PASS" if fails == 0 else "FAIL (%s)" % fails)
    sys.exit(1 if fails else 0)
