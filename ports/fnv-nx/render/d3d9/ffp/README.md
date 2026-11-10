# D3D9 fixed-function pixel pipeline (T-FFP-PS, rule RD5)

One program, behaviour selected by data. `ffp.frag.glsl` (deko3d GLSL, include `d9_epilogue.glsl` for alpha test + fog) and
`ffp_ps_ref.c` (CPU twin, same function names) read `NxFfpPsState` (`ffp_state.h`, std140, 800 bytes, UBO binding 3;
fnvnx_dk_layout.h uses 0..2). Test: `python3 tests/l6/ffp_ps/test_ffp_ps.py`.

## State layout (bytes)
stage[8] x 96: +0 colorOp alphaOp resultArg texType | +16 cArg0 cArg1 cArg2 ttff | +32 aArg0 aArg1 aArg2 pad |
+48 constant rgba | +64 bump m00 m01 m10 m11 | +80 lscale loffset 0 0; 768 tfactor rgba; 784 specularEnable + pad.
All D3D9 values are stored RAW (`nx_ffp_ps_state_from_d3d9` only converts D3DCOLOR and float bit patterns); the program is
total over every bit pattern. Interface: varyings COLOR0=0, COLOR1=1, TEXCOORDn=2+n (vec4), fog at 18 (epilogue).
Samplers: 2D = n, CUBE = 8+n, VOLUME = 16+n. SHADEMODE flat: build with `NX_FFP_FLAT` (locations 0,1 `flat`, the vertex stage
must match). Vertex-side, not here: COLORVERTEX, TEXCOORDINDEX/texgen, texture matrix (but see ttff below).

## Domain functions (out-of-range values; WineD3D has no defined behaviour for most)
- `ffp_norm_op`: 1..26 itself, everything else (0, 27.., 2^32-1) = NOP: reads no arg, writes no colour; its alpha op still runs.
  (wined3d: index 0 hits the `default:` of shader_glsl_ffp_fragment_op = no-op; >26 reads args[] out of bounds, we extend 0's case.)
- arg: `a&15` 0..6 as D3DTA, 7..15 = ZERO (reads vec4(0)); bit4 COMPLEMENT then bit5 ALPHAREPLICATE; bits 6.. ignored. (wined3d
  emits "<unhandled arg>" = shader compile failure; the choice is ours.)
- texType: 1 2D, 2 cube, 3 volume; anything else = none. RESULTARG: exactly D3DTA_TEMP (5) writes TEMP, any other value CURRENT.

## Decisions where the docs are silent (source: wined3d_ffp_get_fs_settings, is_invalid_op, shader_glsl_ffp_fragment_op/
## generate_ffp_fragment_shader, compute_texture_matrix unless noted)
1. COLOROP DISABLE (after normalisation) ends the cascade; stage 0 disabled = DIFFUSE (+SPECULAR). CURRENT at stage 0 = DIFFUSE; TEMP starts 0.
2. Unbound texture (none) sampled = (0,0,0,0) (wined3d dummy texture, non-legacy). A stage whose op READS an argument slot selecting
   TEXTURE while no texture is bound is replaced by SELECTARG1 CURRENT (is_invalid_op); colour and alpha decided separately.
   Slots read: SELECTARG1/PREMODULATE arg1; SELECTARG2 arg2; MULTIPLYADD/LERP arg0,1,2; all others arg1,2 (incl. BUMPENVMAP*, DOT3).
3. ALPHAOP DISABLE (and NOP, BUMPENVMAP*): alpha of the destination is left unchanged. COLOROP BUMPENVMAP*: stage writes nothing at all.
4. DOTPRODUCT3 in COLOROP forces the alpha op to DOT3 with the colour args; result replicated to rgba. As ALPHAOP it replicates the
   dot of the alpha args' rgb (wined3d does the same).
5. Blend factors (BLEND*ALPHA) are the raw alpha of DIFFUSE/TEXTURE/TFACTOR/CURRENT: no argument modifiers. LERP = Arg1*Arg0 + Arg2*(1-Arg0).
6. Clamping: DEVIATION. Every stage result is saturated to [0,1] when written, and the final colour too (D3D). wined3d only clamps
   some ops; identical for inputs in [0,1]. NaN in = unspecified.
7. BUMPENVMAP(*): stage s+1's coordinates (before the texture type's own use) get +(M^T * tex_s.xy), M row-major m00 m01 m10 m11, i.e.
   du' = du*m00 + dv*m10, dv' = du*m01 + dv*m11, added AFTER the projective divide. LUMINANCE: stage s+1's fetched texel (all four
   channels) *= clamp(tex_s.z*lscale + loffset, 0, 1). Only if stage s survived the invalid-op rule.
8. Projective: ttff bit 8 and not cube: coord.xyz /= coord[count-1] (count = ttff&255 in 1..4, else .w), skipped when the divisor is 0.
   DEVIATION: applies to every sample of the stage (wined3d only when an argument names TEXTURE explicitly).
9. PREMODULATE (wined3d: no-op, "Unhandled operation"): DEVIATION, per D3D docs: stage result = Arg1 and the next stage's CURRENT is
   multiplied by that stage's texture (rgb for the colour op, alpha for the alpha op) before use. Not applied if the cascade ends.
10. SPECULARENABLE (any non-zero): spec.rgb added after the last stage, alpha untouched; specular ARG ignores the flag.
11. TEXTURETRANSFORMFLAGS count outside 1..4 with PROJECTED divides by w (wined3d's vertex side would use the attribute count).
