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
- fuzzing `v6n_gl`: final generator 600 cases, 595 match, 5 unclassified (committed). `v6n_s6ngl` (after the
  y_fill_bias fix): 300 cases, 287 match; 8 differ only in FF table fog (S6n's epilogue implements Nine's
  programmable-PS fog depth, out of S6n's scope), 5 are the unclassified class seen on `v6n_gl` too.
- shader parity self-test: 6/7 match; finding P1 for S6b (fog depth).

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

## Defects filed (tests/v6n/DEFECTS.md)
- `border_swizzle`: fixed by S6n, verified.
- `y_fill_bias`: fixed by S6n, verified.

## Next
- capture/replay (RENDER_VERIFY.md §6) once S6n's N1 capture is wired to the Nine path; shader draws with VS in
  the scene format (`vs`, `vb`, `ib`).
- PS path for `v6n_s6ngl` (S6n's shader package families), float/depth read-back when S6n adds it.
- Run parity.py on the player's .sdp packages; classify the 5 unclassified reproducers.
