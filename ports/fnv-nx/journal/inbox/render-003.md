### J-XXXX | 2026-11-04 | stream: V6n-render | status: wip | parent: J-0064,J-0066

* **found** - The decomp's `eBSSMPassEnum` header is wrong for FNV. The true numbering has **759 ids**. Its anchors
  are the binary's own pass-name function (FUN_00b4f9d0, 742 cases) and code anchors: COUNT 0x2f7, Lighting30
  `pass - 376`, blood splatter 0x2f0+flag = 752/753, the accumulator render groups ending at 753. FIRST_ALPHA_PASS is
  **568**, not 494 (the batch renderer excludes 0x238..0x23e). The header's blocks map to true ids at offsets +0..+86,
  and its "unused" block 673..755 is live passes. RENDER_NATIVE.md §1.3 (756/667/494, family ranges) is therefore
  wrong. The method is in `docs/RENDER_VERIFY.md` §1; the table stays in `build/v6n`.
* **did** - The verifier's tool set (`render/verify/`, tests in `tests/v6n/`, `docs/RENDER_VERIFY.md`), commits
  `58d34f4`..`60bc556`:
  - `enum/pass_space.py`: emitted x configured x render groups. It scans 347 `ShaderPassList::Add` sites and resolves
    346. Output stays in build/ only.
  - `statediff`: S6n's `d3d9_state.c` against Nine 20.1's own `nine_pipe.c` on random state vectors, plus 23 trap
    mutants of the translator.
  - Pixel level:
    - oracle `v6n_nine` (Nine on softpipe; colour/depth/stencil read back);
    - V6n's own GL 4.5 executor `v6n_gl`;
    - `v6n_s6ngl`, S6n's backend driven through NxGpuApi;
    - exact D3D9 top-left coverage reference `raster_ref.py`;
    - 31 pixel trap mutants, 62 format conformance scenes.
  - Thresholds: 2/255 on 8-bit targets, 1e-3 relative on float, stencil exact, depth 8 LSB on D24 and 2 LSB on D16.
    Measured float32 setup differences reach 6/2 LSB, and every depth trap moves depth by >= 2^16 LSB.
  - `fuzz/fuzz.py`: a trap-biased render-state fuzzer with a minimiser; text reproducers in `tests/v6n/repro/`.
  - `shaders/parity.py`: the shader parity harness on S6b's assembler and sdp2dksh. It takes the player's
    `shaderpackage*.sdp`, or `--selftest`.
  - Two defects in S6n filed as failing tests, `border_swizzle` and `y_fill_bias`. S6n fixed both (`17077d6`,
    `521ffaf`) and V6n re-verified them.
* **found** - Stock softpipe is not D3D9-exact as an oracle:
  - Its span setup truncates edge x for D3D9 pixel centres (6,628 wrong px). The oracle links a patched
    `sp_setup.c` ahead of the library; S6c's Nine build is untouched and the stock build is kept as `v6n_nine_stock`.
    S6c's host gate is affected by this; the Switch path (nouveau) is not.
  - It applies blend factors before MIN/MAX. Also patched in the oracle.
  - Worked around in the fuzzer instead: X8 destination alpha with depth, V8U8 -128, unbound fixed-function sampler
    crashes, stencil test without a stencil buffer, texture-alpha blends without a texture argument.
  - Two D3D9 ambiguities are left for the device: RHW vertices vs the viewport, and sRGB reads on R5G6B5-like
    formats.
* **found** - Finding P1 for S6b: its GLSL epilogue uses `gl_FragCoord.z` as table-fog depth, while Nine and S6n use
  z x w. 6 of 7 synthetic shaders in the parity self-test match pixel-exactly; the seventh is this case.
* **found** - On Mesa GL the edge tie rule follows the clip-control origin, so a y-down backend needs LOWER_LEFT with
  y negated in the VS. S6n adopted this.
* **verified** - Re-run by the render machine at the merge with the integration branch (`9359ce3`), after
  `render/verify/build.sh`: `tests/v6n/run.sh` rc 0, "selfcheck PASS, open defects: none". Raster: `v6n_gl` and
  `v6n_s6ngl` 0 wrong px, oracle 3 px. Analytic checks c40_depthbias_d24/d16 and o1_x8_dst_alpha PASS. The 5
  committed reproducers are still unclassified and listed as such. Also `make -C tests/s6n scenes`: 17/17 goldens
  bit-exact.
* **metrics** - true_pass_ids=759 declared=758 named=742 configured=637 emitted=439 in_render_group=753
  reachable=395 first_alpha_pass=568 add_sites=347 add_sites_resolved=346 statediff_vectors=8000000
  statediff_mismatches=0 translator_mutants_caught=23/23 pixel_mutants_caught=31/31 format_scenes=62/62
  fuzz_v6ngl=595/600 fuzz_s6ngl=287/300 (8 fixed-function table fog, out of scope; 5 unclassified)
  random_mutant_kill=26/33 parity_selftest=6/7 defects_filed=2 defects_open=0
* **claims** - S6n claims checked:
  - FAIL: RENDER_NATIVE.md §1.3 pass enum.
  - FAIL, then fixed: C30/C32 border colour, C59 / the nx/state.h viewport coverage claim.
  - PASS: C1-C31, C33, C18/C40 (depth bias, D3D9 units), C32/C34 (formats incl. texel conversion), C24 (fog depth).
* **note** - The render machine could not rebase `claude/port-nsw-render`: the force-push needed after a rebase was
  refused in this session. It merged `claude/port-nsw-un6t9s` into the branch instead (`9359ce3`). The tree is
  identical to the tested rebase. R1-R3 (render-001) still fail after the merge (`tests/s6n/test_requests.py`: 1
  error, 2 failures), although the integration branch now reads `render/native/drop_units.txt` (7d1efb5).
* **next** - Scene-level capture/replay joined with `build/v6n/pass_space.tsv` (RENDER_VERIFY §6). This needs VS/IB
  commands in the scene format and S6n's N1 capture fed to the Nine path. Then a pixel-shader path plus float/depth
  read-back for `v6n_s6ngl` (S6n's API has neither today), `parity.py` on the player's `.sdp` files, classification
  of the 5 reproducers, and on-device conformance (RENDER_VERIFY §8). S6n corrects RENDER_NATIVE.md §1.3 (render
  machine file) next round. Orchestrator: decide whether `render/verify/` and `tests/v6n/` go into MACHINES.md's ownership table for the
  render machine.
