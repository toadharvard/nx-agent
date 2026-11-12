#!/usr/bin/env python3
"""T-SM-LINK: the VS/PS interface rule (README "Varying completion"): every translated vertex shader writes the fog factor at location 18 and
every varying a pixel stage can read with the value the reference (WineD3D 9.0) defines, so every (VS, PS) pair LINKS on llvmpipe GL 4.5 core and
reads defined values.   python3 -I tests/l6/sm_link/test_sm_link.py --build DIR [--work DIR] [--jobs 2] [--part NAME]
  --build DIR   the shader host build root (render/shaders/build.sh output): DIR/host/sdp2dksh, DIR/deps/mojoshader/mojoshader_internal.h (REQUIRED:
                without it the test fails, there is no skip)
Synthetic shaders only (hand-assembled with render/shaders/total/sm_hand.py, read-only import).  Parts:
  rule   fnvnx_dk_vs_needs_complete() of the header against an independent python statement of the rule, exhaustively over (VS pair, PS pair);
         the canonical varying table is a bijection onto the 31 slots 0..30 (the totality argument of the header)
  total  every one of the 224 (usage, index) pairs read by a ps_3_0 that a vs_3_0 does not write: links, reads the defined default
  link   every stage model pair class (vs_1_1/2_0/2_x/3_0 x ps_1_1/1_2/1_3/1_4/2_0/2_x/3_0) x VS outputs with / without oFog, each colour, each texcoord
         (whole and partial) x every PS probe links with the VS variant the rule selects; the other variant is not needed
  values the rendered values of every PS input equal the rule (written components: the VS's value, the others: the default, COLOR0's from the driver word)
  fog    the fog factor (ps < 3.0 epilogue; ps_3_0: none) equals an independent python model for a grid of FOGENABLE / table mode / start / end /
         density / depth / oFog written or not / oFog value
  uam    uam accepts every shader of the matrix (both variants)
  mutate >= 6 mutations of the translator's GLSL, each must be caught by the same checks"""
import argparse, itertools, math, os, re, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "render", "shaders", "total"))
import sm_hand, mutsrc

GLENV = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", EGL_PLATFORM="surfaceless")

# ---- the canonical varying table, stated independently of the header (D3DDECLUSAGE numbering)
POSITION, BLENDWEIGHT, BLENDINDICES, NORMAL, PSIZE, TEXCOORD, TANGENT, BINORMAL, TESSFACTOR, POSITIONT, COLOR, FOG, DEPTH, SAMPLE = range(14)
UNAMES = ["position", "blendweight", "blendindices", "normal", "psize", "texcoord", "tangent", "binormal", "tessfactor", "positiont", "color", "fog", "depth", "sample"]
def canon(u, i):
    if u == COLOR and i < 2: return i
    if u == TEXCOORD and i < 16: return 2 + i
    if u == FOG and i == 0: return 18
    if u == NORMAL and i < 2: return 19 + i
    if u == TANGENT and i == 0: return 21
    if u == BINORMAL and i == 0: return 22
    if u == BLENDWEIGHT and i == 0: return 23
    if u == BLENDINDICES and i == 0: return 24
    if u == POSITION and 1 <= i < 4: return 24 + i
    if u == DEPTH and i == 0: return 28
    if u == PSIZE and 1 <= i < 3: return 28 + i
    return -1
def pair(u, i): return u * 16 + i
ALLPAIRS = [(u, i) for u in range(14) for i in range(16)]

def default(u, i, d):
    """the value an unwritten component of pair (u, i) takes: WineD3D 9.0 (see the README): diffuse = d (1.0 below ps_3_0, 0.0 for ps_3_0), all else 0.0"""
    return d if (u, i) == (COLOR, 0) else 0.0

def needs_complete(vs_written, ps_read):
    """the static variant rule, stated again: the PS reads a canonical pair the VS does not write"""
    return any(canon(*divmod(q, 16)) >= 0 and q not in vs_written for q in ps_read)

class Part:
    def __init__(self, name): self.name, self.n, self.f, self.lines = name, 0, 0, []
    def check(self, ok, msg):
        self.n += 1
        if not ok:
            self.f += 1
            if self.f <= 25: self.lines.append("  FAIL " + msg)
        return ok
    def done(self):
        for l in self.lines: print(l)
        if self.f > 25: print("  ... %d more failures" % (self.f - 25))
        print("PART %s: %d checks, %d failed" % (self.name, self.n, self.f)); sys.stdout.flush()
        return self.f == 0

class Lab:
    def __init__(self, build, work):
        self.build, self.host, self.w = build, os.path.join(build, "host"), work
        self.sdp = os.path.join(self.host, "sdp2dksh")
        hdr = os.path.join(build, "deps", "mojoshader", "mojoshader_internal.h")
        for p in (self.sdp, hdr):
            if not os.path.exists(p): sys.exit("missing build artefact: " + p + " (pass --build DIR, the render/shaders/build.sh output root)")
        self.ops = sm_hand.load_opcodes(hdr); self.cache = {}; self.nid = 0
        self.gl = os.path.join(work, "gl_link"); n = os.path.join(ROOT, "render", "native")
        r = subprocess.run(["gcc", "-std=gnu11", "-O1", "-w", "-I", os.path.join(ROOT, "render", "gl4"), "-I", n, "-I", os.path.join(n, "gl"),
                            os.path.join(HERE, "gl_link.c"), os.path.join(n, "gl", "nxgl.c"), "-lEGL", "-ldl", "-lm", "-o", self.gl], capture_output=True, text=True)
        if r.returncode: sys.exit("gl_link build failed\n" + r.stderr[-800:])
    def translate(self, asm, complete=False, link=(), dksh=False):
        """asm text -> dict(ok, glsl path, json dict, msg). Cached by (text, variant)."""
        key = (asm, complete, tuple(link), dksh)
        if key in self.cache: return self.cache[key]
        self.nid += 1; nm = "t%d" % self.nid; od = os.path.join(self.w, nm); os.makedirs(od)
        try: bc = sm_hand.assemble(asm, self.ops)
        except Exception as e:
            r = dict(ok=False, glsl=None, json=None, msg="assemble: %s" % e); self.cache[key] = r; return r
        bp = os.path.join(od, "in.bin"); open(bp, "wb").write(bc)
        cmd = [self.sdp, "-o", os.path.join(od, "o"), "--glsl"] + ([] if dksh else ["--no-dksh"]) + (["--vs-complete"] if complete else []) + (["--link", ",".join("%d:%d" % l for l in link)] if link else []) + ["--blob", bp]
        od2 = os.path.join(od, "o"); os.makedirs(od2); p = subprocess.run(cmd, capture_output=True, text=True)
        g = [f for f in os.listdir(od2) if f.endswith(".glsl")] if os.path.isdir(od2) else []
        if not g:
            r = dict(ok=False, glsl=None, json=None, msg=(p.stdout + p.stderr).strip().replace("\n", " | ")[-300:])
        else:
            import json
            js = json.load(open(os.path.join(od2, g[0][:-5] + ".json")))
            ix = open(os.path.join(od2, "index.tsv")).read().splitlines()[1:]
            ok = True if not dksh else (ix[0].split("\t")[4] == "ok")
            r = dict(ok=ok, glsl=os.path.join(od2, g[0]), json=js, msg="" if ok else "uam: " + ix[0].split("\t")[-1][:200])
        self.cache[key] = r; return r
    def run(self, jobs):
        """jobs: list of (id, vs glsl, ps glsl, vs consts {n: 4 floats}, ps consts, vs drv {word: v}, ps drv {word: v}) -> {id: ('ok', r, g, b, a, depth) | ('link_fail', msg) | ...}"""
        self.nid += 1; jp = os.path.join(self.w, "jobs%d.txt" % self.nid); L = []
        for (jid, vs, ps, vc, pc, vd, pd) in jobs:
            L.append("job %s %s %s" % (jid, vs, ps))
            for n, v in sorted(vc.items()): L.append("vc %d %s" % (n, " ".join(repr(float(x)) for x in v)))
            for n, v in sorted(pc.items()): L.append("pc %d %s" % (n, " ".join(repr(float(x)) for x in v)))
            for n, v in sorted(vd.items()): L.append("vd %d %r" % (n, float(v)))
            for n, v in sorted(pd.items()): L.append("pd %d %r" % (n, float(v)))
            L.append("go")
        open(jp, "w").write("\n".join(L) + "\n")
        r = subprocess.run([self.gl, jp], capture_output=True, text=True, env=GLENV)
        if r.returncode: sys.exit("gl_link failed rc=%d: %s" % (r.returncode, r.stderr[-400:]))
        out = {}
        for ln in r.stdout.splitlines():
            w = ln.split(" ", 2)
            out[w[0]] = (w[1],) + tuple(float(x) for x in w[2].split()) if w[1] == "ok" else (w[1], w[2] if len(w) > 2 else "")
        return out

# ---- vertex shader / pixel shader generators
VS_MODELS = ["vs_1_1", "vs_2_0", "vs_2_x", "vs_3_0"]
PS_MODELS = ["ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_3_0"]
def tcv(k): return tuple(0.15 + 0.05 * k + 0.1 * c for c in range(4))                      # texcoord k's constant (c4 + k)
COL = {0: (0.12, 0.22, 0.32, 0.42), 1: (0.13, 0.23, 0.33, 0.43)}                            # c2, c3
VCONST = {1: None}                                                                          # c1.x = the fog value, set per job
def vs_consts(z, fogv):
    c = {0: (0.0, 0.0, z, 1.0), 1: (fogv, 0.0, 0.0, 0.0), 2: COL[0], 3: COL[1]}
    for k in range(8): c[4 + k] = tcv(k)
    return c
# a VS variant = (name, [(kind, index, mask)]) kind fog / color / tex
VS_VARIANTS = [
    ("bare", []), ("fog", [("fog", 0, "x")]), ("c0", [("color", 0, "xyzw")]), ("c0xy", [("color", 0, "xy")]), ("c1", [("color", 1, "xyzw")]),
    ("t0", [("tex", 0, "xyzw")]), ("t3xy", [("tex", 3, "xy")]), ("t7", [("tex", 7, "xyzw")]),
    ("all", [("fog", 0, "x"), ("color", 0, "xyzw"), ("color", 1, "xyzw")] + [("tex", k, "xyzw") for k in range(8)]),
    ("mix", [("fog", 0, "x"), ("color", 0, "xyz"), ("tex", 1, "xy"), ("tex", 5, "xyzw")])]
def vs_asm(model, outs):
    m = lambda s: "" if s == "xyzw" else "." + s
    src = lambda kind, i: "c1.x" if kind == "fog" else "c%d" % (2 + i if kind == "color" else 4 + i)
    if model != "vs_3_0":
        t = [model, "mov oPos, c0"]
        for kind, i, mk in outs:
            dst = {"fog": "oFog", "color": "oD%d" % i, "tex": "oT%d" % i}[kind]
            t.append("mov %s%s, %s" % (dst, "" if kind == "fog" else m(mk), src(kind, i)))
        return "\n".join(t) + "\n"
    t = ["vs_3_0", "dcl_position o0"]; regs = {}
    for r, (kind, i, mk) in enumerate(outs, 1):
        regs[(kind, i)] = r
        t.append("dcl_%s%s o%d%s" % ({"fog": "fog", "color": "color", "tex": "texcoord"}[kind], "" if kind == "fog" else str(i), r, m(mk)))
    t.append("mov o0, c0")
    for kind, i, mk in outs: t.append("mov o%d%s, %s" % (regs[(kind, i)], m(mk), src(kind, i)))
    return "\n".join(t) + "\n"
def vs_cfg(outs, fogv):
    """pair -> (mask bits, value vec) the VS writes"""
    cfg = {}
    for kind, i, mk in outs:
        bits = sum(1 << "xyzw".index(c) for c in mk)
        if kind == "fog": cfg[(FOG, 0)] = (bits, (fogv, 0, 0, 0))
        elif kind == "color": cfg[(COLOR, i)] = (bits, COL[i])
        else: cfg[(TEXCOORD, i)] = (bits, tcv(i))
    return cfg
def vs_written_pairs(outs): return sorted({pair(FOG, 0)} | {pair({"fog": FOG, "color": COLOR, "tex": TEXCOORD}[k], i if k != "fog" else 0) for k, i, _ in outs})

# PS probes: (label, asm, pair read, components observable)
def ps_probes(model):
    P = []
    if model in ("ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4"):
        P += [("v0", "%s\nmov r0, v0\n" % model, (COLOR, 0), 7), ("v1", "%s\nmov r0, v1\n" % model, (COLOR, 1), 7)]
        for k in ((0, 3) if model != "ps_1_4" else (0, 5)):
            # ps_1_1..1_3 read t# only through a texture instruction (the stage samples at its coordinate): the link is checked, the sampled value
            # (an unbound texture) is not part of the rule; ps_1_4 texcrd copies the coordinate: its rgb is compared
            body = "tex t%d\nmov r0, t%d\n" % (k, k) if model != "ps_1_4" else "texcrd r0.xyz, t%d\n" % k
            P.append(("t%d" % k, "%s\n%s" % (model, body), (TEXCOORD, k), 0 if model != "ps_1_4" else 7))
    elif model in ("ps_2_0", "ps_2_x"):
        P += [("v0", "%s\ndcl v0\nmov oC0, v0\n" % model, (COLOR, 0), 15), ("v1", "%s\ndcl v1\nmov oC0, v1\n" % model, (COLOR, 1), 15)]
        for k in (0, 3, 7): P.append(("t%d" % k, "%s\ndcl t%d\nmov oC0, t%d\n" % (model, k, k), (TEXCOORD, k), 15))
    else:
        P += [("color0", "ps_3_0\ndcl_color0 v0\nmov oC0, v0\n", (COLOR, 0), 15), ("color1", "ps_3_0\ndcl_color1 v0\nmov oC0, v0\n", (COLOR, 1), 15)]
        for k in (0, 3, 7, 12): P.append(("t%d" % k, "ps_3_0\ndcl_texcoord%d v0\nmov oC0, v0\n" % k, (TEXCOORD, k), 15))
        P.append(("fog0", "ps_3_0\ndcl_fog v0\nmov oC0, v0\n", (FOG, 0), 15))
    return P
def ps_fog_asm(model):
    return "%s\nmov r0, c0\n" % model if model.startswith("ps_1") else ("%s\nmov oC0, c0\n" % model)
def ps_d(model): return 0.0 if model == "ps_3_0" else 1.0      # COLOR0's default: 1.0 below ps_3_0, 0.0 for ps_3_0
def ps_pairs(lab, model, asm):
    t = lab.translate(asm); return None if not t["ok"] else t["json"]["varyings_read"]

def expected(cfg, pr, mask, d):
    """expected components of PS input pair pr given the VS cfg (written comps), default d for COLOR0"""
    bits, val = cfg.get(pr, (0, (0, 0, 0, 0)))
    return [val[c] if bits & (1 << c) else default(pr[0], pr[1], d) for c in range(4)], mask

def close(got, want, mask, tol=2e-6):
    return all(abs(got[c] - want[c]) <= tol for c in range(4) if mask & (1 << c))

# ---------------------------------------------------------------------------------------------------------------------------------------------
def part_rule(lab):
    P = Part("rule")
    # the canonical map is a bijection onto 0..30 (the totality argument: every slot a pixel stage can read is some pair's canonical slot)
    slots = sorted(canon(u, i) for u, i in ALLPAIRS if canon(u, i) >= 0)
    P.check(slots == list(range(31)), "canonical varying slots are exactly 0..30 (%d slots)" % len(slots))
    # the header's function against the python statement, exhaustively over one VS pair x one PS pair (+ the empty / full sets)
    c = os.path.join(lab.w, "rule.c"); exe = os.path.join(lab.w, "rule")
    open(c, "w").write('#include <stdio.h>\n#include "fnvnx_dk_layout.h"\nint main(void){ int a, b; for (a = -1; a < 224; a++) for (b = 0; b < 224; b++) {\n'
                       'unsigned char v[2], p[1]; int nv = 0; v[nv++] = (unsigned char)FNVNX_DK_PAIR(FNVNX_USAGE_FOG, 0); if (a >= 0) v[nv++] = (unsigned char)a; p[0] = (unsigned char)b;\n'
                       'printf("%d %d %d\\n", a, b, fnvnx_dk_vs_needs_complete(v, nv, p, 1)); } return 0; }\n')
    r = subprocess.run(["gcc", "-std=gnu11", "-I", os.path.join(ROOT, "render", "shaders", "include"), c, "-o", exe], capture_output=True, text=True)
    P.check(r.returncode == 0, "header rule program builds: " + r.stderr[-200:])
    if r.returncode == 0:
        out = subprocess.run([exe], capture_output=True, text=True).stdout.split("\n")
        bad = 0; n = 0
        for ln in out:
            if not ln: continue
            a, b, got = map(int, ln.split()); n += 1
            vs = [pair(FOG, 0)] + ([a] if a >= 0 else [])
            if needs_complete(vs, [b]) != bool(got): bad += 1
        P.check(n == 225 * 224 and bad == 0, "fnvnx_dk_vs_needs_complete == python rule over %d (VS, PS) pair combinations (%d differ)" % (n, bad))
    return P.done()

def part_total(lab):
    P = Part("total"); jobs = []; meta = {}
    vs_bare = "vs_3_0\ndcl_position o0\nmov o0, c0\n"
    for (u, i) in ALLPAIRS:
        can = canon(u, i) >= 0
        psa = "ps_3_0\ndcl_%s%d v0\nmov oC0, v0\n" % (UNAMES[u], i)
        link = [] if can else [(u, i)]
        ps = lab.translate(psa, link=link)
        if not P.check(ps["ok"], "translate ps_3_0 reading %s%d: %s" % (UNAMES[u], i, ps["msg"])): continue
        vs = lab.translate(vs_bare, complete=can, link=link)
        if not P.check(vs["ok"], "translate vs_3_0 bare (%s, link %s): %s" % ("complete" if can else "base", link, vs["msg"])): continue
        for d in ((1.0, 0.0) if (u, i) == (COLOR, 0) else (1.0,)):
            jid = "p%d_%d_%d" % (u, i, int(d)); meta[jid] = (u, i, d)
            jobs.append((jid, vs["glsl"], ps["glsl"], vs_consts(0.25, 0.0), {}, {4: d}, {}))
    res = lab.run(jobs)
    for jid, (u, i, d) in meta.items():
        r = res.get(jid)
        if not P.check(r is not None and r[0] == "ok", "%s%d: %s" % (UNAMES[u], i, r)): continue
        if (u, i) == (POSITION, 0): want = [0.0, 0.0, 0.25, 1.0]            # a PS-read POSITION0 is the raw oPos
        else: want = [default(u, i, d)] * 4
        P.check(all(abs(r[1 + c] - want[c]) <= 2e-6 for c in range(4)), "%s%d (canonical %s, d=%g): got %s want %s" % (UNAMES[u], i, canon(u, i) >= 0, d, r[1:5], want))
    return P.done()

def matrix(lab, complete_for):
    """every (vs model, vs variant) x (ps model, probe): -> list of dict(key, vsm, vsv, psm, probe, ...) with both VS variants translated"""
    M = []
    for vm in VS_MODELS:
        for vname, outs in VS_VARIANTS:
            vb = lab.translate(vs_asm(vm, outs)); vc = lab.translate(vs_asm(vm, outs), complete=True)
            for pm in PS_MODELS:
                for label, pasm, pr, mask in ps_probes(pm):
                    ps = lab.translate(pasm)
                    M.append(dict(vm=vm, vname=vname, outs=outs, pm=pm, label=label, pr=pr, mask=mask, vb=vb, vc=vc, ps=ps))
    return M

def part_link_values(lab):
    PL = Part("link"); PV = Part("values"); M = matrix(lab, None); jobs = []; meta = {}
    for m in M:
        okt = PL.check(m["vb"]["ok"] and m["vc"]["ok"] and m["ps"]["ok"], "translate %s/%s %s/%s: %s %s %s" % (m["vm"], m["vname"], m["pm"], m["label"], m["vb"]["msg"], m["vc"]["msg"], m["ps"]["msg"]))
        if not okt: m["skip"] = True; continue
        written = vs_written_pairs(m["outs"]); read = m["ps"]["json"]["varyings_read"]
        m["sel"] = needs_complete(written, read)
        # the reflection lists exactly what the rule needs
        PL.check(m["vb"]["json"]["varyings_written"] == written, "%s/%s varyings_written %s != %s" % (m["vm"], m["vname"], m["vb"]["json"]["varyings_written"], written))
        PL.check(pair(*m["pr"]) in read, "%s/%s PS probe reads its pair in varyings_read" % (m["pm"], m["label"]))
        d = ps_d(m["pm"]); fogv = 0.3
        for var, t in (("base", m["vb"]), ("complete", m["vc"])):
            jid = "m%d%s" % (len(meta), var[0]); meta[jid] = (m, var)
            jobs.append((jid, t["glsl"], m["ps"]["glsl"], vs_consts(0.25, fogv), {}, {4: d}, {}))
    res = lab.run(jobs); n_complete = 0; n_base_fail = 0
    for jid, (m, var) in meta.items():
        r = res.get(jid); sel = "complete" if m["sel"] else "base"
        tag = "%s/%s -> %s/%s [%s]" % (m["vm"], m["vname"], m["pm"], m["label"], var)
        if var == sel:
            n_complete += var == "complete"
            if PL.check(r is not None and r[0] == "ok", "selected variant links: %s: %s" % (tag, r)):
                want, mk = expected(vs_cfg(m["outs"], 0.3), m["pr"], m["mask"], ps_d(m["pm"]))
                if m["pm"].startswith("ps_1"): want = [min(max(x, 0.0), 1.0) for x in want]       # ps_1_x results are clamped
                PV.check(close(r[1:5], want, mk), "value %s: got %s want %s" % (tag, r[1:5], want))
        elif var == "complete":
            # the complete variant also links and reads the same values wherever the base variant is valid (superset)
            if r is not None and r[0] == "ok":
                want, mk = expected(vs_cfg(m["outs"], 0.3), m["pr"], m["mask"], ps_d(m["pm"]))
                if m["pm"].startswith("ps_1"): want = [min(max(x, 0.0), 1.0) for x in want]
                PV.check(close(r[1:5], want, mk), "value (complete, not selected) %s: got %s want %s" % (tag, r[1:5], want))
            else: PL.check(False, "complete variant must link everywhere: %s: %s" % (tag, r))
        else:   # base while the rule selects complete: the base VS is missing an output the PS reads, GL cannot link it
            n_base_fail += (r is None or r[0] != "ok")
            PL.check(r is not None and r[0] == "link_fail", "base variant of a pair that needs completion does not link (test not vacuous): %s: %s" % (tag, r))
    print("  link matrix: %d pairs, %d select the complete variant, %d base variants (not selected) fail to link as expected" % (len(M), n_complete, n_base_fail))
    a = PL.done(); b = PV.done(); return a and b

def fog_model(mode, z, start, end, dens, written, fogv):
    f = {0: 1.0, 1: min(max(fogv, 0.0), 1.0) if written else 0.0, 2: math.exp(-z * dens), 3: math.exp(-(z * dens) * (z * dens)), 4: (end - z) / (end - start)}[mode]
    return 1.0 if mode == 0 else min(max(f, 0.0), 1.0)

def part_fog(lab):
    P = Part("fog"); jobs = []; meta = {}
    for vm in VS_MODELS:
        for vname, outs in (VS_VARIANTS[0], VS_VARIANTS[1], VS_VARIANTS[9]):
            vs = lab.translate(vs_asm(vm, outs)); written = any(k == "fog" for k, _, _ in outs)
            for pm in PS_MODELS:
                ps = lab.translate(ps_fog_asm(pm))
                if not P.check(vs["ok"] and ps["ok"], "translate %s/%s %s: %s %s" % (vm, vname, pm, vs["msg"], ps["msg"])): continue
                for mode, z, (st, en, de), fogv in itertools.product(range(5), (0.25, 0.75), ((0.0, 1.0, 0.5), (0.2, 0.9, 1.5)), (-0.5, 0.3, 1.7)):
                    jid = "f%d" % len(meta); meta[jid] = (vm, vname, pm, mode, z, st, en, de, written, fogv)
                    jobs.append((jid, vs["glsl"], ps["glsl"], vs_consts(z, fogv), {0: (1, 1, 1, 1)}, {4: ps_d(pm)}, {4: mode, 5: st, 6: en, 7: de}))     # PS block: fogcolor = 0, fog = (mode, start, end, density)
    res = lab.run(jobs)
    for jid, (vm, vname, pm, mode, z, st, en, de, written, fogv) in meta.items():
        r = res.get(jid); tag = "%s/%s %s mode %d z %g (%g, %g, %g) oFog %s%g" % (vm, vname, pm, mode, z, st, en, de, "" if written else "unwritten ", fogv)
        if not P.check(r is not None and r[0] == "ok", "%s: %s" % (tag, r)): continue
        want = fog_model(mode if pm != "ps_3_0" else 0, z, st, en, de, written, fogv)      # ps_3_0: no fixed-function fog
        P.check(all(abs(r[1 + c] - want) <= 2e-5 for c in range(3)), "fog %s: got %s want %.6g" % (tag, r[1:4], want))
    return P.done()

def part_uam(lab):
    P = Part("uam"); seen = set(); n = 0
    for vm in VS_MODELS:
        for vname, outs in VS_VARIANTS:
            for comp in (False, True):
                t = lab.translate(vs_asm(vm, outs), complete=comp, dksh=True); n += 1
                P.check(t["ok"], "uam %s/%s %s: %s" % (vm, vname, "complete" if comp else "base", t["msg"]))
    for pm in PS_MODELS:
        for label, pasm, _, _ in ps_probes(pm):
            t = lab.translate(pasm, dksh=True); P.check(t["ok"], "uam %s/%s: %s" % (pm, label, t["msg"]))
    return P.done()

def part_mutate(lab):
    P = Part("mutate")
    def verdict(vsm, vname, pm, label, mutate=None, complete=False, d=None, fog=None, selected=None):
        """True when the pair (with the VS GLSL mutated) links and reads the rule's value: the check of parts link/values/fog on one case"""
        outs = dict(VS_VARIANTS)[vname]; vt = lab.translate(vs_asm(vsm, outs), complete=complete)
        if fog is None:
            pr = [p for p in ps_probes(pm) if p[0] == label][0]; pt = lab.translate(pr[1]); cfg = vs_cfg(outs, 0.3)
            want, mk = expected(cfg, pr[2], pr[3], ps_d(pm) if d is None else d)
            consts = ({4: (ps_d(pm) if d is None else d)}, {})
        else:
            pt = lab.translate(ps_fog_asm(pm)); consts = ({4: ps_d(pm)}, {4: fog[0], 5: 0.0, 6: 1.0, 7: 0.5})
        g = open(vt["glsl"]).read()
        if mutate: g2 = mutate(g); assert g2 != g, "mutation did not change the GLSL"; g = g2
        mp = os.path.join(lab.w, "mut%d.glsl" % lab.nid); lab.nid += 1; open(mp, "w").write(g)
        if fog is None: job = ("x", mp, pt["glsl"], vs_consts(0.25, 0.3), {}, consts[0], {})
        else: job = ("x", mp, pt["glsl"], vs_consts(fog[1], 0.3), {0: (1, 1, 1, 1)}, consts[0], consts[1])
        r = lab.run([job])["x"]
        if r[0] != "ok": return False
        if fog is None: return close(r[1:5], want, mk if not pm.startswith("ps_1") else mk)
        return all(abs(r[1 + c] - fog_model(fog[0], fog[1], 0.0, 1.0, 0.5, any(k == "fog" for k, _, _ in outs), 0.3)) <= 2e-5 for c in range(3))
    # sanity: the unmutated cases pass
    base_cases = [("vs_2_0", "bare", "ps_2_0", "t3", True), ("vs_2_0", "c0xy", "ps_2_0", "v0", False), ("vs_1_1", "mix", "ps_2_x", "t7", True), ("vs_3_0", "t3xy", "ps_3_0", "t3", False)]
    for c in base_cases: P.check(verdict(c[0], c[1], c[2], c[3], complete=c[4]), "unmutated case passes: %s" % (c,))
    P.check(verdict("vs_2_0", "bare", "ps_2_0", "v0", complete=True), "unmutated complete case passes")
    P.check(verdict("vs_1_1", "bare", "ps_2_0", "v0", fog=(1, 0.25)), "unmutated fog case passes")
    muts = [
        ("fog default 0.0 -> 1.0",       dict(args=("vs_1_1", "bare", "ps_2_0", "v0"), fog=(1, 0.25)), lambda g: mutsrc.apply_re(g, r"(dk_pad_18 = vec4\()0\.0\)", r"\g<1>1.0)", 'sm_link')),
        ("COLOR0 default -> constant 0", dict(args=("vs_2_0", "c0xy", "ps_2_0", "v0")),               lambda g: mutsrc.apply(g, "vs_drv_pad.x", "0.0", 'sm_link')),
        ("COLOR0 default -> 1.0 (d=0)",  dict(args=("vs_1_1", "c0xy", "ps_3_0", "color0"), d=0.0),     lambda g: mutsrc.apply(g, "vs_drv_pad.x", "1.0", 'sm_link')),
        ("texcoord completion 0 -> 0.5", dict(args=("vs_2_0", "t3xy", "ps_2_0", "t3")),               lambda g: mutsrc.apply_re(g, r"(io_5_3\.zw = vec2\()0\.0\)", r"\g<1>0.5)", 'sm_link')),
        ("pad slot moved 18 -> 17",      dict(args=("vs_1_1", "bare", "ps_2_0", "v0"), fog=(1, 0.25)), lambda g: mutsrc.apply(g, "layout(location = 18) out highp vec4 dk_pad_18", "layout(location = 17) out highp vec4 dk_pad_18", 'sm_link')),
        ("fog pad declaration removed",  dict(args=("vs_1_1", "bare", "ps_2_0", "v0"), fog=(1, 0.25)), lambda g: mutsrc.apply_re(g, r"layout\(location = 18\) out highp vec4 dk_pad_18;\n", "", g)),
        ("complete: texcoord pad 0 -> 1", dict(args=("vs_2_0", "bare", "ps_2_0", "t3"), complete=True), lambda g: mutsrc.apply_re(g, r"(dk_pad_5 = vec4\()0\.0\)", r"\g<1>1.0)", 'sm_link')),
        ("complete: specular pad 0 -> 1", dict(args=("vs_3_0", "bare", "ps_3_0", "color1"), complete=True), lambda g: mutsrc.apply_re(g, r"(dk_pad_1 = vec4\()0\.0\)", r"\g<1>1.0)", 'sm_link')),
        ("oFog value overwritten by pad", dict(args=("vs_1_1", "fog", "ps_2_0", "v0"), fog=(1, 0.25)), lambda g: mutsrc.apply(g, "\tgl_Position.xy +=", "\tio_11_0.x = 0.0;\n\tgl_Position.xy +=", 'sm_link')),
    ]
    for name, kw, fn in muts:
        a = kw["args"]; ok = verdict(a[0], a[1], a[2], a[3], mutate=fn, complete=kw.get("complete", False), d=kw.get("d"), fog=kw.get("fog"))
        P.check(not ok, "mutation caught: %s" % name)
    # the rule's selection is itself checked: always choosing the base variant is caught by the link matrix (a canonical read of an unwritten slot)
    o = dict(VS_VARIANTS)["bare"]; pb = [p for p in ps_probes("ps_2_0") if p[0] == "t3"][0]
    r = lab.run([("y", lab.translate(vs_asm("vs_2_0", o))["glsl"], lab.translate(pb[1])["glsl"], vs_consts(0.25, 0.3), {}, {4: 1.0}, {})])["y"]
    P.check(r[0] == "link_fail", "mutation caught: a selection rule that always picks the base variant (base VS + PS reading an unwritten t3: %s)" % (r,))
    return P.done()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--build", required=True, help="shader host build root (build.sh output): DIR/host/sdp2dksh, DIR/deps")
    ap.add_argument("--work", default=None); ap.add_argument("--part", default="")
    a = ap.parse_args()
    if a.work: os.makedirs(a.work, exist_ok=True)
    work = tempfile.mkdtemp(prefix="smlink", dir=a.work)
    lab = Lab(os.path.abspath(a.build), os.path.abspath(work)); ok = True
    for name, fn in (("rule", part_rule), ("total", part_total), ("link", part_link_values), ("fog", part_fog), ("uam", part_uam), ("mutate", part_mutate)):
        if a.part and a.part not in (name, "values") : continue
        ok = fn(lab) and ok
    print("ALL OK" if ok else "FAILED"); return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
