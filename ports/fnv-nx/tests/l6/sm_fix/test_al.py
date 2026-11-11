#!/usr/bin/env python3
"""T-SM-AL: the loop counter register aL (class A of the SM totality gate).  python3 -I tests/l6/sm_fix/test_al.py [--build DIR]
Translates D3D9 vs_3_0 / vs_2_0 loop programs with the glsldk translator, runs the GLSL on llvmpipe (EGL surfaceless) and compares
with values computed from the D3D9 definition: `loop aL, i#` runs i#.x iterations with aL = i#.y + k * i#.z (k = 0..x-1); an inner
loop has its own counter and the outer one is restored by endloop; a subroutine called inside a loop reads the caller's counter;
c[aL + n] addresses register aL + n.  aL read as a source is the integer value replicated to xyzw as float."""
import argparse, os, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(); ap.add_argument("--build", default=os.environ.get("SM_BUILD", "/home/user/work/l6/sm3a")); a = ap.parse_args()
HOST = os.path.join(a.build, "host"); W = tempfile.mkdtemp(prefix="smal")
os.environ["PATH"] = os.path.join(a.build, "tools", "bin") + ":" + os.environ["PATH"]
r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-o", W + "/run", os.path.join(HERE, "gl_vs_run.c"), "-lEGL", "-lGL"], capture_output=True, text=True)
if r.returncode: sys.exit("driver build failed\n" + r.stderr[:600])

def iters(n, start, step): return [start + k * step for k in range(n)]      # D3D9: aL of iteration k
def cconst(n): return (float(n), 100.0 + n, 0.0, 0.0)
CONSTS = [(0, 0, 0, 1)] + [(0, 0, 0, 0)] * 2 + [(1, 2, 3, 4)] + [(0, 0, 0, 0)] * 6 + [cconst(n) for n in range(10, 256)]
HDR = "dcl_position o0\ndcl_texcoord0 o1\n"
def tail(): return "mov o0, c0\nmov o1, r0\n"
CASES = []
def vadd(x, y): return [p + q for p, q in zip(x, y)]
def rep(v): return [float(v)] * 4
# 1. nested loops, non-zero start, outer counter restored after the inner endloop, positive and negative steps
def nested():
    r0 = [0.0] * 4
    for o in iters(3, 2, 3):
        r0 = vadd(r0, rep(o))
        for i in iters(2, 10, -4): r0 = vadd(r0, rep(i))
        r0 = vadd(r0, rep(o))
    return r0
CASES.append(("nested", "vs_3_0", "defi i0, 3, 2, 3, 0\ndefi i1, 2, 10, -4, 0\nmov r0, c1\nloop aL, i0\nmov r1, aL\nadd r0, r0, r1\nloop aL, i1\nadd r0, r0, aL\nendloop\nadd r0, r0, aL\nendloop\n" + tail(), nested()))
# 2. negative step from a positive start, aL as a mad source, 4 iterations: aL = 5,3,1,-1
def neg():
    r0 = [0.0] * 4
    for o in iters(4, 5, -2): r0 = vadd(r0, [o * c for c in (1, 2, 3, 4)])
    return r0
CASES.append(("negstep", "vs_3_0", "defi i0, 4, 5, -2, 0\nmov r0, c1\nloop aL, i0\nmad r0, aL, c3, r0\nendloop\n" + tail(), neg()))
# 3. c[aL + n] and c[aL] (relative addressing by the same counter), nested with the aL ALU read
def rel():
    r0 = [0.0] * 4
    for o in iters(3, 2, 3):
        r0 = vadd(r0, cconst(o + 10)); r0 = vadd(r0, cconst(o + 20)); r0 = vadd(r0, rep(o))
    return r0
CASES.append(("relative", "vs_3_0", "defi i0, 3, 2, 3, 0\nmov r0, c1\nloop aL, i0\nadd r0, r0, c10[aL]\nadd r0, r0, c[aL+20]\nadd r0, r0, aL\nendloop\n" + tail(), rel()))
# 4. subroutine called from a loop reads the caller's counter; called from the inner loop of a nest it sees the inner one
def sub():
    r0 = [0.0] * 4
    for o in iters(2, 1, 1):
        for i in iters(3, 7, -3): r0 = vadd(r0, [i * c for c in (1, 2, 3, 4)])
        r0 = vadd(r0, rep(o))
    return r0
CASES.append(("subroutine", "vs_3_0", "defi i0, 2, 1, 1, 0\ndefi i1, 3, 7, -3, 0\nmov r0, c1\nloop aL, i0\nloop aL, i1\ncall l0\nendloop\nadd r0, r0, aL\nendloop\n" + tail() + "ret\nlabel l0\nmul r1, aL, c3\nadd r0, r0, r1\nret\n", sub()))
# 5. vs_2_0 (loop counter legal as source in vs_2_0)
CASES.append(("vs_2_0", "vs_2_0", "defi i0, 3, 2, 3, 0\nmov r0, c1\nloop aL, i0\nadd r0, r0, aL\nendloop\nmov oPos, c0\nmov oT0, r0\n", [sum(iters(3, 2, 3))] * 4))
cb = b"".join(struct.pack("<4f", *c) for c in CONSTS); open(W + "/c.bin", "wb").write(cb)
bad = 0
for name, model, body, want in CASES:
    hdr = model + "\n" + ("" if model == "vs_2_0" else HDR)
    asm = hdr + body
    open(W + "/%s.asm" % name, "w").write(asm)
    r = subprocess.run([HOST + "/s6b_asm", W + "/%s.asm" % name, W + "/%s.bin" % name], capture_output=True, text=True)
    if r.returncode: print("FAIL %s: assemble: %s" % (name, r.stderr.strip()[:200])); bad += 1; continue
    od = W + "/o_" + name; os.makedirs(od)
    r = subprocess.run([HOST + "/sdp2dksh", "-o", od, "--glsl", "--no-dksh", "--blob", W + "/%s.bin" % name], capture_output=True, text=True)
    g = [f for f in os.listdir(od) if f.endswith(".glsl")]
    if r.returncode or not g: print("FAIL %s: translate: %s" % (name, (r.stdout + r.stderr).strip()[-200:])); bad += 1; continue
    r = subprocess.run([W + "/run", od + "/" + g[0], W + "/c.bin"], capture_output=True, text=True)
    if r.returncode: print("FAIL %s: GL run rc=%d %s" % (name, r.returncode, r.stderr.strip()[-300:])); bad += 1; continue
    got = [float(x) for x in r.stdout.split()]
    ok = len(got) == 4 and all(abs(x - y) < 1e-4 for x, y in zip(got, want))
    print("%s %-11s got %s want %s" % ("ok  " if ok else "FAIL", name, got, want)); bad += not ok
print("T-SM-AL: %d cases, %d failed" % (len(CASES), bad)); sys.exit(1 if bad else 0)
