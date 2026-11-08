# recon2-010 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-009

* **duplicate-body recons (orchestrator)**: both fixed. Each unit compiles with the game GCC flags
  (`compile.sh --print-flags`) on the head tree with every recon applied.
  - recon/staging_blk_00460000_od: the GCC-only FUN_0045d930_class::FUN_0046ffd0 block is gone, because the prepared
    tree keeps that definition now. xSplitPath (0x00462d40) stays. build_and_match: 140/140 functions, 17658/17658 bytes.
  - recon/havok_common_base_memory_util_hkmemutil: withdrawn as `fnvnx-recon-pending/1`. The prepared tree keeps static
    CopyElementsBackward (STAND1 fix), so the restore had become a second definition.
* **REC1-DUP-DEF guard** (s1_compile.py, b56deb5b): an append that defines a namespace-scope function the prepared unit
  already defines (same namespace path, qualified name and normalized parameters) is rejected whole, like a REC1-REPL
  mismatch. It goes to stderr and to `build/recon_rejected.tsv` with count -2, and is counted in stats `REC1-REJECT`.
  tests/s1: 0 failed, d1-d5 added. Over all 283 recons on the head tree it flags exactly the 2 above.
