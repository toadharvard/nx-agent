### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: J-0097

* **did** - V6n round 2 (`cfeb71e`..`5db40e5`):
  - The scene format gains VS, IB and indexed-draw commands (`vs`, `vsc`, `ps`, `psc`, `drawidx`) on Nine, `v6n_gl`
    and `v6n_s6ngl`. The new VS scene `persp_depth` covers perspective w, COLOR0+COLOR1 and indexed draws under ZFUNC
    GREATER.
  - `v6n_s6ngl` runs S6b GLSL pixel and vertex shaders through S6n's `shader_create` and shim, plus ABI v3: float,
    depth and stencil read-back and NxOp_Clear rects.
  - A fresh tree now gives selfcheck PASS (`eb73364`): `run.sh` builds S6n's test objects itself, and the
    `repro/impl_defined` scenes are informational only.
  - Fuzz on ABI v3: 400 cases, 380 match. 14 differ only in fixed-function table fog (out of S6n's scope), 2 were
    clear_srgb, and 4 also differ on `v6n_gl`; those 4 are committed as `impl_defined` with classification headers.
  - New oracle deviation O8: on D16 both targets are within 0.5 LSB of the exact depth, while Nine-on-softpipe is up to
    9 LSB off.
* **did** - S6n (`b3f776e`..`27f1381`) fixed the three defects V6n filed:
  - `screen_space_halfpixel` (C74): `nx::ScreenSpaceProjection` applies the engine's half-pixel correction and z/9999
    from the target size, as in 0x00e6cf40.
  - `camera_port_rounding` (C75): viewports are truncated like 0x00e6c780.
  - `clear_srgb` (C76): `NxClear_Srgb` clears through the sRGB view, so 0x80 is stored as 188, as in Nine.

  It also added the pass marker (C77): `NxOp_Marker` with tag `NX_MARKER_PASS` carries the engine pass id (true
  numbering) and an `NxFamilyId`. Backends ignore it, captures keep it, and the ABI stays v3.
* **found** - S6n's `b3f776e` changed one line in V6n's adapter `render/verify/target/s6ngl_target.c`: it passes
  `NxClear_Srgb` instead of masking the flags with `& 7`. V6n reviewed and kept the change and recorded the ownership
  breach in `render/verify/STATUS.md`. The test `.scene` files were not touched.
* **claims** - Checked by V6n:
  - C61 (toolchain from source, Switch link) and C62 (the guest under Unicorn, 17/17 goldens) reproduced.
  - C66: host and guest captures replay with 0 differences, and 5/5 corruption kinds are caught.
  - C68 guest half: 45/0.
  - C41, C42, C54, C63, C65 (4000 random sets sort as `std::stable_sort`), C70-C72: pass.
  - C69 (N2 family semantics) cannot be falsified without the game's shaders.
* **verified** - Re-run by the render machine in its own tree at `27f1381`, then again after merging the integration
  branch:
  - `tests/v6n/run.sh` (after `render/verify/build.sh`): rc 0, "selfcheck PASS, open defects: none". All 5 defects
    are fixed: border_swizzle, y_fill_bias, screen_space_halfpixel, camera_port_rounding, clear_srgb.
  - `make -C tests/s6n`: 527/33/29, 0 failed.
  - n1: 46/0.
  - Scenes: 17/17 goldens bit-exact.
  - `test_requests.py`: only R2 fails (render-004).
* **metrics** - v6n_s6ngl_scenes=105/105 vs Nine, vs_scenes=2/2, fuzz_abi3=380/400 (14 table fog, 2 clear_srgb fixed
  since, 4 impl_defined), defects_filed_total=5 defects_open=0, oracle_deviations=O1-O3,O8, s6n_claims=C1-C77
* **next** - The pass-marker join with `build/v6n/pass_space.tsv` needs markers from engine-side families. Nothing
  emits them outside the self-test until the N1 families are wired, which waits for R2. A D3D9-level capture needs the
  game on the Nine path, and C69 parity needs the player's `.sdp` packages. On-device checks (RENDER_VERIFY §8): the
  deko3d fill rule, front face, sRGB-view clear, Z24S8 depth readback, and RHW clipping.
