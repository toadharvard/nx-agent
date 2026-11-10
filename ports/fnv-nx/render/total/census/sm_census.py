#!/usr/bin/env python3
"""SM1-3 translator totality census (R11).  python3 -I sm_census.py --build DIR --deps DIR [--work DIR] [--jobs 2]

  --build  render/shaders build dir holding s6b_asm and sdp2dksh   (build.sh --tests output)
  --deps   build.sh deps dir (needs mojoshader/mojoshader_internal.h for the opcode numbers)
Universe: sm_cases.cases().  Per row: assemble with s6b_asm; if that fails encode with sm_hand.py (encoded_by=hand);
when both succeed the bytes are compared (mismatches counted in the summary, s6b_asm bytes win).  Then
sdp2dksh --no-dksh --glsl (translate stage) and sdp2dksh (uam stage); status = ok | translate_fail(msg) | uam_fail(msg) |
cannot_encode(msg).  Writes sm_ops.tsv next to this script; prints the summary used in sm_README.md.
"""
import argparse, collections, concurrent.futures as cf, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sm_cases, sm_hand

def first_line(s):
    s = (s or "").strip().splitlines()
    return s[0].replace("\t", " ")[:200] if s else ""

def tool(exe, args, cwd=None):
    r = subprocess.run([exe] + args, capture_output=True, text=True, cwd=cwd, timeout=120)
    return r

def run_case(i, row, a, ops):
    m, kind, feat, text, legal = row
    d = os.path.join(a.work, "c%04d" % i)
    os.makedirs(d, exist_ok=True)
    asmf, binf = os.path.join(d, "x.asm"), os.path.join(d, "x.bin")
    open(asmf, "w").write(text)
    enc, note, hand = "asm", "", None
    try: hand = sm_hand.assemble(text, ops)
    except Exception as e: note = "hand:" + str(e)[:80]
    r = tool(os.path.join(a.build, "s6b_asm"), [asmf, binf])
    mism = 0
    if r.returncode == 0 and os.path.exists(binf):
        got = open(binf, "rb").read()
        if hand is not None and hand != sm_hand.strip_comments(got): mism = 1
    else:
        asmerr = first_line(r.stderr)
        if hand is None: return dict(row=row, status="cannot_encode", detail="asm: %s; %s" % (asmerr, note), enc="-", mism=0)
        open(binf, "wb").write(hand); enc = "hand"; note = "asm: " + asmerr
    out = {}
    for stage, flags in (("t", ["--no-dksh"]), ("u", [])):
        od = os.path.join(d, stage); os.makedirs(od, exist_ok=True)
        r = tool(os.path.join(a.build, "sdp2dksh"), ["-o", od, "--glsl"] + flags + ["--blob", binf])
        idx = os.path.join(od, "index.tsv")
        if r.returncode > 1 and not os.path.exists(idx):
            return dict(row=row, status="translate_fail" if stage == "t" else "uam_fail", enc=enc, mism=mism,
                        detail="tool crashed rc=%d %s" % (r.returncode, first_line(r.stderr)))
        f = open(idx).read().splitlines()[1].split("\t")
        out[stage] = (f[4], f[10] if len(f) > 10 else "", f[3])
        if f[4] != "ok":
            dg = [x for x in r.stderr.splitlines() if x.strip() and not x.startswith(("Shader failed", "FAIL", "sdp2dksh"))]
            if dg and stage == "u": f[10] = f[10] + " :: " + dg[0][:120]
            return dict(row=row, status=("translate_fail" if stage == "t" else "uam_fail"), enc=enc, mism=mism,
                        detail=first_line(f[10] if len(f) > 10 else r.stderr) + ((" [" + note + "]") if enc == "hand" else ""))
    return dict(row=row, status="ok", enc=enc, mism=mism, detail=note)

# static scan: every fail()/failf() site of the patched MojoShader parser + glsl/common profile files, classified by hand
#   unimplemented  = valid opcode/feature with no emitter;  rejects-valid = valid D3D9 input refused;  limit = profile/layout limit;
#   conditional = rejects valid input only under the stated condition;  bug = reserved-mask/other upstream defect
STATIC = [  # (file, message prefix, class, reachable via glsldk path, note)
 ("mojoshader.c", "reserved bits in DCL dword", "bug", "y", "vs_3_0 sampler dcl: reserved_mask 0x0FFFFFFF covers texture-type bit 27, so dcl_cube is refused (census: vs_3_0 cube)"),
 ("mojoshader.c", "CALL to this label must be wrapped", "rejects-valid", "y", "one label called both inside and outside LOOP/ENDLOOP"),
 ("mojoshader.c", "CALL to this label must not be wrapped", "rejects-valid", "y", "same label, other order"),
 ("mojoshader.c", "TEXLD src0 must have no modifiers", "rejects-valid", "y", "sm2+ texld coordinate with negate/abs; ps_3_0 neg is legal"),
 ("mojoshader.c", "TEXLD src0 must not swizzle", "conditional", "y", "ps_2_0/2_x texld coordinate swizzle (legal=? for sm2); ps_3_0 passes"),
 ("mojoshader.c", "TEXLD src1 must have no modifiers", "conditional", "y", "sampler operand modifiers"),
 ("mojoshader.c", "TEXLD src1 must not swizzle", "conditional", "y", "sampler operand swizzle"),
 ("mojoshader.c", "Partial precision result mod in non-pixel shader", "conditional", "y", "_pp in vs_2_0+/vs_3_0 (legal=?)"),
 ("mojoshader.c", "Centroid result mod in non-pixel shader", "conditional", "y", "centroid outside ps"),
 ("mojoshader.c", "illegal source mod for this Shader Model", "conditional", "y", "bias/sign/comp/x2 in ps_2_0+ (legal=?)"),
 ("mojoshader.c", "%s needs a 3D or Cubemap sampler", "rejects-valid", "y", "texm3x3/texm3x3tex etc. without a CTAB sampler declaration; sampler type cannot be derived from the opcode"),
 ("mojoshader.c", "TEXM3X2TEX needs a 2D sampler", "conditional", "y", "same, without CTAB"),
 ("mojoshader.c", "Temp register r%d used uninitialized", "conditional", "y", "whole-register flow-insensitive check; see rows if@temp-written-in-branch-only, rep@temp-written-in-loop"),
 ("mojoshader.c", "opcode '%s' not available in this shader type", "conditional", "y", "dst in ps (legal=?), vertex-only/pixel-only table in mojoshader_internal.h"),
 ("mojoshader.c", "Relative addressing in pixel shader version < 3.0", "conditional", "y", "c[aL] in ps_2_x (legal=?)"),
 ("mojoshader.c", "relative addressing unsupported without a CTAB", "limit", "n", "glsldk branch (glsldk_rel_const) bypasses it when there is no CTAB; reachable only if a CTAB exists and the glsldk branch is skipped"),
 ("mojoshader.c", "relative addressing of indeterminate array", "conditional", "?", "relative c[] with a CTAB: census rows c[a0.x+n]@ctab-* all passed"),
 ("mojoshader.c", "Relative addressing of dest tokens is unsupported", "conditional", "n", "non-glsldk #else branch; glsldk handles o[aL] (census ok)"),
 ("mojoshader.c", "Shader Model %u.%u is currently unsupported", "rejects-valid", "y", "versions other than 1.1-1.4, 2.0, 2.1, 3.0 (vs_1_0, ps_1_0, *_2_sw, *_3_sw are not in the SM1-3 model list of the census)"),
 ("mojoshader.c", "coissue instruction in Shader Model >= 2.0", "limit", "y", "coissue bit (ps_1_x only): not exercised by the census, see README"),
 ("profiles/mojoshader_profile_glsl.c", "TEXREG2AR unimplemented", "unimplemented", "y", "ps_1_1-1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXREG2GB unimplemented", "unimplemented", "y", "ps_1_1-1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXREG2RGB unimplemented", "unimplemented", "y", "ps_1_2-1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXDP3TEX unimplemented", "unimplemented", "y", "ps_1_2-1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXM3X2DEPTH unimplemented", "unimplemented", "y", "ps_1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXDP3 unimplemented", "unimplemented", "y", "ps_1_2-1_3"),
 ("profiles/mojoshader_profile_glsl.c", "TEXDEPTH unimplemented", "unimplemented", "y", "ps_1_4"),
 ("profiles/mojoshader_profile_glsl.c", "BEM unimplemented", "unimplemented", "y", "ps_1_4"),
 ("profiles/mojoshader_profile_glsl.c", "TEXLDP on a cubemap", "rejects-valid", "y", "texldp with a cube sampler (ps_2_0, ps_2_x, ps_3_0)"),
 ("profiles/mojoshader_profile_glsl.c", "glsldk: no attribute location", "limit", "y", "vs input usages psize, tessfactor, positiont, fog, depth, sample, and texcoord index 15 have no location in fnvnx_dk_layout.h"),
 ("profiles/mojoshader_profile_glsl.c", "glsldk: no varying location", "limit", "y", "vs_3_0 output / ps_3_0 input usages position, psize, tessfactor, positiont, sample have no location"),
 ("profiles/mojoshader_profile_glsl.c", "SRCMOD_DZ unsupported", "conditional", "y", "generic source path; ps_1_4 texld/texcrd _dz/_dw take another path (census ok)"),
 ("profiles/mojoshader_profile_glsl.c", "SRCMOD_DW unsupported", "conditional", "y", "same"),
 ("profiles/mojoshader_profile_glsl.c", "Relative addressing of input registers not supported", "limit", "n", "non-glsldk branch; glsldk uses dk_finalize_relative (v[aL] ok)"),
 ("profiles/mojoshader_profile_glsl.c", "centroid on a built-in input", "conditional", "y", "centroid on vPos/vFace dcl (not legal)"),
 ("profiles/mojoshader_profile_glsl.c", "centroid unsupported in", "conditional", "n", "non-glsldk branch"),
 ("profiles/mojoshader_profile_glsl.c", "predicated destinations unsupported", "limit", "n", "guarded by !support_glsldk"),
 ("profiles/mojoshader_profile_glsl.c", "Too many source args", "limit", "?", "internal buffer limit"),
 ("profiles/mojoshader_profile_glsl.c", "operation string too large", "limit", "?", "internal 256-byte statement buffer"),
 ("profiles/mojoshader_profile_glsl.c", "BUG: unhandled misc register", "limit", "?", "misc register types other than vPos/vFace"),
 ("profiles/mojoshader_profile_glsl.c", "unknown pixel shader attribute register", "limit", "?", "unknown input register type"),
]

def static_scan(deps, out):
    import re
    rows = []
    for rel in ("mojoshader.c", "profiles/mojoshader_profile_glsl.c", "profiles/mojoshader_profile_common.c"):
        for n, ln in enumerate(open(os.path.join(deps, "mojoshader", rel), errors="replace"), 1):
            m = re.search(r'fail[f]?\(ctx, "((?:[^"\\]|\\.)*)"', ln) or re.search(r'fail\(ctx, #op " (unimplemented[^"]*)"', ln)
            if not m: continue
            msg = m.group(1)
            for f, pre, cls, reach, note in STATIC:
                if f == rel and (msg.startswith(pre) or (pre.endswith(" unimplemented") and False)): rows.append((rel, n, msg, cls, reach, note)); break
    # the unimplemented emitters come from a macro: one row per EMIT_GLSL_OPCODE_UNIMPLEMENTED_FUNC use
    for n, ln in enumerate(open(os.path.join(deps, "mojoshader", "profiles/mojoshader_profile_glsl.c")), 1):
        m = re.match(r"EMIT_GLSL_OPCODE_UNIMPLEMENTED_FUNC\((\w+)\)", ln)
        if m:
            for f, pre, cls, reach, note in STATIC:
                if pre.startswith(m.group(1) + " unimplemented"):
                    rows.append(("profiles/mojoshader_profile_glsl.c", n, m.group(1) + " unimplemented in glsl profile", cls, reach, note))
    rows = [r for r in rows if "unimplemented in glsl profile" not in r[2] or r[2].split()[0] in r[2]]
    with open(out, "w") as o:
        o.write("file\tline\tmessage\tclass\treachable_in_glsldk_path\tnote\n")
        seen = set()
        for r in sorted(rows):
            if (r[0], r[1]) in seen or r[2] == "TEXREG2AR unimplemented" : continue
            seen.add((r[0], r[1])); o.write("\t".join(str(x) for x in r) + "\n")
    print("static fail sites listed:", len(seen))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", required=True); ap.add_argument("--deps", required=True)
    ap.add_argument("--work", default="/home/user/work/l6/sm/cases"); ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(HERE, "sm_ops.tsv"))
    ap.add_argument("--static-only", action="store_true")
    a = ap.parse_args()
    static_scan(a.deps, os.path.join(HERE, "sm_static_fails.tsv"))
    if a.static_only: return
    ops = sm_hand.load_opcodes(os.path.join(a.deps, "mojoshader", "mojoshader_internal.h"))
    rows = sm_cases.cases()
    os.makedirs(a.work, exist_ok=True)
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda t: run_case(t[0], t[1], a, ops), enumerate(rows)))
    with open(a.out, "w") as o:
        o.write("model\tkind\tfeature\tstatus\tdetail\tencoded_by\n")
        for r in res:
            m, kind, feat, text, legal = r["row"]
            st = r["status"]
            st = st if st == "ok" else "%s(%s)" % (st, r["detail"].split(" [")[0]) if st != "cannot_encode" else "cannot_encode"
            det = ("legal=? " if legal == "?" else "") + (r["detail"] if r["status"] == "cannot_encode" or r["status"] == "ok" else "")
            if r["mism"]: det += " hand!=asm"
            if r["status"] in ("translate_fail", "uam_fail") and " [" in r["detail"]: det += r["detail"].split(" [", 1)[1].rstrip("]")
            o.write("\t".join([m, kind, feat, st.replace("\t", " "), det.strip().replace("\t", " "), r["enc"]]) + "\n")
    cnt = collections.Counter(r["status"] for r in res)
    print("universe=%d ok=%d translate_fail=%d uam_fail=%d cannot_encode=%d" % (len(res), cnt["ok"], cnt["translate_fail"],
          cnt["uam_fail"], cnt["cannot_encode"]))
    print("encoded_by hand=%d asm=%d; asm-vs-hand byte mismatches=%d" % (sum(r["enc"] == "hand" for r in res),
          sum(r["enc"] == "asm" for r in res), sum(r["mism"] for r in res)))
    per = collections.Counter((r["status"], first_line(r["detail"]).split(" [")[0][:90]) for r in res if r["status"] != "ok")
    for (s, d), n in per.most_common(): print("%4d %s %s" % (n, s, d))

if __name__ == "__main__":
    main()
