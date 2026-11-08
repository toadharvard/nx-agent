### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> boot milestones: the platform path archive-list..masters-loaded | status: wip | parent: orchestrator 09:45

* **The original's Win32 calls, per scenario (95e917a2).** bt1's trace/97deacd38aec traces game functions only and
  stops at its 1M-call cap inside memory init, so it has no Win32 calls to list.
  - tools/perf/plat_calls.py lists every import of the exe's own import directory: 280, where imports.tsv has 207
    slots, about a dozen of them named after the decomp. It writes a fnvtrace `dll!Export` list.
  - wine_scenario.sh traces that list in every scenario's Wine run. Each API logs its first 64 calls, then only
    counts (FNVTRACE_PERFN). Only calls from the game image count (FNVTRACE_CALLER). CreateFileA still logs every
    call for plat_wine.
  - Output: original_calls.{json,md} (fnvnx-plat-calls/1). Per API: calls, boot order, threads, return classes.
    Strings appear as hashes only. scenario_job.sh publishes them on scenario/<link12>.
  - boot_trace.py leaves API nodes out of the lockstep forest, so S5's compare is unchanged.
  - Arrives with the first scenario run after the merge. Tests: tests/s15/test_plat_calls.py, plus test_boot_trace.
* **Static coverage: no gap.** All 280 imports resolve: the boot log says "280 slots, 280 resolved". The ones
  exports.cpp's table doesn't name directly go through aliases, the ordinal map or S7's modules.
* **Where the boot stops (link-head 009faa8e).** 1 of 10 milestones (ini-read). The raw boot faults in the game's
  own heap (HeapFreeBlock::RemoveFromFreeList from operator delete). The stub boot runs into the wall-clock limit in
  BSSystemUtility::Destroy. Neither is platform yet. Asked S5 for the deadlock and its discovery rows.
* **Getting ahead: Win32 conformance review of the critical-path groups, 3 Sonnet workers, each in its own
  worktree with host tests in tests/s4.**
  - files: CreateFile dispositions and last errors, ReadFile at EOF, 64-bit SetFilePointer, FindFirstFile wildcards,
    path mapping;
  - threads and sync: event, semaphore and mutex semantics, WaitForMultipleObjects, thread handles signalled at
    exit, critical sections, TLS, Interlocked. Deadlock suspects first.
  - time, window, messages and input: QPC/GetTickCount, FILETIME, PeekMessage/DispatchMessage, ShowCursor,
    GetAsyncKeyState.
  - Each worker also reviews os_horizon.c's side by reading. I review every result before it lands.
* **Next.** Merge the reviewed fixes. Then, when the first original_calls.json lands, check every API the original
  calls up to main-menu against the guest: same order, same return classes.
