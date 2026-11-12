# fnv-nx native renderer: measured per-frame cost (stream P6n)

Measurements that replace the estimates of `docs/RENDER_NATIVE.md` §3.7. Machine-readable result:
`render/native/perf/report.json` (schema §5). One command regenerates everything (about 7 minutes, one core):

```sh
render/native/perf/run.sh [work dir]     # default /home/user/work/p6n; needs env.sh's toolchain + uam, pip unicorn,
                                         # valgrind, V6n's host Nine build (NINE=..., render/nine/build.sh <dir> 2 host)
```

Numbers below are from the run of 2026-11-04 (commit in `report.json` `summary.commit`). All inputs are synthetic
(§2); the real game's D3D9 streams plug into the same tools (§7).

## 0. Hotspots: what eats the 60 fps frame (top 10, kept current)

The gate reads `summary.targets_budget_fraction` (§5). Owner's target: **60 fps, 16.7 ms for the whole frame at 720p
handheld**, dynamic resolution off, native renderer only (the D3D9 / Nine fallback is out of scope and in no budget
row). **The GPU metric is real019** (owner, 2026-11-04): the synthetic pass mix costed with the game's own
shaderpackage019 programs translated by S6b's sdp2dksh (class averages; S5-data's real-game captures join it when
they land). The synthetic native row costs water_refl, LOD and trees with lit.frag, grass and particles with
nolight_tex and the whole image-space chain with one tex.frag; it is a lower bound (`*_native_lower_bound`, rows
marked `stand_in` in `report.json`) and is not optimised against.

Verdict at 60 fps (run of 2026-11-04, translator pinned to the sdp2dksh build of 15:2x, see below): **handheld 720p
1.99 of the budget (fail), GPU-bound: real019 33.16 ms** (target < 16.7); docked 720p 1.22. CPU: main 8.75 ms (9.79
with the kept engine part), render thread 5.89 ms.

| resource | ms | share of 16.7 ms |
|---|---|---|
| GPU real019, handheld 720p (model, efficiency 0.6) | 33.16 | 1.99 |
| GPU native row (lower bound, stand-in shaders) | 10.20 | 0.61 |
| main thread (packet build + store term) / with the kept engine part | 8.75 / 9.79 | 0.52 / 0.59 |
| render thread (Finish + sort + deko3d recording) | 5.89 | 0.35 |

real019 in this round (each a commit, `metric:` lines): carried 27.22 -> **rebuilt 38.41** (the current translator
translates all 1,007 programs; the carried scene predates the SM3 lighting programs) -> 37.22 (early-z for
depth-writing draws without discard; alpha-tested draws lose it) -> 34.61 (LOD costs DISTLOD's pixel program, not
the SM3 lighting class) -> 34.22 (early-z for shadow-map passes too) -> **33.16** (renderer: opaque draws ordered front
to back by 4 depth buckets, state inside a bucket; costs the render thread 0.47 ms: 283 -> 811 state binds). The
synthetic mix now alpha-tests every tree and grass draw and lights lit draws with the sun + 4 point lights.

Top 10 (every row: thread, share of 16.7 ms; GPU on real019):

| # | where | what | thread | ms | share | lever |
|---|---|---|---|---|---|---|
| 1 | GPU pass | image space + UI (3.5 full-screen equivalents, IS pso 172 instr avg) | GPU | 9.42 | 56 % | translator: compile-time epilogue variants -> 4.33 ms (below) |
| 2 | GPU pass | lit (1,600 draws; SLS vso 247, SLS pso 247) | GPU | 6.59 | 39 % | 4.02 ms is vertex; translator variants -> 5.98 |
| 3 | GPU pass | grass (all alpha-tested: no early-z; GRASS vso 377) | GPU | 4.93 | 30 % | 3.83 ms vertex (2,000 verts x 300 draws); variants -> 4.36 |
| 4 | GPU pass | shadow maps (SLS programs) | GPU | 3.02 | 18 % | variants -> 2.21 |
| 5 | GPU pass | actors (SKIN vso 506) | GPU | 2.86 | 17 % | 2.57 ms vertex |
| 6 | GPU pass | land | GPU | 2.46 | 15 % | variants -> 1.79 |
| 7 | CPU function | `record` (nxgpu.c) | render | 1.65 | 10 % | more state binds since the depth buckets |
| 8 | CPU function | `nx::Renderer::Render` | main | 1.45 | 9 % | |
| 9 | CPU function | `nx::Frame::Finish` | render | 1.27 | 8 % | |
| 10 | GPU pass | trees (alpha-tested) | GPU | 1.27 | 8 % | variants -> 0.90 |

**Translator levers (S6b's area, measured here, not committed: the inputs are the game's programs).**
1. *Compile-time epilogue variants.* Every translated pixel program carries the runtime D3D9 epilogue: an
   alpha-test `switch` that uam's GLSL front end keeps as a seven-test loop even for a constant function, and the
   four-way fog chain; ISCOPY is 2 D3D instructions and 106 Maxwell instructions. Building each program also as
   `__e0` / `__ea` / `__ef` (as render/native does for its own package, RENDER_NATIVE.md §4.3) plus a branch-free
   compare in the runtime one, and binding by the draw's alpha test / fog state: class averages IS pso 172 -> 79
   (e0), SLS pso 247 -> 153, STLEAF 118 -> 32, STB 135 -> 42; **real019 handheld 720p 33.16 -> 24.09 ms**. The
   renderer side (choosing the variant from the resolved shader bits) is what `Renderer::Render` already does for
   package shaders; the Translated path being wired into n1 would need the same lookup.
2. *Relative constant reads.* The sdp2dksh build of 15:47 routes every relative constant read of a vertex program
   through `vs_dk_relc` (a def'd-literal check and a bound check per access): 195 programs change, SLS vso 247 ->
   474, SKIN vso 506 -> 1,017; **real019 33.16 -> 42.08 ms** with no renderer change. Uploading the def'd literals
   into the constant buffer when the program is bound (as D3D9 drivers do) would keep the plain indexed read.
   `run.sh` now records the translator's sha256 (`gpu.assumptions.real019_translator`); `REAL019_DIR` pins a build. (ASM-025)

After lever 1 the remaining real019 cost is mostly vertex work (lit 4.0, grass 3.8, actors 2.6 ms: the model's
vertices per draw x the translated programs' instructions, no post-transform cache).

S6n takes the rows in order, one commit each; every commit is re-profiled and recorded in `report.json`
`optimizations[]` (§5) by

```sh
python3 -I render/native/perf/opt_delta.py <commit> [--before <commit>] [--name <text>]   # ~1 min per new commit
python3 -I render/native/perf/report.py /home/user/work/p6n render/native/perf/report.json --reuse
```

`opt_delta.py` checks each commit out as a detached worktree (`<work>/opt/trees/`), puts the current perf tools over
its `render/native/perf`, and measures mix_hi: AArch64 instructions of main / render front / deko3d recording, the
GPU model at docked and handheld 720p (the commit's shaders), the top functions. Baseline: 443daf3 (main 5.64 M,
render 3.38 M + 2.04 M instructions; GPU 12.44 / 23.22 ms docked / handheld at efficiency 0.6).

## 1. What is counted

```
 engine (NOT counted here: update, cull, accumulation; its own D3D9 draw path is measured separately, §13)
   |  D3D9-level calls: SetRenderState / SetSamplerState / SetTexture / shaders / constants / DrawIndexedPrimitive
   v
 native path (ILP32 guest, AArch64, counted under Unicorn)        D3D9 -> Nine path (x86-64, counted by callgrind)
   nx::StateTracker (every set, redundant ones too)                 NiDX9RenderState-style redundancy filter (modelled)
   nx::Renderer::Render: Resolve (d3d9_state translator),           st/nine 20.1 (+ cso_cache, u_upload_mgr, util,
     StateCache, sampler translation + handles, packet,               libc it calls), CSMT off
     UBO slice + epilogue + dyn records                             |
   -- "main" -----------------------------------------------        | (excluded: every call into softpipe, i.e. the
   Frame::Finish: radix sort, encode with bind elimination          |  pipe driver: state objects, draw module,
   -- "render" (front) ----------------------------------------     |  rasterizer, flush; and the present backend)
 deko3d backend (LP64, AArch64, counted under Unicorn)              |
   nxgpu.c record() + real libdeko3d: state-block replay,           nouveau/nvc0 validation + push buffer: NOT
   dkCmdBuf* recording, FinishList, QueueSubmitCommands             measured (no GPU here)
   -- "render" (backend) --------------------------------------
```

* **Main thread** = everything up to the packet (StateTracker + `Renderer::Render`); **render thread** = `Frame::Finish`
  + the deko3d recording (RENDER_NATIVE.md §3.6). Both are what N1/N2 code does today (`render/native/n1`, `front`).
* Not counted here: engine code (its D3D9 draw path, NiDX9Renderer to the device calls, is counted in §13); the families' own work to fill per-draw constants (the stream hands ready
  64-256 B blocks); skinning palettes; dynamic vertex data (particles, UI text); kernel time of real nvdrv ioctls
  (the deko3d harness stubs libnx, `perf/dk/stubs.c`).

## 2. Input: the synthetic §3.7 mix

`render/native/perf/gen_stream.py` writes D3D9-level streams (`.nxd3d`, `perf/nxd3d.h`); every parameter is copied into
`report.json` (`assumptions.mix`). Per draw the engine issues `rs` render-state sets (most redundant), and per bound (ASM-025)
stage SetTexture + 5 sampler states (ADDRESSU/V, MAG/MIN/MIP), the shader pair, stream/indices, VS and PS constants.

| family | draws lo / hi | tex stages | RS sets/draw | P(material change) | P(texture change) | P(shader change) | constants VS+PS (B) | targets |
|---|---|---|---|---|---|---|---|---|
| shadow | 100 / 300 | 1 (20 % of draws) | 6 | 0.05 | 0.20 | 0.10 | 64 + 0 | 3 x 1024² depth |
| water reflection | 100 / 300 | 2 | 12 | 0.15 | 0.80 | 0.20 | 128 + 32 | 640x360 |
| lit (static objects, 600-1000 x 1.2-1.6 passes) | 720 / 1600 | 3 | 14 | 0.20 | 0.85 | 0.25 | 192 + 64 | main |
| land | 100 / 150 | 6 | 10 | 0.05 | 1.00 | 0.30 | 96 + 32 | main |
| LOD | 100 / 200 | 2 | 8 | 0.05 | 0.50 | 0.10 | 80 + 16 | main |
| trees | 150 / 450 | 2 | 10 | 0.33 | 0.40 | 0.33 | 112 + 16 | main |
| grass | 100 / 300 | 1 | 8 | 0.02 | 0.10 | 0.05 | 48 + 16 | main |
| actors (skinned; palette not modelled) | 50 / 200 | 3 | 14 | 0.20 | 0.70 | 0.30 | 192 + 64 | main |
| particles (alpha) | 50 / 150 | 1 | 12 | 0.50 | 0.40 | 0.30 | 64 + 16 | main |
| image space + UI | 20 / 60 | 2 | 10 | 0.60 | 1.00 | 0.70 | 16 + 64 | 4 chain targets + main, switch every 6 draws |
| **frame** | **1490 / 3710** | | | | | | | 15 graph passes (hi) |

Streams: `mix_lo`, `mix_hi` (whole frames), `wo_<family>` (mix_hi without one family: the family's marginal cost
inside a full frame = mix_hi - wo), `fam_<family>` (one family alone), `empty`. Each file holds 3 frames; the last
one (same draws as frame 0, caches warm) is measured.

## 3. Method

* **AArch64 ILP32 guest** (`perf/native_drv.cpp` + `libnxrender.a` from `n1/build.sh`, -O2 -mtune=cortex-a57, the
  real guest runtime): run under S5's Unicorn host (`tests/s5/emu_guest.py`) by `perf/emu_count.py`, which adds
  size/4 per basic block (UC_HOOK_BLOCK) and reads the count at marks (ext_call module 0x50). The stream decoder's
  own cost is measured by a dry run (decode only) and subtracted. Backend = a null `nx::Backend` (ids only),
  frames in flight 2 (see finding F1).
* **AArch64 LP64 deko3d backend** (`perf/dk/`): `harness.c` `#include`s `render/native/host/nxgpu.c` unchanged, links the
  real `libdeko3d.a` and libnx stand-ins (`stubs.c`: nvdrv/nwindow/kernel calls return success), runs
  `nxgpu_init` and feeds `record()` the command streams of `.nxcap` captures of the same synthetic frames
  (`native_drv_cap`). Counted per frame: `dkCmdBufClear` + `AddMemory` + `record()` (decode, state-block replay,
  every dkCmdBuf* call, FinishList, QueueSubmitCommands, swapchain acquire/present bookkeeping). Shaders: 10 real
  DKSH from S6n's package cycled over the stream's shader ids.
* **x86-64** (same `.nxd3d`, callgrind `Ir`, `CALLGRIND_DUMP_STATS_AT` at the marks): `native_drv_host` (the same front,
  -O2) and `nine_drv` (Gallium Nine 20.1 + softpipe from V6n's host build, release -O3). `perf/cg_split.py` removes
  the driver: the inclusive cost of every call edge from a measured function into softpipe_*/sp_*/gate_* (and,
  to a fixed point, every function only the driver calls). CSMT is off: softpipe lacks
  PIPE_CAP_BUFFER_MAP_PERSISTENT_COHERENT, so Nine disables its worker (on the Switch nouveau would run it: the queue
  record/replay adds to, does not remove from, the Nine count). Nine's dry run decodes only, so the engine-side
  redundancy filter (what NiDX9RenderState does before calling the device) is counted on the Nine side, as
  StateTracker's filtering is on the native side.
* **Time** = instructions / (IPC x clock). Clocks: 1.02 GHz (retail CPU clock) and 1.785 GHz (boost profile).
  **IPC 0.6-1.0**: the A57 is out of order but 3-wide with a 15-stage pipe; this code is branchy (per-state switches,
  hash probes, memcmp) with dependent loads, and small (L1-resident tables except below), so 0.6 is the conservative
  bound and 1.0 the optimistic one; published A57 figures for integer/branchy code sit in that band. The gate uses
  0.6 at 1.02 GHz. **Caches are not modelled** by the counter; one term is added to the main thread's high estimate:
  the per-draw streaming writes that miss L1/L2 (512 B UBO slice + 256 B epilogue + 64 B packet + ~32 B words =
  864 B/draw) at an effective 4 GB/s store bandwidth (0.80 ms for mix_hi). The 256 KiB radix count table (F2) is
  in L2 (2 MiB) but not L1 (32 KiB): its cost is likely underestimated by the IPC range.
* **Same-ISA ratio**: Nine / native on x86-64, same stream, same exclusion rule. The AArch64 Nine figure in the summary
  is an *estimate*: x86-64 Nine x (native AArch64 / native x86-64 = 0.896).

## 4. Results (CPU)

### 4.1 Instructions per draw by family (AArch64, marginal inside mix_hi)

| family | main | render: front | render: deko3d | **total** | x86-64 native | x86-64 Nine | Nine / native |
|---|---|---|---|---|---|---|---|
| shadow | 758 | 359 | 524 | **1,642** | 1,192 | 1,392 | 1.17 |
| water reflection | 1,439 | 410 | 577 | **2,426** | 1,922 | 2,419 | 1.26 |
| lit | 1,741 | 412 | 562 | **2,715** | 2,240 | 3,000 | 1.34 |
| land | 2,339 | 419 | 574 | **3,333** | 2,887 | 4,053 | 1.40 |
| LOD | 1,319 | 391 | 527 | **2,236** | 1,799 | 1,924 | 1.07 |
| trees | 1,412 | 388 | 508 | **2,308** | 1,903 | 2,132 | 1.12 |
| grass | 1,054 | 356 | 463 | **1,873** | 1,494 | 1,052 | 0.70 |
| actors | 1,734 | 409 | 573 | **2,716** | 2,230 | 2,904 | 1.30 |
| particles | 1,214 | 399 | 558 | **2,171** | 1,700 | 2,097 | 1.23 |
| image space + UI | 1,566 | 449 | 968 | **2,983** | 2,129 | 3,330 | 1.56 |

x86-64 columns are front only (no backend) on both sides. `report.json` `families.<f>.isolated` has the same family
measured alone (then the per-frame fixed cost F2 lands on its few draws).

### 4.2 Per frame

| | draws | main (instr) | render: front | render: deko3d | main ms @1.02 | render ms @1.02 | main ms @1.785 | render ms @1.785 |
|---|---|---|---|---|---|---|---|---|
| mix_lo | 1,490 | 2.02 M | 0.65 M | 0.74 M | 1.98 - 3.62 | 1.36 - 2.27 | 1.13 - 2.21 | 0.78 - 1.30 |
| mix_hi | 3,710 | 4.84 M | 1.60 M | 1.73 M | 4.75 - 8.71 | 3.27 - 5.45 | 2.71 - 5.32 | 1.87 - 3.11 |

(Before S6n's perf commits, 443daf3: mix_hi 5.64 M / 3.38 M / 2.04 M, main 5.53 - 10.02 ms, render 5.32 - 8.86 ms.)
Ranges: IPC 1.0 - 0.6; the main high value includes the store term. Fixed per frame: ~1.9 M instructions in
`Frame::Finish` (the radix sort, F2), visible as mix_lo's render front (2.50 M for 1,490 draws vs ~0.4 k/draw marginal).

CPU per draw over a whole frame (fixed costs included): native **1.67 - 6.25 µs** (summary `per_draw_us.native`;
mix_hi at 1.02 GHz IPC 0.6: 4.9 µs). The §3.7 estimate for the native path (~1-2 µs/draw on each thread at 1.02 GHz)
holds only at IPC 1.0 and the high draw count.

### 4.3 Frame-rate targets (owner, updated: 60 fps = 16.7 ms whole frame at 720p handheld is the target; 720p, dynres off)

Gate field (`targets_budget_fraction`), mix_hi, conservative values (CPU IPC 0.6 at 1.02 GHz; GPU efficiency 0.6):

| target | CPU renderer @1.02 | CPU main + kept engine @1.02 | GPU docked 720p | GPU handheld 720p | **frame docked 720p** | **frame handheld 720p** |
|---|---|---|---|---|---|---|
| 30 fps (33.3 ms) | 0.26 | 0.29 | 0.17 | 0.31 | **0.26 pass** | **0.31 pass** |
| 45 fps (22.2 ms) | 0.39 | 0.44 | 0.25 | 0.46 | **0.39 pass** | **0.46 pass** |
| 60 fps (16.7 ms) | 0.52 | 0.58 | 0.33 | 0.61 | **0.52 pass** | **0.61 pass** |

frame = max(CPU worst thread at 1.02 GHz, GPU) / budget. The CPU-only verdicts below are kept for reference.

Verdict per target and clock from the measured renderer CPU time: max(main hi, render hi) of mix_hi, i.e. the
**low IPC bound (0.6) decides**; both threads run in parallel (§3.6), so the larger one is the bound.

| target | budget | 1.02 GHz | 1.785 GHz |
|---|---|---|---|
| 30 fps | 33.3 ms | pass (8.7 ms = 26 %) | pass (5.3 ms = 16 %) |
| 45 fps | 22.2 ms | pass (39 %) | pass (24 %) |
| 60 fps | 16.7 ms | pass (52 %) | pass (32 %) |

This covers the renderer only. Against RENDER_NATIVE.md §9.4's split of the frame, the measured renderer is still
**over its own budget lines** at 1.02 GHz: packet build 4.7 - 8.7 ms vs 3.0 ms (60 fps); render thread 3.3 - 5.5 ms
vs 3.5 ms (it was 5.5 - 10.0 / 5.3 - 8.9 ms before S6n's perf commits). At 1.785 GHz the low ends fit (main 3.2 ms, render 3.0 ms) and the high ends do not. The
main thread's remaining slack for game update, AI and cull at 45 fps / 1.02 GHz is 12 - 17 ms, not ~16 ms. F2-F4 (§10)
would recover about 2.5-3 M instructions per mix_hi frame. GPU time is a separate estimate (§9).

## 5. `report.json` schema (v1)

Top-level `summary` (what the R0 gate reads):

```
"summary": {
  "commit": "<git sha of the measured tree>", "render_native_tree": "<tree hash of render/native: survives rebases>",
  "worktree_dirty": false,
  "per_draw_us": {"native": {"lo", "hi", "gate"}, "nine": {"lo", "hi", "gate"}},   // µs per draw, whole frame
  "frame_ms":    {"main": {"lo", "hi", "gate"}, "render": {"lo", "hi", "gate"}},   // ms per frame
  "assumptions": {"ipc": {"lo": 0.6, "hi": 1.0}, "clock_ghz": [1.02, 1.785], "gate_value": "..."}, (ASM-025)
  "targets_budget_fraction": {"30": {"1.02", "1.785", "gpu_docked_720p", "gpu_handheld_720p",
                                      "frame_docked_720p", "frame_handheld_720p"}, "45": {...}, "60": {...}},
                                    // THE GATE FIELD: fraction of the budget, <= 1.0 passes (rule in
                                    // targets_budget_fraction_rule); frame_<cfg> = max(CPU @1.02, GPU) / budget
  "targets": {"30": {"1.02": "pass|fail", "1.785", "frame_docked_720p", "frame_handheld_720p"}, "45": {...}, "60": {...}},
  "targets_rule", "per_draw_us_rule", "frame_ms_rule": "<text>", "render_backend_counted": true
}
```

Convention: a range is `{"lo", "hi"}`, lo = optimistic (IPC 1.0, 1.785 GHz, mix_lo where it is lower), hi =
conservative (IPC 0.6, 1.02 GHz, mix_hi, store term). **The gate uses `gate` (= hi).** `per_draw_us.nine` excludes the
nouveau driver (see §6.1) and is scaled from x86-64; compare it with `native` only with that in mind.

Detailed sections:

| key | content |
|---|---|
| `engine_d3d9_path` | §13: `source` (MEASURED, replaces the 1-3 k / 10-20 µs assumption in `replaced_assumption`), `per_draw_instr` {lo, exp, hi, path_min, path_max}, `per_draw_us`, `parts_per_draw_exp` (D3D9 path vs kept engine code), `by_function_per_draw_exp`, `d3d_calls_per_draw_exp`, `frame.<mix>` {instr, ms_gate}, `coverage`, `limits` (ASM-025) |
| `summary.frame_budget` | owner's target: 60 fps, 16.7 ms, handheld_720p; `targets_budget_fraction.<t>` adds `1.02_with_engine_kept` and `d3d9_path_main_1.02` |
| `hotspots` | `top10` (ranked rows {kind, name, ms, share_of_16_7ms, ...}), `engine_d3d9_path_by_function` (§13), `cpu_by_function` (front + deko3d backend symbols, mix_hi frame), `cpu_by_family_pass` (leave-one-out marginals incl. backend), `gpu_by_pass/<scene>/<cfg>`, `gpu_by_program/<scene>/<cfg>`, `gpu_frame_ms/<scene>/<cfg>` for scenes mix_hi (native shaders) and real019_mix_hi, cfg docked_720p / handheld_720p at efficiency 0.6; `input` |
| `optimizations[]` | one entry per S6n optimisation commit: {commit, name, cpu_instr_delta_main, cpu_instr_delta_render, gpu_ms_docked_720p, gpu_ms_handheld_720p, before, after} (before/after: the summary numbers of the two measured trees); source `render/native/perf/optimizations.json` |
| `families.<f>` | `draws {lo, hi}`; `instr_per_draw {main, render, total (front), backend_record, total_with_backend}` (AArch64, marginal in mix_hi); `x64 {native_per_draw, nine_per_draw, nine_over_native}`; `isolated` (family alone); `params` (the mix row) |
| `frame.mix_lo` / `frame.mix_hi` | `draws`; `instr {main, render_front, render_backend, render}`; `ms.<clock> {main, render} {lo, hi}`; `memory_term_ms`; `x64 {native, nine, ratio_nine_over_native, nine_driver_excluded}` |
| `fixed_per_frame_instr` | the empty frame (no packets) |
| `profile_mix_hi_a64` | instructions per function in mix_hi's measured frame (guest; `main` = the stream decoder) |
| `ring_overflow_at_3_frames_in_flight` | native_drv's result line at NXGPU_FRAMES = 3 (F1) |
| `nine` | method, `ratio_same_isa` per mix, `per_family_ratio`, `a64_over_x64_native`, `nine_a64_estimate_instr`, top functions, driver entries excluded |
| `gpu` | `model` (text), `assumptions` (GPU, clocks, per-family coverage/overdraw/vertices), `shaders.<name>` {gprs, code_bytes, maxwell_static, maxwell_dyn, tgsi_static, tgsi_dyn, tex, light_loops, occupancy_warps_per_sm, scratch_per_warp}, `scenes.<mix>[]` per config and efficiency, `summary_ms`, `targets` (ASM-025) |
| `passes` | per-pass counters (§8) of mix_lo, mix_hi and S6n's N0 capture |
| `backend_record` | the deko3d counter's raw output per capture |
| `assumptions` | every constant: IPC, clocks, frames in flight, store bandwidth, bytes per draw, budgets, the whole mix spec, material sets, RS order, sampler states, what is not counted (ASM-025) |

## 6. D3D9 -> Nine ratio

### 6.1 Measured

Same stream, same ISA (x86-64), front vs st/nine, both without their backend/driver:

| | native front | Nine (st/nine) | **Nine / native** |
|---|---|---|---|
| mix_lo frame | 5.68 M | 3.91 M | **0.69** |
| mix_hi frame | 10.07 M | 9.30 M | **0.92** |
| per draw, marginal (families) | 1.2 - 2.9 k | 1.1 - 4.1 k | **0.70 - 1.56** (lit 1.34) |

**The "3-5x" of §3.7 is not supported at this layer.** Per draw st/nine costs 1.1-1.6x the native front; per frame the
native front's fixed sort cost (F2) makes it more expensive than Nine at these draw counts. What the measurement does
not cover, and where a 3-5x would have to come from:
1. **The driver.** The native side's deko3d recording is measured (460-970 instructions per draw). Nine's nouveau/nvc0
   side (state validation, constant-buffer upload through the push buffer, texture/sampler descriptor updates,
   pushbuf flushes) is not: nvc0 needs a GPU. It is excluded wholesale (`nine.driver_entries_mix_hi` lists what
   softpipe stood in for). With CSMT on (as on the Switch) Nine also pays the queue's record + replay.
2. **The engine side.** On the D3D9 path the engine's own per-draw work stays. It is now measured (§13): 2.3 k
   AArch64 instructions per draw (3.8 µs at the gate), 8.5 M per mix_hi frame, not §3.7's 10-20 µs. With it the
   D3D9 path is 16.9 M instructions per mix_hi frame against 11.7 M for native + the engine code it keeps:
   **1.44x per frame** (1.16x mix_lo), before nouveau.
3. The native front is not yet optimised (F2-F4).

### 6.2 Method notes

* x86-64 for both because Nine is only built for the host (V6n's build); an AArch64 Nine (switch target, nouveau)
  cannot run without a GPU. The ratio is same-ISA; the AArch64 Nine absolute (`nine_a64_estimate_instr`) is a
  scaled estimate, not a measurement.
* Rasterization and vertex shading are excluded by the driver cut (degenerate geometry, softpipe's draw module and
  rasterizer are driver functions), not by subtraction of a second run.

## 7. Input formats and the hook for S16's captures

Choice: **`.nxd3d` stays internal; real captures come in as NXD9** (S6n's D3D9-level capture format,
docs/RENDER_NATIVE.md §8.3, written by S16) through `perf/nxd9_import.py` (removed 2026-11-10 with `nxd9_gpu.py`, see render/d3d9; the commands below are history):

```sh
python3 -I render/native/perf/nxd9_import.py <capture.nxd9> <work>/streams/game.nxd3d   # removed 2026-11-10
python3 render/native/perf/emu_count.py <work>/b/native_drv.nso <work>/streams/game.nxd3d <dir> [--dry]
<work>/b/nine_drv <work>/streams/game.nxd3d 0          # under valgrind --tool=callgrind as in report.py
<work>/b/native_drv_cap <work>/streams/game.nxd3d 0 game.nxcap - 2 && python3 -I render/native/perf/passes.py game.nxcap
```

The importer's approximations are listed in its header (shaders: Nine gets stand-in bytecode with the real sampler
and constant counts; constants: vec4 counts set since the previous draw, from register 0; DRAW treated as indexed;
NXD9 has no PASS tags yet, so imported draws are untagged). Checked on a synthetic NXD9 written with
`tests/s6n/nxd9.py` (3 frames x 10 draws through native_drv, nine_drv and passes.py). `.nxd3d` keeps the engine's
redundant sets and the synthetic family/pass tags, which NXD9 does not need to carry.

## 8. Per-pass counters (milestone N2's counter)

`render/native/perf/passes.py <capture.nxcap> [--json out] [--frame N | --all]`: per frame-graph pass and per run of
draws with the same PASS marker (engine pass id + family, §6.1): packets, draws, clears, state binds, texture binds
and handles, UBO binds and bytes, vertex/index binds, dynamic-state ops, command words.

mix_hi, measured frame (synthetic pass ids 900+family index, not engine numbering):

| graph pass | pass id | family | draws | state binds | tex binds | UBO bytes |
|---|---|---|---|---|---|---|
| 0-2 (shadow maps) | 900 | depth | 3 x 100 | 8 / 10 / 13 | 5 / 4 / 7 | 3 x 77 k |
| 3 (reflection) | 901 | lit | 300 | 36 | 249 | 231 k |
| 4 (main) | 902-908 | lit, land, lit (LOD), lit (trees), nolight (grass), lit (actors), nolight (particles) | 1600 / 150 / 200 / 450 / 300 / 200 / 150 | 95 / 14 / 8 / 12 / 3 / 22 / 16 | 1440 / 150 / 112 / 274 / 31 / 163 / 96 | 1.23 M / 115 k / 154 k / 346 k / 230 k / 154 k / 115 k |
| 5-14 (image space + UI) | 909 | imagespace | 10 x 6 | 2-6 each | 6 each | 10 x 4.9 k |
| **frame** | | | **3,710** | **283** | **2,591** (7,242 handles) | **2.85 MB** (7,435 binds) |

mix_lo: 1,490 draws, 199 state binds, 1,091 texture binds, 1.15 MB of UBO. S6n's N0 capture (`make -C tests/s6n capture`,
18 frames, no PASS markers yet): 165 draws, 48 state binds, 46 texture binds, 132 KB of UBO. The UBO volume is
768 B per draw (512 B fixed slice + 256 B epilogue) regardless of the 64-256 B the family needs (F3).

## 9. GPU estimate (model, not measured)

`render/native/perf/gpu_cost.py`: per DKSH of S6n's package (30 shaders, `shaders/build.py` + uam): registers from the
DKSH program header, Maxwell instructions from the code (scheduling words and uam's NOP/BRA padding removed), texture
instructions and loops from uam's TGSI. Dynamic count: lit*.frag's light loop x 3 active lights; other TGSI loops are
compiled switches (once); both IF arms counted (upper bound).

| shader | GPRs | Maxwell instr (static) | est. per invocation | tex |
|---|---|---|---|---|
| lit.vert | 12 | 56 | 56 | 0 |
| lit_spec_d1p2 __e0 / __ef / runtime (alpha + fog) | 26 | 179 / 223 / 273 | same | 1 |
| lit_d1p2 __e0 / __ea | 20 | 105 / 130 | same | 1 |
| lit_spec / lit (dynamic loop, runtime epilogue; bound only for > 1 directional light) | 26 / 19 | 248 / 196 | 474 / 363 | 1 |
| land__e0 | 17 | 34 | 34 | 4 |
| depth.vert / depth__e0 | 8 / 4 | 31 / 11 | 31 / 11 | 0 |
| nolight_tex__e0 / __ea / __ef, tex__e0 (image space stand-in) | 6-7 | 32 / 35 / 58, 32 | same | 1 |
| sky_clouds__e0 | 7 | 35 | 35 | 1 |

**Which shader a family costs** (since this round, P6n "GPU model binds the renderer's ... permutations"): what `nx::Renderer::Render` binds - the static light permutation
for the model's 1 directional + 2 point lights (`lit[_spec]_d1p2`), then the epilogue permutation for the draw's
alpha test / fog: `__e0`, `__ea`, `__ef`, or the runtime epilogue when both. The (alpha test, fog) mix per family is
replayed from the synthetic stream's D3D9 state in the measured frame (`gpu.assumptions.epilogue_mix_hi`; e.g. lit: (ASM-025)
51 % fog only, 34 % neither, 16 % both). Light loops are the for-loops with a non-literal bound after the
preprocessor (the old count of every `for (` in the source also took #ifdef'd-out loops and multiplied the
alpha-compare switch). `PERMUTATIONS = False` in gpu_cost.py reproduces the runtime-shader model (24.7 ms handheld
on today's shaders).

Scene model (Tegra X1 GM20B: 256 FP32 lanes (fp16x2 HFMA2/HADD2/HMUL2 at twice the rate: ALU slots count them
at half cost; detected from the Maxwell code, register forms only, 0 in today's uam output), 16 TMU, 16 ROP (half
rate on 64-bit colour targets), 25.6 GB/s at 60 % efficiency; GPU 768 MHz docked, 384 MHz handheld). Presets, dynres
off: docked_720p (shadow 3 x 1024², reflection 512²) and handheld_720p (3 x 512², 256²) are the gate configs;
docked_1080p and handheld_540p_dynres are informational. DRAM bytes per pixel from each pass's render-target format
(`TARGET_FORMATS`: scene, reflection and image space RGBA16F as FNV's HDR path, depth D24S8; colour x2 when
blending, depth x 0.5 compression, 0.5 B per texture fetch). S6n's target changes go into `gpu_cost.py`
`TARGET_FORMATS` or an override file `render/native/perf/gpu_targets.json` (`{"formats": {"main": "R11G11B10F"}}`).
Before these terms (first report) the frame was 7.4-12.4 / 14.9-24.8 ms docked/handheld 720p; now: per family pixels = target x coverage x overdraw (shadow 3 x 1024² x 0.6 x 1.5, lit 0.55 x 1.8,
land 0.45 x 1.2, LOD 0.15 x 1.2, trees 0.15 x 2.5, grass 0.20 x 3.0, actors 0.05 x 1.3, sky 0.35, particles 0.10 x
4.0, image space 3.5 full-screen equivalents, reflection at quarter size); fragment time = pixels x max(ALU = instr /
256, TEX = tex x 1.5 / 16, ROP = 1/16) / clock / issue efficiency (1.0 or 0.6); vertex time = draws x vertices x VS
instr / 256; frame = max(sum, DRAM traffic / bandwidth). No cache, latency or early-z modelling beyond those factors.

| mix_hi (efficiency 1.0 - 0.6) | docked 720p | handheld 720p | docked 1080p | handheld 540p (dynres) |
|---|---|---|---|---|
| native shaders (S6n, bound permutations; lower bound: stand-ins) | 5.0 - 5.5 | 6.1 - 10.2 | 10.3 - 10.4 | 4.1 - 6.8 |
| **game's shaders (real019, translated: the metric)** | 12.2 - 20.3 | 19.9 - 33.2 | 18.7 - 31.2 | 15.3 - 25.6 |
| 60 fps (16.7), real019 | fail (1.22) | **fail (1.99)** | fail | fail |

(Before this round, carried real019: 10.1 - 16.8 / 16.3 - 27.2 ms. Since this round both scenes use the early-z term
and the depth-bucket order, `gpu.assumptions.early_z` / `depth_buckets`; the native scene lights lit draws with the (ASM-025)
sun + 4 points and alpha-tests all tree and grass draws.)

(Before: native 7.5 - 12.5 docked 720p, 14.0 - 23.3 handheld 720p.) Handheld 720p at efficiency 0.6: lit 4.20 ms
(3.29 fragment + 0.91 vertex), image space + UI 1.75, land 0.96 (TEX-bound), trees 0.76, grass 0.64, actors 0.52,
LOD 0.38, particles 0.34, water reflection 0.28, shadow 0.23, sky 0.19. Docked 720p at 1.0 is DRAM-bound (5.5 ms).
The real019 scene (the game's shaders through the D3D9 translation) is 2.7x the native one at handheld. The model
has no measured anchor yet: the first console run's `NxFrameStats.gpu_us` replaces it.

## 10. Findings for S6n (render/native)

| # | finding | measured cost | suggestion |
|---|---|---|---|
| F1 (fixed in f4c70ed: 0 dropped draws at 3 frames in flight, optimizations[]) | `Renderer` reserves 512 B UBO + 256 B epilogue per draw in an 8 MiB ring split over `frames_in_flight`; at NXGPU_FRAMES = 3 a region holds 3,640 draws | mix_hi (3,710 draws) drops 74 draws per frame at 3 frames in flight (`ring_overflow_at_3_frames_in_flight`); measured at 2 | size the ring for 4-5 k draws x 3 frames, or allocate the slice at the family's real size (F3) |
| F2 (fixed in 1b0665e: 3.74 -> 1.21 ms) | `SortPackets`: 16-bit LSD radix, 65,536-entry count table, memset + prefix sum per varying digit (4 here) | 2.29 M instructions per mix_hi frame (of 3.38 M in Finish), ~1.9 M fixed per frame; 256 KiB table is not L1-resident | 8-bit digits (or 11-bit), or sort only the 36 high bits (pass, layer, state, tex) and keep engine order inside; expected < 0.3 M |
| F3 (partly: slice written once C95, epilogue block shared 6d305cd / C100; the slice stays 512 B) | `Render` clears and copies a fixed 512 B slice, writes a 256 B epilogue every draw | memset/memmove 0.53 M per mix_hi frame; 2.85 MB of UBO writes (the store term, 0.8 ms) | slice of the family's size (64-256 B); share the epilogue block while the dyn state is unchanged (as `AddDyn` does) |
| F4 (fixed in C94: nx_d3d9_sampler 1.01 -> 0 ms) | `StateTracker::Handles` translates every stage's sampler (`nx_d3d9_sampler`) and searches the sampler cache linearly on every draw | 1.05 M per mix_hi frame (618 k + 433 k) | cache the translated sampler per stage, re-translate only when that stage's sampler state changed |
| F5 (fixed: ed96991 permutations, C98 branch-free alpha compare; the GPU model costs them since this round) | every fragment shader carries the full runtime D3D9 epilogue (fog modes, alpha-test switch) | ~120 of tex.frag's 131 Maxwell instructions; depth.frag is 11 | static permutations (`NX_FOG_MODE`/`NX_ALPHA_TEST`, already supported by the sources) for the hot families |
| F6 | `Frame::kMaxPasses` = 16 | the synthetic mix uses 15 (image space switches every 6 draws; with a switch every 3 draws it overflowed) | raise to 32-64 (bloom/DOF chains alone use 10+) |

## 11. Limitations

* Synthetic input: draw counts and change rates are the §3.7 estimates, not the game's. S16's NXD9 captures replace
  them through §7; `passes.py` then gives real per-pass counts.
* Instruction counts, not cycles: IPC 0.6-1.0 is an assumption; caches only through the store term; no (ASM-024)
  contention with the other cores (L2 is shared by the 4 A57s).
* The engine's D3D9 draw path is a static count (§13), not an execution trace; the rest of the engine and the nouveau
  driver (Nine path) are not measured.
* The deko3d count stubs libnx: no ioctl/kernel time, no GPU back-pressure (command memory never waits).
* GPU numbers are a model with assumed coverage/overdraw; shader cost uses S6n's current N2 families as stand-ins (ASM-025)
  for LOD, trees, actors, particles and image space (no dedicated shaders yet).

## 12. Proposed replacement for RENDER_NATIVE.md §3.7 (for S6n)

> ### 3.7 Per-frame cost (Mojave exterior), measured by P6n
>
> Synthetic D3D9-level streams with the draw mix below (docs/RENDER_PERF.md §2), counted instruction by instruction
> (AArch64 under Unicorn: ILP32 front + LP64 deko3d backend; x86-64 callgrind for the Nine comparison); report:
> `render/native/perf/report.json`, regenerated by `render/native/perf/run.sh`.
>
> | source | draws | AArch64 instr/draw (main + render) |
> |---|---|---|
> | static objects (lit, x 1.2-1.6 passes) | 720-1,600 | 1,741 + 974 |
> | land | 100-150 | 2,339 + 993 |
> | distant LOD | 100-200 | 1,319 + 917 |
> | trees | 150-450 | 1,412 + 896 |
> | grass | 100-300 | 1,054 + 819 |
> | actors | 50-200 | 1,734 + 982 |
> | shadow maps | 100-300 | 758 + 883 |
> | water reflection | 100-300 | 1,439 + 987 |
> | particles | 50-150 | 1,214 + 956 |
> | image space + UI | 20-60 | 1,566 + 1,417 |
> | **frame** | **1,490-3,710** | main 2.3-5.6 M, render 3.4-5.4 M instructions (incl. ~1.9 M fixed in the sort) |
>
> At 1.02 GHz (IPC 0.6-1.0): main thread 2.3-10.0 ms, render thread 3.3-8.9 ms; at 1.785 GHz 1.3-6.1 ms and
> 1.9-5.1 ms. That is within every frame-rate target on its own, but over §9.4's renderer budget lines at 1.02 GHz
> until the sort, the fixed-size UBO slices and the per-draw sampler translation are fixed (RENDER_PERF.md §10,
> about 2.5-3 M instructions per high frame). The D3D9 -> Nine state tracker measured on the same streams costs
> 0.7-1.6x the native front per draw (0.69-0.92x per frame): the advantage of the native path is not in this
> layer but in the driver (nouveau validation vs deko3d recording, 460-970 instructions per draw, not measured on
> the Nine side) and in the engine-side work the families remove (measured: 2.1 k of the engine's 2.3 k instructions
> per draw are its D3D9 path, which the native renderer replaces). GPU (model): 7-12 ms docked
> 720p, 15-25 ms handheld 720p, 8-15 ms handheld 540p.

## 13. The engine's own D3D9 path (measured on the prepared objects)

Until 2026-11-04 the CPU estimate assumed this side: "the engine's own per-draw work before the device: 1-3 k (ASM-025)
instructions [E]" (PERF.md §5), "10-20 µs/draw" (RENDER_NATIVE.md §3.7). It is now counted on the AArch64 build
of the game: the prepared ILP32 objects of `c/b6585b87bb73` (gcc 13.3 -O2, decomp 87eb9373a0bb), resolved through
a link's address tables (`link-head`, same decomp). Result: `report.json` `engine_d3d9_path`. Source: **measured
(static path count)**; the assumption is replaced.

```sh
python3 -I render/native/perf/engine_path.py <obj dir> <link out dir> <spec.json> <out.json>   # seconds, 1 core
python3 -I render/native/perf/engine_fold.py render/native/perf/report.json <out.json> --lo <lo.json> --hi <hi.json>
```

(`run.sh` runs both when `ENGINE_OBJ`, `ENGINE_LINK` and `ENGINE_SPEC[_LO,_HI]` are set; `report.py` folds
`<work>/out/engine_path*.json`.) The spec is local: it names engine addresses and carries vtables read from the
user's exe (`render/native/tools/exe_vftables.py`: NiDX9Renderer, NiDX9RenderState, NiD3DShader, NiD3DPass,
NiD3DShaderConstantMap, NiD3DVertexShader, the lit BSShader class, and IDirect3DDevice9's method order). Never commit
it.

**What is on the path.** Per draw the world path enters through BSBatchRenderer's per-geometry pass (the shader's
Prepare/Update/Finish virtuals), then the Gamebryo draw: Do_RenderShape -> the render-mesh pass loop -> the
shader pipeline (PreProcess, UpdatePipeline, FirstPass/NextPass, SetupRenderingPass with the pass's texture
stages, SetupTransformations, PrepareGeometryForRendering, SetupShaderPrograms with the constant-map walk,
PostProcess) -> NiDX9RenderState (property state, render-state groups, the value filter in front of every
SetRenderState / stage state / SetTexture) -> the device. Device calls end at our D3D9 front (§1 counts that side).

**Method.** `engine_path.py` disassembles each function on the path (llvm-objdump), builds its blocks, follows
direct calls through the link's refs/addrmap tables, and resolves virtual calls by a register type flow (this,
fields, globals -> vptr -> slot) against the exe's vtables. Per invocation it reports the shortest path, the
longest path and the static expectation (the return heuristic for early exits; spec branch rules where they are
known: the trishape/indexed/unskinned common path, the value filters at mix_hi rates; loop trips from the mix:
2.4 texture stages, 8 hardware stages for the texcoord states, 2.5 predefined + 1 other constant-map entries per
map, 2 state-group entries per pass). Range: redundancy 90 % / 60 % and 1 / 4 group entries.

Loops nested in one function multiply: loops are taken innermost first, and an outer iteration costs its blocks
plus the extra trips already put on the inner headers (V1's refutation L1: a 4 x 4 nest had counted 39 of its 66
instructions). `tests/s6n/test_engine_path_loops.py` checks exact counts on hand-written AArch64 (4 x 4, 2 x 3 x 4,
two sibling inner loops, a triangular nest at its mean trip count, a conditional block inside the inner body).
The re-count after the fix (2026-11-04) is identical to the first one: on the counted path no function nests a
loop with more than one trip inside another (the render-mesh pass loop's inner loops run once; the
constant-map walk's two loops are siblings), and loops in different functions already multiplied through the
callee's inclusive count. Only the informational `set_sampler_state` role moved (16.8 instead of 18.0 per call),
because S6n's overrides row now names the real sampler-state slot.

| per draw (AArch64 instructions, mix_hi) | value |
|---|---|
| **expectation (gate)** | **2,303** (range 2,280 - 2,355), 3.76 µs at IPC 0.6 / 1.02 GHz, 1.28 µs at IPC 1.0 / 1.785 GHz |
| shortest / longest path (bounds only) | 192 / 8,207 |
| D3D9 path (replaced by the native renderer) | 2,131 |
| kept on the native path (BSBatchRenderer + BSShader Prepare/Update/Finish) | 172 |
| device calls per draw (after the engine's filter) | DrawIndexedPrimitive 1.0, SetRenderState 3.8 (2.0-8.4), SetTransform 0.6, SetTextureStageState 0.6, constants 0.16 (the rest of the constants come from the BSShader class) |

Hot functions, instructions per draw (expectation): NiDX9RenderState::SetRenderState 399 (about 20 calls per draw,
~20 instructions each, most filtered); the render-mesh pass loop 164; NiD3DPass::SetupShaderPrograms 127 (8
texcoord stage states every pass); NiDX9RenderState property application (stencil 93, alpha 71, helpers 114,
UpdateRenderState 39); PrepareGeometryForRendering 99; SetupRenderingPass / texture stages 94; the BSShader lit
class's Prepare/Update/Finish 86; constant-map walk 82 + predefined switch 39 + SetShaderConstant 21; Do_RenderShape
78; NiD3DDefaultShader pass setup 78; SetupTransformations 75 + skin 31; BSBatchRenderer pass 74. Full list:
`report.json` `hotspots.engine_d3d9_path_by_function` (ms per mix_hi frame at the gate).

| per frame (gate: IPC 0.6, 1.02 GHz) | mix_lo (1,490 draws) | mix_hi (3,710 draws) |
|---|---|---|
| engine D3D9 path + kept part | 3.4 M instr, 5.6 ms | 8.5 M instr, **14.0 ms** (14.3 hi) |
| of which kept on the native path | 0.26 M, 0.4 ms | 0.64 M, **1.0 ms** |

**Effect on the frame estimate.** Native path: the D3D9 path is replaced; the kept part adds 1.0 ms to the main
thread: 8.71 -> 9.76 ms, 0.52 -> 0.58 of the 16.7 ms budget (`1.02_with_engine_kept`; before S6n's perf commits
10.0 -> 11.1 ms). The frame verdict does not change (handheld is GPU-bound at 0.61). The D3D9 (Nine) fallback is out of scope (owner): its numbers
(the engine path 14.0 ms, which applies **only** to the fallback, plus st/nine; `d3d9_path_main_1.02`) are kept for
reference and are in no budget row. On the native path only the kept engine part (1.04 ms) runs, on main. That
settles the value of the native renderer on the CPU side: it removes 12.9 ms per mix_hi frame of engine code, not only the driver.

**Limits.** A static count, not a trace: branch probabilities are rules, and 69 indirect calls stay unresolved
(70 on the re-count: geometry/skin getters, the strip path), counting only their call; the BSShader virtuals use the lit class;
per-frame engine work (BeginFrame/EndFrame, target groups, the batch sort) is not in it; the class code that fills
most shader constants is counted only for the lit class. The slot-48 NiDX9RenderState function (overrides row
"SetSamplerState") called the device's SetTextureStageState: fixed this round (S6n "overrides - slots 48/49 are texture stage states"; slots 48/49 are NiD3DRenderState's
texture stage states, the sampler states are slots 51-53; checked on the user's exe).
