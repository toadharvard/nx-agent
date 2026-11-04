### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: J-0015,J-0064,J-0066

* **did** - Round N0 on the render machine (commits `617d4a9`..`0046d1f` on `claude/port-nsw-render`):
  - `render/native/env.sh` rebuilds the environment from source, idempotent: devkitA64 gcc 15.2 with ILP32
    multilibs (~45 min at -j2), libnx, dekotools, libdeko3d `373bfd4`, uam `5a5afc2`.
  - Pure C99 D3D9 state translator `front/d3d9_state.{h,c}` (+ `include/nx/state.h`), `extern "C"`, mirroring
    Nine 20.1: render state 4 x u64, dynamic values, shader bits, 16-byte sampler state, D3DFORMAT -> format +
    swizzle, and `nx_d3d9_convert` (texel expansion for A8/L8/A8L8/L16/A2R10G10B10/ATI1).
  - Front generalised: 12-word DefineState with the full state; ops for scissor, stencil ref, blend constant, depth
    bias, 6-value viewport; shaders from `{stage, dksh, glsl}`; texture handle = image | sampler<<20; readback.
    Guest -> host through `ext_call` module 10 (`nxgpu_dispatch()`); the obsolete `host/nx_host.c` is replaced by
    `host/nxgpu_host.c`.
  - GL 4.5 reference backend `gl/nxgl.c` (EGL surfaceless, llvmpipe): `GL_LOWER_LEFT` + y negated in the VS,
    `GL_ZERO_TO_ONE`, first-vertex provoking, sRGB views + `GL_FRAMEBUFFER_SRGB`; shim rewrites `#version`, maps
    bindings to stage*16+slot, renames UBO blocks per stage.
  - deko3d backend `host/nxgpu.c` rewritten for the new ABI; compiles and links.
  - `shaders/build.py`: one GLSL source -> DKSH + GL text, with variants; 18 shaders (N0, D3D9 epilogue for fog and
    alpha test, `tile` and `nolight` families), all compile with uam.
  - 13 N0 scenes (`n0/`: textured, sorted, instanced, cull, blend, stencil, sampler, format, epilogue, border, tile,
    raster_a, raster_b) with goldens in `tests/s6n/golden/`; `switch/build.sh` builds exefs.nsp + guest.nso (not
    committed) that writes readbacks to `sdmc:/fnvnx/n0/out/`.
  - The ILP32 guest runs under Unicorn with nxgpu calls forwarded to the GL backend: bit-identical to the goldens.
  - N1 groundwork: `N1_PLAN.md`, `overrides.tsv` (19 rows, all `N1-pending`, some `vslot:` rows), `REQUESTS.md`
    R1-R3 with failing tests; claims C1-C65 in `CLAIMS.md` for V6n.
  - Fixed the two defects V6n filed as failing tests: `border_swizzle` (a view swizzle also swizzles the border
    colour) and `y_fill_bias` (the 1/256 px bias fixed only horizontal-edge ties).
* **found** - `0x00bcb180` is the geometry-decal shader, not the tile shader; TILE is true pass id 757 (consistent
  with V6n's finding that the header's pass enum is wrong for FNV).
* **found** - Deviation D1 (documented): depth bias follows D3D9 (DEPTHBIAS x 2^24 for D24), about twice what
  Nine-on-softpipe applies.
* **verified** - The render machine re-ran it at `0046d1f`: `make -C tests/s6n` 527 + 33 checks, 0 failed;
  `make -C tests/s6n scenes` 13/13 goldens bit-exact; `tests/v6n/run.sh defects` y_fill_bias 0 wrong px,
  border_swizzle match. V6n's own verification of the full claim list is still running.
* **metrics** - translator_checks=527 front_checks=33 n0_scenes=13 scene_checks=31500 goldens_exact=13/13
  shaders=18 claims=C1-C65 v6n_defects_filed=2 v6n_defects_fixed=2 console_runs=0
* **request** - Orchestrator, from `render/native/REQUESTS.md` (`python3 -I tests/s6n/test_requests.py` fails until
  done):
  - R1: `fnvlink.py resolve --override render/native/overrides.tsv` with free-text column 3,
    `vslot:<vftable>:<slot>` rows and `--override-lib <archive>`;
  - R2: an image vftable dump (`render/native/vftables.tsv`) for NiDX9Renderer, NiDX9RenderState, TileShader and
    BSShaderNoLighting;
  - R3: prepare rule NATIVE1 that drops whole units listed in `render/native/drop_units.txt`.
* **note** - V6n's tools live in `render/verify/` (where its pre-move WIP was), which MACHINES.md does not list for
  the render machine yet.
* **next** - A console run of the N0 package (`scenes.py --compare <sd out> --tol 2`). It checks the deko3d front
  face, the fill-rule ties, the A4R4G4B4 swizzle and the border swizzle on Maxwell, none of which has run on hardware
  yet. Then N1 once R1/R2 land: `libnxrender.a`, renderer frame/target/camera virtuals, record-only
  NiDX9RenderState, TileShader path to the menu. Then N2 (lit SM3, depth, land, sky).
