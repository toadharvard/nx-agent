### J-XXXX | 2026-11-07 | stream: R4 + S4-platform (claude/fnv-recon4) -> #11 on the real package | status: wip | parent: recon4-011

* **did**
  - Ran tools/report/nro_check.py on full1's newest package, nro/0540aff0d154, with link/0540aff0d154's guest ELF and
    boot summary (in the container only). Before: 3 of 16 automated items failed.
  - **imports** (check_svc: `mrs ctr_el0` in the host and the launcher). The only reader is libnx's armDCacheFlush,
    which every libnx program and deko3d's flushes run. check_svc said Mesosphère leaves SCTLR_EL1.UCT clear. It
    doesn't: kernel_ldr/source/kern_init_loader.cpp sets `SctlrValue = 0x34D5D92D`, with UCT (bit 15) and UCI
    (bit 26) set. EL0 reads of CTR_EL0 are now allowed; writes stay refused. test_check_svc covers both.
  - **paths.policy**: false positives in my own tool. It took a launcher message (`sdmc:/atmosphere missing: ...`) as
    a path, and refused the parents the launcher checks (sdmc:/switch, sdmc:/atmosphere). It now takes the path
    token and allows those parents.
  - **memory.peak** fails, and the failure is real: the boot stops at WinMain (the frontier: a virtual function
    defined nowhere), so its 6.6 MiB peak isn't the game's. It passes once a boot reaches the first cell. That isn't
    platform work.
* **measured** - #11 on nro/0540aff0d154: 3 -> 1 (15 of 16 automated items pass; 11 manual items for the owner).
* **plat metric (#3/#4/#6)** - No plat-lane report exists. The quick build exits 128 before lane D, and the boots stop
  at WinMain, so almost no platform call has run yet. The only data is the raw boot's file faults, handled in
  recon4-011. I did not fan out subagents: without a report or a deeper boot they would have nothing to measure.
  Once the plat lane reports, or the frontier moves past WinMain, the split is unimplemented calls, file opens, and
  audio/input.
* **tests** - test_check_svc: 0 failed. test_nro_check: 36 ok, 0 failed (a message after the path, and the parents).
