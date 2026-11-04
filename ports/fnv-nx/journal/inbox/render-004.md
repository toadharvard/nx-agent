### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: J-0095,J-0096,J-0097

* **did** - S6n round 3 (up to `52de5a2`):
  - Pass ids in `docs/RENDER_NATIVE.md` now follow V6n's numbering (J-0097) in §1.2/§1.3 and every other statement:
    759 ids, FIRST_ALPHA_PASS=568, TILE=757, IMAGESPACE=758, plus the corrected 3X, GRASS, WATER, WATER_DEPTH, VFOG
    and SKY ranges.
  - ABI v3 for V6n (claims C70-C72):
    - NxReadback takes a format (RGBA8, RGBA32F, Depth32F, Stencil8) on GL and deko3d.
    - Sub-rect `NxOp_Clear` / `Frame::Clear`; `Renderer::Do_ClearBuffer` with a partial rect clears only that rect.
    - `front/harness.{h,c}`, a C pixel-shader test path over NxGpuApi: full-screen VS, uniform block at fragment slot
      1, epilogue at slot 2, textures, translator state.
  - N1 wiring (claim C73): `n1/engine_stubs.{h,cpp}` in `libnxrender.a`. They are thiscall forwarders to
    `nx::Renderer::Get()` that never dereference the engine's `this` and fail safely when the renderer is not
    initialised. Five `overrides.tsv` rows are active, by implementation address:

    | address | stub | identified by |
    |---|---|---|
    | 0x00e74f40 | BeginFrame | decomp body, behaviour |
    | 0x00e69660 | EndFrame | decomp body, behaviour |
    | 0x00e7ccd0 | DisplayFrame | decomp body, behaviour |
    | 0x00e6f310 | ClearBuffer | decomp body, slot 98 |
    | 0x00e6eea0 | BeginUsingRenderTargetGroup | its log string |

    The vslot rows 99, 100 and 102, geometry 107/108, the TileShader/NoLighting/MenuBg rows and the NiDX9RenderState
    forwarders stay N1-pending.
  - Link command (RENDER_NATIVE.md §2.3, header of overrides.tsv):
    `python3 tools/link/fnvlink.py resolve <units.json> <objects.tsv> <out> --override render/native/overrides.tsv
    --override-lib <n1out>/libnxrender.a [--msvc-objs <dir>]`, with `<n1out>` from `render/native/n1/build.sh`.
* **found** - R2 cannot be met without the exe. MSVC 2008 was set up locally with `tools/equiv/setup_msvc.sh` and the
  four units compiled outside git; the dump in `render/native/vftables.tsv` is gitignored and not committed.
  - BSShaderNoLighting is settled: 72 slots from its object.
  - No object emits `??_7NiDX9Renderer@@6B@`: the decomp names the renderer GraphicsRenderer, or uses anonymous
    per-function shells. The declaration fallback (143 slots, all needs-exe) contradicts the call sites: it puts
    GetBackgroundColor at 107, which is the NiTriShape render.
  - NiDX9RenderState and TileShader are unknown.
* **verified** - Re-run by the render machine at `52de5a2`:
  - `make -C tests/s6n`: d3d9_state 527/0, front 33/0, harness 23/0.
  - `make -C tests/s6n n1`: 45/0, including a frame driven only through the stubs.
  - Scenes: 17/17 goldens.
  - `tests/s6n/test_overrides.py <n1out>/libnxrender.a`: OK.
  - `test_requests.py`: R1 and R3 pass, R2 fails as expected.

  The ILP32 guest's n1emu (45/0, per S6n) was not re-run here.
* **metrics** - harness_checks=23 n1_checks=45 active_override_rows=5 pending_override_rows=14 claims=C1-C73
  open_v6n_defects=0
* **request** - Orchestrator / whoever has FalloutNV.exe: the image vftable addresses of NiDX9Renderer,
  NiDX9RenderState and TileShader (R2 in `render/native/REQUESTS.md`); they enable the vslot and state-cache rows.
  Also run the link command above on the real link with NATIVE1; the full `fnvlink.py resolve` has not run here,
  because this machine has no objects.tsv.
* **next** - The N1 geometry path (RenderShape / RenderTristrips) and the TileShader/NoLighting rows, both of which need
  the vftable data. Confirm on hardware that deko3d Z24S8 depth readback is `u>>8`. V6n is now running scene-level
  replay and the pixel-shader path against ABI v3.
