### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-0156

* **shard** - D23 recon shard 0 of 10: rows of `tools/link/bodycause.py` with cause `no-body` on link/9f865b0a4037
  (link-head; the newest link on fnv-nx-build), whose unit gives `int(sha1(unit).hexdigest(), 16) % 10 == 0`.
  - The unit of a row is the first entry of its `units` column (the referencing units, sorted). Other recon streams
    should use the same rule, so no row falls between two shards or into two.
  - bodycause ran with the integration head's tools/link/bodycause.py on that link's out/ and fixed/, and on a tree
    prepared locally with tools/prepare.py at port 9f865b0a4037 (the link's port commit).
* **measured** - bodycause on link/9f865b0a4037: missing 1,142 = crt-import 126 + data 37 + name-mismatch 110 +
  not-compiled 43 + no-body 826.
  - no-body by shard (0..9): 61, 121, 121, 53, 63, 36, 131, 93, 82, 65.
  - Shard 0 (this stream): 61 functions in 24 units (largest unit 13 functions, then 9, 6, 5, 4; 14 units with
    one). By detail: other 30, msvc-direct 13, exe-shape 11, template-special 7.
* **metric** - functions of shard 0 still without a body: 61.
* **setup** - MSVC 9 (toolchain/msvc9, sp1 and rtm) runs under Wine 9 in this container. build_and_match on
  src/bscore/memoryheap.cpp, pristine: 17 of 17 functions match.
* **next** - Work the shard unit by unit, largest first (the bsmenu tile unit, the Havok contact-point unit, the
  shadow scene node unit). Each unit is pushed to fnv-nx-build `recon/<unit>`.
