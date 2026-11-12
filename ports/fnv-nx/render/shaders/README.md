# render/shaders - libfnvnx_shaders and sdp2dksh

D3D9 shader bytecode (SM1.1-3.0, from `shaderpackageNNN.sdp`) -> deko3d DKSH, plus reflection.
Per D6 the translation runs at install time on the player's machine (host build here); the library is
portable C/C++ so it can later run on the console at first boot with an SD cache keyed by bytecode hash.

Since J-0015 the renderer is native deko3d with hand-written GLSL per shader family. The translated
shaders are the **correctness/parity reference** and a temporary per-family fallback; correctness comes
before performance, and the CTAB dump (below) feeds S6n's constant layouts.

## Build and use

```
render/shaders/build.sh [--tests]        # clones pinned MojoShader + uam, applies our patches, builds all
build/host/sdp2dksh -o OUT [--glsl] shaderpackage*.sdp   # OUT/<hash>.dksh, <hash>.json, index.tsv
build/host/sdp2dksh --ctab shaderpackage*.sdp            # TSV: package name hash constant set reg count class type rows columns elements
build/host/sdp2dksh --list shaderpackage019.sdp
tests/s6b/run.py                          # corpus + unit tests (+ exec scenes on V6n's oracle), prints the matrix
```

Pins (build.sh): MojoShader `ad5dff8` (zlib), uam `5a5afc2` (Mesa-derived, MIT-style), and for tests
only mojoshader_assembler `c4b96e2` (zlib). Our changes: `mojoshader.patch` (the `glsldk` profile),
`uam.patch` (static library target, in-memory DKSH + stats, `uam_api.h` C API). Nothing is vendored.
Host needs git, cmake, ninja, gcc/g++, bison, flex, python3 with meson and mako.

## Pipeline and the seam

```
bytecode --> front end (frontend.c) --> reflection JSON / CTAB    <- stable
                 |
                 v  fnvnx_glsl_backend (backend.h)                <- replaceable
            GLSL 4.50 obeying include/fnvnx_dk_layout.h          <- stable contract
                 |
                 v  uam (dk.cpp -> uam_api)                       --> DKSH (validated)
```

* **Front end** parses with MojoShader's `bytecode` profile (no code generation): stage, model,
  instruction count, constants read (by register, relative ranges), literals, samplers + types, inputs,
  outputs with their fixed locations, and the full CTAB. It does not depend on the GLSL backend.
* **Backend** = `{ name, emit(bytecode, reflection) -> GLSL }`. The only one today is
  `mojoshader-glsldk`. An optimising backend (own IR lifted from the token stream: SSA, constant
  folding, DCE, register-array scalarisation, swizzle simplification, or SPIR-V + spirv-opt) plugs in
  here by emitting GLSL against the same layout contract; front end, reflection format, front-end
  D3D9 code and the DKSH stage stay untouched. Not built (and, after J-0015, not planned).
* **Layout contract** (`fnvnx_dk_layout.h`, mirrored in the patch):
  UBO 0 = float constants `vec4 c[256]` by register; UBO 1 = `ivec4 i[16]` + `bool b[16]` (std140,
  bools at byte 256 + 16n); UBO 2 = driver constants (the VS block is 64 bytes: `posfix` at 0, `pad` at 16, below; the PS block is 352 bytes: `uvec4 stype[2]` appended at byte 320,
  the ps_1_x sampler type words, below); sampler binding = sampler register (ps_1_x: 2D n, CUBE 8 + n, VOLUME 16 + n, below); vertex
  attribute location and VS->PS varying location by the total declaration-set rule below (fog at 18).
* **D3D9 conventions** in the `glsldk` profile: VS epilogue `gl_Position.xy += posfix.xy * w` (half-pixel,
  driver sets `(-1/w, +1/h)`) and `gl_Position.y *= posfix.z` (y flip, -1 with OriginUpperLeft); depth is
  left in D3D's [0,1] (device uses DepthZeroToOne); vPos = `gl_FragCoord.xy - 0.5`; PS epilogue for alpha
  test (`ALPHAFUNC/ALPHAREF` from the driver UBO, all 8 compare functions) and, for ps < 3.0,
  fixed-function fog (vertex factor / exp / exp2 / linear on depth); ps_1_x texbem matrices from the
  driver UBO; `def` constants used with relative addressing become GLSL `const` arrays (uam rejects
  default-block uniforms).
* **Numerics** (D3D9 leaves them implementation-defined): `rcp`/`rsq`/`log` of 0, `nrm` of a zero vector and
  `lit` where D3D9's pseudo-code and the implementations part follow WineD3D (wine 9's GLSL: IEEE infinities,
  `nrm(0)` = the source, `lit` z = `w == 0 ? 0 : pow(max(0, y) * step(0, x), clamp(w, -128, 128))`), the reference of the S16 sweeps, which render the original game under Wine; this is the default
  and what the game ships. `--numerics nine` (or `FNVNX_NUMERICS=nine`; flag `FNVNX_SHADER_NUMERICS_NINE`)
  selects Gallium Nine's clamped results instead, for V6n's oracle (`v6n_nine` on softpipe): the s6b exec
  scenes and `render/verify/shaders/{parity,sdpfuzz,vsscenes}.py` set it. Both bodies are in the GLSL behind
  `#ifdef DK_NUMERICS_NINE`. Relative constant reads of a shader that defs float constants see the def'd values
  (`dk_relc`), as WineD3D and Nine load them into the constant file; `texkill` tests xyzw from ps_2_0 on (xyz in
  ps_1_x), as both do. Measured on the sweep's failing programs (S16, sweep 74a18a60420e, local replay): zero
  set 171 over -> 0, atest 28 -> 1, neg 5 -> 1, large 43 -> 38, no program newly over in any set.

## Location rule (vertex inputs, VS->PS varyings): total over usage 0..13 x index 0..15

Limits (uam: `nv_attributes.h` `NvAttrib_Generic(0..31)`, slot 31 = clip vertex in `compiler_iface.cpp`; Maxwell fetch): **16** vertex attribute
locations, **31** usable varying locations (0..30). 224 (usage, index) pairs cannot map injectively, so the slot is a function of the program's
declaration set (`fnvnx_dk_assign`, `fnvnx_dk_layout.h`, proof there): a pair with a canonical slot (the old tables) keeps it; the other declared
pairs, in ascending (usage, index), take the free slots in ascending order. A legal program (<= 16 inputs, <= 12 varyings) always fits.
Design choice: no run-time remap. VS inputs: the rule over the VS's own dcls; the reflection JSON lists each dcl's location and the device
binds the stream element with the same (usage, index) to it. Varyings: both stages of a draw are translated with the same link set L
(`fnvnx_shader_opts.link_pairs`, `sdp2dksh --link U:I,...`, part of the variant key); with canonical pairs only (the FFP programs, nearly all real
shaders) L is irrelevant and one translation serves every pairing. A PS-read POSITION0/PSIZE0 is a varying copy the VS writes only if in L.
Consequences for consumers: pairs that had a location keep it, so `d3d9_exec.cpp` `AttribLocation()`, `n1/`, `ffp.vert/frag.glsl`
(COLOR0=0, COLOR1=1, TEXCOORDn=2+n, fog=18) are unchanged and correct for canonical shaders. A full redesign would make `AttribLocation()` read the
reflection JSON `inputs[].location` (needed for TEXCOORD8..15, POSITION1.., PSIZE, FOG, DEPTH, SAMPLE, TESSFACTOR, POSITIONT inputs) and make the
device compute L per VS/PS pair when either stage has a non-canonical varying.

## Varying completion: the unwritten pixel input rule (vertex shader epilogue; `tests/l6/sm_link/test_sm_link.py`)

A pixel input (usage, index) that the paired vertex shader does not write is undefined in D3D9 (the MS register pages for oD#/oT#/oFog and
ps_3_0 v# list "Defaults: None") and **unlinked in GL** (`dk_fog_in` at location 18 made all 33 recorded pairs fail to link on llvmpipe; deko3d
links it and reads garbage). The project's reference profile, WineD3D 9.0, defines every case, so the rule is Wine's, per component:

| input | value when the VS does not write it | WineD3D 9.0 `glsl_shader.c` |
|---|---|---|
| fog factor, VS writes oFog / FOG0 | the value, saturated (vs < 3.0 oFog is saturated in the epilogue; ps < 3.0 clamps again) | `shader_glsl_generate_vs3_rasterizer_input_setup` (`ffp_varying_fogcoord = clamp(outputs[].x, 0, 1)`) |
| fog factor, VS does not write it | **0.0** ("fully fogged: start = 1.0, end = 0.0") | `shader_glsl_generate_vs_epilogue` (`else if (!reg_maps->fog) ffp_varying_fogcoord = 0.0`) |
| diffuse (COLOR0) components not written | **1.0** below ps_3_0 and for fixed function, **0.0** for ps_3_0 | `..._rasterizer_input_setup` (ps_major < 3: `ffp_varying_diffuse = vec4(1.0)`), `shader_glsl_setup_vs3_output` (unwritten comps = 0.0) |
| specular, texcoord, every other pair, components not written | **0.0** | same two functions |
| PS-read PSIZE0, VS has no oPts | **0.0** (not the undefined `gl_PointSize`) | `shader_glsl_setup_vs3_output` |

Fog source and table fog: with a vertex shader the factor is the oFog value (MS "Vertex Fog": "When using a vertex shader, you must use vertex fog ... write
the per-vertex fog intensity to oFog"; Wine `state_fog_fragpart`: FOGTABLEMODE NONE with a VS = linear fog with start 1, end 0, which is the factor
itself). FOGTABLEMODE != NONE is pixel fog: MS "Pixel Fog" / "Fog Parameters" define it on the device-space depth in [0, 1], which is exactly what the
ps < 3.0 epilogue reads (`gl_FragCoord.z`, mode 2..4 of `ps_drv_fog.x`); the varying is read only in mode 1. (Wine routes `gl_Position.z` through
the same varying, `VS_FOG_Z` in `find_vs_compile_args`, an approximation of the depth GL cannot give a VS; here the depth is exact, so the VS needs
no render state: the fog rule has **no driver field other than the existing `ps_drv_fog.x`**, mode 1 iff FOGENABLE && FOGTABLEMODE == NONE.)

What a VS writes, with no run-time decision (`dk_pad_varyings` in the patch, `fnvnx_dk_layout.h` "Varying completion"):

* **base** (every vertex shader): the components a declared output leaves out (union of the instruction write masks, per component), the pairs of
  the link set L it does not declare, and **FOG0 whenever it does not declare it** (so ps < 3.0's location 18 is always written).
* **complete** (`FNVNX_SHADER_VS_COMPLETE`, `sdp2dksh --vs-complete`, a Create-time variant like the epilogue variants): additionally every slot 0..30
  still unwritten, with the default of the pair whose canonical slot it is. The canonical map is a bijection onto 0..30 (checked in the test), so no slot
  a pixel stage with canonical inputs can read is left unwritten. Cost: about 5 Maxwell instructions per extra vec4 slot (19 -> 164 for a vs_1_1 that
  writes oPos only), which is why it is a variant and not the base.
* The device picks the variant by `fnvnx_dk_vs_needs_complete(vs_written, ps_read)`: complete iff the PS reads a canonical pair the VS does not
  write. The reflection JSON carries the two arrays (`varyings_written` of a VS: its outputs without POSITION0/PSIZE0, plus FOG0 always;
  `varyings_read` of a PS: its inputs, plus FOG0 for ps < 3.0, ps_1_x `t#` included). Non-canonical pairs the PS reads are in L (location rule above).
  Proof: a PS-read pair is declared by the VS (missing components base-completed), or in L (base writes it), or canonical and unwritten (complete writes its slot).
* **Driver data**: COLOR0's default depends on the pair's pixel model, which a stage translated alone does not know: VS driver block word
  `vs_drv_pad.x` (byte `FNVNX_DK_VS_PAD_OFFSET` = 16 of the 208-byte block; `posfix` at 0 unchanged; `vs_drv_clip[8]` at byte 64 = the user clip planes, `vs_drv_point` at byte 192 = (POINTSIZE, min, max): Clip planes and point size below) = **1.0 unless the pixel stage is ps_3_0, then 0.0**.
  A zeroed block means the ps_3_0 rule. Everything else is bytecode.
* **No rule**: none left. The MS pages say "None" where Wine defines a value, and the only per-pair datum (COLOR0's) is data. Not covered by
  either source: a *partial* PS read mask on a scalar register (FOG, PSIZE) is completed like any component (Wine does the same through the output mask).

Also here: the front end reports vs < 3.0 oFog / oPts as FOG0 / PSIZE0 (the bytecode profile said index 1 / 2, which made the location table give oFog slot 1,
the slot of COLOR1) and lists ps_1_x `t#` among the PS inputs.

## API (`include/fnvnx_shaders.h`)

`fnvnx_shader_translate(bytecode, n, opts, result)` -> `result.dksh`, `result.reflection_json`,
optional `result.glsl`, `result.stats` (D3D slots, GLSL statements, Maxwell instructions, GPRs, code
bytes: the baseline for any later backend). `fnvnx_shader_ctab` returns the constant table as structs.
The cache key is the SHA-256 of the bytecode (`fnvnx_sha256`, lib/sha256; hex name `fnvnx_shader_key_hex`). `fnvnx_dksh_validate` checks DKSH headers.
SDP reader/writer: `include/fnvnx_sdp.h`, format in [`SDP.md`](SDP.md).

## Fixes to MojoShader in the patch (besides the profile)

* Predicated instructions: GLSL emission added (per-component `mix`/`if` on the predicate swizzle), and
  the predicate token is read right after the destination token, as D3D9 encodes it (wined3d
  `shader_sm1.c` reads dst, predicate, sources; upstream MojoShader read it last).
* ps_1_4 `texld` / `texcrd` (incl. `_dz`/`_dw`), vs_1_1 `mov a0` (float -> int, floor), ps_1_x `t#`
  inputs, vs < 3.0 `oFog`/`oPts` indices, `setp` write masks, the epilogue placement with subroutines.

## D3D9 semantics fixed after V6n's whole-package fuzz (RENDER_VERIFY §7 P2-P5)

All in the `glsldk` profile (other MojoShader profiles unchanged); unit + execution tests in `tests/s6b`.

* **Centroid** (P5): `dcl_centroid t#/v#` (ps_2_x) and `dcl_<usage>_centroid v#` (ps_3_0) read the input once
  with `interpolateAtCentroid()`: the same sampling as a `centroid in` declaration, which Mesa 25's GL linker (the
  V6n parity target) rejects for some explicit-location inputs ("no matching output"). VS outputs need no
  qualifier (GLSL >= 4.30, and deko3d compiles the stages separately).
  On deko3d both forms compile to the same IPA with the centroid bit (Maxwell `0xe053...` vs plain `0xe043...`,
  checked on uam's raw output for one input: same code length and SPH, one more GPR for the
  `interpolateAtCentroid` form). Token-level tests in `tests/s6b/s6b_centroid.c` (handwritten bytecode: ps_3_0
  `dcl_<usage>_centroid` incl. partial mask + `_pp`, ps_2_x `dcl_centroid t#/v#`, an unread centroid `t#`, a
  centroid input read through `v[aL]`, and the VS output locations of the same usages); they fail on the patch
  from before the fix (3/23) and pass after.
* **Relative addressing** (P5): `c[a0 + n]` outside every CTAB array, or without a CTAB, reads the constant UBO by
  register through `vs_dk_relc(i)` (def'd values overlaid, as Nine/wined3d load them into the constant file;
  out of range -> 0). vs_3_0 `v[a0/aL + n]` and ps_3_0 `v[aL + n]` read a copied input array (undeclared ->
  (0,0,0,1) as Nine's `v_consecutive`); vs_3_0 `o[a0/aL + n]` writes `vs_o_arr`, copied to the outputs in the
  epilogue (Nine 20.1 itself ignores the index on outputs: not executable against the oracle). The front end
  (bytecode profile) accepts the same.
* **Colour saturation** (P2): vs < 3.0 `oD0`/`oD1` (and `oFog`) clamped to [0, 1] in the epilogue, written
  components only (the registers are write-only, so this equals clamping each write).
* **Numerics** (P3, P4), as Nine gives them on a driver without `TGSI_MUL_ZERO_WINS` (softpipe):
  `log`/`logp` = max(-FLT_MAX, log2|x|), `rcp` clamped to +-FLT_MAX/2 (rcp(+-0) = +-FLT_MAX/2), `rsq` =
  min(FLT_MAX, 1/sqrt|x|), `nrm` = v * min(FLT_MAX, rsq(dot3(v))) (3-component length also for a .w write mask;
  nrm(0) = 0). Zero is tested explicitly (llvmpipe's inversesqrt(0) is NaN). `min`/`max` are written as TGSI's
  and D3D9's `a < b ? a : b` / `a > b ? a : b` (a NaN operand yields the second), not GLSL min()/max().
* Also: `def` literals printed exactly (`%.9g`; upstream printed 9 fixed decimals, so 1e-30 became 0 and
  1.2345678e-5 kept 5 digits), `loop aL, i#` runs i#.x times with any step (upstream's bound `aL < x + y` broke
  step != 1 and never ended for step 0), vs_1_1 `expp` = TGSI EXP (2^floor, frac, 2^x, 1) as in Nine, vs_3_0
  `FOG` output a vec4 varying at location 18 like vs < 3.0.
* Not done: D3D9 hardware's `0 * anything = 0` (Nine enables it only with `TGSI_MUL_ZERO_WINS`; softpipe, the
  oracle, does not have it, so matching hardware would move away from the oracle; on deko3d it would need
  nouveau's `dnz` multiply flag through uam, not expressible in GLSL). GLSL compilers may reassociate
  (`a*M + a*M` -> `a*(M+M)`), which the bytecode does not: visible only with FLT_MAX-sized values.

## Census of the game's packages (`tests/s6b/census.py`)

`python3 -I tests/s6b/census.py shaderpackage*.sdp [--build DIR] [--work DIR] [--jobs 2]` translates every program
(identical bytecode once), classifies the failures (uam's stderr diagnostics included) and counts `_centroid`
use from the tokens. It prints counts only; `--work` holds game-derived shards and stays out of git.
The player's 16 packages: 15,791 programs, 1,809 unique blobs; 4,614 programs (965 unique; 4,164 ps_2_x,
450 ps_3_0) declare a `_centroid` input, none uses `_centroid` on another instruction.

| cause | before 8975dc6 (programs / unique) | at 8975dc6 and later |
|---|---|---|
| `centroid unsupported in glsl profile` | 4,614 / 965 (ps_2_x 4,164, ps_3_0 450) | 0 |
| relative addressing of an indeterminate array | 16 / 1 (vs_2_0) | 0 |
| **total failing** | **4,630 / 966** | **0 of 15,791** (all 1,809 unique give a valid DKSH) |

The 4,614 of V6n's whole-package fuzz (RENDER_VERIFY P5) were measured on the patch before 8975dc6. A census
run takes about 3 minutes on 2 cores. Translating is not parity: the fuzz has to be re-run on these programs.

## fp16 (Maxwell HADD2/HMUL2/HFMA2) through uam: investigated, not implemented

Requested by S6n for the lit and image-space families. Findings on the pinned uam (`5a5afc2`):

* The path is GLSL IR -> `st_glsl_to_tgsi` -> TGSI -> nv50_ir (`nv50_ir_from_tgsi`) -> GM107 emitter. There is no
  NIR in uam's Mesa snapshot, and no `lower_precision` pass (Mesa 20.1's GLSL IR pass that turns mediump into
  float16): `mediump` is parsed and dropped. `GL_EXT_shader_explicit_arithmetic_types_float16` is not in the
  parser. TGSI has no 16-bit float type (only the `precise` bit).
* nv50_ir has `TYPE_F16` only for conversions and loads/stores; it is a scalar IR with no packed-half2 values.
  The GM107 emitter has no HADD2/HMUL2/HFMA2/HSETP2 encoder (neither has upstream nouveau: NVK's NAK, a separate
  Rust NIR compiler, is the only Mesa backend with packed fp16, and it needs NIR).
* Needed, cheapest route (keep TGSI): (1) a per-instruction half flag from GLSL IR precision through
  `st_glsl_to_tgsi` and ureg; (2) `nv50_ir_from_tgsi` building F16 ops with F2F at the boundaries (inputs, UBO
  loads, outputs and texture results stay F32); (3) a pass pairing independent scalar F16 ops into one half2 op
  (the 2x rate exists only for pairs) with H0/H1 operand selects, plus RA of 16-bit halves of a GPR (only 64-bit
  split/merge exists today); (4) SM53 encoders for HADD2/HMUL2/HFMA2/HSETP2 and packed F2F, scheduling info
  included. A NIR route is strictly larger (import NIR + glsl_to_nir + nv50_ir_from_nir into the snapshot, and
  steps 3-4 remain).
* Validation: there is no Maxwell disassembler (nvdisasm/envydis) or console here, so encodings and results could
  only be checked by bit patterns, not run. Estimate 2-4 weeks of compiler work at high risk, for a gain limited to
  ALU-bound shaders (conversions eat part of it; texture- and bandwidth-bound passes gain nothing).
* Decision: not tractable in this stream now. Cheaper levers for S6n first: half-float render targets and
  `packHalf2x16` storage for bandwidth, and P6n's per-family profile to show whether lit / image-space are ALU
  bound at all. If they are, the TGSI route above is the plan, starting with an HFMA2 encoder test against
  bit patterns confirmed on hardware.

## SM1-3 totality classes (the gate `tests/l6/test_sm_total.py`; behavioural tests `tests/l6/sm_fix/test_ps1x.py`, `test_sm_ops.py`)

Rules, each from the MS D3D9 asm reference page named in the code comment, each with a test that runs the GLSL on llvmpipe and compares
with values computed by hand:

* **ps_1_x sampler dimensionality is data.** ps_1_x has no dcl: the type is that of the texture bound at draw time (and a CTAB is D3DX
  metadata, never trusted). Every ps_1_x stage that samples declares `sampler2D` at binding n, `samplerCube` at 8 + n, `sampler3D` at
  16 + n (the numbering `ffp.frag.glsl` uses) and one helper `ps_dk_smpN(vec3)` that selects by the stage's type word
  `ps_drv_stype[n / 4][n % 4]` (0 2D, 1 CUBE, 2 VOLUME; PS driver block byte 320, `FNVNX_DK_PS_STYPE_OFFSET`); every texture instruction
  samples through it (2D uses xy, the others xyz; a 2D-coordinate instruction passes z = 0). A zeroed block = 2D everywhere. The reflection
  JSON lists such a stage as `"type": "bound"` with its three bindings and `type_word`. Hosts must allocate 352 bytes for the PS block.
* **Relative constant reads need no CTAB**: `c[a0 + n]` / `c[aL + n]` always read the stage's constant file by register through
  `dk_relc` (def'd values overlaid, 0 outside c[0..256) vs / c[0..224) ps).
* **ps_1_x texture instructions**: `texreg2ar/gb/rgb`, `texdp3`, `texdp3tex`, `bem`, `texdepth`, `texm3x2depth` implemented (depth: `z / w`,
  1.0 when `w == 0`, clamped to [0, 1], written to `gl_FragDepth`); `texm3x3spec` (upstream multiplied componentwise instead of the dot
  product and its eye vector read the wrong register), `texbeml` (the luminance offset came from the bump matrix) repaired.
* **Predicates**: `setp` writes p0 by its write mask (upstream reduced every p0 write to `.x`); `if` / `callnz` / `breakp` take one component
  by the replicate swizzle with an optional NOT (`[!]p0.c`).
* **texld family**: `texldd` / `texldl` on cube and volume (`textureGrad`), `texldp` on a cube (xyz / w), ps_3_0 `texld*` coordinate with a source
  modifier, vs_3_0 `dcl_cube` / `dcl_volume` (reserved-bit mask).
* **Front-end refusals of legal programs**: one-source `sgn` in vs_3_0 (the form is read from the instruction's length field; the MS form's two
  scratch temporaries are written by the instruction, so they need no earlier write, and the translator never writes them: their contents
  after the instruction are undefined), `m4x4` / `m3x4` with `.xyz`, `_sat` on `frc` / `sincos` in pixel shaders, `sincos` `.xyz` (z, w are
  undefined by MS: written 0), vs_1_1 `frc` any mask, labels up to l2047 in sm3, forward calls (prototypes for every called label).

## Known gaps

* `setp` with scalar sources into a wider mask.
* Vertex attributes: 16 locations (uam limit), so only POSITION0, BLENDWEIGHT0, BLENDINDICES0, NORMAL0,
  COLOR0-1, TEXCOORD0-7, TANGENT0, BINORMAL0 are mapped; other usages fail translation.
* mojoshader_assembler (tests only) lacks predication and SM1 relative addressing (patched in
  `tests/s6b/mojoshader_assembler.patch`) and still rejects predicate swizzles and `breakp p0.x`.
* Semantics are checked by compiling, by text expectations and (when V6n's `build/v6n/bin` exists) by executing
  `tests/s6b/exec` scenes on Nine vs V6n's GL executor; no deko3d run.
* User clip planes (`gl_ClipDistance` from `SetClipPlane`), sRGB, point sprites and vertex-texture
  sampler numbering (D3DVERTEXTEXTURESAMPLER0 = 257) are front-end work, not in the profile yet.
* uam reports GLSL errors on stderr only (the library returns a generic error).
* uam's thin `libuam.a` is host-only; a console build needs a regular archive (meson option) and
  devkitA64 for the deps.
* No `shaders_nx.pak` packer yet: output is a directory of `<hash>.dksh` + `.json` + `index.tsv`.

### Clip planes and point size (VS driver block, WineD3D 9.0)

Every translated VS epilogue writes `gl_ClipDistance[i] = dot(gl_Position, vs_drv_clip[i])` (i < 8, the raw oPos before the posfix; WineD3D 9.0
glsl_shader.c shader_glsl_generate_vs_epilogue, planes unflipped as shader_glsl_clip_plane_uniform) and `gl_PointSize = min(max(PSIZE or vs_drv_point.x,
vs_drv_point.y), vs_drv_point.z)` (PSIZE when the shader writes oPts / PSIZE, else the POINTSIZE state). Which planes act is the backend's enable mask
(CLIPPING ? CLIPPLANEENABLE : 0, state.c state_clipping), and the host writes a disabled plane as zero (distance 0, never clipped), so the result does not depend on how a GL implementation treats an unenabled distance. The block is 208 bytes.
