# First-launch readiness (stream R0-gates)

The question this answers: can the owner copy the package to the console and expect the game to reach the main
menu and the first cell at an acceptable frame rate? Every stream measures something on the farms. R0 collects
those numbers into one scorecard, checks each against a threshold, and says what is still missing.

Tool: `tools/gates/ready.py` (thresholds `tools/gates/thresholds.json`, hardware risks
`tools/gates/hardware_risks.json`). Tests: `python3 -I tests/gates/test_ready.py`. Farm job:
`tools/farm/gates_job.sh`, which publishes `reports/gates1/<c12>-gates.md` and `.json`.

## The farm job

    farm/start.sh gates1 gates            # with RUNNER_REPEAT_S=1800 in the environment

- `tools/farm/gates_job.sh <commit> <report.md>` runs `ready.py --fetch` at the integration head the runner checked
  out, and writes the scorecard and its JSON sidecar (`<report>.json`).
- It reads the artifacts from a bare partial clone of its own, `$FARM_HOME/gates-artifacts.git` (blob:none), so
  the farm's publishing clone never fetches `link/*`. A run takes about a minute; `GATES_TIMEOUT_S` (540) caps it.
- `runner.sh` publishes a report's `.json` sidecar next to its `.md`, in the same commit, for every job.
- Reports keep arriving while the head stays the same. `RUNNER_REPEAT_S=<s>` makes the runner score the same head
  again once the last run is that old; without it, the runner runs only on a new head.

## Running it

    tools/gates/ready.py --artifacts <git dir of fnv-nx-build> --fetch --md card.md --json card.json

- `--artifacts` is any clone of the artifacts repository. The tool reads it with `git --git-dir`, so a bare
  `--filter=blob:none` clone is enough, and blobs are fetched when read.
- `--fetch` first updates `reports/*`, `link/*` and `gamedata/exe` in that clone. It also runs
  `git fetch origin claude/port-nsw-render` in this checkout, for the render machine's report.
- `--head` is the integration head the distances are counted from. It defaults to `HEAD` of this checkout, so run
  the tool from an up-to-date checkout of the integration branch.
- `--fixtures DIR` reads a directory laid out as `<branch>/<path>` instead (the tests).

The output holds numbers, addresses, rule and class names and report paths only: no prepared source, no unit
text, no game data. It can be published next to the other reports.

## How a gate is decided

- **Which report.** For every farm, the tool takes the newest report that parses and is not a failed run
  (`# <job> <c12> FAILED`). For sharded jobs (compile, S11, S12, S13), it takes per shard the newest report
  of the shard count in `thresholds.json`, so a report of another shard count is ignored. Try farms are skipped.
- **Value, threshold, source, status.** Every gate records its value and threshold, and its source: report path,
  commit, and the distance from the head. The distance reads *N behind*, *N ahead* (a farm ran a newer commit)
  or *not on the head's history*. Each gate is one of **pass**, **fail** or **no-data** (nobody measures it
  yet, or the evidence is unreadable).
- **Stale.** A pass whose source is more than `max_commits_behind` (25) commits behind, or not on the head's
  history, is *stale*.
- **Informative gates.** Gates marked `required: false` (the stock-clock frame gates, below) are shown, but do not
  count.
- **Verdict.** **READY** only when every counted gate passes on fresh evidence. A no-data gate keeps the verdict
  at NOT READY: missing evidence is not a pass.
- **Top blockers.** The scorecard lists the failing (or stale) gate with the lowest rank in each group, ordered
  by rank, five at most. The rank is the order in which the work unblocks the first launch: boot first, then
  what the boot runs into, then correctness at scale, then the tail. Missing evidence is listed separately.

## The gates

| id | what | source | threshold | why that threshold |
|---|---|---|---|---|
| compile.census | units that compile clean | `reports/farm1/*-census.md` | 2978/2978 | a unit that does not compile has no code in the guest |
| compile.objects | objects built, the 4 compile shards | `reports/farm2..5/*-compile.md` | 2978/2978 | the link takes these objects |
| link.undefined | trial-link undefined names | link1 report, or the full report nearest the head | 0 | each one is a trap stub at run time |
| link.multidef | multiple definitions | same | 0 | two bodies for one name: which one runs is an accident |
| link.split_global | split-global objects / relocations | same | 0 / 0 | D-rule invariant of LINK.md |
| link.traps | guest symbols bound to trap stubs | newest full report, `link/<c12>/meta.json` | 0 | the boot hits them (trapped keys) |
| link.v1 | V1-link audits failing (D14) | newest full report that ran them | 0 | each audit is a class of wrong binding |
| abi.x86_inconsistent | S2 x86-inconsistent pairs not explained | `link/<c12>/out/abi.tsv`, the kept link nearest the head | 0 | a caller and callee disagree on the stack; only `pop-mismatch` is explained (bridged) |
| abi.manual | S2 pairs with a manual verdict | same | 0 | no automatic bridge exists for them |
| equiv.code | S11 functions whose code differs, not by design | `reports/eq1..6` | 0 | a prepare rule changed x86 semantics; by design: CTOR1 and the PATCHES.md "By-design MSVC differences" sites |
| equiv.nobuild | S11 units MSVC cannot compare, not by design | same | 0 | an uncompared unit is unverified; by design: ENUM1 |
| static.hazards | S12 open hazard sites | `reports/st1..4` | 0 | non-benign classes of `tools/static/report.py`; a class is closed while the build closes it: report.py's `CLOSED`, whose `build_closures()` reads compile.sh (the fnvnx_ftoi and fnvnx_order plugins, ORDER_ARGS, ORDER_KEYS), fnvnx_compat.h and sync.cpp, plus `static_closed` (file + regex) |
| static.gate | S12 shards over baseline / units GCC did not compile | same | 0 / 0 | an uncompiled unit was not checked |
| fuzz.hard | S13 functions whose worst cause is real-bug or triage | the `fnvnx-causes` block of `reports/fz1..26`, or `reports/s13b-causes:causes_all.json`, whichever covers more shards | 0, all 26 shards | the class of a cause comes from this checkout's `tools/difffuzz/causes.py` POLICY (an unknown cause is triage); by-design causes are not a divergence of the port; reports without a block fall back to the hard table and `fuzz_by_design_causes` |
| fuzz.artifacts | S13 functions whose worst cause is a harness artifact | same | informative | the harness or its oracle model, not the port; S13 fixes them |
| fuzz.coverage | functions with a verdict / with a native object | same | >= 90 %, all shards | the rest is indirect control flow the fuzzer does not model yet |
| boot.real_exe | the boot ran on the player's FalloutNV.exe | `meta.json` `boot_image_sha256` against `gamedata/exe`, else the report's data image line | yes | the synthesized image proves the loader, not the game |
| boot.winmain, boot.renderer_init, boot.main_menu, boot.first_frame, boot.first_cell | boot milestones on the real exe | `link/<c12>/boot/milestones.json`, see the formats below | reached | the first launch has to reach the first cell; a null milestone is no-data, the stub-fault boot never counts |
| boot.init_skips | static initializers skipped after a fault | full report | 0 | each one is a global left unconstructed |
| frames.compare | S16 scenes matching the reference | `reports/<farm>/<c12>-frames.json` | all scenes | |
| perf.frame.stock / perf.frame.boost | main-thread frame time from frameprof | `reports/<farm>/<c12>-frameprof.json` | three levels, below | J-0132 |
| perf.boot | estimated device time to WinMain (stock clock) | same | <= 3 s | a black screen longer than this reads as a hang |
| perf.memory | peak guest heap / heap the guest can have | `meta.json` `boot_peak_heap_bytes` | <= 75 % of 1984 MiB | headroom for fragmentation and the cells after the first one |
| perf.cores | core placement | `toolchain/host/placement.h`, `toolchain/npdm/fnvnx-app.json` in the checkout | main on core 0, workers on 1-2, core 3 opt-in and off | PERF.md placement table |
| perf.render.stock / perf.render.boost | render machine: the renderer's worst thread as a share of each target's frame | `render/native/perf/report.json`, summary `targets_budget_fraction` (hi bound) | <= 20 % of the frame at the target | RENDER_NATIVE.md 9.4: render thread 4.5 of 22.2 ms, packet build 4.0 ms |
| perf.gpu.docked_720p / handheld_720p / handheld_540p | GPU frame time of the render machine's model | same report, `gpu.summary_ms.mix_hi` | three levels, informative | a model with no measured anchor (RENDER_PERF.md section 9) |
| perf.budget.stock / perf.budget.boost | S15 whole-frame CPU budget, scenario `placed` | `reports/<farm>/<c12>-frame-budget.json`, else `docs/PERF_budget.json` | `pass_45` verdict pass on a measured frame | PERF.md section 11; a model-only or uncertain verdict is no-data |
| package.exefs | exefs = main + main.npdm, guest.nso on the SD tree | full report | yes | Atmosphere's exefs override layout |
| package.program_id | program id of an installed title | full report | not 0100000000000000 | the override needs a title that is installed |
| package.pic | guest PC-relative absolute references | full report | 0 | the guest must be relocatable (D9) |
| package.check | `tests/s5/check_package.py` on the package | `meta.json` `check_package_rc`, or `check_package: exit N` in the full report | exit 0 | the package checks that do not need a console |

## Frame-rate levels (J-0132)

The owner's targets:
- below 30 fps is a fail;
- 45 fps stable is the target;
- 60 fps is the stretch, even if unstable.

`thresholds.json` `perf.fps` holds the three levels, and `perf.clocks` the two CPU clocks: 1.02 GHz stock and
1.785 GHz boost. Each frame gate exists once per clock.

| level | frame | judged on | 1.02 GHz, IPC 0.8 | 1.785 GHz, IPC 0.8 |
|---|---|---|---|---|
| floor 30 fps | 33.3 ms | worst frame | ~27 M instructions | ~48 M |
| target 45 fps stable | 22.2 ms | worst frame | ~18 M | ~32 M |
| stretch 60 fps | 16.7 ms | median frame | ~14 M | ~24 M |

- **Status.** A frame gate passes only at the target. Its note gives all three levels (`floor yes, target no,
  stretch no`), and the JSON gives `levels: {floor, target, stretch, level}`.
- **Level names.** The level reads *fail* (under the floor), *below target*, *target* or *stretch*.
- **Main thread.** `perf.frame.<clock>` converts frameprof's main-thread instructions per frame to time at
  that clock and `perf.ipc`.
- **Render machine.** `perf.render.<clock>` reads the summary's `targets_budget_fraction`: the slower of the main
  side and the render thread, hi bound (IPC 0.6 at 1.02 GHz, mix_hi), divided by each target's frame time.
  - Its `targets` verdicts are not used: they compare the renderer alone with the whole frame, while the owner's
    targets are for the whole frame.
  - The renderer has a share of the frame, `render_perf.max_budget_fraction` (0.2, from RENDER_NATIVE.md 9.4). A
    level is met when the renderer fits its share of that level's frame.
  - A report without the fractions gives them from `frame_ms` (gate values), scaled from the lowest
    `assumptions.clock_ghz`.
- **GPU.** `perf.gpu.<config>` judges the efficiency-0.6 bound of the model's mix_hi frame against the three levels.
  It is informative (`render_perf.gpu[].required`) until a console run measures the GPU.
- **Whole frame (S15).** `perf.budget.<clock>` takes the scenario as the build is placed today (`perf.budget`), not a
  plan.
  - Its `pass_45` verdict passes only when the budget's inputs hold a measurement (frameprof or the render report).
  - `uncertain` (the lo case fits, the hi case does not) and a model-only pass are no-data; `fail` fails.
- **Which clock counts.** The owner's console can raise the clock, so the boost clock counts for the verdict
  (`required: true`). The stock clock is reported as informative (`required: false`). Set the stock clock to
  `required: true` to demand the target at stock.

## Formats the gates read (agreed with S5, S16 and the render machine)

- **Milestones (S5).** `link/<c12>/boot/milestones.json`, `fnvnx-boot-milestones/1` (`tests/s5/boot_milestones.py`):
  the raw boot's `{"winmain", "renderer_init", "main_menu", "first_frame", "first_cell": true|false|null}` at the
  top level, plus `image` ("player exe" | "synthesized"), `milestone` (the last reached), `boot_peak_heap_bytes`,
  `stop`, and the same for the stub-fault boot under `stub`.
  - null is not measured (no-data). The stub boot is named in a note and never counted.
  - `image` decides `boot.real_exe` when there is no image hash; `boot_peak_heap_bytes` feeds `perf.memory` when
    meta.json has none.
  - Alternatives: the keys `boot_<name>` in `link/<c12>/meta.json`, or `fnvnx milestone: <name>` lines in
    `boot/summary.txt`.
  - WinMain also counts from `fnvnx boot: calling WinMain`, or from "WinMain entered" in the full report.
- **Image (S5).** `meta.json` `boot_image_sha256`, compared with `gamedata/exe:FalloutNV.exe.sha256`.
- **Peak memory (S5).** `meta.json` `boot_peak_heap_bytes`.
- **Package check (S5).** `meta.json` `check_package_rc`, or a `check_package: exit N` line in the full report.
- **Frames (S16).** `reports/<farm>/<c12>-frames.json`: `{"commit": ..., "scenes": [{"name", "metric", "value",
  "threshold", "pass"}]}`.
- **Frameprof (S15).** `reports/<farm>/<c12>-frameprof.json`: the output of `tools/perf/frameprof.py --json`
  (`[{label, instructions, ...}]`).
  - Frames are labelled `frame N`.
  - The boot segment ends with a label `until WinMain` (or `until __fnv_0086a850`).
- **Render perf (render machine).** `ports/fnv-nx/render/native/perf/report.json` on `claude/port-nsw-render`,
  else the copy merged into the checkout. The schema is `docs/RENDER_PERF.md` section 5.
  - The tool reads the summary block, at the top level or under `summary`: `{commit, per_draw_us, frame_ms: {main,
    render}, assumptions: {ipc, clock_ghz}, targets_budget_fraction: {"30"|"45"|"60": {"1.02", "1.785"}}}`. A range
    `{lo, hi, gate}` is read by its gate value (= hi).
  - The GPU model is `gpu.summary_ms.<mix>.<config>: {eff_1.0, eff_0.6}` (`handheld_540p_dynres` counts as
    handheld_540p).
  - The distance is counted on the render branch (on the integration head for the merged copy).
- **Fuzz causes (S13b).** The `json fnvnx-causes` fenced block of each fuzz report (`tools/farm/fuzz_job.sh`), and the
  `causes.py summary` output on branch `reports/s13b-causes` (`causes_all.json`; its shards come from the campaign
  directories `c<k>`).
  - Per cause: `verdict`, `divergences`, `functions`, `worst_of`.
  - `worst_of` counts the functions whose worst cause it is, so the counts add up without overlap.
- **Frame budget (S15).** `fnvnx-frame-budget/1` (`tools/perf/frame_budget.py --out`), published as
  `reports/<farm>/<c12>-frame-budget.json`: `inputs`, `scenarios[].results.<MHz>.{worst_frame_ms, verdicts, level}`.
- **Sidecars.** `tools/farm/runner.sh` publishes a report's `.json` sidecar (`<c12>-<job>.json`) next to its `.md`,
  in the same commit, so a job can hand machine-readable numbers to the gates.

## Hardware-only risks

What the emulator cannot see, with a mitigation and the on-device evidence for each, is in
`tools/gates/hardware_risks.json`. The scorecard prints them as a table:
- the GPU driver;
- real timing and threading;
- weak memory ordering;
- the LP64/ILP32 host boundary;
- the memory limit;
- instruction cache coherence;
- the Atmosphere override;
- storage;
- audio and input;
- clocks and thermals.

No gate covers these. They are the checklist for the first launch on the console.

## Scorecard at 72f7520 (integration 7b3b202 + R0), 2026-11-04

Verdict: **NOT READY**. 5 pass, 18 fail, 10 no-data, 4 stale passes, of 33 counted gates; 7 more are informative.
The scorecard came from `tools/farm/gates_job.sh`: 60 s with a cold clone, 9 s with a warm one.

Top blockers, from the tool:

1. **Emulated boot.** No boot on the player's exe yet.
   - The newest full report (`full1` ff0fd85, 110 commits behind) booted `synth.exe`. WinMain is reached, and
     4,343 initializers are skipped after a fault.
   - full1 has published nothing since 12:23, so no `boot/milestones.json` exists yet. renderer_init, main_menu,
     first_frame and first_cell are no-data.
2. **Link.** 3,811 guest symbols are bound to trap stubs, and the boot hits 6 of them (240 hits). The trial link at
   0bd144d has 4,427 undefined names.
3. **ABI.** 1,875 of 3,266 x86-inconsistent pairs are not explained (`ref-passes-fewer` 1,632), and 2,632 pairs
   have a manual verdict.
4. **Fuzz.** 2,836 functions have a real-bug (1,073) or triage (1,763) worst cause. This comes from S13b's campaign
   summary, which covers 25 of 26 shards (shard 22 has no cache).
   - Owners: S13b 1,763 (unclassified 567, native-inlined 306, oracle-fault 220, call-flow 171), S1 556, S1b 309,
     S2c 208.
   - Harness artifacts: 771 (informative). By design: 2,387.
   - Coverage is 73 %. Shards 1, 4, 8, 11, 12, 19 and 22 still have no 26-shard report.
5. **Compile.** 2,936 of 2,978 units compile clean, and 56 units have no object.

Also failing:
- S11: 46 functions and 73 units not by design.
- S12: 5,466 open sites, down from 14,777.
  - The build closes 14 classes (12,625 sites), among them VOLATILE-DECL, ORDER-ARGS, BARRIER, ATOMIC-USE and the
    float->int classes.
  - Top open: ORDER-OBJECT 1,086, W-NONPOD-VARARGS 1,062, FLOAT-BITS 758.
  - The static reports are from 979cb1d, 100 commits behind, because the farms st1-st4 are paused.
- The render machine at 1.785 GHz: the renderer takes 27 % of the 45 fps frame (6.1 ms), against 20 % allotted. At
  1.02 GHz it takes 45 % (10.0 ms; informative).
- 6 of 19 V1 audits.
- The placeholder PROGRAM_ID.

Informative:
- GPU model, mix_hi at efficiency 1.0-0.6, from the render branch's report at 443daf3:
  | config | frame | level |
  |---|---|---|
  | docked 720p | 7.5-12.5 ms | stretch |
  | handheld 720p | 14.0-23.3 ms | below target |
  | handheld 540p, dynamic resolution | 8.6-14.3 ms | stretch |
  The copy merged at 7b3b202 had 7.4-12.4, 14.9-24.8 and 9.0-15.1 ms.
- S15 frame budget, model only: placed at 1.785 GHz, worst frame 17.2 ms mid (7.0-39.4); `pass_45` is uncertain.

Missing evidence:
- the milestones after WinMain;
- S16 frames;
- frameprof (frame time and boot time);
- a measured frame budget;
- peak memory;
- check_package.

## Changing a threshold

Change `tools/gates/thresholds.json` only with a journal entry that says why, and update this page. Shard counts
must match the farms' configuration: a farm on another shard count is reported as a missing shard, not merged.
