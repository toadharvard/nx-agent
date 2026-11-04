### J-XXXX | 2026-11-04 | stream: S1-fuzzA | status: wip | parent: S1-fuzzA-003

* **re-baselined** - Measured over fuzz/cache/*of26 of 15:50: the fz farms' link is defb010a2a1b, and head fd686ae
  has a new harness and a new cause taxonomy. The integration branch's causes.py retires `new-null`.
  - Before my refinement: native-null-read 474 functions, native-trap 192, so the metric is **666**.
  - The caches cover more functions than at 14:00: 145 of the S1 functions are new.
* **did** - causes.py `vslot-trap` (`70719f2`; tests/s13 test_causes has 7 new checks).
  - A native-trap record whose pc, in the unit image, holds the link's trap stub becomes `vslot-trap`: real bug,
    owner S2c. The stub is `fnvnx_vslot_trap` (`brk #0xf5`) or `fnvnx_special_trap` (`brk #0xf6`).
  - The image is `FNVNX_CAUSES_A64`, by default the run's a64 directory. On a farm that is the shard's own image.
  - A pc that another image layout moved does not hold one of those two words, so the record stays native-trap. A
    record can therefore be missed but never routed to the wrong owner.
  - Result over the 15:50 caches, with my local images of 297 units: native-trap 192 -> 74 functions. The metric
    goes **666 -> 548**.
  - The remaining 74 native-trap functions:
    - 4 hit GCC's `brk #0x3e8`, a real `__builtin_trap` (FALL1), for S1.
    - 8 hit `udf` after an intercepted noreturn stub (S13).
    - The rest sit at pcs whose image moved. Request for S13: the runner should record the trap's symbol (the
      image symbol at the pc) in the divergence. With that, this refinement and S1-fuzzA-001's `noreturn-returned`
      would not need the image.
* **measured: former new-null functions (60, null-read again since the retirement)** - A/B of 17 of them in 8 units,
  current rules and link, with and without `-fcheck-new`.
  - 9 are clean in both arms. The flag changes nothing here.
  - These functions null-read after a non-allocator call. They join the open list.
* **status of the open null-read list** - 474 functions. Expected to drop when the farms fuzz a head whose link
  has STAND2, STAND3, `-fcheck-new` and S2d `8c2cfc2`:
  - about 90 (S1-fuzzA-003);
  - the 30 routed to S13 in S1-fuzzA-001/002;
  - 13 secondary-base overrides for S13, which S2c `73d8499` now makes enterable through the thunk.
* **tool note** - A one-unit compile that mixes this branch's rules with an older commit's shim fails with
  `FNVNX_EBO2` (blk_00a20000_o2, blk_00ba0000_o2). Use the same commit for compile.sh, the shim and the rules
  (`ORDER_KEYS=unit` for a local one-unit build).
