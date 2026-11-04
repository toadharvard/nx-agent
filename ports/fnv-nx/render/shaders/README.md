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
  bools at byte 256 + 16n); UBO 2 = driver constants; sampler binding = sampler register; vertex
  attribute location by usage/index; VS->PS varying location by usage/index (fog at 18).
* **D3D9 conventions** in the `glsldk` profile: VS epilogue `gl_Position.xy += posfix.xy * w` (half-pixel,
  driver sets `(-1/w, +1/h)`) and `gl_Position.y *= posfix.z` (y flip, -1 with OriginUpperLeft); depth is
  left in D3D's [0,1] (device uses DepthZeroToOne); vPos = `gl_FragCoord.xy - 0.5`; PS epilogue for alpha
  test (`ALPHAFUNC/ALPHAREF` from the driver UBO, all 8 compare functions) and, for ps < 3.0,
  fixed-function fog (vertex factor / exp / exp2 / linear on depth); ps_1_x texbem matrices from the
  driver UBO; `def` constants used with relative addressing become GLSL `const` arrays (uam rejects
  default-block uniforms).

## API (`include/fnvnx_shaders.h`)

`fnvnx_shader_translate(bytecode, n, opts, result)` -> `result.dksh`, `result.reflection_json`,
optional `result.glsl`, `result.stats` (D3D slots, GLSL statements, Maxwell instructions, GPRs, code
bytes: the baseline for any later backend). `fnvnx_shader_ctab` returns the constant table as structs.
`fnvnx_shader_hash` = FNV-1a 64 of the bytecode (cache key). `fnvnx_dksh_validate` checks DKSH headers.
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

## Known gaps

* MojoShader GLSL does not implement `texreg2rgb`, `texdp3tex`, `texdp3`, `texm3x2depth` (ps_1_2/1_3);
  `setp` with scalar sources into a wider mask.
  Translation fails with a clear error (ps12_texdp3tex in the corpus checks this).
* ps_1_x sampler types are not declared in the bytecode: `tex`/`texld` assume 2D (cube only where the
  opcode implies it). D3D picks the type from the bound texture; the front end needs a per-shader sampler
  type map (MojoShader's `MOJOSHADER_samplerMap`) or per-type variants.
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
