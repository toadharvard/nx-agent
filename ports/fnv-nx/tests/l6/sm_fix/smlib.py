"""smlib.py - shared helpers of the tests/l6/sm_fix behavioural tests: assemble D3D9 shader text with the gate's token encoder
(render/shaders/total/sm_hand.py, read-only import), translate it with the glsldk translator (sdp2dksh --glsl --no-dksh), run the GLSL on
llvmpipe (gl_run.c) and compare with values computed by hand from the documented formulas.  Used by the test_*.py of this directory."""
import argparse, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "render", "shaders", "total"))
import sm_hand

def args():
    ap = argparse.ArgumentParser(); ap.add_argument("--build", default=os.environ.get("SM_BUILD", "/home/user/work/l6/sm3b"))
    ap.add_argument("--only", default="")
    return ap.parse_args()

class Lab:
    def __init__(self, build):
        self.build = build; self.host = os.path.join(build, "host"); self.w = tempfile.mkdtemp(prefix="smlab")
        self.ops = sm_hand.load_opcodes(os.path.join(build, "deps", "mojoshader", "mojoshader_internal.h"))
        os.environ["PATH"] = os.path.join(build, "tools", "bin") + ":" + os.environ["PATH"]
        r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-o", self.w + "/run", os.path.join(HERE, "gl_run.c"), "-lEGL", "-lGL"], capture_output=True, text=True)
        if r.returncode: sys.exit("driver build failed\n" + r.stderr[:600])
        self.n = 0
    def translate(self, asm, glsl_only=True):
        """-> (glsl path or None, message)"""
        self.n += 1; nm = "s%d" % self.n
        try: bc = sm_hand.assemble(asm, self.ops)
        except Exception as e: return None, "assemble: %s" % e
        open(self.w + "/%s.bin" % nm, "wb").write(bc)
        od = self.w + "/o_" + nm; os.makedirs(od)
        r = subprocess.run([self.host + "/sdp2dksh", "-o", od, "--glsl"] + (["--no-dksh"] if glsl_only else []) + ["--blob", self.w + "/%s.bin" % nm], capture_output=True, text=True)
        g = [f for f in os.listdir(od) if f.endswith(".glsl")]
        if not g: return None, ((r.stdout + r.stderr).strip().replace("\n", " | "))[-300:]
        if not glsl_only:                                                    # uam must accept it too
            ix = open(od + "/index.tsv").read().splitlines()[1:]
            if ix and ix[0].split("\t")[4] != "ok": return None, "uam: " + ix[0].split("\t")[-1][:200]
        return od + "/" + g[0], ""
    def run(self, ps_glsl, spec, vs_glsl=None):
        sp = self.w + "/spec%d.txt" % self.n; open(sp, "w").write(spec)
        r = subprocess.run([self.w + "/run", ps_glsl, sp] + ([vs_glsl] if vs_glsl else []), capture_output=True, text=True)
        if r.returncode: return None, "GL run rc=%d %s" % (r.returncode, r.stderr.strip()[-300:])
        return [float(x) for x in r.stdout.split()], ""

PASS_PS = """#version 450 core
layout(location = 2) in vec4 t; layout(location = 0) out vec4 o; void main() { o = t; }
"""

def cst(n, *v): return "c %d %s\n" % (n, " ".join(repr(float(x)) for x in v))
def tex2d(u, w, h, f):
    """2D texture on unit u, texel (i, j) = f(i, j) -> rgba"""
    return "t2 %d %d %d\n" % (u, w, h) + "".join(" ".join(repr(float(x)) for x in f(i, j)) + "\n" for j in range(h) for i in range(w))
def texcube(u, f):
    """cube map on unit u, 1x1 faces, face k (+X -X +Y -Y +Z -Z) = f(k) -> rgba"""
    return "tc %d 1\n" % u + "".join(" ".join(repr(float(x)) for x in f(k)) + "\n" for k in range(6))
def tex3d(u, w, h, d, f):
    return "t3 %d %d %d %d\n" % (u, w, h, d) + "".join(" ".join(repr(float(x)) for x in f(i, j, k)) + "\n" for k in range(d) for j in range(h) for i in range(w))
# the standard test textures: the blue channel tags the dimensionality, the other channels the texel
def T2(i, j): return ((i + 1) / 8.0, (j + 1) / 8.0, 0.25, 1.0)                    # 4x4
def TC(k): return ((k + 1) / 8.0, 0.5, 0.75, 1.0)                                  # faces
def T3(i, j, k): return ((i + 1) / 4.0, (j + 1) / 4.0, (k + 1) / 4.0 * 0.0 + 0.125 * (k + 1), 1.0)   # 2x2x2

class Suite:
    def __init__(self, name, a):
        self.name, self.a, self.lab, self.bad, self.total = name, a, Lab(a.build), 0, 0
    def case(self, name, asm, spec, want, ps=True, tol=1e-4, depth=None, vs_asm=None, uam=False):
        """translate asm (a ps_* program, or a vs_* one when ps=False), run it, compare the colour with `want` (4 floats) and,
        when `depth` is not None, the depth with `depth`."""
        if self.a.only and self.a.only not in name: return
        self.total += 1
        g, msg = self.lab.translate(asm, glsl_only=not uam)
        if g is None: print("FAIL %-26s translate: %s" % (name, msg)); self.bad += 1; return
        if ps: got, msg = self.lab.run(g, spec)
        else:
            pp = self.lab.w + "/pass.glsl"; open(pp, "w").write(PASS_PS); got, msg = self.lab.run(pp, spec, g)
        if got is None: print("FAIL %-26s %s" % (name, msg)); self.bad += 1; return
        ok = all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(got[:4], want))
        if depth is not None: ok = ok and abs(got[4] - depth) <= 1e-5
        print("%s %-26s got %s want %s%s" % ("ok  " if ok else "FAIL", name, ["%.5g" % x for x in got[:4]], ["%.5g" % x for x in want],
                                             "" if depth is None else " depth got %.6g want %.6g" % (got[4], depth)))
        self.bad += not ok
    def done(self):
        print("%s: %d cases, %d failed" % (self.name, self.total, self.bad)); sys.exit(1 if self.bad else 0)
