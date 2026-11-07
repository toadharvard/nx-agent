### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #7 tooling | status: wip | parent: recon4-001

* **did**
  - **Reading the report**: new section in tools/threads/README.md.
    - Headline counters: what `checks 0` means, and the undercount counters (untracked, evictions, read-evictions,
      pairs-full).
    - Races per function pair: look up names with addr2line in the container; fix order write-write, then
      write-read, then read-write.
    - Deadlock cycles: always real for that run, since only owned objects make cycles.
    - Long waits: owner-less events and semaphores show up here, and so do expected parked workers.
    - The suppressed and stale lists, and the verdict and metric of threads_job.sh.
  - **Known-benign suppression**: `tools/threads/report.py --suppress <file>` (repeatable).
    - Format: tab-separated `kind a b build reason`. The kind is a race kind, `race` (any of the three) or
      `long-wait`. A race entry takes its two function addresses in either order; a long-wait entry takes the
      caller and an object kind.
    - Kinds are a filter: a pair is hidden only when every kind seen on it is one the entry names.
    - The build column is the image's sha256 prefix or `*`, because function addresses move between builds.
    - The reason is required.
    - Suppressed counts leave the metric. They are listed with reason and hits, and entries that matched nothing are
      listed as stale. Cycles are never suppressed.
    - threads_job.sh passes `THREADS_SUPPRESS`, by default `$FARM_HOME/threads/suppress.tsv`. That file holds
      game-build addresses, which count as decomp-derived, so it stays with the farm's data or on fnv-nx-build,
      never in this repository.
  - tests/s15/test_threads_report.py (new, synthetic logs, no game data) covers:
    - cumulative periodic dumps (the last count per access pair);
    - unordered function pairs, cycles, long waits;
    - suppressions: kind subset, either order, long-wait by caller and object kind, the build column, stale
      entries, and refusal without a reason.
  - threads_job.sh runs it next to test_mtqemu. It is in quick_plan.py's `threads` class.
* **measured** - Metric (the guest's races + cycles + long waits): not measured here (no guest run in this container).
  full1's first threads_job run gives the first number. This commit is a named prerequisite: suppression makes the
  metric count only what is still to be triaged.
* **tests**
  - tests/s15 test_threads_report: 11 checks, 0 failed.
  - tests/s15 test_mtqemu: 17 cases, 0 failed (previous commit; nothing it builds changed).
  - tests/t1 test_quick.sh: all ok.
* **next**
  1. Triage full1's first report: cycles first, then INFINITE long waits.
  2. Start the farm's suppress.tsv from the expected parked waits, each with its reason.
  3. Reuse thread slots in hb.c (per-slot epochs).
