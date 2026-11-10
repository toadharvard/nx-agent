#!/usr/bin/env python3
"""T-SM-SINK: vec4-valued results (dst, lit) written through a write mask or to a scalar register (class C of the SM totality gate).
python3 -I tests/l6/sm_fix/test_sink.py [--build DIR]
D3D9: the write mask selects which components of the result are stored (channel c takes result component c); a scalar register
(oFog, oPts) takes component x. dst = (1, s0.y*s1.y, s0.z, s1.w); lit = (1, max(s.x,0), x>0 ? pow(max(y,0),w) : 0, 1).
Runs the translated vs_2_0 on llvmpipe and reads oT0 (location 2) and oFog (location 18, .x) from the rasteriser."""
import argparse, os, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ap = argparse.ArgumentParser(); ap.add_argument("--build", default=os.environ.get("SM_BUILD", "/home/user/work/l6/sm3a")); a = ap.parse_args()
HOST = os.path.join(a.build, "host"); W = tempfile.mkdtemp(prefix="smsink")
os.environ["PATH"] = os.path.join(a.build, "tools", "bin") + ":" + os.environ["PATH"]
r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-o", W + "/run", os.path.join(HERE, "gl_vs_run.c"), "-lEGL", "-lGL"], capture_output=True, text=True)
if r.returncode: sys.exit("driver build failed\n" + r.stderr[:600])
C = [(0, 0, 0, 1), (0, 0.5, 0.25, 0), (0, 0.5, 0, 0.75), (1, 2, 3, 4), (5, 3, 0, 0)] + [(0, 0, 0, 0)] * 251
open(W + "/c.bin", "wb").write(b"".join(struct.pack("<4f", *c) for c in C))
# (name, vs body, oT0 expected, oFog.x expected or None)
CASES = [
 ("dst_fog", "dst oFog, c1, c2\nmov r0, c3\nmov oT0, r0\n", [1, 2, 3, 4], 1.0),
 ("lit_fog", "lit oFog, c4\nmov r0, c3\nmov oT0, r0\n", [1, 2, 3, 4], 1.0),
 ("dst_yz", "mov r0, c3\ndst r0.yz, c1, c2\nmov oT0, r0\nmov oFog, c3.x\n", [1, 0.25, 0.25, 4], 1.0),
 ("lit_yw", "mov r0, c3\nlit r0.yw, c4\nmov oT0, r0\nmov oFog, c3.x\n", [1, 5, 3, 1], 1.0),
 ("dst_w", "mov r0, c3\ndst r0.w, c1, c2\nmov oT0, r0\nmov oFog, c3.x\n", [1, 2, 3, 0.75], 1.0),
]
sys.path.insert(0, os.path.join(ROOT, "render", "shaders", "total")); import sm_hand
OPS = sm_hand.load_opcodes(os.path.join(a.build, "deps", "mojoshader", "mojoshader_internal.h"))
bad = 0
for name, body, want_t, want_f in CASES:
    text = "vs_2_0\ndcl_position v0\n" + body + "mov oPos, c0\n"
    try: bc = sm_hand.assemble(text, OPS)
    except Exception as e: print("FAIL %s: assemble %s" % (name, e)); bad += 1; continue
    open(W + "/%s.bin" % name, "wb").write(bc); od = W + "/o_" + name; os.makedirs(od)
    subprocess.run([HOST + "/sdp2dksh", "-o", od, "--glsl", "--no-dksh", "--blob", W + "/%s.bin" % name], capture_output=True, text=True)
    g = [f for f in os.listdir(od) if f.endswith(".glsl")]
    if not g: print("FAIL %s: translate" % name); bad += 1; continue
    got = {}
    for loc in (2, 18):
        r = subprocess.run([W + "/run", od + "/" + g[0], W + "/c.bin", str(loc)], capture_output=True, text=True)
        got[loc] = [float(x) for x in r.stdout.split()] if r.returncode == 0 else None
        if got[loc] is None: print("FAIL %s: GL run %s" % (name, r.stderr.strip()[-200:])); bad += 1
    if None in got.values(): continue
    ok = all(abs(x - y) < 1e-4 for x, y in zip(got[2], want_t)) and abs(got[18][0] - want_f) < 1e-4
    print("%s %-8s oT0 %s want %s | oFog.x %.4f want %.4f" % ("ok  " if ok else "FAIL", name, got[2], want_t, got[18][0], want_f)); bad += not ok
print("T-SM-SINK: %d cases, %d failed" % (len(CASES), bad)); sys.exit(1 if bad else 0)
