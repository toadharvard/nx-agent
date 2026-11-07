### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> frames profiler | status: wip | parent: recon4-002

* **did** (groundwork for 60 fps at 1.02 GHz; tools/perf/FRAMES.md)
  - **Sampler in linhost** (tests/s15/mtqemu/linhost.c `--profile FILE [--profile-hz N] [--frame SYM]...`).
    - Each thread gets its own CPU-time timer (timer_create CLOCK_THREAD_CPUTIME_ID, SIGEV_THREAD_ID, SIGPROF). The
      handler records the guest pc.
    - The installed qemu 8.2 has no TCG plugin support, so sampling is a signal in linhost, not a plugin.
    - qemu-user merges close ticks. The kernel's si_overrun is written as the sample's weight, which keeps CPU time
      right: in the mtqemu frame loop the worker reads ~100% busy and main ~48% at 1000 Hz, and 99% / 49% at
      250 Hz. Before weighting it read 25% / 13%.
    - Each thread deletes its timer at its end (qemu-user keeps 32).
  - **Frame marks**: `--frame` patches the function's entry into a trampoline that records the mark and runs the
    displaced instruction. PC-relative first instructions are refused. Records are text lines, buffered per thread
    and flushed at every exit path.
  - **tools/perf/frames.py**:
    - frames cut at the frame thread's marks (warm-up skipped);
    - per frame: wall ms, CPU ms per thread, busy %, the critical-path thread;
    - top functions overall and for the critical thread (function start addresses from nm, tools/threads' Symbols);
    - the slowest frames, and placement roles from linhost's log;
    - `--scale` / `--budget-ms`: a device-ms estimate and the over-budget count, once a calibration exists;
    - JSON output fnvnx-frames/1.
  - **Lane**: tools/farm/frames_job.sh.
    - Step 1: test_frames.py.
    - Step 2: the full job's guest as built (no relink) under boot_game.sh. The new LINHOST_ARGS passes
      `--profile` and `--frame <Present>`; Present is found by nm (FRAMES_MATCH / FRAMES_SYM).
    - Step 3: frames.py. FRAMES_PROFILES takes S18's profiles instead.
    - quick_job.sh check `frames` runs on lane A after threads. quick_plan.py class `frames` covers
      tests/s15/mtqemu, test_frames.py, tools/perf/frames.py, frames_job.sh and tools/threads/report.py.
* **measured** - Metric (the guest's frame time against 16.7 ms): not measured here; there is no guest build or exe
  in this container. Prerequisite for D25 #12. The first frames_job run on full1 gives emulated per-thread shares.
  Device milliseconds need a FRAMES_SCALE calibration.
* **tests**
  - tests/s15 test_frames: synthetic checks plus the live mtqemu frame loop. The live run is critical worker in 29 of
    29 frames, mtq_spin on top.
  - tests/s15 test_mtqemu: 18 cases (new: frames).
  - tests/t1 test_quick.sh.
  - Results in the commit body.
* **asks**
  - **S15-perf**: a FRAMES_SCALE calibration (emulated ms -> device ms at 1.02 GHz). One way: frameprof.py's
    instruction counts against these samples on the same run, through budget_model.json's IPC.
  - **S18**: hand in `FRAMES_PROFILES` / `FRAMES_ELF` / `FRAMES_LOG` from the scenario runs (`linhost --profile`).
* **next**
  1. The first full1 report: critical thread and top functions per scene.
  2. Per-phase marks (`--frame` for the main loop's phases) once the frame path is known.
  3. Sampling the guest's own call chain (frame-pointer walk in the handler) for inclusive time.
