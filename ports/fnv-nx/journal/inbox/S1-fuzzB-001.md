### J-XXXX | 2026-11-04 | stream: S1-fuzzB | status: wip | parent: J-0149

* **inputs** - fuzz/cache/{1..26}of26 (about 14:30; 22of26 present), link/c0d6c71516f6 (link-head), tree
  t/7b3b20231f0d (the link's tree), msvc/pristine/87eb9373a0bb, the owner's exe. Hand-off lists with names and
  reproducer paths are on the private branch `reports/s1-fuzzb` (README there).
* **metric** - S1-owned fuzz divergences, in functions, over the caches: falloff? + float + signedness.
  - **266 -> 99**: falloff? 176 -> 48, float 51 -> 19, signedness 39 -> 32.
  - Measured the same against the integration branch's own tools (2bce418): 266 there too.
  - The falloff? and signedness figures come from causes summary on the cached records. Float and the STAND2 item
    are measured by replay on link c0d6c71516f6: the cache keeps the old verdicts until the functions are fuzzed
    again.
* **did** - Commits on claude/fnv-s1-fuzz-b, each with `metric: before -> after`:
  - `d9cc589` **STAND2** (rule, platform class), merged as 57fe267.
    - STAND1 widened to an address that another unit defines (units.json `functions`).
    - Only where MSVC cannot have inlined the stand-in, so the oracle never ran its body:
      - a non-inline, non-static, non-virtual out-of-class or free definition;
      - in a unit compiled without /O1, /O2, /Ox or /Ob2;
      - every spelling of the name in the unit's symbols binds an address that another unit defines and this unit
        does not.
    - Sites on the pristine decomp: 196 in 105 units. STAND1 is unchanged at 168. 14 virtual members are left
      alone (vtable emission, kind-V pairing).
    - Replay: blk_00640000_od, one-unit resolve + apply. The link binds `GlobalObject_00869f00::Clear` to
      `__fnv_008454f0`. 006442e0-0/1/2 are gone, and a fresh 40-exec fuzz finds 0 divergences.
    - Correction to the commit text: the replay compiled from the rolling prepared tree (prepare of b6585b8), not
      t/7b3b. A force-push to amend the commit was refused.
  - `ee1cf13` **falloff? attribution** (report.reclassify). A call-count with no oracle call was always called
    falloff?, before causes.refine could see it. Two shapes have other causes:
    - `deleting-dtor-flags` (S13b's by-design cause on the integration branch; its _rule_callflow never saw these
      records): a `??_G` fuzzed with flags bit 0 clear, where the native D0 adds only deallocator calls. 42
      records: 38 falloff?, 4 native-extra-calls. The commit text says `dtor-flags`: that was the name before
      the rebase.
    - `native-undefined` / `after-native-undefined` (S2c/S2d): the native calls a named stub where the oracle ran
      the body. 107 + 2 records.
  - `774a36f` **float, harness**.
    - CRT math (log, log10, exp, pow, atan2, tan, atan, asin, acos, fmod, floor, ceil, sin, cos) runs as C99
      builtins on both machines, at the addresses the link gives libm providers. Before:
      - the log10 x87 `fyl2x` stub gave log10(0) = 0.0 under Unicorn;
      - the native gave NaN;
      - log, exp, pow, tan and atan2 got canned results.
    - An untyped intercepted call (a wild or unplanted vcall, or a C-only CRT spelling) now gives the oracle the
      canned value in ST0 where the code after the call consumes ST0 (`x86emu.x87_result_use`). The native always
      had it in v0. This was the actor.cpp group: `vf3` through `this-4`.
    - The port itself needs no change: the link already binds these names to libc.
    - Replay of the 51 float records: 2 gone at HEAD, 31 gone with this branch. A 200-record random sample of all
      causes replays the same at HEAD and with this branch.
  - `47727a1`, `d20b859` **signedness**. The top-bit probe also fires on by-design shapes, which now get their own
    cause:
    - a float argument within tolerance: x87-precision (007d2eb0, 00649390, 009a7c60);
    - a bool result 1 vs 0x80: bool-repr (004de080);
    - x86 #DE: division;
    - a native load across 4 GiB at the fuzzed pointer 0xfffffffe: ilp32-pointer (005ec8f0, 005ece20).
    - A first difference that calls a named stub is native-undefined (00b1be50, 0077f3c0).
* **checked** - tests/s1: 0 failed (3 new STAND2 cases). tests/s11: ok. tests/s13 causes, difffuzz and fuzzcache: 0
  failures (17 new cases).
* **for the orchestrator**
  - `fnkeys.HARNESS` is not bumped. The float fix reaches the caches only when a function's key changes; the 105
    STAND2 units re-fuzz anyway. Bump to difffuzz-14 with the next batch of harness changes if you want a full
    refresh. On the current caches it moves about 31 float records (and any wild-call float results elsewhere).
  - **_FORTIFY_SOURCE=3 is on in compile.sh.** Ubuntu's aarch64 g++ predefines it, and PERF.md item 10 already
    asked for `-U_FORTIFY_SOURCE`, but compile.sh lacks it. The link has 114 `__*_chk` references (54 sprintf,
    30 memset, 22 memcpy) in class "runtime".
    - These checks can abort where MSVC wrote past an object.
    - The runtime must provide them.
    - Locally, font.cpp with `OPT="-O2 -U_FORTIFY_SOURCE"` has 0 `_chk` symbols instead of 3. 00a12fb0's
      divergence moves (sprintf length) and does not go.
    - Adding it needs one full recompile: your call. Census and compile should agree.
* **for S2c / S2d** - 109 former falloff? records call 74 unbound names (`reports/s1-fuzzb/falloff_unbound_names.txt`).
  Largest families:
  - `BSStringT<char>::operator const char*`, global (12 functions) and per-namespace copies (`fnv_<addr>::`, about
    25, mostly animation.cpp, modelloader.cpp, tesobjectcell.cpp);
  - `NiPointer<T>::operator T*` for NiAVObject, AnimIdle, PathingSolution, NiNode, QueuedFile and others (about
    20);
  - decomp-local handle conversions (Flt, GO, StrH, StrOp, ObstacleMeshHandle) and a few destructors.
  Not faked here: they stay real bugs under native-undefined.
* **for S13**
  - `deleting-dtor-flags`: the 79 records of `dtor_flags.tsv` (37 were already yours through _rule_callflow).
    Fuzzing `??_G` with flags & 1, or pairing flags 0 with D1, would test them for real.
  - native-private-address, 5 signedness records (00a5b870, 00e48290, 00af9bf0, 00afa120, 00a672b0). Both
    machines compute the same wild address in 0x09000000-0x0bffffff. The oracle reads background there (it is
    the other machine's range, difffuzz-12), but the native faults because the address is its own private range.
    - Checked by replay: the oracle touches the same address.
    - Fix: either map unmapped holes of the native private range as background too, or classify a native fault
      there as an artifact when the oracle touched the address.
    - 00a358b0 (a native `blr` into its own range where the oracle's `call edx` is wild) looks the same.
* **for S1b** - 0047f940 and its group (falloff? "other", first effect none): the pop-mismatch shape.
  `GetChangeFlags(8)` pops only its hidden result pointer (`ret 4`), so the 8 becomes `FormChangeMask::operator()`'s
  mask. Natively, the flags are tested against themselves. Also 0069dfd0 (native-garbage-arg).
* **left in the metric (99)**
  - falloff? 48. Of these, 20 are already gone on link c0d6c71516f6 and drop at the re-fuzz. The rest are pop-
    mismatch (S1b), store/read differences, and the 0x00b436b0 bool byte through memory.
  - float 19. NaN vs inf on degenerate geometry inputs (00eb50f0, 00dea450, 00eaf310), a garbage return
    (00a26b40, 00c70550), and natively-read uninitialised stack slots of float out-parameters.
  - signedness 32:
    - 5 native near-null (S1-fuzzA's family);
    - 5 native-private (S13);
    - 4 gone on link c0d6c71516f6;
    - 3 native faults in its own range;
    - 00e30fb0, by design: an out-of-range float -> unsigned short with x87 53-bit intermediates;
    - the rest triage (stores, call counts).
* **next** - The largest bucket left is falloff? "other" and float garbage returns: effects-driven fixes, S1-owned
  ones first. Then rerun the summary on the caches after the farms re-fuzz the STAND2 units.
