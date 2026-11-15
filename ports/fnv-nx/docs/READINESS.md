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
- **S1b's ABI verdicts.** After the first pass the job runs S1b's `tests/s1b/pop/popscan.py` and `abiclass.py` on the
  `out/abi.tsv` of the link the scorecard read, and scores again with `ready.py --abiclass`.
  - Inputs: the decomp at the `tools/fetch.sh` pin (`$FARM_HOME/ext/fnv_decomp`), the pristine MSVC objects of
    branch `msvc/pristine/<decomp12>` (`$FARM_HOME/oracle-<decomp12>`, shared with fuzz_job.sh), capstone (a venv
    when missing). `GATES_DECOMP` and `GATES_PRISTINE` take local copies instead.
  - Cost: popscan ~100 s and abiclass ~25 s once per link and `patches/` tree; cached in `$FARM_HOME/gates-abiclass`
    for the three newest links. The tables are decomp-derived and are never published: the scorecard carries counts.
  - A failure there leaves the gate on classes alone (note "no abiclass table"); `GATES_ABICLASS=0` skips it.

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
- **Evidence that cannot show the claim.** These never pass a gate (V1's R1-R14, `tests/v1/test_ready_adversarial.py`):
  - a shard header `k/N` with k outside 1..N, which is no shard of N;
  - a scene whose `pass` is anything but JSON `true`, or an S16 gate file whose top-level status disagrees with its
    scenes, or whose candidate is not the port;
  - a frameprof run with a frame or phase of 0, negative or missing instructions (the run is no measurement);
  - a render report with a non-positive or non-finite frame time or budget fraction, or lo > hi (no-data);
  - an image hash checked against a checksum line for any file other than `FalloutNV.exe`, or against an exe other
    than the pinned one (`player_exe_sha256`, GOG 1.4.0.525(a)); an unchecked hash is no-data, and no label stands in;
  - without a hash, image labels that contradict each other (R13): S5's milestones.json `image` (and its stub
    boot's), meta.json `boot_image` and the full report's `- data image:` line must all agree, else the gate is
    no-data; an unknown label value contradicts a known one, and the report line alone can only fail the gate;
  - anything a try farm (`reports/try<k>of<N>`) published: they run agents' unmerged wip heads (R14). This holds for
    every reader, the per-farm reports and the frames, frameprof and frame-budget files alike;
  - `link/<c12>` boot files when the full build's own boot stage failed (fail) or did not run (no-data);
  - a `boot/summary.txt` line or a meta.json key for a milestone that `boot/milestones.json` gives (false or null);
  - a fuzz report titled `HARNESS BROKEN`, or whose `fnvnx-fuzz-summary/1` block names another shard or says
    `harness-broken`.
  - JSON of the wrong shape (a list for meta.json, a string for a scene) reads as empty.
- **Informative gates.** Gates marked `required: false` (the stock-clock frame gates, below) are shown, but do not
  count.
- **Verdict.** **READY** only when every counted gate passes on fresh evidence. A no-data gate keeps the verdict
  at NOT READY: missing evidence is not a pass.
- **Top blockers.** The scorecard lists the failing (or stale) gate with the lowest rank in each group, ordered
  by rank, five at most. The rank is the order in which the work unblocks the first launch: boot first, then
  what the boot runs into, then correctness at scale, then the tail. Missing evidence is listed separately.

## Scorecard

The scorecard is the single number per stage of how far the port is from done. `ready.py` derives it from the
gates, so every row cites the farm reports its gates read; the md report shows it right under the verdict, the JSON
under `scorecard` (`[{stage, what, value, target, gap, unit, status, stale, gates, sources, note}]`). The gates
job publishes it per integration head (`reports/gates1/<c12>-gates.md` / `.json`).

| stage | number | from gate(s) | target |
|---|---|---|---|
| compile | census errors (`errors total`) | compile.census | 0 |
| link | trial-link undefined names | link.undefined | 0 |
| link | guest functions bound to trap stubs | link.traps | 0 |
| ABI | x86-inconsistent pairs not explained + manual pairs | abi.x86_inconsistent, abi.manual | 0 |
| fuzz | hard divergences (real-bug + triage causes); functions in the note | fuzz.hard | 0 |
| equivalence | S11 functions + units that differ, not by design | equiv.code, equiv.nobuild | 0 |
| boot | milestones reached on the real exe | boot.* | 5/5 |
| render parity | S16 scenes matching the reference | frames.compare | all |
| frame budget | S15 worst frame, mid case (lo, hi in the note), at the `scorecard.frame_clock` | perf.budget.<clock> | 1000 / `frame_fps` ms |
| frame budget | GPU frame of the render model, efficiency-0.6 bound, `scorecard.gpu_config` | perf.gpu.<config> | 1000 / `frame_fps` ms |

- **gap** is how far the value is from its target (0 = done); `-` means no farm measures it yet (no-data).
- **Frame target.** The frame rows use `thresholds.json` `scorecard`: 60 fps at 720p handheld at the 1.02 GHz retail
  clock (J-0150), the same target as the frame gates.

## Trend and regressions (fnvnx-trend/1)

Merges into the integration branch are fast and do not run every test first, so the scorecard has to catch what
they break.
- **Trend.** Every scored head appends one entry `{head, generated, metrics: {<row id>: {gap, value, from, to}}}`
  (`from`/`to`: the oldest and newest source commit of the row). The gates job keeps the list in its JSON sidecar
  (`trend`, `trend_format: fnvnx-trend/1`) on the private repo, `reports/gates1/<c12>-gates.json`. Each run reads
  the newest sidecar of `thresholds.json` `trend.farm` and appends. A re-score of the same head that changes nothing
  replaces the last entry. The newest `trend.keep` (400) entries are kept.
- **Row ids.** compile.errors, link.undefined, link.traps, abi.rows, fuzz.hard, fuzz.s13, equiv.diffs,
  boot.milestones, render.scenes, frame.budget, frame.gpu.
- **Regressions.** A row whose gap rises from one entry to the next (`rose`), or whose measurement is lost (`lost`:
  a number before, none now), is a regression. It carries the range to bisect: from the earlier entry's oldest
  source commit to the later entry's newest, and the two heads. It stays open until a later entry is back at or
  below its `before`. The md report lists the open ones under "Regressions"; the JSON has them all
  (`regressions`).
- **Stale passes.** A stale pass does not count as a pass (counts: `stale`, not `pass`). A pass is stale when:
  - its source is more than `max_commits_behind` behind, or not on the head's history;
  - the farm published a newer report of the same job that FAILED or does not parse (`superseded_by`). The row
    of that stage is then not measured (`lost`), since its number is an older run's;
  - it is overdue: its source is behind the head and was published more than `report_due_s` (12 h) before the
    newest publish time.
- **Tests.** `python3 -I tests/gates/test_trend.py`, a synthetic set of regressions and stale passes. Its metric is
  `undetected N, false alarms M`, both 0.

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
| abi.x86_inconsistent | S2 x86-inconsistent pairs not explained | `link/<c12>/out/abi.tsv`, the kept link nearest the head; S1b's abiclass table for that link | 0 | a caller and callee disagree on the stack; explained: the class `pop-mismatch` (bridged), and S1b's verdicts `patched` and `benign-*` (below) |
| abi.manual | S2 pairs with a manual verdict | same | 0 | no automatic bridge exists for them |
| equiv.code | S11 functions whose code differs, not by design | `reports/eq1..6` | 0 | a prepare rule changed x86 semantics; by design: CTOR1 and the PATCHES.md "By-design MSVC differences" sites |
| equiv.nobuild | S11 units MSVC cannot compare, not by design | same | 0 | an uncompared unit is unverified; by design: ENUM1 |
| static.hazards | S12 open hazard sites | `reports/st1..4` | 0 | non-benign classes of `tools/static/report.py`; a class is closed while the build closes it: report.py's `CLOSED`, whose `build_closures()` reads compile.sh (the fnvnx_ftoi and fnvnx_order plugins, ORDER_ARGS, ORDER_KEYS), fnvnx_compat.h and sync.cpp, plus `static_closed` (file + regex) |
| static.gate | S12 shards over baseline / units GCC did not compile | same | 0 / 0 | an uncompiled unit was not checked |
| fuzz.hard | S13 functions whose worst cause is real-bug or triage | the `fnvnx-causes` block of `reports/fz1..26`, or `reports/s13b-causes:causes_all.json`, whichever covers more shards | 0, all 26 shards | the class of a cause comes from this checkout's `tools/difffuzz/causes.py` POLICY (an unknown cause is triage); by-design causes are not a divergence of the port; reports without a block fall back to the hard table and `fuzz_by_design_causes` |
| fuzz.artifacts | S13 functions whose worst cause is a harness artifact | same | informative | the harness or its oracle model, not the port; S13 fixes them |
| fuzz.coverage | functions with a verdict / with a native object | the reports' `fnvnx-fuzz-summary/1` block, else their text | >= 90 %, all shards | the rest is indirect control flow the fuzzer does not model yet; partial shards are named |
| boot.real_exe | the boot ran on the player's FalloutNV.exe | `meta.json` `boot_image_sha256` against the `FalloutNV.exe` line of `gamedata/exe:FalloutNV.exe.sha256`, which must be the pinned sha256; without a hash, S5's image label, else the report's data image line | yes | the synthesized image proves the loader, not the game |
| boot.winmain, boot.renderer_init, boot.main_menu, boot.first_frame, boot.first_cell | boot milestones on the real exe | `link/<c12>/boot/milestones.json`, see the formats below | reached | the first launch has to reach the first cell; a null milestone is no-data, the stub-fault boot never counts |
| boot.init_skips | static initializers skipped after a fault | full report | 0 | each one is a global left unconstructed |
| frames.compare | S16 scenes matching the reference | `reports/<farm>/<c12>-frames.json`: S16's `fnvnx-gate/1` or R0's first format | all scenes, gate status pass, candidate kind `port` | a scene that differs from the reference renders wrong |
| perf.frame.stock / perf.frame.boost | main-thread frame time from frameprof | `reports/<farm>/<c12>-frameprof.json` | 60 fps worst frame at 1.02 GHz, below | J-0150 |
| perf.boot | estimated device time to WinMain (stock clock) | same | <= 3 s | a black screen longer than this reads as a hang |
| perf.memory | peak guest heap / heap the guest can have | `meta.json` `boot_peak_heap_bytes` | <= 75 % of 1984 MiB | headroom for fragmentation and the cells after the first one |
| perf.cores | core placement | `toolchain/host/placement.h`, `toolchain/npdm/fnvnx-app.json` in the checkout | main on core 0, workers on 1-2, core 3 opt-in and off | PERF.md placement table |
| perf.render.stock / perf.render.boost | render machine: the renderer's worst thread as a share of each target's frame | `render/native/perf/report.json`, summary `targets_budget_fraction` (hi bound) | <= 20 % of the frame at the target | RENDER_NATIVE.md 9.4: render thread 4.5 of 22.2 ms, packet build 4.0 ms |
| perf.gpu.docked_720p / handheld_720p / handheld_540p | GPU frame time of the render machine's model | same report, `gpu.summary_ms.mix_hi` | three levels, informative | a model with no measured anchor (RENDER_PERF.md section 9) |
| perf.budget.stock / perf.budget.boost | S15 whole-frame CPU budget, scenario `placed` | `reports/<farm>/<c12>-frame-budget.json`, else `docs/PERF_budget.json` | worst frame, hi case, <= 16.7 ms on a measured frame (S15's verdicts are not used) | PERF.md section 11; a model-only or uncertain verdict is no-data |
| package.exefs | exefs = main + main.npdm, guest.nso on the SD tree | full report | yes | Atmosphere's exefs override layout |
| package.program_id | program id of an installed title | full report | not 0100000000000000 | the override needs a title that is installed |
| package.pic | guest PC-relative absolute references | full report | 0 | the guest must be relocatable (D9) |
| package.check | `tests/s5/check_package.py` on the package | `meta.json` `check_package_rc`, or `check_package: exit N` in the full report | exit 0 | the package checks that do not need a console |

## Frame-rate levels (J-0150)

The owner's target (J-0150, replacing J-0132's 45 fps target and 60 fps stretch): **60 fps, a 16.7 ms whole frame,
at the 1.02 GHz retail clock and 720p handheld**. Below 30 fps is a fail. 45 fps stays as an informational level only.

`thresholds.json` `perf.fps` holds the levels (`floor`, `info`, `target`), and `perf.clocks` the two CPU clocks:
1.02 GHz stock and 1.785 GHz boost. Each frame gate exists once per clock.

| level | frame | judged on | 1.02 GHz, IPC 0.8 | 1.785 GHz, IPC 0.8 |
|---|---|---|---|---|
| target 60 fps | 16.7 ms | worst frame | ~14 M instructions | ~24 M |
| floor 30 fps | 33.3 ms | worst frame | ~27 M | ~48 M |
| 45 fps (informational) | 22.2 ms | worst frame | ~18 M | ~32 M |

- **Status.** A frame gate passes only at the target. Its note gives every level (`target 60 fps no, floor 30 fps yes,
  45 fps (informational) yes`), and the JSON gives `levels: {floor, info, target, level}`.
- **Level names.** The level reads *fail* (under the floor), *below info* (under 45 fps), *below target* or
  *target*.
- **Every level on the worst frame.** A median frame at 60 fps no longer counts: one slow frame misses the target.
- **Main thread.** `perf.frame.<clock>` converts frameprof's main-thread instructions per frame to time at
  that clock and `perf.ipc`.
- **Render machine.** `perf.render.<clock>` reads the summary's `targets_budget_fraction`: the slower of the main
  side and the render thread, hi bound (IPC 0.6 at 1.02 GHz, mix_hi), divided by each level's frame time.
  - Its `targets` verdicts are not used: they compare the renderer alone with the whole frame, while the owner's
    target is for the whole frame.
  - The renderer has a share of the frame, `render_perf.max_budget_fraction` (0.2, from RENDER_NATIVE.md 9.4). A
    level is met when the renderer fits its share of that level's frame.
  - A report without the fractions gives them from `frame_ms` (gate values), scaled from the lowest
    `assumptions.clock_ghz`. (ASM-024)
- **GPU.** `perf.gpu.<config>` judges the efficiency-0.6 bound of the model's mix_hi frame against the levels.
  It is informative (`render_perf.gpu[].required`) until a console run measures the GPU.
- **Whole frame (S15).** `perf.budget.<clock>` takes the scenario as the build is placed today (`perf.budget`), not a
  plan.
  - The gate judges the scenario's `worst_frame_ms` itself against the target's frame (16.7 ms): pass when the hi
    case fits, fail when even the lo case misses, otherwise uncertain. S15's own `verdicts` are shown but not used:
    frame_budget.py judges a `stretch` target on the typical frame (V1 R16).
  - A pass counts only when the budget's inputs hold a measurement (frameprof or the render report).
  - `uncertain` and a model-only pass are no-data; `fail` fails.
- **Which clock counts.** The stock clock, 1.02 GHz (`required: true`). The boost clock is reported as informative
  (`required: false`).

## ABI verdicts of S1b (J-0141)

`tests/s1b/pop/abiclass.py` gives every call of an open x86-inconsistent pair a verdict (docs/PATCHES.md, "ABI
verdicts"). `thresholds.json` `abi_explained_verdicts` lists the ones that explain a pair: `patched`,
`benign-more`, `benign-unread`, `benign-nocall`, `benign-conv`. A pair is explained when every call of it has one.
- **benign-\*** has no native effect: extra words the callee ignores, words it never reads, no call at all, or only
  who pops differs.
- **patched** says the caller is an S1b site, fixed in the source, so the next link drops the row. The gate checks
  that claim against the patches: the site must be a hunk of `patches/*.s1b` at the head (else `patched-gone`), and
  not yet at the link's commit (else `patched-in-link`: the link had the patch and still has the row). When the
  link commit's patches cannot be read, the verdict is `patched-unverified`. All three stay open.
- The note gives the open rows by verdict (`fix-donor`, `fix-other`, `ecx`, ...; `no-verdict` for rows the table
  does not cover), and the JSON gives `abiclass: {verdict: rows}`.
- Tables: `ready.py --abiclass <dir>` (`<dir>/<link c12>.tsv`, from the gates job), else `link/<c12>/out/abiclass.tsv`.

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
- **Image (S5).** `meta.json` `boot_image_sha256`, compared with the `FalloutNV.exe` line of
  `gamedata/exe:FalloutNV.exe.sha256` (`<sha256>  FalloutNV.exe`, sha256sum format).
- **Precedence of milestones.** `boot/milestones.json` decides every milestone it names, even as null; meta.json
  keys and summary.txt lines only fill the ones it leaves out.
- **Peak memory (S5).** `meta.json` `boot_peak_heap_bytes`.
- **Package check (S5).** `meta.json` `check_package_rc`, or a `check_package: exit N` line in the full report.
- **Frames (S16).** `reports/<farm>/<c12>-frames.json`, either S16's `tools/compare/compare.py` gate file
  (`{"format": "fnvnx-gate/1", "status", "candidate": {"kind", "commit"}, "scenes": [{"id", "status"}]}`: a scene
  passes on status `pass`; `unstable` and `incomplete` do not; the candidate kind must be one of
  `thresholds.json` `frames.candidate_kinds`, default `port`), or R0's first format `{"commit": ..., "scenes":
  [{"name", "metric", "value", "threshold", "pass": true|false}]}`.
- **Frameprof (S15).** `reports/<farm>/<c12>-frameprof.json`: the output of `tools/perf/frameprof.py --json`
  (`[{label, instructions, ...}]`).
  - Frames are labelled `frame N`.
  - The boot segment ends with a label `until WinMain` (or `until __fnv_0086a850`).
- **Render perf (render machine).** `ports/fnv-nx/render/native/perf/report.json` on `claude/port-nsw-render`,
  else the copy merged into the checkout. The schema is `docs/RENDER_PERF.md` section 5.
  - The tool reads the summary block, at the top level or under `summary`: `{commit, per_draw_us, frame_ms: {main,
    render}, assumptions: {ipc, clock_ghz}, targets_budget_fraction: {"30"|"45"|"60": {"1.02", "1.785"}}}`. A range (ASM-024)
    `{lo, hi, gate}` is read by its gate value (= hi).
  - The GPU model is `gpu.summary_ms.<mix>.<config>: {eff_1.0, eff_0.6}` (`handheld_540p_dynres` counts as
    handheld_540p).
  - The distance is counted on the render branch (on the integration head for the merged copy).
- **Fuzz summary (S13).** The last fenced block `json fnvnx-fuzz-summary` of each fuzz report (fnvnx-fuzz-summary/1,
  the same object as the `<c12>-fuzz.json` sidecar): `shard`, `shards`, `status` (complete | partial |
  harness-broken), `functions.{with_native_object, verdicts, not_modelled}`. It must name the report's own shard.
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
