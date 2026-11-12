#!/usr/bin/env python3
"""sm_domain.py --sdp DIR --nfix N --np2 N --tex2 N [--private DIR] [--out sm_domain.json]
Sizes (and the per-shader domain table) of the compile set and the reorder set of wined3d 9.0's GLSL generator over the shipped shader packages.
The rule is render/journal inbox l6-render-w3b.md, "Lemmas L1 and L2" (wine-9.0 glsl_shader.c / shader.c, mirror c8373ee4).  D3D9 device: no GS/HS/DS.
Inputs per shader (token stream via sm_reach.decode_full):
  vs  p = popcount((input registers read or declared) & 0xffff), maj.   |A_V| = 2^p * 2 (clip) * 2 (point_size) * (4 if maj<3 else 1)  [fog_src, flatshading only for maj<3]
  ps  declared samplers d (dcl types 2d/volume: texture type in {2D,3D}; cube: {CUBE}), each with an np2 bit (--np2 values) and a colour fixup (--nfix values); texture types per 2d/volume sampler: --tex2.
      |A_P| = prod_declared (t_i * NP2 * NFIX) * vpmode * fog * 2 (pointsprite) * 2 (flatshading) * 8 (alpha_test_func) * ydim * 2^(colour outputs written) * 1 (srgb_correction: only when !srgb_write_control)
        vpmode = 3 for ps_3_0 else 1;  fog = 4 for ps<3 else 1;  ydim = 2 if the shader reads vPos (misc reg 0) or uses dsy else 1;
        dual_source_blend, texcoords_initialized, shadow: constant (D3D9 / full ffp varyings / not read).
  R   |R| = (sum over vs signature classes (1 + [class writes point size])) * (sum over ps signature classes (2 if ps major<3 else 1)), plus the ps-less class (major 0: 2).
        signature classes: sm_argdomain.sig (masked).
Totals are exact integers, over all shaders and over distinct bytecodes (sha256).  Public output: counts only.  --private DIR writes one row per distinct bytecode
(sha256, model, p/d/dims, |A|) as TSV, for the farm.  Context constants are the canonical state of the link probe (stated by the caller via --nfix)."""
import argparse, collections, glob, hashlib, json, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.dont_write_bytecode = True
import sm_reach, sm_argdomain as AD

def rt(t): return AD.rt(t)

def vs_dims(model, ins):
    maj = int(model[3]); regs = set()
    for x in ins:
        if x["dcl"] is not None:
            d = x["dst"]
            if rt(d) == 1: regs.add(d & 0x7FF)
            continue
        for s, rel in x["srcs"]:
            if rt(s) == 1: regs.add(s & 0x7FF)
    p = bin(sum(1 << r for r in regs if r < 16)).count("1")
    return maj, p, 2 ** p * 2 * 2 * (4 if maj < 3 else 1)

def ps_dims(model, ins, nfix, np2, tex2):
    maj = int(model[3]); decl = {}; outs = set(); vpos = False
    for x in ins:
        if x["dcl"] is not None:
            if rt(x["dst"]) == 10: decl[x["dst"] & 0x7FF] = (x["dcl"] >> 27) & 0xF
            continue
        d = x["dst"]
        if d is not None and rt(d) == 8: outs.add(d & 0x7FF)
        for s, rel in x["srcs"]:
            if rt(s) == 17 and (s & 0x7FF) == 0: vpos = True
    per = 1
    for i, ty in decl.items(): per *= (1 if ty == 3 else tex2) * np2 * nfix
    size = per * (3 if maj >= 3 else 1) * (4 if maj < 3 else 1) * 2 * 2 * 8 * (2 if vpos else 1) * 2 ** max(1, len(outs)) * 1      # srgb_correction: 1 value, srgb_write_control (ARB_framebuffer_sRGB, GL>=3.0; shader.c:2778)
    return maj, len(decl), len(outs), vpos, size, size // per

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sdp", required=True); ap.add_argument("--nfix", type=int, required=True); ap.add_argument("--np2", type=int, required=True, help="values of the np2 bit per sampler (1 if the bit is always 0)"); ap.add_argument("--tex2", type=int, required=True, help="texture types per declared 2d/volume sampler (2 = Wine override by the bound texture, 1 = dcl only)")
    ap.add_argument("--private"); ap.add_argument("--out", default=os.path.join(HERE, "sm_domain.json")); ap.add_argument("--commit", default="")
    a = ap.parse_args()
    seen = {}; tot = collections.Counter(); per_model = collections.defaultdict(lambda: collections.Counter()); vcls = set(); pcls = set(); rows = []
    for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
        d = open(f, "rb").read(); magic, count, size = struct.unpack("<III", d[:12]); p = 12
        for _ in range(count):
            sz = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + sz]; p += 260 + sz
            stage = "vs" if (struct.unpack("<I", code[:4])[0] >> 16) == 0xFFFE else "ps"
            h = hashlib.sha256(code).hexdigest(); model, ins = sm_reach.decode_full(code, stage)
            if stage == "vs": maj, pp, size_a = vs_dims(model, ins); extra = (pp,)
            else: maj, nd, no, vpos, size_a, base_a = ps_dims(model, ins, a.nfix, a.np2, a.tex2); extra = (nd, no, int(vpos)); tot[('ps', 'distinct_base')] += (base_a if h not in seen else 0); tot[('ps','dh%d' % nd)] += (h not in seen)
            tot[(stage, "all_shaders")] += 1; tot[(stage, "all_args")] += size_a
            per_model[model]["all_shaders"] += 1
            if h not in seen:
                seen[h] = 1; tot[(stage, "distinct_shaders")] += 1; tot[(stage, "distinct_args")] += size_a
                per_model[model]["distinct_shaders"] += 1; per_model[model]["distinct_args"] += size_a
                rows.append((h, model) + extra + (size_a,))
            masked, regs = AD.sig(model, ins, stage)
            (vcls if stage == "vs" else pcls).add(masked)
    def vs_psize(c):
        for k, m, u in c[1]:
            if (k[0] == 4 and k[1] == 2) or (k[0] == 6 and (u & 0x1F) == 4 and int(c[0][3]) == 3): return 1
        return 0
    rv = sum(1 + vs_psize(c) for c in vcls); rp = sum(2 if int(c[0][3]) < 3 else 1 for c in pcls) + 2
    out = {"commit": a.commit, "nfix": a.nfix, "np2": a.np2, "tex2": a.tex2, "vs_classes": len(vcls), "ps_classes": len(pcls) + 1, "R": rv * rp,
           "compile_set_distinct": {"vs": tot[("vs", "distinct_args")], "ps": tot[("ps", "distinct_args")], "total": tot[("vs", "distinct_args")] + tot[("ps", "distinct_args")]},
           "ps_distinct_without_sampler_dims": tot[("ps", "distinct_base")], "ps_distinct_by_declared_samplers": {k[1][2:]: v for k, v in sorted(tot.items()) if k[0] == "ps" and k[1].startswith("dh")},
           "compile_set_all": {"vs": tot[("vs", "all_args")], "ps": tot[("ps", "all_args")]},
           "shaders": {"vs_all": tot[("vs", "all_shaders")], "ps_all": tot[("ps", "all_shaders")], "vs_distinct": tot[("vs", "distinct_shaders")], "ps_distinct": tot[("ps", "distinct_shaders")]},
           "per_model": {m: dict(c) for m, c in sorted(per_model.items())}}
    json.dump(out, open(a.out, "w"), indent=1, sort_keys=True); print(json.dumps(out, indent=1, sort_keys=True))
    if a.private:
        os.makedirs(a.private, exist_ok=True)
        with open(os.path.join(a.private, "sm_domain_rows.tsv"), "w") as fo:
            for r in sorted(rows): fo.write("\t".join(map(str, r)) + "\n")

if __name__ == "__main__":
    main()
