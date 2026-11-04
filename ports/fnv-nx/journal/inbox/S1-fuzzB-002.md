### J-XXXX | 2026-11-04 | stream: S1-fuzzB | status: wip | parent: J-XXXX (S1-fuzzB-001)

* **did** - `-U_FORTIFY_SOURCE` in the game build, as approved by the orchestrator. Ubuntu's aarch64 g++
  predefines `_FORTIFY_SOURCE=3`.
  - The flag goes into tools/link/compile.sh FLAGS, tools/census.sh FLAGS (a preprocessor flag: the census sees
    the same headers), tools/static/flags.sh BUILD_FLAGS, and tools/link/bodies.py CODEGEN.
  - New test tests/s1/test_fortify.py:
    - every flag list carries the flag;
    - a hand-written unit with memcpy, memset, sprintf and strcpy into a sized member, compiled with BUILD_FLAGS
      -O2, calls no `__*_chk`;
    - the control without the flag does call them.
  - **The recompile it rides on**: compile.sh is in compile_job.sh's FLAGS_HASH, so every compile shard rebuilds
    once. That is the same full recompile as S12's order-defaults revert 3f7d571 (object=first, operator=cpp17),
    if this lands before the farms pick that up; otherwise one more full rebuild.
* **HARNESS bump, for S13** (approved by the orchestrator; S13's file, to land with S13's own harness fixes):
  - tools/difffuzz/fnkeys.py line 27: `HARNESS = 'difffuzz-13'` -> `HARNESS = 'difffuzz-14'`.
  - DIFFFUZZ.md: add `**difffuzz-14**` to the paragraph "Float results of untyped calls and CRT math (S1-fuzzB,
    after difffuzz-13)".
  - Why: S1-fuzzB's harness changes (x86emu.x87_result_use, libcalls.CRT_MATH builtins, engine's preference for a
    decorated spelling) change verdicts of functions whose keys do not change. Replay on link c0d6c71516f6: 31 of 51
    float records go; a 200-record random sample of all causes is unchanged.
* **checked** - tests/s1 (test_rules, test_fortify): 0 failed. tests/s11: ok. tests/s12/run.py: the flag checks pass (BUILD_FLAGS == compile.sh, census.sh has every build flag); its one failure is fnvcheck, which needs llvm-18-dev in this container (unrelated).
* **metric** - 99 -> 99. The `__sprintf_chk` / `__memset_chk` records leave native-undefined after the recompile;
  none is in this stream's three causes.
