# recon2-012 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-011

* **STAND1 false positives** (S1 rules, f106af0d + 6699a197): found from topdown.tsv rank 939, __fnv_0073b730
  code-defmissing.
  - STAND1 dropped untagged definitions bound to a tagged address even when the unit's own units.json `functions`
    place that very name.
  - It now keeps such a definition when no tagged definition of the unit has the same name. An untagged overload of a
    tagged, placed name is still dropped (test).
  - Measured over every `@fnv` unit of the decomp, old rule against new: 3 units change, and every change keeps a
    placed function:
    - caravanmenu.cpp NPCHandArrayInfo::FUN_0073b730 and vatsmenu.cpp FUN_007f4f30: the tag sits on a virtual of
      another name (`..._card`, `..._check`);
    - tesscriptfunctions.cpp FUN_005b9ea0, FUN_005c4940, FUN_005d4400: the tag sits above `__declspec(thread)`
      declarations, which the tag-gap check does not accept, so the placed function looked untagged.
  - tests/s1: 0 failed.
* **S2-bodies** (05:27): ranks 2, 3 and 16 are closed on claude/fnv-s2d-bodies. 113 of its top 120 rows were bound
  in 0540aff0, i.e. the oscillation a3bf7fe8 fixes. It takes the re-ranked top 50 once a full1 link with a3bf7fe8
  lands.
