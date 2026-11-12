#!/usr/bin/env python3
"""sm_linkclass.py - classify the failing rows of sm_link.tsv (compile/link failed + CreateShader refused) into root-cause classes.
Each class: Mesa error, emitting wined3d function (glsl_shader.c / shader.c, Wine 9.0 mirror c8373ee4), D3D9 doc verdict
(msdocs-sm mirror e18183f8): DEFINED (Wine defect, translator implements per the docs), UNDEFINED (docs define no semantics; the
failure is a static form predicate), ARGS (compile failure is a function of one compile arg).  Output: sm_linkclass.tsv (row->class)
and a class summary on stdout.  Usage: sm_linkclass.py [sm_link.tsv]"""
import csv, re, sys, collections
# (class id, row regex on the 'feature' part after model|set|, error regex, verdict, doc page(s), emitter)
C = [
 ("pred_reg", r"(breakp|callnz_pred|if_pred|not@if\.p0)$", r"unrecognised_register", "DEFINED",
  "breakp---vs/ps, callnz-pred---vs/ps, if-pred---vs/ps (condition = predicate register p0, optional !)",
  "glsl_shader.c shader_glsl_get_register_name: no case for the predicate register type -> default 3057 'unrecognised_register' (reads no arg)"),
 ("sincos_xyz", r"sincos@\.xyz$", r"syntax error", "UNDEFINED",
  "sincos---vs: 'exactly one of .x | .y | .xy' (sincos---ps has no write-mask other than the same three)",
  "glsl_shader.c shader_glsl_sincos (major<4): default case 4806-4809 ERR() emits nothing after shader_glsl_append_dst opened '(' -> unbalanced text"),
 ("texm3x3_2d", r"texm3x3(pad|tex)@ctab=", r"texture\(sampler2D, vec3\)", "ARGS",
  "texm3x3tex---ps: result (u,v,w) samples the texture of stage 3 (3 coordinates)",
  "glsl_shader.c shader_glsl_texm3x3tex -> shader_glsl_get_sample_function: sampler type from ps_args->tex_types[stage]; 2D sampler + vec3 coordinate"),
 ("aL_alu", r"operand\|\w+@src\d=aL$", r"cannot be assigned|no matching function", "UNDEFINED",
  "ps/vs register pages: aL 'R, 1 component', docs name only relative addressing (c[aL+n], v[aL]) as its use; no ALU-operand value rule",
  "glsl_shader.c shader_glsl_get_register_name case WINED3DSPR_LOOP -> 'aLn' (2832), declared 'int aLn' (2542): scalar int in a vec/float expression"),
 ("fog_pts_vec4", r"(dst|expp|lit)@dst=o(Fog|Pts)$", r"vec4 cannot be assigned to variable of type float", "DEFINED",
  "vs-registers-vs-1-1/2-0/2-x: oFog, oPts dimension 1, write mask '' or .x admitted; dst/lit/expp pages define dest.x",
  "glsl_shader.c shader_glsl_dst 4752 / shader_glsl_lit 4699 / shader_glsl_expp 4277: hard vec4(...) constructor + dst_mask '' for a scalar sink"),
 ("b_alu", r"b0@mov\.s0$", r"bool cannot be assigned", "UNDEFINED",
  "vs-registers: b# 'Constant Boolean Register', R; used by if/callnz/break_comp bool pages only; no ALU-operand value rule",
  "glsl_shader.c shader_glsl_get_register_name case WINED3DSPR_CONSTBOOL -> 'vs_b[n]' (2821), 'uniform bool vs_b[N]' (2271)"),
 ("a0_mov", r"a0@mov\.dst$", r"vec4 cannot be assigned to variable of type ivec4", "UNDEFINED",
  "mov---vs: 'For version vs_1_1 it can also be used to write the address register'; mova---vs for 2_0+",
  "glsl_shader.c shader_glsl_mov 3906: for vs_2_0+ and handler != MOVA the final else emits the vec4 source into 'ivec4 An' (2506)"),
 ("label_2047", r"flow\|label(=|-max-)l2047$", r"no function with name 'subroutine2047'", "DEFINED",
  "label---vs/ps: label number 0..2047 for vs_2_sw, vs_3_0, ps_3_0",
  "shader.c 1411 reg_maps->labels |= 1u << idx (uint32 bitmap, idx 2047 -> bit 31); glsl_shader.c 2176-2181 prototypes only set bits; shader_glsl_call 5142 prints subroutine2047"),
 ("create_refused", r"(opcode\|(lit|sgn|loop|endloop)|addr\|c\[aL\+n\]|dcl\|in:position0)$", r"^$", "UNDEFINED",
  "lit---vs, sgn---vs (vs only); loop---ps, endloop---ps (ps_3_0/3_sw columns only, not ps_2_x); aL in ps_2_x not defined; dcl-usage---ps (no position input)",
  "CreateShader returns D3DERR_INVALIDCALL 8876086c (not a link failure)"),
]
def main():
    r = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "sm_link.tsv"), delimiter="\t"))
    bad = [x for x in r if x["min_compile"] != "ok" or x["create_hr"] != "00000000"]
    out, cnt, unk = [], collections.Counter(), []
    for x in bad:
        feat = x["row"].split("|", 2)[2] if x["row"].count("|") >= 2 else x["row"]
        key = x["row"]
        err = x["min_gl_error"] if x["create_hr"] == "00000000" else ""
        for cid, rr, er, verdict, doc, emit in C:
            if re.search(rr, key) and re.search(er, err):
                out.append((x["row"], cid, verdict)); cnt[(cid, verdict)] += 1; break
        else: unk.append(x["row"])
    with open("sm_linkclass.tsv", "w") as f:
        f.write("row\tclass\tverdict\n")
        for o in out: f.write("\t".join(o) + "\n")
    print("failing rows %d of %d; classified %d; unclassified %d" % (len(bad), len(r), len(out), len(unk)))
    for (cid, v), n in sorted(cnt.items(), key=lambda t: t[0]): print("%-15s %-9s %3d" % (cid, v, n))
    for u in unk: print("UNCLASSIFIED", u)
    return 1 if unk else 0
sys.exit(main())
