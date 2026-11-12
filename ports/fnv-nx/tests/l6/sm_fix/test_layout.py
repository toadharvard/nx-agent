#!/usr/bin/env python3
"""T-SM-LAYOUT: the location rule of render/shaders/translator/include/fnvnx_dk_layout.h (class B of the SM totality gate).
python3 -I tests/l6/sm_fix/test_layout.py [--build DIR]
 (a) rule properties in C on the header itself: every (usage 0..13, index 0..15) is assignable alone; random legal declaration sets
     (up to the slot limit) are injective, in range, and keep canonical slots; illegal sets are rejected.
 (b) translator agreement on ALL 224 pairs x {vs_3_0 input, vs_3_0 output, ps_3_0 input}: the reflection JSON location equals the
     layout(location) in the GLSL, and uam accepts it (--glsl without --no-dksh).
 (c) a VS and a PS translated with the same link set, non-canonical varyings (TEXCOORD9, COLOR3) and a PS-read POSITION0
     (the VS writes the copy), linked by location on llvmpipe: the PS output equals the values the VS wrote."""
import argparse, json, os, random, re, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ap = argparse.ArgumentParser(); ap.add_argument("--build", default=os.environ.get("SM_BUILD", "/home/user/work/l6/sm3a")); a = ap.parse_args()
HOST = os.path.join(a.build, "host"); W = tempfile.mkdtemp(prefix="smlay"); bad = 0
os.environ["PATH"] = os.path.join(a.build, "tools", "bin") + ":" + os.environ["PATH"]
INC = os.path.join(ROOT, "render", "shaders", "include")
def fail(m):
    global bad; bad += 1; print("FAIL " + m)
# (a)
open(W + "/p.c", "w").write(r'''
#include <stdio.h>
#include <stdlib.h>
#include "fnvnx_dk_layout.h"
int main(void) {
  int bad = 0; signed char loc[32]; unsigned char s[32]; srand(7);
  for (int kind = 0; kind < 2; kind++) {
    const int slots = kind ? FNVNX_DK_VARYING_SLOTS : FNVNX_DK_ATTRIB_SLOTS;
    for (int u = 0; u < 14; u++) for (int i = 0; i < 16; i++) { s[0] = FNVNX_DK_PAIR(u, i); if (fnvnx_dk_assign(kind, s, 1, loc) || loc[0] < 0 || loc[0] >= slots) { printf("single %d %d %d\n", kind, u, i); bad++; } }
    for (int it = 0; it < 200000; it++) {
      int n = 1 + rand() % slots; int ok = 1;
      for (int k = 0; k < n; k++) { int p; do { p = FNVNX_DK_PAIR(rand() % 14, rand() % 16); ok = 1; for (int j = 0; j < k; j++) if (s[j] == p) ok = 0; } while (!ok); s[k] = p; }
      if (fnvnx_dk_assign(kind, s, n, loc)) { printf("legal set rejected\n"); bad++; continue; }
      int seen[32] = {0};
      for (int k = 0; k < n; k++) {
        int c = kind ? fnvnx_dk_varying_location(s[k] / 16, s[k] % 16) : fnvnx_dk_attrib_location(s[k] / 16, s[k] % 16);
        if (loc[k] < 0 || loc[k] >= slots || seen[(int)loc[k]]++) { printf("range/injective %d\n", kind); bad++; break; }
        if (c >= 0 && c != loc[k]) { printf("canonical moved\n"); bad++; break; }
      }
    }
    s[0] = FNVNX_DK_PAIR(5, 1); s[1] = s[0]; if (!fnvnx_dk_assign(kind, s, 2, loc)) { printf("duplicate accepted\n"); bad++; }
    s[0] = 14 * 16; if (!fnvnx_dk_assign(kind, s, 1, loc)) { printf("usage 14 accepted\n"); bad++; }
    unsigned char big[40]; for (int k = 0; k < 40; k++) big[k] = (unsigned char)k; if (!fnvnx_dk_assign(kind, big, slots + 1, loc)) { printf("over capacity accepted\n"); bad++; }
  }
  printf("bad %d\n", bad); return bad != 0;
}
''')
r = subprocess.run(["gcc", "-std=c99", "-O1", "-I", INC, "-o", W + "/p", W + "/p.c"], capture_output=True, text=True)
if r.returncode: sys.exit(r.stderr[:500])
r = subprocess.run([W + "/p"], capture_output=True, text=True); print("(a) rule properties:", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?")
if r.returncode: fail("(a) " + r.stdout[:300])
# (b)
U = ["position", "blendweight", "blendindices", "normal", "psize", "texcoord", "tangent", "binormal", "tessfactor", "positiont", "color", "fog", "depth", "sample"]
sys.path.insert(0, os.path.join(ROOT, "render", "shaders", "total")); import sm_hand       # the gate's token encoder (reads only)
OPS = sm_hand.load_opcodes(os.path.join(a.build, "deps", "mojoshader", "mojoshader_internal.h"))
def asm(path, text):
    open(path, "w").write(text)
    try: bc = sm_hand.assemble(text, OPS)
    except Exception as e: fail("assemble %s: %s" % (path, str(e)[:120])); return None
    open(path[:-3] + "bin", "wb").write(bc); return path[:-3] + "bin"
bins = {}
for u in range(14):
    for i in range(16):
        n = "%s%d" % (U[u], i)
        for kind, text in (("in", "vs_3_0\ndcl_%s v0\ndcl_position o0\nmov o0, v0\n" % n),
                           ("out", "vs_3_0\ndcl_position o0\ndcl_%s o1\nmov o0, c0\n" % n if n != "position0" else None),
                           ("pin", "ps_3_0\ndcl_%s v0\nmov oC0, v0\n" % n)):
            if text is None: continue
            bins[(kind, u, i)] = asm("%s/%s_%s.asm" % (W, kind, n), text)
n_ok = 0
for kind in ("in", "out", "pin"):
    od = "%s/o_%s" % (W, kind); os.makedirs(od)
    files = [b for (k, u, i), b in bins.items() if k == kind and b]
    subprocess.run([HOST + "/sdp2dksh", "-o", od, "--glsl", "--blob"] + files, capture_output=True, text=True)
    idx = {}
    for ln in open(od + "/index.tsv").read().splitlines()[1:]:
        f = ln.split("\t"); idx[f[2]] = f
    for (k, u, i), b in bins.items():
        if k != kind or not b: continue
        h = idx.get(os.path.basename(b))
        nm = "%s:%s%d" % (kind, U[u], i)
        if not h or h[4] != "ok": fail("%s translate/uam: %s" % (nm, h[10] if h and len(h) > 10 else "no result")); continue
        js = json.load(open("%s/%s.json" % (od, h[0]))); gl = open("%s/%s.glsl" % (od, h[0])).read()
        ent = [e for e in js["inputs" if kind != "out" else "outputs"] if e["usage"] == U[u].upper() and e["index"] == i]
        if kind == "in" and u == 0 and i == 0: pass
        if not ent: fail("%s not in reflection" % nm); continue
        m = re.search(r"layout\(location = (\d+)\) (?:in|out) highp vec4 io_%d_%d;" % (u, i), gl) if kind != "in" else re.search(r"layout\(location = (\d+)\) in vec4 vs_v0;", gl)
        loc = int(m.group(1)) if m else -2
        if ent[0]["location"] != loc and not (ent[0]["location"] == -2 and kind == "out"): fail("%s json %d glsl %d" % (nm, ent[0]["location"], loc))
        else: n_ok += 1
print("(b) %d declarations agree (reflection == GLSL layout, uam accepts)" % n_ok)
# (c)
def tr(name, text, link):
    p = asm("%s/%s.asm" % (W, name), text); od = "%s/o_%s" % (W, name); os.makedirs(od)
    r = subprocess.run([HOST + "/sdp2dksh", "-o", od, "--glsl", "--no-dksh", "--blob", "--link", link, p], capture_output=True, text=True)
    g = [f for f in os.listdir(od) if f.endswith(".glsl")]
    if not g: fail("(c) translate %s: %s" % (name, r.stdout[-200:])); return None
    return od + "/" + g[0]
LINK = "5:9,10:3,0:0"                      # TEXCOORD9, COLOR3 (non-canonical) and POSITION0 (PS-read, VS copy)
vs = tr("c_vs", "vs_3_0\ndcl_position o0\ndcl_texcoord9 o1\ndcl_color3 o2\nmov o0, c0\nmov o1, c10\nmov o2, c11\n", LINK)
ps = tr("c_ps", "ps_3_0\ndcl_color3 v1\ndcl_texcoord9 v0\ndcl_position v2\nadd r0, v0, v1\nadd oC0, r0, v2\n", LINK)
if vs and ps:
    C = [(0, 0, 0, 1)] + [(0, 0, 0, 0)] * 9 + [(1, 2, 3, 4), (10, 20, 30, 40)] + [(0, 0, 0, 0)] * 244
    open(W + "/c.bin", "wb").write(b"".join(struct.pack("<4f", *c) for c in C))
    r = subprocess.run(["gcc", "-std=gnu99", "-O1", "-o", W + "/run", os.path.join(HERE, "gl_vs_run.c"), "-lEGL", "-lGL"], capture_output=True, text=True)
    if r.returncode: fail("driver build")
    else:
        r = subprocess.run([W + "/run", vs, W + "/c.bin", "2", ps], capture_output=True, text=True)
        got = [float(x) for x in r.stdout.split()] if r.returncode == 0 else None
        want = [11, 22, 33, 45]
        print("(c) linked VS+PS on llvmpipe: got %s want %s" % (got, want) if got else "(c) run failed rc=%d %s" % (r.returncode, r.stderr[-300:]))
        if not got or not all(abs(x - y) <= 1e-3 for x, y in zip(got, want)): fail("(c) value mismatch")
print("work dir", W) if bad else None
print("T-SM-LAYOUT: %s" % ("FAIL" if bad else "ok")); sys.exit(1 if bad else 0)
