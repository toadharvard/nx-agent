# V6n verification status

V6n is the independent verifier (D14) of the native renderer. Method and results: `docs/RENDER_VERIFY.md`.
Defects: `tests/v6n/DEFECTS.md`. One command: `tests/v6n/run.sh` (needs the host Nine build).

## Done
1. True pass numbering (`render/verify/enum/pass_names.py`): 759 ids, anchored on the binary's pass-name function
   (742 cases) + code anchors (COUNT 0x2f7, Lighting30 `pass - 376`, blood splatter 0x2f0, alpha exclusion
   0x238..0x23e, accumulator render groups). The header enum is wrong for FNV (RENDER_VERIFY.md §1).
2. Pass space (`render/verify/enum/pass_space.py`): 758 declared, 742 named, 637 configured, 439 emitted,
   753 in a render group, **395 reachable** (per class: RENDER_VERIFY.md §2). Tables in build/ only.
3. Oracle `v6n_nine`: Nine + softpipe on S6c's host files, colour/depth/stencil read-back, with two softpipe
   fixes (span rounding for D3D9 centres, MIN/MAX blend factors) proven against the exact coverage reference.
4. Targets: `v6n_gl` (S6n translator + V6n's executor of the nx/state.h contract + FF model + PS path, 32 trap
   mutants) and `v6n_s6ngl` (S6n's own GL backend through NxGpuApi). State-level differential `statediff`
   (+23 mutants).
5. Fuzzer with minimiser (`render/verify/fuzz/fuzz.py`), reproducers in `tests/v6n/repro/`.
6. Shader parity harness (`render/verify/shaders/parity.py`) on S6b's assembler/sdp2dksh; takes the player's
   `shaderpackage*.sdp`; self-test on a synthetic corpus.
7. docs/RENDER_VERIFY.md.

## Results (2026-11-04)
- statediff: 8M random vectors, 0 mismatches outside deviation D1; state mutants 23/23 killed.
- pixel trap mutants 31/31 killed (unmutated 31/31 match); analytic mutants 2/2.
- conformance: 62/62 format scenes match on `v6n_gl`.
- coverage vs exact D3D9 rule (240 triangles): `v6n_gl` 0 px, oracle 3 px, S6n backend 0 px after its fix (204 before).
- fuzzing `v6n_gl`: final generator 600 cases, 595 match; the 5 since classified (1 fixed model gap kept as
  regression test, 4 implementation-defined). `v6n_s6ngl` (after the
  y_fill_bias fix): 300 cases, 287 match; 8 differ only in FF table fog (S6n's epilogue implements Nine's
  programmable-PS fog depth, out of S6n's scope), 5 are the class seen on `v6n_gl` too.
- fuzzing `v6n_s6ngl` on ABI v3: 400 cases, 380 match; 14 FF table fog, 2 `clear_srgb` (rediscovered), 4
  common to both targets, classified (O8 oracle D16 depth, sub-precision edge, alpha-test threshold, MODULATE4X).
- shader parity self-test: 6/7 match; finding P1 for S6b (fog depth).

- whole-package fuzz (sdpfuzz.py, 16 packages, 4 seeds): 15,791 programs, 11,161 translated (P5: 4,614 centroid),
  8,386 pass, 2,775 fail (2,257 D3D9 numerics P2-P4, 128 cube samplers, 154 oracle NaN, 236 open);
  results.json in build/v6n/sdpfuzz (schema RENDER_VERIFY.md §3.7).

## Rebuild
```sh
render/nine/build.sh /home/user/work/v6n/nine 2 host
NINE=/home/user/work/v6n/nine render/verify/build.sh
python3 -I render/verify/enum/pass_names.py ext/fnv_decomp build/v6n
python3 -I render/verify/enum/pass_space.py ext/fnv_decomp build/v6n
tests/v6n/run.sh
```

## S6n claims checked
| claim | verdict |
|---|---|
| RENDER_NATIVE.md §1.3: 756 pass values, 667 live, FIRST_ALPHA_PASS=494, family ranges | **FAIL** (759 ids, FIRST_ALPHA_PASS 568, ranges shifted +2..+86) |
| C1-C31, C33 translator rules (depth, stencil, blend, raster, shader bits, viewport, sampler, defaults) | PASS (statediff vs Nine, 8M vectors; pixel traps; fuzzing) |
| C18/C40 deviation D1 (D3D9 bias units) | PASS (analytic, D24 and D16) |
| C30 + C32 border colour on swizzle-emulated formats | **FAIL** -> defect `border_swizzle`, fixed by S6n 17077d6 (C34/C35), re-verified PASS |
| C32/C34 formats incl. NXFMT_CONVERT | PASS (62/62 conformance scenes; 8-bit RTs also on S6n's backend) |
| nx/state.h "NxViewport +0.5 makes a +0.5-centre rasterizer cover exactly the D3D9 pixels"; C59 | **FAIL** on GL (y bias) -> defect `y_fill_bias`, fixed by S6n 521ffaf, re-verified PASS |
| C24 / d3d9_epilogue.glsl fog depth z/w (= Nine programmable path) | PASS by Nine's source; S6b's translator differs (P1) |
| C35 border colour after the fix | PASS (defect test + 6 border conformance scenes) |
| C50, C53, C56-C58, C64, C67 | reproduced with S6n's own tests (17 goldens bit-exact, 31618 scene checks, 38 N1 checks); C64 also independently by rastercheck (0 px) |
| C51, C52, C55 | PASS independently (V6n trap + conformance scenes on S6n's backend vs Nine) |
| C54 MAXMIPLEVEL / mip selection | PASS independently (8 scenes vs Nine, both targets) |
| C59 rasterization after the fix | PASS (rastercheck 0 px on all grids) |
| C60 shim | PASS in use (S6b GLSL compiled through it, 6/7 parity like V6n's executor) |
| C63 tile family | structure consistent with the decomp (VS plain/TEXSCROLL/VC with TexScroll in c4; PS plain/ALPHAMAP/VC with tintcolor c0); pixel semantics provisional, need TILE1000-1002 |
| C65 radix sort / SortKey | PASS (property test, 4000 random sets) |
| C66 capture/replay | PASS: LP64 host capture and the ILP32 guest capture (Unicorn) both replay with 0 differing read-backs, and 5/5 corruption kinds are flagged on each |
| C68 N1 camera | view/projection agree with the engine (0x00e6c780); **FAIL** -> defects `screen_space_halfpixel`, `camera_port_rounding` |
| C63 / C69 families | **FAIL** against the originals (c69_families.py, FNVNX_SDP): 5 cases match (tile, tile scroll offset, nolight tex / texvc / vc), 7 defects filed (tile_texscroll_scale, tile_alphamap, tile_vc, nolight_falloff_curve, sky_vertex_weights, land_light, depth_alphatest); lit not checked |
| C41, C42 (sRGB views; D3 no native D32) | consistent with Nine's tables (L8/A8L8 sRGB, L16/V8U8 none) and exercised by the conformance + fuzz sRGB restriction A2; D3 is a declared deviation, not falsified |
| C70 ABI v3 read-back formats | PASS in use: `v6n_s6ngl` reads RGBA32F for float targets and Depth32F/Stencil8 for depth-stencil; 104/104 committed scenes and 380/400 fuzz cases match Nine (the rest classified, none a read-back error) |
| C71 NxOp_Clear rectangles | PASS in use (V6n clears viewport ∩ scissor through it, as D3D9 Clear requires; trap `clear_ignores_scissor` matches); its sRGB rule differs from Nine -> defect `clear_srgb` |
| C72 C pixel-shader harness | not used by V6n (V6n drives NxGpuApi through its own adapter); note: uv = x/W at integer centres puts point-sampled texel boundaries on pixel centres when the texture is W wide (same root as `screen_space_halfpixel`), so harness goldens are sensitive to sub-ulp filtering differences |
| C77 pass markers | PASS: property test (3000 random frames, 3 front.cpp mutants caught), capture verbatim, GL replay with injected markers 0 differ, N1 46/46 on GL and the ILP32 guest; ids use the true numbering; pass join in `nxcap.py markers` |
| C61 | reproduced: toolchain built from source with render/native/env.sh, switch/build.sh links guest.nso, host.elf and exefs.nsp; not run on hardware (nor by V6n) |
| C62 | reproduced: the ILP32 guest under Unicorn gives 17/17 scene images equal to the goldens (max diff 0) |
| C68 guest half | `n1/build.sh` + `make n1emu`: 45 checks, 0 failed under Unicorn (the claim says 36; S6n's selftest has grown) |

## Defects filed (tests/v6n/DEFECTS.md)
- `border_swizzle`: fixed by S6n, verified.
- `y_fill_bias`: fixed by S6n, verified.
- `screen_space_halfpixel`, `camera_port_rounding`, `clear_srgb`: fixed by S6n b3f776e (C74-C76), verified by V6n
  (defect tests pass; 105/105 scenes and 2/2 VS scenes match Nine on `v6n_s6ngl`; n1 45 checks).
- Open: tile_texscroll_scale, tile_alphamap, tile_vc, nolight_falloff_curve, sky_vertex_weights, land_light,
  depth_alphatest (C63/C69 families vs the originals; need FNVNX_SDP). Note: b3f776e also edited V6n's `render/verify/target/s6ngl_target.c` (one line: NxClear_Srgb from
  SRGBWRITEENABLE); the change is correct and kept, but that file is V6n's per MACHINES.md.

## Next
- capture/replay (RENDER_VERIFY.md §6) once S6n's N1 capture is wired to the Nine path; shader draws with VS in
  the scene format (`vs`, `vb`, `ib`).
- PS path for `v6n_s6ngl`: done (S6b GLSL through shader_create); float/depth/stencil read-back: done (ABI v3).
  VS + indexed draws on `v6n_s6ngl`: done (2 VS scenes match Nine on both targets).
- Run parity.py on the player's .sdp packages.
- Fuzz reproducers: classified (1 FF-model gap fixed, 4 implementation-defined in tests/v6n/repro/impl_defined).
