### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: render-001

* **did** - N1 core, capture/replay and the first N2 families (commits `afe06b6`..`9fa54d4`):
  - `render/native/n1/` builds `libnxrender.a` and `n1_test.nso` (`n1/build.sh`):
    - `nx::Renderer` implements the frame, target and camera virtuals: Do_BeginFrame, Do_EndFrame (submits with
      present), Do_DisplayFrame, Do_ClearBuffer, Do_SetCameraData, Do_SetScreenSpaceCameraData and
      Do_Begin/EndUsingRenderTargetGroup. `Render()` turns a draw into a packet.
    - `nx::StateTracker` is a record-only NiDX9RenderState. It keeps Gamebryo's save/restore and resolves through
      the translator only when something changed; sampler objects are cached.
  - `render/native/capture/`: `CaptureBackend` wraps any backend, in the guest or on the host, and records
    resources, every stream and the bound memory ranges. `replay_gl` replays a capture and compares readbacks. On the
    console, the N0 guest writes `sdmc:/fnvnx/n0/out/n0.nxcap` for replay on the host.
  - N2 shader families `lit` (SM3 path, up to 8 lights, specular variant), `depth` (ZONLY/DEPTHMAP), `land` (base +
    3 vertex-weighted layers) and `sky`, with scenes and goldens; the package holds 30 shaders.
  - Claims C66-C69 added to `CLAIMS.md`.
* **found** - Two assumptions are still unverified: the Gamebryo screen-space convention ((0,0) at the top left, y
  down) and the semantics of the tile, nolight, lit, land and sky families. Both wait for the engine and the parity
  harness. A partial-rect ClearBuffer currently clears the whole target.
* **verified** - Re-run by the render machine at `9fa54d4`: translator 527/0, front 33/0, scenes 31,618 checks,
  0 failed, 17/17 goldens bit-exact; capture replay 17 frames, 0 readbacks differ; N1 renderer 36/0. The S6n report
  also gives the ILP32 guest under Unicorn: 17/17 goldens, replay 0 differ, N1 36/0 (not re-run here).
* **metrics** - scenes=17 scene_checks=31618 shaders=30 replay_frames=17 replay_diffs=0 n1_checks=36
  claims=C1-C69 console_runs=0
* **next** - Still blocked on R1-R3 (render-001) for the engine stubs and the TileShader `RenderShape` wiring. A
  console run settles the deko3d traps and gives the first real capture. Then the parity harness for the families,
  then N2 skinning, depth pre-pass order and per-pass packet counters.
