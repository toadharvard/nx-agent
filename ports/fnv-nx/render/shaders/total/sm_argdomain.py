#!/usr/bin/env python3
"""sm_argdomain.py --sdp DIR: global counts that size the compile-arg domain of wined3d 9.0's GLSL generator over the shipped shader packages (.sdp): numbers only, no shader text.
Decoder: sm_reach.decode_full (token stream; refuses what does not decode to its end token). Definitions (register type = bool_census.regtype of the token):
 vs OUTPUT SIGNATURE of a shader = the set of its output registers (type 4 RASTOUT pos/fog/psize, 5 ATTROUT oD, 6 TEXCRDOUT oT (vs_1_x/2_x) or OUTPUT o# (vs_3_0)) written by any instruction,
   each with the union of the write masks; vs_3_0: each o# also carries its dcl usage word (usage | index<<16).   [vs_sig_masked]  [vs_sig_regs = the register set without masks]
 ps INPUT SIGNATURE of a shader = the ps major + the set of input registers it reads (type 1 INPUT v#, type 3 TEXTURE t# for ps_2_x) with the union of read swizzle components;
   ps_3_0: each v# also carries its dcl usage word.   [ps_sig_masked]  [ps_sig_regs]
 sampler / texture-type usage: every dcl of a sampler register (type 10) with its dcl type (2d/cube/volume), per ps model; ps_1_x shaders: count of shaders and of their tex* instructions.
Per set: all shaders, and distinct bytecodes (sha256).  usage: sm_argdomain.py --sdp DIR"""
import argparse, collections, glob, hashlib, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.dont_write_bytecode = True
import sm_reach

def rt(t): return ((t >> 28) & 7) | ((t >> 8) & 0x18)
def wmask(t): return (t >> 16) & 0xF
def rmask(t):
    sw = (t >> 16) & 0xFF; return sum(1 << ((sw >> (2 * i)) & 3) for i in range(4))      # components read by the swizzle

def sig(model, ins, stage):
    vs = stage == "vs"; regs = {}; dcl = {}
    for x in ins:
        if x["dcl"] is not None:
            d = x["dst"]; dcl[(rt(d), d & 0x7FF)] = x["dcl"]; continue
        if vs and x["dst"] is not None and rt(x["dst"]) in (4, 5, 6):
            k = (rt(x["dst"]), x["dst"] & 0x7FF); regs[k] = regs.get(k, 0) | wmask(x["dst"])
        if not vs:
            for s, rel in x["srcs"]:
                if rt(s) in (1, 3): k = (rt(s), s & 0x7FF); regs[k] = regs.get(k, 0) | rmask(s)
    major = int(model[3])
    if vs and major == 3:                                    # vs_3_0 declares its outputs
        for x in ins:
            if x["dcl"] is not None and rt(x["dst"]) == 6: k = (6, x["dst"] & 0x7FF); regs[k] = regs.get(k, 0) | wmask(x["dst"])
    if not vs and major == 3:                                # ps_3_0 declares its inputs
        for x in ins:
            if x["dcl"] is not None and rt(x["dst"]) == 1: k = (1, x["dst"] & 0x7FF); regs.setdefault(k, 0); regs[k] |= wmask(x["dst"])
    udcl = {k: dcl.get(k) for k in regs if major == 3 and dcl.get(k) is not None}
    return (model, tuple(sorted((k, m, udcl.get(k, 0)) for k, m in regs.items()))), tuple(sorted(k for k in regs))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sdp", required=True); a = ap.parse_args()
    seen = set(); C = collections.Counter(); sets = collections.defaultdict(lambda: [set(), set(), set(), set()])
    samp = collections.defaultdict(lambda: collections.Counter()); ps1 = 0; ps1tex = 0; nsh = collections.Counter()
    for f in sorted(glob.glob(os.path.join(a.sdp, "*.sdp"))):
        d = open(f, "rb").read(); magic, count, size = struct.unpack("<III", d[:12]); p = 12
        for _ in range(count):
            name = d[p:p + 256].split(b"\0")[0].decode(); sz = struct.unpack("<I", d[p + 256:p + 260])[0]; code = d[p + 260:p + 260 + sz]; p += 260 + sz
            stage = "vs" if (struct.unpack("<I", code[:4])[0] >> 16) == 0xFFFE else "ps"
            model, ins = sm_reach.decode_full(code, stage); h = hashlib.sha256(code).hexdigest(); nsh[(stage, "all")] += 1; first = h not in seen; seen.add(h)
            if first: nsh[(stage, "distinct")] += 1
            masked, regsonly = sig(model, ins, stage)
            S = sets[stage]; S[0].add(masked)
            S[1].add((masked[0], regsonly))
            if first: S[2].add(masked); S[3].add((masked[0], regsonly))
            for x in ins:
                if x["dcl"] is not None and rt(x["dst"]) == 10 and stage == "ps": samp[model][(x["dcl"] >> 27) & 0xF] += 1
            if stage == "ps" and model.startswith("ps_1_"): ps1 += 1; ps1tex += sum(1 for x in ins if x["op"] in (0x42, 0x41, 0x40, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59))
    print("set: shaders vs all %d distinct-bytecode %d; ps all %d distinct-bytecode %d" % (nsh[("vs", "all")], nsh[("vs", "distinct")], nsh[("ps", "all")], nsh[("ps", "distinct")]))
    for st, nm in (("vs", "vs OUTPUT signatures"), ("ps", "ps INPUT signatures (with ps major)")):
        S = sets[st]; print("%s: distinct with masks/dcl usage: all-shaders %d, distinct-bytecode %d; distinct (model, register set) without masks: %d / %d" % (nm, len(S[0]), len(S[2]), len(S[1]), len(S[3])))
        by = collections.Counter(m[0] for m in S[0]); print("   per model (masked signatures):", dict(sorted(by.items())))
    print("sampler dcl types per ps model (type code 2=2d 3=cube 4=volume):", {m: dict(sorted(c.items())) for m, c in sorted(samp.items())})
    print("ps_1_x shaders in the set: %d (their tex-addressing instructions: %d)" % (ps1, ps1tex))

if __name__ == "__main__":
    main()
