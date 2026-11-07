# tools/threads: races, deadlocks and hangs in emulator runs (D25 #7)

Two checkers, one report lane. All of it is project code; the reports name functions and objects by address only.

## The race checker (tests/s15/mtqemu/hb.c)

A happens-before checker for ILP32 guest code, as the runtime behind GCC's `-fsanitize=thread` calls (docs/PERF.md
6.6). Edges come from C11 atomics by memory order, fences, and the Win32 sync calls, which are wrapped at link time.
For the real guest:

- **Pairs.** Every race is aggregated by its access pair: kind, the pc of the earlier access, the pc of the later
  one. `hb_dump()` prints one `hb-race <kind> prev 0x.. cur 0x.. addr 0x.. n N threads a b` line per pair and a
  summary line `hb: summary races .. pairs .. checks .. threads .. untracked .. evictions .. read-evictions ..`.
- **Bounds.**
  - The shadow table has 2^`HB_SHADOW_LOG2` granules (17 for mtqemu, 20 for the game). When a probe finds no free
    slot, the home slot is evicted.
  - A granule keeps its last write and up to `HB_READERS` (4) last reads. A further reader replaces one of them.
  - A thread beyond 64 runs untracked; CreateThread no longer fails for it.
  - All three are counted. They can hide a race, never invent one.
- **Build.** `RACE=1 tools/link/compile.sh` compiles the game objects with `-fsanitize=thread`. Use its own object
  directory: the flag is part of the cache key. `RACE=1 tools/threads/variant.sh` then links hb.c and the
  `--wrap`s into the guest. A full RACE compile is a farm job (D17).

## The wait-graph watchdog (platform/win32/waitgraph.cpp)

**What is recorded.** Every blocking wait in the Win32 stand-ins records the waiting thread's `w32_wait_rec`
(platform/win32/w32.h). That covers the slow path of a critical section and every WaitForSingleObject /
WaitForMultipleObjects that does not return at once. A scan builds the wait-for graph, with one edge from the waiter
to each owner:

- a critical section → its `OwningThread`;
- a mutex → its owner;
- a thread handle → that thread;
- an event or semaphore → no owner.

**Deadlocks.** The scan takes the greatest set D of waiting threads that are stuck, counting only waits that were
the same at the previous scan:

- an all-wait (one object, a section, or bWaitAll) is stuck when one of its objects has an owner in D;
- an any-wait is stuck only when every alternative is owned by a thread in D.

Each cycle in D is reported once, as a `wg: deadlock cycle ...` line.

**Long waits.** A wait older than the limit is reported once, as a `wg: long-wait ...` line. That catches a
thread waiting for an event nobody sets.

**Cost.** With the watchdog off, each slow path pays one load. The recorded caller is the Win32 call's return
address. In a race build, hb.c's wraps hand the game's own call site through `fnvnx_waitgraph_set_caller`.

**API** (platform/include/fnvnx_win32_ext.h):

| function | what it does |
|---|---|
| `fnvnx_waitgraph_start(limit_ms, period_ms, sink)` | turns recording on and starts a scanner every `period_ms` |
| `fnvnx_waitgraph_scan` | one scan, now |
| `fnvnx_waitgraph_stop` | stops the scanner thread (recording stays on) |
| `fnvnx_waitgraph_counts` | cycles, long waits and scans so far |
| `fnvnx_waitgraph_summary` | prints the `wg: summary` line |
| `fnvnx_waitgraph_autostart(limit_ms)` | from a constructor: tools/threads/autostart.c |
| `fnvnx_threads_flush` | runs every 10 s and at ExitProcess; prints the race pairs and the watchdog summary |

## The lane

| step | tool | what it does |
|---|---|---|
| guest | `tools/threads/variant.sh <pipeline build> <out>` | relinks a build's guest exactly as its guest stage did (same script, link outputs, override archive, D3D9, GAME_NEW, Vorbis), adding autostart.o, plus hb.o and the wraps with `RACE=1`, through the link script's `EXTRA_OBJS`; the build directory is not touched |
| run | `tests/s15/mtqemu/boot_game.sh <elf> <exe> <work>` | runs the guest under qemu-aarch64 on real host threads |
| report | `tools/threads/report.py <logs> --elf <elf> --out r.md` | races per unordered function pair, deadlock cycles and long waits per caller function; each pc is mapped to its function's start address with nm; also writes `r.json` (fnvnx-threads/1) |
| job | `tools/farm/threads_job.sh <commit> <report.md>` | `tests/s15/test_mtqemu.py`, then the guest (variant + boot, or `THREADS_LOGS` handed in), then the report |

**Metric.** races + cycles + long waits, D25 #7's zero target.

**Where it runs.**
- **T1:** quick_job.sh check `threads`, on lane A after build, on its build directory. quick_plan.py selects it for
  platform/, tests/s15/mtqemu, tools/threads and threads_job.sh changes.
- **S18:** the scenario job builds its guest with variant.sh, runs its scenarios, and hands the run logs in:
  `THREADS_LOGS="<log>..." THREADS_ELF=<guest> tools/farm/threads_job.sh <commit> <report.md>`.
