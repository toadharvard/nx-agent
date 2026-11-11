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
| guest | `tools/threads/variant.sh <pipeline build> <out>` | relinks a build's guest exactly as its guest stage did (same script, link outputs, override archive, D3D9, GAME_NEW), adding autostart.o, plus hb.o and the wraps with `RACE=1`, through the link script's `EXTRA_OBJS`; the build directory is not touched |
| run | `tests/s15/mtqemu/boot_game.sh <elf> <exe> <work>` | runs the guest under qemu-aarch64 on real host threads |
| report | `tools/threads/report.py <logs> --elf <elf> --out r.md` | races per unordered function pair, deadlock cycles and long waits per caller function; each pc is mapped to its function's start address with nm; also writes `r.json` (fnvnx-threads/1) |
| job | `tools/farm/threads_job.sh <commit> <report.md>` | `tests/s15/test_mtqemu.py`, then the guest (variant + boot, or `THREADS_LOGS` handed in), then the report |

**Metric.** races + cycles + long waits, D25 #7's zero target.

**Where it runs.**
- **T1:** quick_job.sh check `threads`, on lane A after build, on its build directory. quick_plan.py selects it for
  platform/, tests/s15/mtqemu, tools/threads and threads_job.sh changes.
- **S18:** the scenario job builds its guest with variant.sh, runs its scenarios, and hands the run logs in:
  `THREADS_LOGS="<log>..." THREADS_ELF=<guest> tools/farm/threads_job.sh <commit> <report.md>`.

## Reading the report

The lane writes `threads.md` (and `threads.json`, fnvnx-threads/1). Read it top down.

1. **Headline.**
   - The first line gives races, function pairs, deadlock cycles and long waits.
   - `race checker:` and `watchdog:` give the checkers' own counters.
   - The no-coverage case: `checks 0` in a RACE run means the game objects were not compiled with RACE=1, so the
     race count says nothing.
   - Undercounting: non-zero `untracked`, `evictions`, `read-evictions` or `pairs-full` means races may be missing.
     They never invent one.
   - `build` is the image's sha256 prefix, for the suppression file.
2. **Races per function pair.**
   - One row per unordered pair of function start addresses: the kinds seen, how many races, how many distinct
     access pairs (pc pairs), and one sample data address.
   - Look up a function with `aarch64-none-elf-addr2line -f -e <guest.elf> <address>` in the container. The report
     itself never carries names.
   - `?` means the earlier access is unknown: its reader was replaced, or the granule was evicted.
   - Order of work: write-write first (lost updates), then write-read (stale or torn reads), then read-write.
   - Many access pairs in one function pair usually means a structure shared without a lock, not a single field.
3. **Deadlock cycles.**
   - Each cycle lists its threads in order. Each thread waits on an object (cs / mutex / thread, its address) in a
     caller function, and the object is owned by the next thread.
   - Only owned objects make cycles, so every cycle is a real lock-order or join-order bug for that run.
   - Fix by ordering the locks, or with a timeout plus a retry the game already has.
4. **Long waits per caller.**
   - Waits older than the limit (THREADS_LIMIT_MS, default 10 s), grouped by caller function and object kind.
   - "without timeout" counts the INFINITE ones.
   - An event or semaphore that nobody signals shows up here: the watchdog cannot see who should signal it.
   - Some long waits are expected: an idle worker parked on its event, a loading thread between loads. Suppress
     those, with a reason.
5. **Suppressed.** Every entry that applied, with its count and reason, then the stale entries that matched
   nothing. A stale entry usually means a new build moved the functions: renew it or delete it.

`threads_job.sh`'s verdict line:

| verdict | meaning |
|---|---|
| FAIL | an unsuppressed race or cycle remains |
| PASS | no race and no cycle remains; long waits are listed but do not fail the run |
| no guest data | the guest did not run |
| FAIL (checker tests) | the checkers' own tests failed, so the numbers mean nothing |

The metric is races + cycles + long waits, after suppression.

## Suppressing known-benign entries

`report.py --suppress <file>` (repeatable). threads_job.sh passes `THREADS_SUPPRESS`, or
`$FARM_HOME/threads/suppress.tsv` when that file exists. The format is tab-separated, and `#` starts a comment:

    # kind      a           b           build         reason
    race        0x0012a3c0  0x0012b100  3f9c2a1e77d0  frame counter read for the HUD only; torn value harmless (S15, J-...)
    long-wait   0x00456780  event       *             task worker parks on its queue event between frames

| column | meaning |
|---|---|
| kind | `write-write`, `write-read`, `read-write`, `race` (any of the three), or `long-wait` |
| a, b | for a race, the two function start addresses, in either order; for a long wait, `a` is the caller function and `b` is the object kind (`cs`, `mutex`, `event`, `semaphore`, `thread`) or `*` |
| build | the report's `build` value, or `*` |
| reason | required: why the entry is benign and where that was decided |

- **Kinds are a filter.** An entry hides a pair only when every kind seen on the pair is one the entry names. A
  write-write showing up on a pair suppressed as `write-read` is reported again.
- **Pin the build.** Function addresses change between builds, so prefer a build-pinned entry. A `*` entry can
  hide a different function after a relink; use it only for addresses that do not move (fixed-image code).
- **Where the file lives.** A suppression file holds addresses of a game build, which ARCHITECTURE.md (Legal) treats
  as decomp-derived. It lives with the farm's data or on the private artifacts repository, never in this
  repository. tests/s15/test_threads_report.py checks the format on synthetic addresses.
- **Cycles are never suppressed.**
