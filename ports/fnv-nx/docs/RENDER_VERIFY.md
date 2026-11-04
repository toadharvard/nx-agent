# fnv-nx native renderer: independent verification (stream V6n, decision D14)

V6n checks the native deko3d renderer (S6n, `render/native/`, `docs/RENDER_NATIVE.md`) against Direct3D 9 semantics.
The correctness reference is Gallium Nine (Mesa st/nine, the path S6c ships) running on softpipe on the host. V6n
sees S6n's code and claims only. It derives D3D9 semantics from the D3D9 documentation and from Nine's source, and
treats every S6n claim as something to falsify.

Everything here is rebuilt from scripts. Decomp-derived tables (pass names, address lists, enumerations) are written
to `build/` (git-ignored) and never committed; this document carries counts and method only.

## 1. The real pass numbering

### 1.1 The finding

The decomp's enum header `src/bsshader/bsshader_shader_layout.h` (`BSShaderManager::eBSSMPassEnum`, 756 named
values, `COUNT = 756`) is **not** the numbering the FNV binary uses. It is a re-sorted/older list. The binary's own
numbering has **759** ids (0 = disabled, 1..758), and family blocks sit at different offsets.

### 1.2 Method (`render/verify/enum/pass_names.py <decomp> build/v6n`)

1. **Primary anchor: the binary's pass-name function** `FUN_00b4f9d0` (in `bsshadermanager.cpp`). It is a
   `switch (pass) { case N: return "BSSM_<name>"; }` compiled from the real enum. Every case label is a true id
   with its true name: **742 cases, 742 distinct ids, range 1..757**, no id or name twice.
2. **Join by name** with the header and print constant-offset runs. 737 names join; the header blocks map to
   the true blocks with offsets from +0 (ZONLY..DEPTHMAP) through +2 (grass, no-alpha), +58 (ADTS/ADT...),
   +64 (DIFFUSEPT2..TEXTURE), +70 (2x_SPECULAR and the 3X block), +74 (land ... sky), +80 (water), +86 (water depth,
   blood splatter, shadow volume, tile). The header's "unused" block (673..755, 83 values) maps **into the middle
   of live ranges** (AMBIENT* -> 88..96, AMBDIFFTEX* -> 100..146, DIFFUSEDIR/PT -> 300..305, ENVMAP* -> 580..583,
   SELFILLUM* -> 721..727, ...): those are live passes in FNV, not unused ones. 53 offset runs in all.
3. **Holes**: 15 true ids in 1..757 have no case. They are filled by the header neighbours of the same run:
   376+1..6 = the six `3XPLACEHOLDER*` (header 307..312, offset +70), 567 = `DISTANT_TREE_SILHOUETTE` (+74),
   599..603 = the five `VFOG_*` (+80), 705 = `WATER_NO_REFLECTIONS_Vc` (+80; the decomp lost this case, it left a
   `$LN85 aiShader == 705` label stub in the function), 344/345 = two of the header's four `SPECULAR{DIR,PT}_Sb`,
   `SPECULARDIR_{Mb,Md}` (ambiguous, both unemitted). `IMAGESPACE` (header 672, offset +86) is **758**.
4. **Independent anchors** (each one is code that uses an id, not a name):
   * `COUNT = 759`: the pass-flag dump in `bsshaderproperty.cpp` names entries only `if (type < 0x2f7)`;
     ShadowLightShader's per-pass arrays are `[759]` (`DAT_011fdff8`, `DAT_011fcc80`, `DAT_011fc0a0`, `DAT_011fb4c0`,
     `DAT_011fa8e0`).
   * Lighting30 (3X family): `index = pass - 376` with special cases 730/731/736/737/742/743/748/749 -> 104..111,
     and a 112-entry local table. True 3X block = 376..479 (104 ids) + the eight `3XWATER_DEPTH*` ids. Header: 306..409.
   * Blood splatter emits `flag + 0x2f0` = 752/753 = true `BLOOD_SPLATTER_FLARE`/`BLOOD_SPLATTER` (header 666/667).
   * The batch renderer excludes alpha passes `0x238..0x23e` (568..574) from depth sorting: true SKYBASEPRE..
     SKY_CLOUDS_UNDERWATER. Under the header, 568 would be WATER_STENCIL. So `FIRST_ALPHA_PASS` = **568**, not 494.
   * Accumulator render groups (`RenderPassGroup(first, last, ..)` literals in `bsshaderaccumulator.cpp` and
     staging): 1-567, 568-603, 604-647, 648-649, 650-683, 684-718, 719-727, 728-751 and the whole frame 1-753
     (0x2F1). The last group ends exactly at the true BLOOD_SPLATTER (753); under the header 753 is "unused".
   * Emitter-family agreement: every literal pass id passed to `ShaderPassList::Add` (FUN_00ba9ee0) was looked up
     in both numberings. Under the true numbering every emitter emits ids of its own family (the 1x texture-pass
     file emits 330..338 = TEXTURE*, water emits WATER*, sky emits SKY*, grass shadows 644..647 = GRASS_SHADOW*);
     the only literal not in the name function is 567 (the hole above, emitted next to DISTANT_TREE_BASE = 566).
     Under the header numbering the same ids land in foreign families (330 = 3XLIGHTING_HSpc in the 1x texture
     file) and 30 of them in the "unused" block.

### 1.3 Consequences for S6n's claims

`docs/RENDER_NATIVE.md` §1.3 states "756 values, 667 live, FIRST_ALPHA_PASS=494", "3X* passes 306-409",
"WATER* 568-638", "WATER_DEPTH* 642-665", "GRASS_* 20-59, 524-567", "NOLIGHTING* 66-87". All of these come from the
header and are wrong for FNV (only the 0-offset blocks, ZONLY..DEPTHMAP 1..19 and NOLIGHTING 65..87, survive).
Anything in S6n's code that keys on header pass ids is suspect (checked in `render/verify/STATUS.md`).

## 2. The pass space ("all situations")

`render/verify/enum/pass_space.py <decomp> build/v6n` joins, per true pass id, what the engine **emits** with how
it is **configured**, and writes `build/v6n/pass_space.tsv` (columns: id, name, class, in accumulator group, emit
sites and kind, how configured, VS binary + HLSL source + defines, PS binary + source + defines, static render
states, sampler-stage setup, the four SLS per-pass int arrays, skin and vertex-colour flags) plus
`emit_sites.tsv` and `overrides.tsv` (render-time state overrides). Method, per source:

* **emitted**: all 347 eight-argument calls of `ShaderPassList::Add` (FUN_00ba9ee0). Literal ids, or expressions
  resolved locally (C ternaries and `+`, free boolean terms enumerated over {0,1}, local `v = ..; v += ..;`
  substitution): 346 resolved, 1 unresolved (the id is a parameter of a forwarding helper).
* **configured**: ShadowLightShader by its global pass array (`DAT_011fdff8[759]`, setters for VS/PS/render
  states/stages and the per-pass int arrays; VS/PS references resolved to `SLS1/SLS1S/SLS2%03i.vso` and
  `SLS1/SLS2%03i.pso` and joined with the developer tables: 156 VS entries, 203 PS entries); Lighting30 by its
  `pass - 376` index map; Water by its 74-entry member array (base 648, inferred from the accumulator group);
  other classes by the ids their own files dispatch on (`case`, `==`, range checks); classes with one or two passes
  and no dispatch are marked "single-pass (inferred)".
* **in group**: the id lies in one of the accumulator's render groups. **reachable** = emitted and configured
  and in a group.

Counts (2026-11-04, decomp 87eb937):

| class | declared | named | configured | emitted | in group | reachable |
|---|---|---|---|---|---|---|
| ShadowLight | 403 | 401 | 338 | 227 | 403 | 212 |
| Lighting30 | 120 | 114 | 118 | 36 | 120 | 34 |
| TallGrass | 84 | 84 | 44 | 44 | 84 | 20 |
| Water | 73 | 72 | 71 | 71 | 73 | 69 |
| NoLighting | 22 | 22 | 22 | 22 | 22 | 22 |
| Sky | 13 | 13 | 10 | 10 | 13 | 10 |
| SLS water depth / NoLighting water depth | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 | 8 / 8 |
| VolumetricFog | 5 | 0 | 0 | 0 | 5 | 0 |
| DistantLOD | 3 | 3 | 2 | 3 | 3 | 2 |
| ShadowVolume | 3 | 3 | 0 | 0 | 0 | 0 |
| SpeedTree leaf / frond, DistantTree | 2 / 1 / 2 | 2 / 1 / 1 | 2 / 1 / 2 | 2 / 0 / 2 | 2 / 1 / 2 | 2 / 0 / 2 |
| Particle, GeometryDecal, BloodSplatter | 2 / 2 / 2 | 2 / 2 / 2 | 2 / 2 / 2 | 0 / 2 / 2 | 2 / 2 / 2 | 0 / 2 / 2 |
| Bolt, Beam, Precipitation, Tile, ImageSpace | 1 each | 1 / 1 / 1 / 1 / 0 | 1 each | 1 / 0 / 1 / 0 / 0 | 1 / 1 / 1 / 0 / 0 | 1 / 0 / 1 / 0 / 0 |
| **total** | **758** | **742** | **637** | **439** | **753** | **395** |

Reading the numbers: **395 pass ids are statically reachable** (emitted by a pass-list builder, configured by
their class, and inside a render group). The 439 emitted ids are the lower bound of "situations" the native
renderer must reproduce; ids with no static emitter (particles, beam, tile, image space, volumetric fog) are
selected through non-`Add` paths (their own render calls) and are covered by the scene-level capture of §6.
Static VS/PS bindings were found for 69 SLS passes (the remaining SLS passes take their shaders from setup
helpers that the line scanner does not follow; the pass-space table marks them, the capture/replay of §6 closes
the gap at run time).

## 3. The method

### 3.1 Two levels

| level | what is compared | tool |
|---|---|---|
| state | S6n's translator (`render/native/front/d3d9_state.c`) against Nine's own conversion functions (`nine_pipe.c`: `nine_convert_dsa_state`, `_rasterizer_state`, `_blend_state`, `_sampler_state`, compiled from the pinned Mesa 20.1 tree), field by field in Nine's pipe terms | `render/verify/statediff/statediff.c` |
| pixel | a scene rendered by the oracle and by a target, read back (colour, depth, stencil) and compared per pixel | `render/verify/pixdiff.py`, `fuzz/fuzz.py`, `claims.py`, `rastercheck.py`, `shaders/parity.py` |

The state level is cheap (1M cases/s) and exact. The pixel level checks what the state level cannot: the
backend contract of `nx/state.h` (winding, y orientation, tie rules, half-z, swizzles, sRGB views, border colour)
and the fixed-function and shader paths.

### 3.2 Scenes and the adapter

A scene is a text file (format in the header of `render/verify/scene/scene.c`): target size and formats, Clear,
render/sampler/stage states by D3D9 name, textures from deterministic generators in D3D memory layout, viewport,
scissor, projection, pixel and vertex shaders (`ps` / `vs BYTECODE GLSL`, `psc` / `vsc`), and DrawPrimitiveUP
(`draw`) / DrawIndexedPrimitiveUP (`drawidx`, 16-bit indices) batches of XYZRHW|XYZ + DIFFUSE + SPECULAR + TEX1
vertices. Shader scenes keep their sources as SM1-3 assembly (`tests/v6n/vs/*.vsa|psa`) and are assembled and
translated at test time (`render/verify/shaders/vsscenes.py`). The scene runner calls one implementation of
`render/verify/v6n_target.h` (D3D9-level calls) and writes a `.v6nd` read-back. It forks one child per scene, so a
driver crash costs one scene.

| executable | implementation |
|---|---|
| `v6n_nine` (the oracle) | `IDirect3DDevice9` on S6c's host Nine (`render/nine/host/*`, `gate/gate_winsys.c`), softpipe; colour via `GetRenderTargetData` + `LockRect`, depth/stencil from the surface's pipe resource after the CSMT queue drained and the context flushed. Two local softpipe fixes (§5, O3 and O5) are linked as override objects; `v6n_nine_stock` is the unpatched build. |
| `v6n_gl` | S6n's translator + V6n's GL 4.5 executor of the `nx/state.h` contract (llvmpipe, EGL surfaceless) + V6n's fixed-function model of `nine_ff.c`; `V6N_MUTANT=<name>` breaks one rule (trap mutants) |
| `v6n_s6ngl` | S6n's own GL backend `render/native/gl/nxgl.c` through `NxGpuApi` and its command stream, with S6n's translator and S6n's `d3d9_epilogue.glsl`; V6n supplies only the FF model shaders. Pixel shaders: S6b's GLSL through S6n's `shader_create` + `nxgl_shim_glsl`. RGBA8 read-back only until S6n's float/depth read-back lands |

### 3.3 Thresholds (`render/verify/cmp.py`)

* 8-bit colour: |ref - test| <= 2/255 per channel (alpha only when the target has alpha); R5G6B5: 1 step.
* float colour: |ref - test| <= 1e-3 * max(1, |ref|); NaN equals NaN.
* stencil: exact.
* depth: D24 within 8 LSB (4.8e-7), D16 within 2 LSB. Both rasterizers set up the depth plane and interpolate
  in float32 in a different order and convert differently (softpipe truncates, llvmpipe rounds). Measured: constant
  depth 1 LSB, sloped depth up to 6 LSB (D24) / 2 LSB (D16). Every depth trap (half-z, bias scaling, viewport
  MinZ/MaxZ, write masks) moves depth by at least 2^16 LSB, so the tolerance cannot hide one.
* coverage: exact against `raster_ref.py` (rational edge functions, D3D9 top-left rule, integer pixel centres).

### 3.4 Trap mutants

Each mutant breaks one D3D9 -> modern-API rule; the comparison must flag it.

* State level (`statediff/mutants.py`, textual mutations of a copy of S6n's `d3d9_state.c`): BOTHSRCALPHA fix-up,
  alpha-factor table, non-separate alpha factors, separate alpha op, ZFUNC off by one, depth write with EQUAL,
  stencil INCR/INCRSAT swap, two-sided CCW ops, cull winding, flat from Gouraud, ALPHAREF/256, fog on ps 3,
  EXP/EXP2 swap, BLENDFACTOR ARGB order, border ARGB order, MIPFILTER NONE keeping bias, MIRRORONCE as MIRROR,
  cube not clamped, POINT min filter, MAXMIPLEVEL ignored, COLORWRITEENABLE1 from 0, scissor flag, point size
  clamp: **23/23 killed** (300k cases each).
* Pixel level (`V6N_MUTANT`, one trap scene each in `tests/v6n/traps/`): cull winding, depth write ignored, EQUAL as
  LEQUAL, BOTHSRCALPHA, separate alpha, missing half-z clip control, MIPFILTER NONE LOD, MIRRORONCE, border ARGB,
  blend-constant order, sRGB write blending, fog EXP/EXP2, linear fog clamp, ALPHAREF/256, ATI2 swizzle,
  A4R4G4B4 / L8 / A8 / A8L8 swizzles, provoking vertex, two-sided stencil faces, colour-mask bit order, half-pixel
  viewport offset, clear vs scissor, clear vs colour mask, unbound texture colour, RHW z through MinZ/MaxZ, RHW
  vertex fog source, specular alpha, upper-left origin tie rule: **31/31 killed**, and the unmutated target matches
  Nine on all 31 scenes. Two more are checked analytically (§5): D24 bias scale and X8 destination alpha (2/2).

### 3.5 Fuzzer and minimiser (`render/verify/fuzz/fuzz.py`)

Random scenes biased to the traps: blend factor/op tables incl. BOTH*, BLENDFACTOR, separate alpha; depth funcs and
writes incl. EQUAL/NEVER and ZENABLE=2; stencil with two-sided ops and masks; cull; flat shading; alpha test at
refs 0/127/128/255; fog table/vertex modes; sRGB write; specular; TFACTOR; scissor; viewports with MinZ/MaxZ;
stage 0/1 combiners (17 ops, modifiers); samplers (5 address modes, border, filters, MAXMIPLEVEL, sRGB read);
21 texture formats; RHW quads/triangles on a 1/16 px grid with rhw 0.25..2 and clip-space triangles; 8-bit and
float targets; D24S8/D24X8/D16/none. A mismatch is minimised (ddmin over scene lines with the draw groups and
textures kept, then vertex values simplified) and written as a text reproducer. Excluded on purpose, each with
its reason in the fuzzer header: the oracle deviations of §5, anisotropic filtering, trilinear and LOD bias
(implementation LOD precision), lines/points/wireframe, DOTPRODUCT3 as ALPHAOP (undefined), texel-boundary point
sampling and ties on clipped edges (implementation-defined).

Fuzzing found no S6n translator defect. It did find, and triage fixed, gaps in V6n's FF model (DOTPRODUCT3 alpha
replicate, Nine's per-op saturation, RHW vertices ignoring the viewport, D3D9 Clear validation) and the oracle
bugs O4-O7 (§5). Results: §4.

### 3.6 Shader parity (`render/verify/shaders/parity.py`)

Built on S6b's tools (`render/shaders/build.sh --tests`: SM1-3 assembler `s6b_asm`, `sdp2dksh` with MojoShader's
`glsldk` profile and uam). For every pixel shader of the given `shaderpackage*.sdp` (the player's files; none are
in the repo) or of the synthetic corpus `tests/v6n/shaders/corpus.v6ns` (`--selftest`): `sdp2dksh --glsl`
translates it; the bytecode runs on Nine, S6b's GLSL runs on V6n's GL target under the layout contract
(`fnvnx_dk_layout.h`: UBO 0 constants, UBO 2 driver fog/alpha, varyings by usage), with random c0..c7 and a
textured quad, plus a perspective table-fog scene. Inputs the harness does not drive are reported per shader.
Vertex shaders are translated and validated only. Self-test: 7 shaders (ps_1_1, ps_2_0 arithmetic/exp/log/pow/nrm/
texkill, ps_3_0 vPos): **6 match pixel-exactly, 1 mismatch** = finding P1 (§7).

## 4. Results (2026-11-04)

| check | result |
|---|---|
| statediff, 6M + 2M random state vectors | 0 mismatches (outside deviation D1: S6n bias units / Nine-softpipe = 2.000) |
| state trap mutants | 23/23 killed |
| pixel trap mutants (`v6n_gl`) | 31/31 killed; unmutated 31/31 match |
| format conformance, 31 formats x (point, border+bilinear) | 62/62 match on `v6n_gl` (after S6n's border_swizzle fix) |
| S6n's GL backend (`v6n_s6ngl`) on conformance + traps | 77 match; the rest are API gaps (RGBA8-only read-back, no sub-rect Clear) or the y_fill_bias defect |
| coverage vs exact D3D9 rule, 240 random triangles | `v6n_gl` 0 wrong px; oracle 3 px (float ties); stock softpipe 6628 px; S6n backend 204 px before its fix |
| analytic claims | C40 D24 and D16 depth bias pass; O1 X8 destination alpha passes |
| fuzzing, `v6n_gl` | 1,900 cases over 5 seeds during triage; final generator: 600 cases, 595 match, 5 then unclassified, since classified: 1 V6n FF-model gap (missing texcoord set reads (0,0) in Nine; fixed, kept as regression test `tests/v6n/repro/`), 4 implementation-defined (`tests/v6n/repro/impl_defined/`: mixed MIN/MAG filters x2, the same plus point sampling at uv = 0, NOTEQUAL at the clear depth). None is an S6n defect. |
| fuzzing, `v6n_s6ngl` (after S6n's y_fill_bias fix) | 300 cases, 287 match; 8 differ only in fixed-function table fog (S6n's epilogue implements Nine's programmable-PS fog depth z*w, Nine's FF path uses window z: expected, the engine draws with shaders), 5 in the unclassified class above; before the fix the same generator gave 33 mismatches, mostly single edge pixels |
| fuzzing, `v6n_s6ngl` on ABI v3 (float targets, sub-rect Clear, float/depth read-back enabled) | 400 cases, 380 match. 14 FF table fog (scope, above); 2 `clear_srgb` (rediscovered by the fuzzer, defect filed); 4 also differ on `v6n_gl`, classified in `tests/v6n/repro/impl_defined/s6ngl_*`: D16 depth where both targets are within 0.5 LSB of the exact plane and Nine is up to 9 LSB off (oracle deviation O8), a pixel centre 3e-4 px outside an edge after the RHW perspective divide (below sub-pixel precision), ALPHATEST against a LINEAR-filtered alpha at the reference, MODULATE4X amplifying Nine's 8-bit operand quantisation (252 vs 255). None is an S6n defect. |
| fuzzer sensitivity (`--sensitivity`, 120 random cases, all matching unmutated) | 26 of 33 GL-target mutants killed by random cases alone (cull winding 66, upper-left origin 73, half-pixel 44, half-z 14, colour-mask order 12, two-sided stencil 9 ... down to 1). Not killed: d24_bias_scale, x8_dst_alpha, no_texture_black (excluded from fuzzing by design, covered analytically / by trap scenes), ati2_swizzle, a4r4g4b4_order, a8_swizzle (1 format in 21, rare at this sample size), clear_ignores_scissor (mid-scene scissored clears are rare). Every mutant is killed by its trap scene. |
| shader parity self-test | 6/7 match on `v6n_gl` and on `v6n_s6ngl` (S6b GLSL through S6n's shim; ps_3_0 vPos matches under its y negation), 1 finding (P1) |
| VS + PS + indexed draw (`vsscenes.py`, 2 scenes: affine + tint, perspective w + COLOR0/COLOR1 + two indexed draws under ZFUNC GREATER) | both match Nine on `v6n_gl` and on `v6n_s6ngl` (S6b VS GLSL through S6n's shim, layout 2 = S6b attribute locations, D3DCOLOR uploaded as RGBA bytes, NxOp_BindIdx/DrawIndexed; removing the colour swap is caught by both scenes); trap `vs_posfix_halfpixel` (S6b's posfix half-pixel shift on top of the +0.5 viewport) caught, 174 px |
| C54 MAXMIPLEVEL / mip selection (`tests/v6n/claims/c54_*`) | 8/8 scenes match Nine on both targets |
| C65 radix sort (`tests/v6n/claims/c65_sort.cpp`) | 4000 random packet sets identical to std::stable_sort, SortKey packing exact: PASS |
| C66 capture/replay (`render/verify/capture/nxcap.py mutate`) | S6n's `replay_gl` reproduced (17 frames, 0 differ) and flags all 5 corruption kinds (vertex, UBO, state word, texel, recorded read-back) ; the same holds for the ILP32 guest's capture under Unicorn (C62 reproduced: 17/17 scenes equal to the goldens; `tests/v6n/run.sh` with GUEST=...) |
| N1 camera vs the engine (decomp 0x00e6c780, 0x00e6cf40) | projection and view agree; **defects** `screen_space_halfpixel` (half-pixel and z/9999 missing) and `camera_port_rounding` (rounding vs truncation) |

### 4.1 Rasterization finding

On Mesa GL the edge tie rule follows the clip-control origin (`st_atom_rasterizer.c` flips `bottom_edge_rule` for
GL_UPPER_LEFT), so neither `glClipControl(GL_UPPER_LEFT)` nor "lower-left + flip on read-back" gives D3D9's
top-left rule on image-top edges, and a constant sub-pixel bias only fixes horizontal ties. What works is to render
"upside down": GL_LOWER_LEFT origin, y negated in the vertex shader, viewport and scissor y used as is, GL row 0 =
image row 0, GL_CCW front face. V6n's executor does this and is exact on 240 triangles; S6n adopted it (521ffaf).
deko3d's `DkDeviceFlags_OriginUpperLeft` makes the hardware use the D3D-style rule directly; the on-device plan
(§8) re-checks it.

## 5. Oracle deviations and fixes

| id | what | handling |
|---|---|---|
| D1 | DEPTHBIAS: Nine-on-softpipe passes DEPTHBIAS x 2^23 without UNITS_UNSCALED: about half the bias on D24, 128x on D16 (clamps to 1.0) | S6n's D3D9 units are right; checked analytically (`claims.py`), excluded from fuzzing |
| O1 | X8R8G8B8 DESTALPHA: softpipe reads the stored X byte when a depth buffer is bound | analytic check (D3D9 reads 1.0); fuzzer drops DESTALPHA on X8 |
| O2 | V8U8 -128 decodes to -128/127 on softpipe | conformance seed avoids it; V8U8 not fuzzed |
| O3 | softpipe span setup truncates edge x for `half_pixel_center = 0` (D3D9 centres): every slanted edge with fractional x is off by a pixel | **fixed in the oracle**: patched copy of `sp_setup.c` (left/right = ceil), linked ahead of `libsoftpipe.a`; proven by `rastercheck.py` (6628 -> 3 px). Affects S6c's host gate too, not the Switch (nouveau). |
| O4 | Nine FF with an unbound texture read only by alpha arguments, or a stage-1 op without a texture, crashes softpipe (NULL sampler view) | fuzzer always binds the textures it uses; the unbound-texture colour is a trap scene |
| O5 | softpipe applies the blend factors before MIN/MAX | **fixed in the oracle** (`sp_quad_blend.c` override): D3D9, GL and NV hardware ignore them |
| O6 | softpipe runs the stencil test without a stencil buffer (D24X8, D16) | fuzzer enables stencil on D24S8 only (GL and D3D9: test passes) |
| O7 | Nine's FF samples a stage texture only when an argument names TA_TEXTURE: BLENDTEXTUREALPHA without one blends with alpha 0 | fuzzer adds a TA_TEXTURE argument to those stages |
| O8 | D16 depth with RHW vertices: softpipe's interpolated z is up to 9 LSB off the exact screen-linear plane (both GL targets within 0.5 LSB) | reproducer `tests/v6n/repro/impl_defined/s6ngl_6a56a1c012.scene`; depth checks against D16 prefer `raster_ref`-style analytic planes |
| A1 | RHW vertices and the viewport: Nine's window-space path neither transforms nor clips them | V6n's FF model follows the oracle; D3D9 hardware behaviour should be confirmed on device (§8) |
| A2 | SRGBTEXTURE on R5G6B5 and other formats S6n has no sRGB view for: Mesa decodes (B5G6R5_SRGB), S6n does not | caps-dependent in D3D9; fuzzer restricts sRGB read to formats with an sRGB view on both |

## 6. Scene-level capture/replay plan

Static analysis (§2) cannot resolve every pass that is selected through tables or non-`Add` paths. The scene-level
plan closes that gap with S6n's capture/replay (RENDER_NATIVE.md §7, N1):

1. Capture, on the Nine path (D6, the reference that ships), one frame per test location: every D3D9 call with
   its state vector and resources (`render/nine` proxy layer is the hook: one host call per D3D call).
2. Convert each draw to a V6n scene (states, textures as data, vertex/index buffers, VS/PS bytecode + S6b GLSL);
   the scene format grows a `vs`/`vb`/`ib` command for indexed shader draws.
3. Replay on `v6n_nine` and on S6n's native path for the same pass id; compare per draw with the thresholds of
   §3.3 and per frame after all draws. The pass id of each draw comes from the native renderer's family dispatch
   and is joined with `build/v6n/pass_space.tsv`, so coverage is reported per pass id: reached / matched / differs.
4. The pass-space table defines "all situations": a pass id is verified when at least one captured draw of it
   matches; unreached ids are listed per family.

### 6.1 Status (2026-11-04, second session)

Done: the replay side. The scene format carries what a captured D3D9 draw needs beyond fixed function (VS and PS
bytecode with their S6b translation, constants, indexed draws), runs on the oracle and on both targets, and handles
the VS/PS varying mismatch D3D9 tolerates (§7). `render/verify/capture/nxcap.py` reads S6n's `.nxcap` captures
independently (records, frames, draws, states, bound ranges) and mutates them to prove the replay comparison is
sensitive (C66).

Blocked, with what unblocks it:
* **D3D9-level capture of the game on the Nine path.** S6n's `.nxcap` is recorded after the native front end
  (translated state words, GLSL families), so it cannot be fed to Nine. The capture point is the D3D9 boundary: one
  host call per D3D call through S6c's generated dispatcher (`nine_dispatch`), which needs the game running on the
  Nine path (`test/gate.py --game` reaches only the static-init stop today).
* **Joining captured draws with the pass space.** Neither `.nxcap` nor the native command stream carries the
  engine pass id of a draw. Request to S6n: a no-op marker op (e.g. `NxOp_Marker`: tag `PASS`, true pass id, family
  id) emitted per packet by the families, ignored by the backends and kept by `CaptureBackend`; `nxcap.py stat`
  then reports draws per pass id and joins them with `build/v6n/pass_space.tsv`.

## 7. Findings for other streams

| id | stream | finding |
|---|---|---|
| P1 | S6b (shader translation, parity reference) | MojoShader `glsldk` PS epilogue computes table-fog depth as `gl_FragCoord.z`; Nine (`nine_shader.c` shader_add_ps_fog_stage) and S6n's `d3d9_epilogue.glsl` use z * w (z / gl_FragCoord.w). They differ on any perspective geometry: corpus shader `fog_linear`, 247 px, up to 75/255. |
| V1 | S6n / S6b (any GL path pairing translated shaders) | D3D9 lets a pixel shader read a varying its vertex shader does not write (Nine writes oFog = 0 for vs < 3, the rest is undefined); GL refuses to link (`fragment shader input ... has no matching output`). V6n's executor adds the missing VS outputs written as 0. deko3d/uam compile stages separately and are not affected. |
| N1 | S6n (N1 camera) | besides the two defects, the engine's `Do_SetCameraData` has a camera-relative mode (view translation 0 and the camera position kept in a global when two flags are set) and rewrites the projection's third column for an oblique near clip plane under a global flag (reflections); `nx::CameraView` / `CameraProjection` have neither. Not filed as defects: they belong to later N-milestones, listed so they are not lost. |
| O3 | S6c (host gate) | stock softpipe is not a D3D9-exact rasterizer (O3), so gate images with slanted edges are off by up to a pixel per edge |

## 8. On-device conformance plan and how to rerun

On device (deko3d), the same scenes run through S6n's deko3d backend with the N0/N1 guest; read-back is RGBA8 and,
once S6n adds it, depth/stencil. Order: (1) the 31 trap scenes and 62 format scenes against the goldens produced
here by the oracle; (2) `rastercheck` triangles (exact reference, no oracle needed) to confirm the tie rule with
`DkDeviceFlags_OriginUpperLeft`; (3) analytic claims C40 and O1; (4) A1 and A2 against Nine-on-nouveau on the same
device, which settles the two D3D9 ambiguities; (5) capture/replay frames (§6).

Rerun everything (host):

```sh
render/nine/build.sh /home/user/work/v6n/nine 2 host                  # host Nine + softpipe (once, ~455 steps)
NINE=/home/user/work/v6n/nine render/verify/build.sh                    # build/v6n/bin: statediff, v6n_nine(_stock), v6n_gl, v6n_s6ngl
python3 -I render/verify/enum/pass_names.py ext/fnv_decomp build/v6n   # §1
python3 -I render/verify/enum/pass_space.py ext/fnv_decomp build/v6n   # §2
python3 -I render/verify/gen_scenes.py tests/v6n                       # regenerate trap + format scenes
tests/v6n/run.sh                                                       # selfcheck + defect tests
python3 -I render/verify/fuzz/fuzz.py -n 600 -s 5 [--target s6ngl]    # fuzzing, reproducers -> tests/v6n/repro
python3 -I render/verify/fuzz/fuzz.py -n 150 --sensitivity            # random cases killing each mutant
FNVNX_SHADERS_BUILD=... render/shaders/build.sh --tests                # S6b tools
FNVNX_SHADERS_BUILD=... python3 -I render/verify/shaders/parity.py --selftest | PATH/shaderpackage*.sdp
```
