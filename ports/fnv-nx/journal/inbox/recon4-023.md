### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> boot milestones: INI phase, VM service, conformance, R2's table | status: wip | parent: recon4-022

* **INI phase (owner, urgent): 9f85ce61.**
  - The shim already cached the parsed file by mtime, so re-parsing was not the cost.
  - The cost was the work on every GetPrivateProfileString/Int call: a path re-map, and two linear scans of every line with std::string allocations, plus a third scan for [fnvnx] on every miss.
  - Now: a section/key index per parse and per write, where the first section and the first key of a name win as before, and a name -> file cache for directory-independent names. The stat per call stays, so an edit is seen at the next read.
  - Host: 28.54 -> 1.10 us per lookup on a 1,600-line file (tests/s4 ini_many_lookups). tests/s4: 170 tests, 0 failed.
  - Emulated before/after: guest trace-fn ret lines carry wall ns and plat_calls reports wall_ms per API (f7825f6b, now on S5's branch too). I asked S5 for the before run, since my local stub boot does not reach the INI phase. The after run needs a guest linked with 9f85ce61.
* **The emulator's host VM service (f23907fc..e94bf70a, opt-in --vm-service / FNVNX_EMU_VM=1).**
  - Without it, the guest refused every fixed-address VirtualAlloc reservation: 510 of 510 failed on link-head 009faa8e. The device's host_vm.inc honours them.
  - With it, plus S5's placement fixes, S5's D26 stub boot loses its GetSaveDirectory fault and reaches the same stop.
  - It does skip 31 initializers. The first is a CRT free of 0x3000fe0, a block in the game's own pool at 0x3000000, reached through operator delete -> FNV_MemoryManager::FUN_00aa4060.
  - So the memory-manager stand-ins don't know the pools the game now really builds. That's with S5, and the service stays off by default.
  - tests/s5/test_emu_vm.py: 26 checks.
* **Win32 conformance (3 Sonnet workers, reviewed and cherry-picked: 36393f70, 56de805c, 539b96ef).**
  - Files, 9 fixes: ReadFile loops over short reads, access checks, wildcard names, CopyFile onto itself.
  - Sync and threads, 6 fixes: WAIT_ABANDONED, wait-all with duplicate handles, the self-freed thread leak on Horizon (read-reviewed), TlsFree.
  - Window, time and input: WM_NCCREATE/WM_CREATE at creation, activation on the first show, DefWindowProc answers, the GetAsyncKeyState pressed bit, SystemTimeToTzSpecificLocalTime with a zone.
  - Nothing in the sync group explains the boot deadlock. S5 traced it to a skipped initializer inside libc malloc, and their emulator now marks such boots libc-heap-undefined.
* **Win32 call baseline (95e917a2, c2fe5f86, 39b7e734).** Every scenario Wine run traces the exe's 280 imports (original_calls.json). The guest is traced by platform names, and plat_diff compares the two in boot order. This arrives with the first scenario run after adoption.
* **R2's 155-trap table.** Sent R2 my rows with status:
  - 10 runtime providers, fixed at head;
  - the STL rows, covered by R2's recons. STL1/STL2 step aside for recons and units.json bindings (70764970, a15ba4f6), and the facet and widen are now separate (263d0c6e: rank 87 closed by widen);
  - 2 abi-manual pairs proved layout-equal by R2, now bound directly: tools/link/abi_layout_equal.tsv (97bfda0b).
* **Pending.**
  - Adoption of claude/fnv-recon4.
  - The emulated INI before/after.
  - S5's stand-in update for --vm-service.
  - The first original_calls.json and plat_diff.
