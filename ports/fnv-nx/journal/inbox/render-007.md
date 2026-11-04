### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: render-004,render-006

* **did** - S6n round 4 (up to `d65d05d`), using the owner's own exe and shader packages locally (nothing from them
  is in git):
  - R2 done locally. `render/native/tools/exe_vftables.py <FalloutNV.exe>` reads the image vftables from RTTI, or
    from an anchor slot for the BSShader classes. It found NiDX9Renderer 115 slots, NiDX9RenderState 66, TileShader
    84 and BSShaderNoLighting 79. The output `render/native/vftables.tsv` stays gitignored. Two earlier guesses were
    wrong:
    - slot 97 is the engine's deferred release, not Present 0x00e7ccd0;
    - slots 107/108 are the same function, 0x00e744b0.
  - `overrides.tsv` has 21 active rows, all bound by address, each recording `vft=<class>:<slot>`.
    `test_overrides.py` checks them against the dump when it exists. 4 rows are still N1-pending: the TileShader and
    NoLighting pass setup (not reached on the native path) and MenuBg.
  - N1 geometry: NiTriShape/NiTriStrips go to `Renderer::RenderGeometry` (tile family). The state-cache rows are active
    (render/sampler state set/get, the blend setters, the per-state lock). TileShader/NoLighting construction is a
    no-op.
  - `nx_render`, the S16-compare entry point (`16e23a6`), and its format spec (`abce4af`, RENDER_NATIVE.md §8):
    - usage: `nx_render nxcap|d3d9|scene <input> -o <dir> [--shaders <dir>] [--per-draw] [--until-draw N]
      [--frame F]`;
    - output: `f<frame>.png`, `rb<i>.png`, `f<frame>_d<draw>.png` and `summary.txt`;
    - exit codes: 0 ok, 1 runtime, 2 usage/IO, 3 rendered with skipped draws;
    - NXD9 is the D3D9-level capture format: shaders are embedded GLSL, or looked up by bytecode hash in
      `sdp2dksh --glsl` output via `--shaders`. The reference writer is `tests/s6n/nxd9.py`. Skipped draws (counted,
      exit 3): fixed-function, FVF, pretransformed vertices, streams other than 0, cube textures and vertex textures.
  - Dynamic resolution (`d65d05d`): the `front/dynres.h` controller smooths GPU time:
    - one step down after 3 frames over 95 % of budget;
    - one step up after 30 frames under 80 %;
    - two steps down after one frame over 125 %.

    The `dynres` scene renders at 0.75x, upscales bilinearly, then draws the UI at native resolution. §9 covers the
    frame graph, docked and handheld presets with INI values, and render-thread budgets for 22.2 and 16.7 ms
    (estimates until P6n's measurements).
* **found** - The menu is not fully native yet: tiles draw untextured. Still missing are engine textures
  (NiDX9SourceTextureData), material/alpha properties, skinning and the MenuBg image-space pass.
  `RenderGeometry` reads engine objects through field offsets from the decomp's declarations, like other kept code
  does (D15).
* **found** - S6n changed V6n's adapter `render/verify/target/s6ngl_target.c` once more (the `NxClear_Srgb` line,
  before the explicit ban); V6n is asked to review. P6n defined a second D3D9-level format `.nxd3d`
  (render/native/perf, `43dac22`); it is asked to switch to NXD9 or add an NXD9 reader.
* **verified** - Re-run by the render machine at `d65d05d`:
  - `make -C tests/s6n`: 527 / 41 / 29, 0 failed;
  - n1: 55/0;
  - scenes: 18/18 goldens;
  - nx_render: 42/0;
  - `exe_vftables.py` re-run here from `/home/user/gamedata/exe/FalloutNV.exe` with the documented anchors, then
    `test_requests.py` OK (R1-R3) and `test_overrides.py` OK.

  The ILP32 guest (55/0, 18/18 per S6n) was not re-run here. The full `fnvlink.py resolve` has not run here (no
  objects.tsv).
* **metrics** - vftable_slots NiDX9Renderer=115 NiDX9RenderState=66 TileShader=84 BSShaderNoLighting=79
  active_override_rows=21 pending_override_rows=4 n1_checks=55 scenes=18 nx_render_checks=42
* **next** - The orchestrator runs the full link with `--override render/native/overrides.tsv --override-lib
  <n1>/libnxrender.a` (RENDER_NATIVE.md §2.3). Then engine textures, material/alpha as tint, MenuBg; deko3d GPU
  timestamps into the dynres controller; the depth-readback layout on hardware. V6n's parity on the 16 real packages
  and P6n's measurements are running.
