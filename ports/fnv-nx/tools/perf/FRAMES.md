# Per-thread CPU and frame pacing in emulator runs (frames lane)

This is groundwork for the 60 fps / 1.02 GHz target (D25 #12, D21). It shows where each frame's CPU goes, per
thread, while the real guest runs under qemu-aarch64. It is all project code. The reports name functions by
address only.

## Sampling: linhost --profile

`tests/s15/mtqemu/linhost.c` takes `--profile FILE [--profile-hz N] [--frame SYM]...`.

**Samples.**
- Every thread, the main one and each clone, gets a POSIX timer on its own CPU-time clock: CLOCK_THREAD_CPUTIME_ID
  with SIGEV_THREAD_ID, N Hz, default 1000. Each SIGPROF records the interrupted guest pc.
- Dropped ticks are counted back. qemu-user delivers signals at its own pace and merges ticks that arrive close
  together. The kernel's overrun count (`si_overrun`) is written as the sample's weight, so CPU time per thread
  stays right: the mtqemu frame loop measures worker ~100% and main ~50% at both 1000 and 250 Hz.
- This qemu (8.2) is built without TCG plugin support, so the sampler is a signal in linhost, not a plugin.

**Frame marks.**
- Each `--frame` function's first instruction is patched into a branch to a per-site trampoline. The trampoline
  saves what is live at a function entry (x0-x8, q0-q7, fp/lr), writes an `f` record, then runs the displaced
  instruction and branches back.
- A function that starts with a PC-relative instruction is refused, with a message.
- The lane passes the D3D9 device's Present.

**Output.** Text lines, buffered per thread and written when a buffer is full, when a thread ends, and at every
linhost exit:

    p <hz> <t0 ns>
    t <tid> <slot> <mask>
    s <tid> <t ns> <pc> [<weight>]
    f <tid> <t ns> <frame#>

**Limits.**
- qemu-user keeps 32 POSIX timers. Each thread deletes its own when it ends, so up to 32 threads can be profiled at
  once. A thread beyond that is reported (`timer_create failed 11`) and not sampled.
- CPU time is emulated time: the shares between threads are meaningful, the milliseconds are not device
  milliseconds.

## Analysis: tools/perf/frames.py

    frames.py <profile>... [--elf guest.elf] [--log boot.log] [--frame-tid TID] [--scale X] [--budget-ms 16.7]
              [--top 15] [--skip 1] --out report.md

**Frames.** The frame thread's marks cut the run into frames. The frame thread defaults to the thread with the most
marks. The first frame, the warm-up, is skipped.

**Per frame.**
- Wall ms.
- Each thread's CPU ms: its weighted samples / hz.
- Busy % per thread: CPU / wall.
- The critical-path thread: the one with the most CPU in the frame. With perfect overlap the frame can be no
  shorter than that.

**Report sections.**
- The frame wall ms median / p95 / max.
- A thread table: CPU, busy mean / p95, and how many frames each thread is critical in. Roles come from linhost's
  placement lines in `--log`.
- Top functions overall and for the critical thread, as function start addresses (nm).
- The ten slowest frames.

**Device estimate.** `--scale X` turns the critical thread's CPU into estimated device ms and counts the frames
over `--budget-ms`. X is a calibration: emulated ms → device ms at 1.02 GHz, for example from tools/perf's IPC
model and the emulator's measured rate. Without it, the report gives no device estimate.

**Output.** `<report>.json`, schema fnvnx-frames/1.

## The lane: tools/farm/frames_job.sh

1. `tests/s15/test_frames.py`:
   - synthetic profiles: frames, busy %, critical thread, scale and budget, a cut line, no samples;
   - live: the mtqemu frame loop under `linhost --profile`. The worker must be the critical path, about 100% busy
     against main's 50%, with `mtq_spin` its top function.
2. The full job's guest as built (`$FARM_HOME/full/build/guest/fnvnx.elf`, no relink), under `boot_game.sh` with
   `LINHOST_ARGS="--profile ... --frame <Present>"`, for FRAMES_TIMEOUT seconds (default 300; 180 in quick_job).
   - The Present symbols are found with nm: FRAMES_MATCH, default the null device's and the proxy's
     `IDirect3DDevice9 ... Present` and `IDirect3DSwapChain9 ... Present`. FRAMES_SYM names them directly.
   - FRAMES_PROFILES / FRAMES_ELF / FRAMES_LOG report profiles of a run made elsewhere (S18's scenarios).
3. frames.py, with FRAMES_SCALE once it is calibrated.

**Where it runs.**
- **T1:** quick_job.sh check `frames`, on lane A after build and threads. quick_plan.py selects it for
  tests/s15/mtqemu, test_frames.py, tools/perf/frames.py, frames_job.sh and tools/threads/report.py.

## Reading the report

- **No frame marks.** No Present was hit in the run: the boot did not reach rendering, or the symbol did not match
  (see `linhost: --frame:` in the boot log).
- **Which thread to work on.**
  - A critical-path thread that is critical in most frames is the one to shorten. Its top functions are where.
  - A frame whose wall ms is far above its critical CPU ms is waiting, not computing. Look at the threads lane's
    long waits for the same run.
  - Busy % above about 90 on one thread while the others idle means the work does not spread. That is an MT1
    candidate (docs/PATCHES.md).
