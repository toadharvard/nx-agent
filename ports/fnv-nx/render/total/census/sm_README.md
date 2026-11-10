# SM1-3 translator totality census (R11) - files sm_*

Rerun (about 10 s; build once with `FNVNX_SHADERS_DEPS=D FNVNX_SHADERS_BUILD=B render/shaders/build.sh --tests`; needs flex+bison+meson):
`python3 -I sm_census.py --build B --deps D [--work DIR] [--jobs 2]` -> sm_ops.tsv and sm_static_fails.tsv (`--static-only` for the latter).
Files: sm_cases.py = universe U (one minimal valid asm program per row; legal=y|?), sm_hand.py = independent token-stream encoder.
Opcode numbers come from the INSTRUCTION table of mojoshader_internal.h. Per row: s6b_asm; if it refuses, sm_hand (encoded_by=hand).
Where both exist the bytes were compared (comments stripped): 0 mismatches on 1061 rows, so the hand path is a faithful encoder.
Status: translate_fail = front end or glsldk emit (sdp2dksh --no-dksh), uam_fail = uam/DKSH stage, detail carries the first error line.

Counts (1147 rows): ok 1011, translate_fail 117, uam_fail 19, cannot_encode 0.
By legality: legal=y rows 979 -> ok 918, translate_fail 50, uam_fail 11; legal=? rows 168 -> ok 93, translate_fail 67, uam_fail 8.
By kind: opcode 550, srcmod 183, regtype 175, usage 95, dstmod 55, sampler 51, addr 38.

Failing legal=y features (model sets in sm_ops.tsv):
- unimplemented emitters (ps_1_x): texreg2ar, texreg2gb (1_1-1_3), texreg2rgb, texdp3, texdp3tex (1_2-1_3), texm3x2depth (1_3), texdepth, bem (1_4)
- GLSL syntax error from the emitter: texm3x3spec (ps_1_1-1_3)
- texm3x3 without CTAB cube sampler (ps_1_2, 1_3)
- texldp with cube sampler (ps_2_0, 2_x, 3_0); texldd with cube or volume sampler (ps_2_x, 3_0: textureCubeGrad/texture3DGrad missing)
- vs_3_0 dcl_cube sampler refused (upstream reserved-bit mask bug)
- breakp in ps_2_x/ps_3_0 (predicate used as vector condition)
- ps_3_0 vFace read (float assigned to vec4), also vFace.x
- ps_3_0 texld with negated coordinate
- usage without a location (fnvnx_dk_layout.h): vs inputs psize, tessfactor, positiont, fog, depth, sample (vs_1_1..3_0), vs input texcoord15; vs_3_0 outputs tessfactor, sample

Failing legal=? rows only (spec status not certain): bias/bx2/comp/x2 source mods in ps_2_0+ (42), texld coordinate neg/abs/swizzle in ps_2_x,
dst in ps_2_0+, _pp in vs, predicate flow (if p0, callnz p0, if !p0) in vs_2_x+/ps_2_x+, label called inside and outside a loop, ps_2_x c[aL], ps_3_0 position/psize/tessfactor/sample inputs.

Not covered by the universe: coissue, ps_1_x phase blocks beyond one phase, *_sw versions, vs_1_0/ps_1_0, c[] index above the model limits tested,
swizzle/write-mask combinations (only per-opcode mask rules), instruction-count limits. Legality comes from the author's memory of the D3D9 SDK tables, not a machine-readable spec.
sm_static_fails.tsv lists the fail() sites that can refuse valid input (class, whether the glsldk path reaches them, note); the other ~220 sites validate invalid input or are internal BUG checks and are not listed.
