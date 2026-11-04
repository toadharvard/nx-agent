### J-XXXX | 2026-11-04 | stream: S1-fuzzq | status: wip | parent: J-0148

* **state** - Triage only. No rule and no code change yet; the stream moves to its own cloud session.
* **inputs used** - These are local and stay out of git:
  - link/0bd144db1464 and t/0bd144db1464, the newest link the fz farms use (fz7's a07b79c9dcb7 report names it);
  - msvc/pristine/87eb9373a0bb;
  - fuzz/cache/{1..26}of26 at about 13:47 (shard 22 has none);
  - the exe from /home/user/gamedata.
  - Note: the reproducer paths in `handoffs_S1.tsv` (fuzz/causes/ea77051c97c7) are stale. The caches have been
    re-fuzzed since (difffuzz-13), and the same file name `div/<addr>-<n>.json` now holds another divergence
    (for example, 0040f5c0-2 is now oracle-vcall). Re-derive the queue from the current caches with
    `causes._load_campaign`, which applies refine.
* **measured** - S1 causes over the current caches, in functions (the J-0137 figures in brackets):
  falloff? 164 (147), native-null-read 185 (111), native-trap 70 (7), signedness 35 (26), float 37 (18).
  - A recheck of 66 records on the local engine and link 0bd144: 64 still, 2 gone. So the cached verdicts are
    current enough to triage from.
* **found** - The causes mix real bugs with harness artefacts. Sub-families seen in the records:
  - **falloff?** (the rule is "call-count, x86 0 calls"; nothing is checked for a function end):
    - 101 records: the native calls a named stub, i.e. native-undefined reached through call-count. Example:
      `fnv_00490e40::BSStringT<char>::operator const char*`, 21 of them in animation.cpp. -> S1-refs/S1-STL or S2c,
      with the native-undefined list.
    - 41 records: `??_G` with flags bit 0 = 0. The x86 code does not delete; the native D0 always deletes. Harness
      gap -> S13: fuzz `??_G` only with flags & 1, or map flags 0 to D1.
    - 42 records "other". These include:
      - bool-repr through memory (00b436b0: a bool byte of 0x40; GCC uses tbz bit 0, MSVC tests the byte);
      - a decomp construct, **candidate rule STAND2**: an untagged out-of-class definition that units.json binds
        to an address tagged in ANOTHER unit. Example: blk_00640000_od.cpp `GlobalObject_00869f00::Clear` ->
        0x008454f0. MSVC (/Od) calls it by name, so the exe runs 0x008454f0. GCC inlines the local stand-in body
        (006442e0 ResetPointer: the oracle makes no call, the native calls delete).
      - STAND2 = STAND1 widened from "tagged in the same unit" to "tagged in some unit". Limit it to non-inline
        out-of-class or free definitions in units without /O1, /O2, /Ox or /Ob2. MSVC never inlines those, so the
        oracle never runs the stand-in body, and the rule is equivalent by construction. Measure its sites first.
  - **native-trap**: 70 records.
    - exc:1 (udf): the native falls into padding after a noreturn call that the harness intercepts and lets
      return: `__cxa_throw` (00b43490), `__cxa_throw_bad_array_new_length` from GCC's new[] check (00a63ae0,
      00e89460). Harness -> S13.
    - exc:7 (brk) at shared PCs around 0x0a043000..0x0a049000, the same PC for several functions of one unit:
      probably an abi_manual trap bridge in zz_bridges or a vtable pad. Check this; S2c if confirmed. Replaying
      0081d640 now gives `fault:r:0bc000dc`, a read past a padded vtable copy (vptr-unpaired, S2c).
  - **float**: 0082ffd0/0082fff0 call CRT log10 (0x00ecbc80). The oracle runs it as a builtin; the native calls a
    named stub, which returns garbage. Harness gap -> S13: make native log10/log/pow builtins. The actor.cpp group
    (x86 0.0, native garbage) is still to look at.
  - **signedness**: mixed.
    - 1-ulp F32 differences in call arguments (009a7c60, 007d2eb0, 00649390) look like x87 double rounding, i.e.
      tolerance by design.
    - 004de080 `return a && b` over bool globals with byte 0x80: bool-repr, soft.
    - The rest is not looked at yet.
* **next** - Use the tools in this order:
  - `difffuzz.py effects` over the native-null-read list (it was cut off here);
  - implement STAND2 with tests in tests/s1, then replay 006442e0 after a one-unit prepare, compile and apply;
  - hand the harness sub-families to S13 (dtor flags, noreturn intercepts, log10) so that they leave the S1 counts.
