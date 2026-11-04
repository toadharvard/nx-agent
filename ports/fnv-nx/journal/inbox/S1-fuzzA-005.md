### J-XXXX | 2026-11-04 | stream: S1-fuzzA | status: wip | parent: S1-fuzzA-004

* **did**
  - `dda0d0c`: FALL1 now reads qualifiers that have several nested template arguments.
    - Before, `FNVNX_KEEP void NiTPointerListBase<NiTPointerAllocator<unsigned int>,NiPointer<BSMultiBound> >::AddTail`
      gave the "return type" `void NiTPointerListBase<...>`. That is not void, so FALL1 trapped a void function
      (0x00a5a310, nitpointerlistbase.inl), and the native hit `brk #0x3e8` after every AddTail.
    - Decomp-wide: FALL1 1156 -> 1155 sites. Exactly this trap goes and none is added.
    - Fresh fuzz with the .inl prepared both ways: a trap without the fix, clean with it.
    - The other GCC `brk #0x3e8` sites among the native-trap records are not FALL1:
      - after `bl _Unwind_Resume` (the intercepted noreturn shape, S13);
      - an assertion `__debugbreak()` reached through a canned result read as bool (S13).
  - `e8c5ced`: restores `FNVNX_EBO2` in shim/include/fnvnx_compat.h.
    - The s15-threads adoption `1d1fc46` took the stream's copy of that file and lost S1-refs' three EBO2 lines
      from `77868c1`.
    - EBO2 still writes the macro into 143 bases of 127 units, and these units no longer compile on the integration
      branch (tesobjectrefr.cpp, blk_004e0000_od.cpp, blk_00640000_od.cpp, ...).
    - With the block back, blk_004e0000_od compiles. The orchestrator has been told by message.
* **measured: the whole current toolchain** - 40 units, 72 functions that are native-null-read in the caches.
  - Setup: the integration branch's compile.sh (S12 order modes, `-fcheck-new`, ORDER_KEYS=unit), its rules
    (STAND2, STAND3, EBO2, FALL1 fix) and its fnvlink (S2d `8c2cfc2`), as a one-unit subset link per unit with 40
    iterations of 8 s.
  - Result:
    - 37 functions are clean;
    - 8 are still native-null-read;
    - 0 are native-trap;
    - the rest moved to measured causes: bool-repr 9, native-undefined 6, native-inlined 4,
      after-native-undefined 4, vptr-unpaired 3. A function can have several.
  - If the open list behaves like this sample, about 89 % of it leaves native-null-read once the farms fuzz a head
    linked with these changes.
  - Still null-read in the sample: 00891360, 00655270, 006c85e0, 0052baa0, 00ad1960, and 3 in the second batch.
    These are individual cases with no shared construct. One example: 0052baa0 faults in the array-destruction loop
    of `~FUN_0052bbd0` (tesimagespacemodifier.cpp).
* **metric** - Over the 15:50 caches: 666 at the re-baseline, 547 now (vslot-trap -118, FALL1 -1). The remaining
  reductions are measured locally and wait for the farms. The link farm has to reach a head with `8c2cfc2`, and the
  compile farms have to rebuild with `-fcheck-new` and the EBO2 fix.
* **next** - When the fz farms report a head linked after `e8c5ced`: re-derive the queue and take the largest
  remaining bucket.
