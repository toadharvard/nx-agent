### J-XXXX | 2026-11-04 | stream: P0-perfmodel | status: wip | parent: J-0149

* **did** - A Cortex-A57 cycle model that needs no device: `tools/perf/a57model` (docs/PERF.md section 12).
  - `a57sim.c` simulates the Tegra X1 core's caches (L1I 48 KB, L1D 32 KB, L2 2 MB), TLBs, prefetchers, store
    buffer, branch predictor and BTBs, and counts events per block and per memory instruction.
  - Two front ends drive it: a QEMU TCG plugin (`a57trace.so` in `qemu-system-aarch64 -M virt -cpu cortex-a57`,
    Ubuntu's 8.2 has `-plugin`) and Unicorn hooks over S5's emulator (`uctrace.py`, for the game guest).
  - `cost.py` prices blocks with `llvm-mca -mcpu=cortex-a57`, adds front-end events, load-miss latency (static
    dataflow separates dependent loads from overlapping ones), forwarding and bandwidth (Little's law).
  - `calibrate.py` runs 19 published A57 points (`calib/bench.c`, `calib/truth.json`: Chips and Cheese on the
    Switch's A57, 7-cpu, PTS tinymembench on Jetson Nano/TX1); 5 are fits, 14 checks.
  - `perf_report.py` writes fnvnx-perf/1 (`totals`, `hotspots[]`, `frame_hotspots[]`); `funcmodel.py` prices named
    guest functions statically; `tools/farm/perf_job.sh` is the farm job.
* **measured** - METRIC (mean error over the 14 check points): **64.0 % (flat IPC 0.8) -> 4.9 %**, max 50 % (llvm-mca
  prices ASIMD `mul .4s` at 6 cycles; measured 4).
  - Boot to WinMain, link fa883870e756 guest on its synth.exe: 62.2 M main-thread instructions, IPC 1.36, 44.8 ms at
    1.02 GHz (flat: 76.2 ms). fnvnx_scan_codeptrs 32 % (162,772 mispredicts).
  - perf_job.sh end to end on this branch (link 06857911f5ab, which has no boot/synth.exe, so no image): 18.7 M
    instructions to WinMain, IPC 1.08, 17.0 ms at 1.02 GHz; memset 29 % (bandwidth-bound), early_zero 23 %. The job
    now falls back to the owner's exe when a link has no synth.exe.
  - PERF.md 11.4's per-frame function rows now have an A57 price: coverage of the main thread's 23.8 M per frame
    **0 % -> 26 %** (6.2 M), 6.4-9.7 ms at 1.02 GHz against 7.6 ms at IPC 0.8. Math leaves run at IPC 1.1-2.6,
    refcounts and the per-object cull below 0.8.
* **limits** - The error bar covers the parts one at a time. Composition in compiled game code is not yet checked
  against hardware (read +-20-30 %). Not priced: the `dmb ish` store-buffer drain (the inlined InterlockedIncrement
  prices 13 cycles with or without a store in flight), contention between cores, the shared L2.
* **found** - Link fa883870e756's loader refuses the owner's GOG FalloutNV.exe ("pe_open: not a well-formed PE32
  executable") under tests/s5/emu_guest.py; link 06857911f5ab's loader accepts it (7 sections, IMAGE order). So
  perf_job.sh boots the owner's exe whenever a link has no synth.exe.
* **for the orchestrator** - Please start a farm `perf1` (`start.sh perf1 perf`, Haiku is enough). The calibration
  needs `qemu-system-arm`, `libglib2.0-dev` and QEMU's `qemu-plugin.h` (perf_job.sh fetches it from gitlab.com at
  the installed version). Without them the job uses the committed `calib/last.json` and says so. The job takes
  about 10 minutes.
* **tests** - tests/s15/test_a57model.py (dataflow, simulator, funcmodel, calibration limits check <= 6 %, fit <= 2 %):
  0 failed. tests/s15/test_perf.py: 0 failed. No rules touched.
* **next** - The 3-core schedule and lock model (mtqemu's placement and sync, per-core frame time). Then a mixed
  benchmark with a published A57 score for the composition error. Then the `dmb` drain and an ASIMD latency
  override table. Frames under uctrace.py once the boot passes WinMain.
