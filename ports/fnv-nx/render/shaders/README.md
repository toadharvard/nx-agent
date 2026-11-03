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
tests/s6b/run.py                          # corpus + unit tests, prints the matrix
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

## Known gaps

* MojoShader GLSL does not implement `texreg2rgb`, `texdp3tex`, `texdp3`, `texm3x2depth` (ps_1_2/1_3);
  relative addressing of input registers; centroid; `setp` with scalar sources into a wider mask.
  Translation fails with a clear error (ps12_texdp3tex in the corpus checks this).
* ps_1_x sampler types are not declared in the bytecode: `tex`/`texld` assume 2D (cube only where the
  opcode implies it). D3D picks the type from the bound texture; the front end needs a per-shader sampler
  type map (MojoShader's `MOJOSHADER_samplerMap`) or per-type variants.
* Vertex attributes: 16 locations (uam limit), so only POSITION0, BLENDWEIGHT0, BLENDINDICES0, NORMAL0,
  COLOR0-1, TEXCOORD0-7, TANGENT0, BINORMAL0 are mapped; other usages fail translation.
* mojoshader_assembler (tests only) lacks predication and SM1 relative addressing (patched in
  `tests/s6b/mojoshader_assembler.patch`) and still rejects predicate swizzles and `breakp p0.x`.
* Semantics are checked by compiling and by text expectations, not by executing: no GPU/reference run.
* User clip planes (`gl_ClipDistance` from `SetClipPlane`), sRGB, point sprites and vertex-texture
  sampler numbering (D3DVERTEXTEXTURESAMPLER0 = 257) are front-end work, not in the profile yet.
* uam reports GLSL errors on stderr only (the library returns a generic error).
* uam's thin `libuam.a` is host-only; a console build needs a regular archive (meson option) and
  devkitA64 for the deps.
* No `shaders_nx.pak` packer yet: output is a directory of `<hash>.dksh` + `.json` + `index.tsv`.
