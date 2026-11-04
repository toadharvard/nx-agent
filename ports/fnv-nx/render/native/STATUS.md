# S6n status (native renderer)

Updated 2026-11-04. Claims for the verifier: `CLAIMS.md`. Design and API: `docs/RENDER_NATIVE.md` §7.

## Environment (rebuild from nothing)

```sh
ports/fnv-nx/render/native/env.sh            # apt, sources, toolchain (~45 min at JOBS=2), libnx, dekotools, libdeko3d, uam
source <(ports/fnv-nx/render/native/env.sh --print)   # DEVKITPRO, PATH, UAM
```

Work dir `S6N_WORK` (default `/home/user/work/s6n`, outside git). Result on this machine: fnvnx-a64 (gcc 15.2.0,
binutils 2.45.1, newlib 4.6.0, multilibs lp64/pic/ilp32/ilp32-pic), libnx (ext/libnx b156b477), libdeko3d 373bfd4
(built with dekodef from dekotools c15523c), uam 5a5afc2 (host). Host GL: Mesa 25.2.8 llvmpipe, EGL surfaceless.

## Done

| piece | files | test | result |
|---|---|---|---|
| D3D9 state translator | `front/d3d9_state.{h,c}`, `include/nx/state.h` | `make -C tests/s6n` | 527 checks, 0 failed |
| front + GL shim unit tests | `tests/s6n/test_front.cpp` | `make -C tests/s6n` | 33 checks, 0 failed |
| test harness for V6n (pixel-shader path, float/depth/stencil readback, rect clears; ABI v3) | `front/harness.{h,c}`, `tests/s6n/test_harness.c` | `make -C tests/s6n` | 23 checks, 0 failed |
| front end v2 | `include/nx/front.h`, `front/front.cpp`, `include/nx/gpu_abi.h`, `front/gpu_dispatch.c` | scenes | - |
| GL 4.5 reference backend | `gl/nxgl.{h,c}` | scenes | all scenes pass |
| shader package | `shaders/*.glsl` (N0, N1 tile + nolight, N2 lit + depth + land + sky), `shaders/build.py` (variants; uam -> DKSH + GLSL in one C table) | build | 30 shaders, all compile with uam and on GL |
| N2 families | `shaders/{lit,depth,land,sky}.*.glsl`, blocks in `include/nx/families.h` | scenes lit, depth, land, sky | 118 checks, 0 failed; semantics provisional until the parity check |
| N0 scenes | `n0/scenes.{h,cpp}`, `n0/run_gl.cpp`, `tests/s6n/scenes.py`, `tests/s6n/golden/` | `make -C tests/s6n scenes` | 19 scenes, 31589 conformance checks (30720 of them exact-coverage pixels), 0 failed; 19/19 goldens bit-exact |
| deko3d backend v2 | `host/nxgpu.{h,c}`, `host/nxgpu_host.c` (ext module 10) | compiles + links | not run on hardware |
| N0 for the Switch | `n0/guest_main.cpp`, `switch/build.sh` | builds exefs.nsp + guest.nso | not run on hardware |
| ILP32 guest under emulation | `tests/s6n/emu_n0.py` (S5 emu host + ext module 10 -> GL backend) | `make -C tests/s6n emu GUEST=...` | 31618 checks pass, 17/17 images bit-identical to the goldens |
| capture + host replay | `capture/capture.{h,cpp}` (front side, guest and host), `capture/replay.{h,cpp}`, `capture/replay_gl.cpp` | `make -C tests/s6n capture`; guest capture via `make ... emu` | 19 frames replayed, 19/19 readbacks identical (host and ILP32 guest captures); the console guest writes `sdmc:/fnvnx/n0/out/n0.nxcap` |
| N1 renderer core | `n1/renderer.{h,cpp}` (nx::Renderer frame/target/camera virtuals, nx::StateTracker), `n1/selftest.cpp`, `n1/build.sh` -> `libnxrender.a` (ILP32) | `make -C tests/s6n n1`; `n1/build.sh <o>` + `make -C tests/s6n n1emu N1OUT=<o>` | 90 checks, 0 failed on GL and as the ILP32 guest (incl. a synthetic textured menu through the stubs: D3DX and lockable texture overrides, tile tint, material alpha, alpha property blend/test, stencil, scroll, scissor, the MENUBG image-space dispatch); `n1/engine_stubs.cpp` in libnxrender.a |
| environment | `env.sh` | rebuilt here | ok |

| N1 groundwork / wiring | `N1_PLAN.md`, `overrides.tsv` (29 active address rows (NiDX9RenderState slots 48/49 are texture stage states, sampler states are slots 51-53, checked on the user's exe): renderer, geometry, state cache incl. textures, D3DX and lockable texture creation, the image-space effect dispatch, shader construction; 3 N1-pending: unreached TileShader/NoLighting pass code), `tools/exe_vftables.py` (R2 from the user's exe, output gitignored), `REQUESTS.md` (R1-R3 done) | `python3 -I tests/s6n/test_overrides.py <o>/libnxrender.a`; `python3 -I tests/s6n/test_requests.py` | overrides 4 OK; requests 3 OK (with the local dump) |

Commands:

```sh
make -C ports/fnv-nx/tests/s6n                       # translator unit tests
make -C ports/fnv-nx/tests/s6n scenes UAM=$UAM       # GL backend scenes + goldens (UAM optional: adds DKSH)
source <(ports/fnv-nx/render/native/env.sh --print)
ports/fnv-nx/render/native/switch/build.sh /home/user/work/s6n/sw [program id]   # Switch package (sd/ tree)
# on the console: results in sdmc:/fnvnx/n0/out/; copy it back, then
python3 ports/fnv-nx/tests/s6n/scenes.py --compare <copied out dir> --tol 2
```

## V6n defect reports

| test | status | fix |
|---|---|---|
| `tests/v6n/defects/border_swizzle.scene` (`tests/v6n/run.sh border_swizzle`) | fixed in S6n's code. The V6n harness has to upload NXFMT_CONVERT formats through `nx_d3d9_convert()` (API change in `d3d9_state.h`); its GL target still uploads raw L8/A8/... texels, so the test keeps failing until that adapter follows the new contract. | A8, L8, A8L8, L16, A2R10G10B10 and ATI1 are stored expanded (RGBA8 / RGBA16 / R-B-swapped RGB10A2 / decoded BC4), so no view swizzle duplicates, invents or moves a channel. S6n's own `border` scene checks the border colour on A8, L8, A2R10G10B10, ATI1 (C35). |

| `tests/v6n/defects/y_fill_bias.scene` + `rastercheck.py v6n_s6ngl` | fixed, `tests/v6n/run.sh y_fill_bias` passes here (0 wrong px on all four grids) | the GL backend drops the 1/256 px bias and GL_UPPER_LEFT: it runs GL_LOWER_LEFT with the vertex shader's y negated by the shim (`nxgl_shim_glsl` wraps `main`), front face GL_CCW; llvmpipe's tie rule is then D3D9's top-left rule on every edge. Goldens regenerated (edge pixels of 7 scenes changed; all 780 checks pass before and after). |

## Open problems / traps to settle on hardware

* deko3d front face (`NXGPU_FRONT_FACE = DkFrontFace_CW`), the fill rule on pixel-centre ties (slanted edges too:
  the GL lesson of y_fill_bias; the raster_a/raster_b scenes check it exactly on the console), the A4R4G4B4
  native swizzle, and whether Maxwell applies the TIC (format) swizzle of BGRA8/BGR565/BGR5A1 to the border colour.
  Each is covered by an N0 scene check (cull, textured, format, sampler BORDER on A8R8G8B8): run the Switch package.
* The GL backend ignores the raster flat-shading bit (GLSL needs `flat` qualifiers: families declare them).
* Point size / point sprites are encoded but no N0 scene draws points.
* Scene inputs are computed in float: the guest is built with -ffp-contract=off (GCC fuses a*b+c on AArch64
  otherwise and the sorted scene then differs from the x86 goldens by 1 LSB).

| nx_render (S16-compare) | `capture/nx_render.cpp`, `capture/d3d9_exec.{h,cpp}`, `capture/d3d9cap.h` (NXD9), `capture/png.h`; `tests/s6n/nxd9.py` writer; docs §8 | `make -C tests/s6n nx_render` | 42 checks, 0 failed |
| nx_render input limits (J-0147 V1) | `capture/d3d9_exec.cpp`, `capture/replay.cpp`, `gl/nxgl.c` readback; docs §8.4 | `make -C tests/s6n nx_render_robust` (ASan/UBSan) | bounds 35/35, V1 13/13, 600 mutations 0 crashes |
| dynamic resolution | `front/dynres.h` controller, scene `dynres` (scaled scene + upscale + native UI); docs §9 (frame graph, controller, presets, budgets) | `make -C tests/s6n`, `make -C tests/s6n scenes` | test_dynres OK; 19 scenes match |
| N2 skinning (started) | `include/nx/skin.h`, `lit_skin` / `depth_skin` VS variants, scene `skin`; docs §4.1 | `make -C tests/s6n`, `make -C tests/s6n scenes` | test_skin_palette OK; scene skin 7/7 |
| engine path: routes, dispatch table, translated path, draw counters (RENDER_NATIVE §4.5) | `n1/passes.{h,cpp}` (pass id -> route, `DrawStats`), `n1/translated.{h,cpp}` (translated.nxsp in NX1's NXSP container), `n1/engine_device.cpp` (device hook: program hashes, constant register files), `n1/engine_stubs.cpp` (current RenderPass, `FamilyRows`, constant-map evaluation), `Renderer::RenderTranslated`, `Frame::SetTranslated`, `NxVtxLayout.attr[].location` (nxgl + deko3d), `tools/nxsp_pack.py` (install-time packer, `--check`) | `make -C tests/s6n n1` (+ `FNVNX_TRANSLATED_PACK=<pack>`), `n1emu` | n1 world frame (31 engine draws): skipped or misrendered 29 -> 4 (1 skinned, 3 image-space effects), 25 translated, 2 tile; the player's package 019 packs 815 programs, all with DKSH |
| perf: GPU + CPU front (P6n hotspots, C94-C100) | `n1/renderer.{h,cpp}` (per-stage sampler cache, epilogue lookup table, state id / texture block / epilogue / dyn reuse, prepared light values), `front/front.cpp` (Finish dyn skip), `shaders/d3d9_epilogue.glsl` (branch-free alpha compare), `shaders/lit.frag.glsl`; `perf/gpu_cost.py` costs the bound permutations | `make -C tests/s6n`, `scenes`, `n1`, `capture`, `nx_render`; `perf/opt_delta.py` per commit (`report.json` `optimizations[]`) | mix_hi, AArch64 at IPC 0.6 / 1.02 GHz: main 11.06 -> 7.91 ms, render front 2.78 -> 2.62 ms; GPU model handheld 720p 23.22 -> 10.25 ms (60 fps budget 0.61); mix_hi .nxcap byte-identical before/after the CPU commits |
