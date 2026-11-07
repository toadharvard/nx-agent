### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #7 tooling | status: wip | parent: J-0156

* **recon shard: not taken.** R4 declined the D23 recon shard: rebuilding function bodies from the retail exe's
  disassembly writes reconstructed copies of the game's own code, which ARCHITECTURE.md (Legal) says the decomp gives
  no licence for. The orchestrator reassigned the shard and gave this stream D25 #7 (project code only). Nothing was
  attached, fetched or read from fnv-nx-build or the exe.
* **did** (D25 #7: races, deadlocks, hangs; tools/threads/README.md)
  - **Wait-graph watchdog** (platform/win32/waitgraph.cpp, new; hooks in sync.cpp, thread.cpp, module.cpp; API in
    fnvnx_win32_ext.h).
    - Recording: each blocking wait records the thread's w32_wait_rec. That covers the critical-section slow path and
      every WaitFor* that does not return at once.
    - Scan: builds the wait-for graph, with edges for section owners, mutex owners and joined threads.
    - Deadlocks: the greatest set of threads that are stuck (all-wait / any-wait rules, stable over two scans). Each
      cycle is reported once.
    - Long waits: reported once each, over the limit.
    - Reports name a thread id, an object address and a caller address, nothing else.
    - Off by default: one load on each slow path. Turned on by `fnvnx_waitgraph_start`, or by
      `fnvnx_waitgraph_autostart` from tools/threads/autostart.c.
  - **Race checker for the real guest** (tests/s15/mtqemu/hb.c).
    - Races are aggregated per access pair (kind, the earlier and the later pc). `hb_dump` prints them as
      `hb-race` lines.
    - Game-sized bounds: `HB_SHADOW_LOG2` with home-slot eviction, `HB_READERS`=4 reads per granule, threads past
      64 untracked. All three are counted.
    - The wraps hand the game's call site to the watchdog.
    - `RACE=1` in tools/link/compile.sh adds `-fsanitize=thread`. The flag is part of the cache key, so it needs its
      own object directory.
  - **Lane**:
    - tools/threads/variant.sh relinks a pipeline build's guest the way its guest stage did, through the link
      script's EXTRA_OBJS, so toolchain/ is unchanged. It adds autostart.o, plus hb.o and the wraps with RACE=1.
    - tools/threads/report.py groups races per unordered function pair (function start addresses from nm) and lists
      cycles and long waits per caller function. It writes fnvnx-threads/1.
    - tools/farm/threads_job.sh runs test_mtqemu, then the guest (variant + boot_game.sh under qemu, or logs handed
      in with THREADS_LOGS), then the report.
  - **T1**: quick_job.sh check `threads` on lane A, after build. quick_plan.py class `threads` covers tests/s15/mtqemu,
    test_mtqemu.py, tools/threads and threads_job.sh; platform/ still selects everything.
* **measured** - Metric: the guest's races + cycles + long waits. Not measured yet: there is no guest run here (no
  pipeline build, no exe, by design). The farms' first quick run gives the first watchdog number. The race count
  needs a RACE=1 compile on the farm.
* **tests**
  - tests/s15 test_mtqemu: 17 cases, 0 failed. The new cases are race-pairs (the report maps the race to
    mtq_pair_writer/mtq_pair_reader), wg-clean (0 / 0), wg-deadlock (one section+mutex cycle, attributed to the two
    thread functions), wg-hang (one long wait).
  - tests/s4: 105 tests, 0 failed.
  - tests/t1 test_quick.sh: all ok.
  - tests/s7 does not build here: minimp3 is missing (tools/fetch.sh was not run). The failure is not from this
    change.
* **asks**
  - **S18**: in the scenario job, build the scenario guest with `tools/threads/variant.sh <build> <out>` and hand the
    run logs to `THREADS_LOGS=... THREADS_ELF=<out>/fnvnx.elf tools/farm/threads_job.sh <commit> <report.md>`. The
    S18 scenario job is not on any branch yet, so this lane is not wired there.
  - **Farm (D17)**: one RACE=1 compile + link of the integration head, run through threads_job.sh with
    THREADS_RACE=1, for the first race count. It takes longer than 10 minutes, so it is a farm job.
* **next**
  1. Wire threads_job into S18's scenario job when it lands.
  2. Triage the first watchdog report: cycles first, then long waits without a timeout.
  3. Reuse thread slots in hb.c (per-slot epochs), so long sessions with loading threads stay tracked.
