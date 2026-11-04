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
| front end v2 | `include/nx/front.h`, `front/front.cpp`, `include/nx/gpu_abi.h`, `front/gpu_dispatch.c` | scenes | - |
| GL 4.5 reference backend | `gl/nxgl.{h,c}` | scenes | all scenes pass |
| shader package | `shaders/*.glsl` (N0 + tile + nolight families), `shaders/build.py` (variants; uam -> DKSH + GLSL in one C table) | build | 18 shaders, all compile with uam and on GL |
| N0 scenes | `n0/scenes.{h,cpp}`, `n0/run_gl.cpp`, `tests/s6n/scenes.py`, `tests/s6n/golden/` | `make -C tests/s6n scenes` | 11 scenes, 780 conformance checks, 0 failed; 11/11 goldens bit-exact |
| deko3d backend v2 | `host/nxgpu.{h,c}`, `host/nxgpu_host.c` (ext module 10) | compiles + links | not run on hardware |
| N0 for the Switch | `n0/guest_main.cpp`, `switch/build.sh` | builds exefs.nsp + guest.nso | not run on hardware |
| ILP32 guest under emulation | `tests/s6n/emu_n0.py` (S5 emu host + ext module 10 -> GL backend) | `make -C tests/s6n emu GUEST=...` | 780 checks pass, 11/11 images bit-identical to the goldens |
| environment | `env.sh` | rebuilt here | ok |

| N1 groundwork | `N1_PLAN.md` (entry points, slots with evidence, families), `overrides.tsv` (19 rows, all N1-pending), `REQUESTS.md` (R1 --override table + vslot rows, R2 vftable dump, R3 NATIVE1) | `python3 -I tests/s6n/test_requests.py` | 3 tests, failing until R1-R3 are done (by design) |

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

## Open problems / traps to settle on hardware

* deko3d front face (`NXGPU_FRONT_FACE = DkFrontFace_CW`), the fill rule on pixel-centre ties, the A4R4G4B4
  native swizzle, and whether Maxwell applies the TIC (format) swizzle of BGRA8/BGR565/BGR5A1 to the border colour.
  Each is covered by an N0 scene check (cull, textured, format, sampler BORDER on A8R8G8B8): run the Switch package.
* The GL backend ignores the raster flat-shading bit (GLSL needs `flat` qualifiers: families declare them).
* Point size / point sprites are encoded but no N0 scene draws points.
* Scene inputs are computed in float: the guest is built with -ffp-contract=off (GCC fuses a*b+c on AArch64
  otherwise and the sorted scene then differs from the x86 goldens by 1 LSB).
