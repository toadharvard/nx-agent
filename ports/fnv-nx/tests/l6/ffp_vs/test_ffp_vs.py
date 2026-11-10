#!/usr/bin/env python3
"""T-FFP-VS: D3D9 fixed-function vertex pipeline (render/d3d9/ffp/ffp_vs_*, ffp.vert.glsl). Run: python3 test_ffp_vs.py [--no-gl] [--quick]
 (a) CPU reference (ffp_vs_ref.c + ffp_vs_state.c) vs the independent float64 oracle (oracle.py): hand-computed anchors,
     the structured sweep (every value of every enumeration x random bases) and >= 20000 random full states.
     Tolerance |ref - oracle| <= 3e-4 * max(1, |oracle|): the only difference is float32 vs float64 rounding of chains of
     <= ~40 flops incl. powf/expf/sqrtf (<= 2e-6 each, times pow exponents <= 40 and the matrix condition number of
     the generated, well-conditioned matrices); semantic differences are >= 1e-2 on this data.
 (b) ffp.vert.glsl (plain and NX_FFP_FLAT) through glslangValidator and uam.
 (c) GLSL on llvmpipe (transform feedback) vs the CPU reference, whole sweep + random states, tolerance 1e-4 relative.
 (d) mutation checks: seeded defects in the reference / state code / GLSL must be caught by (a) and (c)."""
import multiprocessing, os, random, shutil, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen, oracle, vsrec
FFP = os.path.join(HERE, "../../../render/d3d9/ffp")
OUT = os.environ.get("FFP_VS_OUT", "/tmp/claude-0/ffpvs")
os.makedirs(OUT, exist_ok=True)
UAM = os.environ.get("UAM", "/home/user/work/l6/sm/deps/uam/build/uam")
TOLA = 3e-4
NAMES = ["pos%d" % i for i in range(4)] + ["c0_%d" % i for i in range(4)] + ["c1_%d" % i for i in range(4)] + \
        ["tex%d.%d" % (s, i) for s in range(8) for i in range(4)] + ["fog", "psize"] + ["clip%d" % i for i in range(6)]

def build(name, src_over=None, extra=()):
    srcs = {k: os.path.join(FFP, k) for k in ("ffp_vs_ref.c", "ffp_vs_state.c")}
    for k, v in (src_over or {}).items(): srcs[k] = v
    exe = os.path.join(OUT, name)
    r = subprocess.run(["gcc", "-std=c99", "-O1", "-Wall", "-I" + FFP, "-o", exe, os.path.join(HERE, "vs_driver.c")] + list(srcs.values()) + ["-lm"], capture_output=True, text=True)
    if r.returncode: print(r.stderr[:1500]); sys.exit("build failed: " + name)
    return exe

def run_driver(exe, blob):
    r = subprocess.run([exe], input=blob, capture_output=True)
    return struct.unpack("<%df" % (len(r.stdout) // 4), r.stdout)

def _orc(b): return oracle.run(vsrec.unpack(b))

def close(a, b, tol): return abs(a - b) <= tol * max(1.0, abs(b)) or a == b or (a != a and b != b)

def compare(labels, blobs, got, exp, tol=TOLA, show=3):
    bad = 0
    for i in range(len(blobs)):
        g, e = got[52 * i:52 * i + 52], exp[i]
        for k in range(52):
            if not close(g[k], float(e[k]), tol):
                bad += 1
                if bad <= show: print("   MISMATCH %s: %s ref %.7g oracle %.7g" % (labels[i], NAMES[k], g[k], float(e[k])))
                break
    return bad

# ---- anchors: hand-computed values (identity state + one feature each)
def eye():
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
def tr(x, y, z): m = eye(); m[12:15] = [x, y, z]; return m
def base():
    r = dict(decl=[0] * 20, rs=[0] * 210, tcIndex=list(range(8)), ttff=[0] * 8, view=eye(), proj=eye(), world0=eye(), texm=eye() * 8,
             clip=[0.0] * 24, mat=[0.0] * 20, vp=[0.0, 0.0, 100.0, 100.0], nLights=[0], lights=[0] * 416, posfix=[0.0, 0.0, 1.0, 0.0],
             pos=[1.0, 2.0, 3.0, 1.0], pos1=[0.0] * 4, n1=[0.0] * 4, bw=[0.0] * 4, bi=[0.0] * 4, n=[0.0, 0.0, 1.0, 0.0], c0=[0.0] * 4,
             c1=[0.0] * 4, psize=[0.0] * 4, tex=[0.0] * 32, palette=eye() * 16)
    r["rs"][154] = vsrec.bits(2.0); r["rs"][155] = vsrec.bits(0.0); r["rs"][166] = vsrec.bits(1000.0); return r
def light(typ, d=(1.0, 1.0, 1.0, 1.0), pos=(0, 0, 0), dr=(0, 0, 1), rng=100.0):
    return [typ] + [vsrec.bits(x) for x in list(d) + [0.0] * 8 + list(pos) + list(dr) + [rng, 0.0, 1.0, 0.0, 0.0, 1.0, 2.0]]

def anchors():
    A = []
    def add(name, setup, exp):
        r = base(); setup(r); A.append((name, r, exp))
    add("defaults: no lighting, no colours", lambda r: None, {0: 1, 1: 2, 2: 3, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 0, 11: 0, 44: 1, 45: 2.0, 46: 1})
    def a2(r): r["view"] = tr(0, 0, 5); r["proj"][11] = 1.0; r["proj"][15] = 0.0
    add("view translate z+5, w = z", a2, {0: 1, 1: 2, 2: 8, 3: 8})
    def a3(r):
        r["rs"][137] = 1; r["decl"][3] = 1; r["mat"][0:4] = [.5, .5, .5, .5]; r["nLights"] = [1]; r["lights"][0:26] = light(3, dr=(0, 0, -1))
    add("directional light, N.L = 1: material diffuse * light diffuse", a3, {4: .5, 5: .5, 6: .5, 7: .5, 8: 0})
    def a4(r): r["view"] = tr(0, 0, 5 - 3); r["rs"][28] = 1; r["rs"][140] = 3; r["rs"][36] = vsrec.bits(0.0); r["rs"][37] = vsrec.bits(10.0)
    add("linear vertex fog: depth 5 of 0..10", a4, {44: 0.5})
    def a5(r): r["rs"][143] = 1; r["decl"][3] = 1; r["n"] = [1.0, 0, 0, 0]; r["pos"] = [0.0, 0.0, 5.0, 1.0]; r["tcIndex"][0] = 0x40000
    add("SPHEREMAP with r = view dir (m = 0): u = v = 0.5", a5, {12: .5, 13: .5, 14: 0, 15: 1})
    def a6(r): r["rs"][157] = 1; r["rs"][158] = vsrec.bits(1.0)
    add("point scale: Vh * size / sqrt(A) = 100 * 2", a6, {45: 200.0})
    def a7(r):
        r["rs"][151] = 1; r["decl"][1] = 1; r["bw"] = [0.25, 0, 0, 0]; r["pos"] = [0.0, 0.0, 0.0, 1.0]
        r["palette"][0:16] = tr(4, 0, 0); r["palette"][16:32] = tr(-4, 0, 0); r["world0"] = tr(4, 0, 0)
    add("1 weight: 0.25 * (+4) + 0.75 * (-4) = -2", a7, {0: -2.0})
    def a8(r):
        r["decl"][10] = 2; r["tex"][0:2] = [0.5, 0.25]; r["ttff"][0] = 2
        r["texm"][0:16] = [1, 0, 0, 0, 0, 1, 0, 0, .25, .5, 0, 0, 0, 0, 0, 0]
    add("COUNT2 on a 2-component set: (u,v,1,0) * M = (.75,.75,0,0)", a8, {12: .75, 13: .75, 14: 0, 15: 0})
    def a9(r):
        r["decl"][0] = 1; r["pos"] = [50.0, 25.0, 0.5, 0.5]
    add("POSITIONT in 100x100, rhw 0.5: ndc (0,0.5,.5), w 2", a9, {0: 0.0, 1: 1.0, 2: 1.0, 3: 2.0})
    return A

def test_anchors(exe):
    A = anchors(); got = run_driver(exe, b"".join(vsrec.pack(r) for _, r, _ in A)); bad = 0
    for i, (n, r, exp) in enumerate(A):
        for k, v in exp.items():
            if abs(got[52 * i + k] - v) > 1e-5: bad += 1; print("   anchor FAIL %s: %s = %g want %g" % (n, NAMES[k], got[52 * i + k], v))
    print("(a) anchors: %d cases, %d failed" % (len(A), bad)); return bad

def mutations_ref():
    return [("ffp_vs_ref.c", "rho > L->cone[0] ? 1.0f", "rho > L->cone[1] ? 1.0f"), ("ffp_vs_ref.c", "wt[nb] = 1.0f - sum;", "wt[nb] = 1.0f;"),
            ("ffp_vs_ref.c", "r[k] = e[k] - d2 * normal[k]", "r[k] = e[k] + d2 * normal[k]"),
            ("ffp_vs_ref.c", "f = expf(-(d * dn) * (d * dn));", "f = expf(-(d * dn));"),
            ("ffp_vs_ref.c", "vm3(nn, st->normalMat, nr)", "vm3(nn, st->view, nr)"),
            ("ffp_vs_ref.c", "float de = sqrtf(dot3(ec, ec)), den", "float de = fabsf(ec[2]), den"),
            ("ffp_vs_ref.c", "!posT && st->rsMisc[1] != 0 && ((", "!posT && (("),
            ("ffp_vs_state.c", "cos((double)l->theta * 0.5)", "cos((double)l->theta)"),
            ("ffp_vs_state.c", "out[i] /= d;", "out[i] /= 1.0;")]
def mutations_glsl():
    return [("float spot = 1.0;", "float spot = 0.5;"), ("wt[nb] = 1.0 - sum;", "wt[nb] = 1.0;"),
            ("vec3 r = e - 2.0 * dot(normal, e) * normal;", "vec3 r = e + 2.0 * dot(normal, e) * normal;"),
            ("gl_PointSize = s > point.z ? point.z : s;", "gl_PointSize = s;"),
            ("f = exp(-(d * dn) * (d * dn));", "f = exp(-(d * dn));"),
            ("if (dist > light[i].direction.w) continue;", "if (dist >= light[i].direction.w * 2.0) continue;")]

def flat(variant):
    p = os.path.join(OUT, "ffp_%s.vert" % variant)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "../ffp_ps/flatten.py"), os.path.join(FFP, "ffp.vert.glsl"), p] + (["NX_FFP_FLAT"] if variant == "flat" else []), check=True)
    return p

def test_compilers():
    bad = 0
    for var in ("plain", "flat"):
        f = flat(var)
        for name, cmd in (("glslangValidator", ["glslangValidator", "-S", "vert", f]), ("uam", [UAM, "-s", "vert", "-o", f + ".dksh", f])):
            if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]): print("(b) %-16s %-5s NOT AVAILABLE" % (name, var)); bad += 1; continue
            r = subprocess.run(cmd, capture_output=True, text=True)
            print("(b) %-16s %-5s %s" % (name, var, "accepted" if r.returncode == 0 else "REJECTED\n" + (r.stdout + r.stderr)[:800])); bad += r.returncode != 0
    return bad

def build_gl():
    r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-I" + FFP, "-o", os.path.join(OUT, "vs_gl"), os.path.join(HERE, "vs_gl.c"), os.path.join(FFP, "ffp_vs_ref.c"),
                        os.path.join(FFP, "ffp_vs_state.c"), "-lEGL", "-lGL", "-lm"], capture_output=True, text=True)
    if r.returncode: print("(c) GL: build failed\n" + r.stderr[:800]); return False
    return True

def run_gl(vert, path):
    return subprocess.run([os.path.join(OUT, "vs_gl"), path, vert], capture_output=True, text=True)

if __name__ == "__main__":
    quick, fails = "--quick" in sys.argv, 0
    exe = build("vs_driver")
    wc = int(subprocess.run([exe, "--words"], capture_output=True, text=True).stdout)
    if wc != vsrec.WORDS: sys.exit("record layout mismatch C %d python %d" % (wc, vsrec.WORDS))
    fails += test_anchors(exe)
    rng = random.Random(20261110)
    sw = gen.sweep_recs(rng, 4 if quick else 12)
    rr = [("random#%d" % i, gen.rand_rec(rng)) for i in range(2000 if quick else 20000)]
    allr = sw + rr
    blobs = [vsrec.pack(r) for _, r in allr]; labels = [l for l, _ in allr]
    with multiprocessing.Pool() as pool: exp = pool.map(_orc, blobs, chunksize=200)
    got = run_driver(exe, b"".join(blobs))
    nb = compare(labels, blobs, got, exp); fails += nb
    worst = max((abs(got[52 * i + k] - float(exp[i][k])) / max(1.0, abs(float(exp[i][k]))) for i in range(len(blobs)) for k in range(52)
                 if got[52 * i + k] == got[52 * i + k] and abs(float(exp[i][k])) < 1e30), default=0)
    print("(a) sweep %d + random %d states (%d outputs each): failed %d, worst error %.3g (tolerance %.0e)" % (len(sw), len(rr), 52, nb, worst, TOLA))
    fails += test_compilers()
    glpath = os.path.join(OUT, "gl_cases.bin")
    gl_sel = [i for i in range(len(allr)) if i < len(sw) or i % 20 == 0]
    open(glpath, "wb").write(b"".join(blobs[i] for i in gl_sel))
    glok = False
    if "--no-gl" not in sys.argv:
        if build_gl():
            res = run_gl(flat("plain"), glpath); sys.stdout.write(res.stdout[-1500:])
            if res.returncode >= 2: print("(c) GL: FAILING - could not run (rc %d): %s" % (res.returncode, res.stderr[-800:])); fails += 1
            else: glok = res.returncode == 0; fails += res.returncode
        else: fails += 1
    # (d) mutations
    msub = [i for i in range(len(allr)) if i < len(sw) or i % 4 == 0]
    mblob = b"".join(blobs[i] for i in msub); mexp = [exp[i] for i in msub]; mlab = [labels[i] for i in msub]
    caught = 0; ms = mutations_ref()
    for j, (fn, old, new) in enumerate(ms):
        src = open(os.path.join(FFP, fn)).read()
        if src.count(old) != 1: print("(d) mutation %d: pattern not found exactly once: %r" % (j, old)); fails += 1; continue
        p = os.path.join(OUT, "mut_" + fn); open(p, "w").write(src.replace(old, new))
        e2 = build("vs_mut%d" % j, {fn: p})
        n = compare(mlab, [None] * len(msub), run_driver(e2, mblob), mexp, show=0)
        print("(d) ref mutation %d (%s): %s (%d states differ)" % (j, new[:40].strip(), "caught" if n else "NOT CAUGHT", n)); caught += n > 0; fails += n == 0
    if glok:
        gblob = glpath
        for j, (old, new) in enumerate(mutations_glsl()):
            src = open(flat("plain")).read()
            if src.count(old) != 1: print("(d) GLSL mutation %d: pattern not found exactly once" % j); fails += 1; continue
            p = os.path.join(OUT, "mut.vert"); open(p, "w").write(src.replace(old, new))
            res = run_gl(p, glpath)
            print("(d) GLSL mutation %d (%s): %s" % (j, new[:40], "caught" if res.returncode == 1 else "NOT CAUGHT (rc %d)" % res.returncode)); fails += res.returncode != 1
    print("RESULT:", "PASS" if fails == 0 else "FAIL (%s)" % fails)
    sys.exit(1 if fails else 0)
