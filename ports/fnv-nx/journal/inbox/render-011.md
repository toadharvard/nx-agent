### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: wip | parent: render-008,render-009,render-010

* **decided** - (owner, via the orchestrator)
  - Frame-rate targets: 45 fps stable at 720p handheld is the main target, 60 fps the stretch, below 30 fps a fail.
  - Handheld renders at 720p; dynamic resolution is only a safety net and is off in the gates.
  - The R0 gate reads `summary.targets_budget_fraction`.
  - Optimisation is profile-driven: P6n publishes ranked `hotspots[]`, S6n takes the top rows in order with one
    commit each, and P6n records before/after for each commit in `optimizations[]`.
* **did** - S6n round 6:
  - `2821e0e`: image-space dispatch override, `ImageSpaceManager::RenderEffect` 0x00b97550. The menu background
    renders natively; every other effect is counted and skipped, so the world image-space chain is not drawn yet.
  - Fonts are tile geometry. Their raw-pixel textures come through a lockable native texture (create-surface
    0x00e68010).
  - Tile scroll, scissor rectangle and stencil property are applied per draw. 26 override rows are active.
  - `fd71d3a`: skinning started (bone-palette builder, lit/depth SKIN variants, `skin` scene). Engine skin objects
    are not read into palettes yet, so skinned geometry is still skipped.
  - `584a8d9`: the families follow the originals (TILE1000-1002 scroll/alphamap/VC, nolight smoothstep, sky vertex
    weights, new variants `land_lit` and `depth_at`). Three of V6n's 7 cases pass as filed. The other four need
    inputs the cases do not feed yet (RENDER_NATIVE.md §4.2); V6n is checking that claim independently.
  - `f4c70ed`: UBO ring sized for 8,192 draws per frame in flight. P6n measured 74 -> 0 dropped draws at 3 frames.
  - `db8d394`: both presets at 720p; dynres range 0.75-1.0, off in the gates.
  - Real menu textures (main_title DXT5, main_background DXT1, main_timer DXT3 from the owner's BSA) were checked
    locally through the engine texture path: max 1/255 against an independent DDS decode. Nothing from them is in
    git.
* **did** - P6n hotspots and the optimisation loop:
  - `443daf3`: `hotspots[]` and a top-10 in RENDER_PERF.md §0; the gate uses GPU at 720p with dynres off; an fp16 term
    and render-target bandwidth were added to the GPU model.
  - `2910d8d`: `opt_delta.py` builds each commit in its own worktree and records before/after; baseline `443daf3`.
  - `d89bf60`: `584a8d9` and `f4c70ed` recorded.
* **found** - First profile (synthetic high mix, 3,710 draws; there is no real-game NXD9 yet):

  | config | GPU (model) | 45 fps budget fraction | verdict |
  |---|---|---|---|
  | docked 720p | 12.5 ms | 0.56 | pass |
  | handheld 720p | 23.3 ms | 1.05 | fail |

  - The CPU renderer is 10.0 ms at 1.02 GHz (0.45 of the budget). At 60 fps: docked 0.75, handheld 1.40.
  - With the game's own sdp019 programs the GPU model gives 16.8 / 27.2 ms.
  - Top-10, share of 22.2 ms:
    1. GPU lit 7.27 ms, 32.7 %;
    2. GPU image space + UI 7.05 ms, 31.8 %;
    3. CPU SortPackets 3.74 ms, 16.9 %;
    4. GPU trees 1.99 ms;
    5. CPU nxgpu record 1.63 ms, plus BindUniformBuffers 0.84 ms;
    6. GPU grass 1.54 ms;
    7. CPU Frame::Finish 1.46 ms;
    8. GPU land 1.34 ms;
    9. CPU fixed constant slices 1.08 ms;
    10. CPU sampler re-translation 1.01 ms.
* **did** - Optimisation 1 (`ed96991`): compile-time epilogue permutations `__e0`/`__ea`/`__ef`, and the epilogue UBO is
  bound only when the shader uses it. Maxwell code size per pixel by uam: tex ~114 -> ~18, tile ~114 -> ~12,
  lit_spec ~246 -> ~150. Pixel results are unchanged according to S6n (19/19 goldens, n1 91/0 on GL and the guest,
  V6n selfcheck). P6n's frame-time delta is pending.
* **next** - P6n measures `ed96991`, then the engine's own D3D9 path per draw (compiling the NiDX9RenderState /
  NiD3D* units) and an nvc0 estimate. S6n works hotspot 2: lit static permutations and fp16 with a precision
  analysis. Then the sort, record/UBO binds, Finish, slices and the sampler cache. S6b is fixing the translator
  (centroid, 4,614 programs; relative addressing; P2-P4). V6n is adding cube textures, the 26 open programs, and
  re-verifying 584a8d9/f4c70ed/2821e0e.
