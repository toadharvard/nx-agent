# First-launch readiness (stream R0-gates)

The question this answers: can the owner copy the package to the console and expect the game to reach the main
menu and the first cell at an acceptable frame rate? Every stream measures something on the farms. R0 collects
those numbers into one scorecard, checks each against a threshold, and says what is still missing.

Tool: `tools/gates/ready.py` (thresholds `tools/gates/thresholds.json`, hardware risks
`tools/gates/hardware_risks.json`). Tests: `python3 -I tests/gates/test_ready.py`. Farm job:
`tools/farm/gates_job.sh`, which publishes `reports/gates1/<c12>-gates.md` and `.json`.

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
| static.hazards | S12 open hazard sites | `reports/st1..4` | 0 | non-benign classes of `tools/static/report.py`; a class is closed only while its fix is present in the checkout (`static_closed`: file + regex) |
| static.gate | S12 shards over baseline / units GCC did not compile | same | 0 / 0 | an uncompiled unit was not checked |
| fuzz.hard | S13 hard divergences not by design | `reports/fz1..26` | 0, all 26 shards | x86 and AArch64 results differ; by design: `x87-precision` |
| fuzz.coverage | functions with a verdict / with a native object | same | >= 90 %, all shards | the rest is indirect control flow the fuzzer does not model yet |
| boot.real_exe | the boot ran on the player's FalloutNV.exe | `meta.json` `boot_image_sha256` against `gamedata/exe`, else the report's data image line | yes | the synthesized image proves the loader, not the game |
| boot.winmain, boot.main_menu, boot.first_frame, boot.first_cell | boot milestones on the real exe | see the formats below | reached | the first launch has to reach the first cell |
| boot.init_skips | static initializers skipped after a fault | full report | 0 | each one is a global left unconstructed |
| frames.compare | S16 scenes matching the reference | `reports/<farm>/<c12>-frames.json` | all scenes | |
| perf.frame.stock / perf.frame.boost | main-thread frame time from frameprof | `reports/<farm>/<c12>-frameprof.json` | three levels, below | J-0132 |
| perf.boot | estimated device time to WinMain (stock clock) | same | <= 3 s | a black screen longer than this reads as a hang |
| perf.memory | peak guest heap / heap the guest can have | `meta.json` `boot_peak_heap_bytes` | <= 75 % of 1984 MiB | headroom for fragmentation and the cells after the first one |
| perf.cores | core placement | `toolchain/host/placement.h`, `toolchain/npdm/fnvnx-app.json` in the checkout | main on core 0, workers on 1-2, core 3 opt-in and off | PERF.md placement table |
| perf.render.stock / perf.render.boost | render machine: main / render thread frame time | `render/native/perf/report.json` on `claude/port-nsw-render` | three levels, below | J-0132 |
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
- **Render machine.** `perf.render.<clock>` takes the slower of `frame_ms.main` and `frame_ms.render` in the
  render machine's summary. It scales that from the report's `assumptions.clock_ghz` to each clock.
- **Which clock counts.** The owner's console can raise the clock, so the boost clock counts for the verdict
  (`required: true`). The stock clock is reported as informative (`required: false`). Set the stock clock to
  `required: true` to demand the target at stock.

## Formats the gates read (agreed with S5, S16 and the render machine)

- **Milestones (S5).** `link/<c12>/boot/milestones.json`: `{"winmain": 0|1, "main_menu": 0|1, "first_frame": 0|1,
  "first_cell": 0|1}`.
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
- **Render perf (render machine).** `ports/fnv-nx/render/native/perf/report.json` on `claude/port-nsw-render`. The
  schema is in that branch's `docs/RENDER_PERF.md`.
  - The tool reads its summary block, at the top level or under `summary`: `{commit, per_draw_us: {native,
    nine}, frame_ms: {main, render}, assumptions: {ipc, clock_ghz}}`.
  - The distance is counted on the render branch.
- **Sidecars.** `tools/farm/runner.sh` publishes a report's `.json` sidecar (`<c12>-<job>.json`) next to its `.md`,
  so a job can hand machine-readable numbers to the gates.

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

## Scorecard at 3a92098 (integration 3197103 + R0), 2026-11-04

Verdict: **NOT READY**. 5 pass, 17 fail, 9 no-data, 0 stale passes, of 31 counted gates; 2 more are
informative.

Top blockers, from the tool:

1. **Emulated boot.** No boot on the player's exe yet. The newest full report (`full1` ff0fd85) booted
   `synth.exe`. WinMain is reached, then a fault in a virtual function defined nowhere; 4,343 initializers are
   skipped.
2. **Link.** 3,811 guest symbols are bound to trap stubs, and the boot hits 6 of them (240 hits). The trial link
   has 4,468 undefined names.
3. **ABI.** 1,835 of 3,208 x86-inconsistent pairs are not explained (`ref-passes-fewer` 1,594), and 2,630 pairs
   have a manual verdict.
4. **Fuzz.** 2,177 hard divergences in 1,821 functions, with coverage at 69 %. Shards 1, 2, 4, 7, 8, 10, 11, 12,
   19 and 22 have no 26-shard report: those farms still run the 32-shard configuration, and their runs fail
   (rc 3) waiting for a link.
5. **Compile.** 2,935 of 2,978 units compile clean, and 57 units have no object.

Also failing:
- S11: 46 functions and 73 units not by design;
- S12: 14,777 open sites (VOLATILE-DECL, ORDER-ARGS, ORDER-OBJECT and BARRIER lead);
- 6 of 19 V1 audits;
- the placeholder PROGRAM_ID.

Missing evidence:
- the milestones after WinMain;
- S16 frames;
- frameprof (frame time and boot time);
- the render machine's report;
- peak memory;
- check_package.

Only 5 `link/<c12>` branches are kept. So the ABI table comes from `link/ea77051c97c7`, 22 commits behind.

## Changing a threshold

Change `tools/gates/thresholds.json` only with a journal entry that says why, and update this page. Shard counts
must match the farms' configuration: a farm on another shard count is reported as a missing shard, not merged.
