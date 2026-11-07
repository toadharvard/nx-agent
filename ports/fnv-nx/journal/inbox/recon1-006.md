### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-005)

* **did (shard 0, orchestrator items 2a-2d)** - All four are on fnv-nx-build. Each unit passes build_and_match with
  all bytes equal and compiles for AArch64. All four need code that already exists in the unit to change, which
  REC1's append cannot do. They therefore carry recon.json `replace` edits (format below).
  - 2a, recon/staging_blk_00da0000_..._rtm_variant: a symbols-only recon does not help here.
    - units.json already maps `?FUN_00da71c0@S00da8180_AabbCastMachine@@...` to 0x00da71c0.
    - The trouble is that GCC compiles the unit's own filler, and the unit's calls bind to it locally.
    - The replace keeps the filler for MSVC. For GCC it forwards to `__fnv_00da71c0`, which is MoppCastMachine's
      native traversal, with the same shape. visitLevel is then no longer referenced (nm).
  - 2b, recon/bsshader_shadowscenenode: for GCC, MultiBoundManagerB5E::RemoveBound forwards to
    `__fnv_00b5cff0(this, &ptr)`. ReleaseBoundB5E is then no longer referenced (nm).
  - 2c, same unit: for GCC, `S00b5eca0_List::FUN_00a5a310` forwards to `__fnv_00a5a310`, the native NiTPointerList
    AddTail, which does the NiPointer assignment with IncRefCount.
  - 2d, recon/fallout_shared_tesobjectcell: the decomp spells this call with its arguments on the wrong function.
    - In 0x0054fd70 the exe pushes (query, 1, 0) for 0x004f6e60 (ret 0xc). MSVC pushes the outer call's arguments
      first. The vtable slot +0x2c itself takes none.
    - The replace makes it `FindQuery()->FUN_004f6e60(query, true, false)`, and recon.json `symbols` maps the new
      MSVC name `?FUN_004f6e60@RegionAnswer@@QAE_NPAURegionQuery@@_N1@Z` to 0x004f6e60. MSVC still gives 325/325.
* **request (rules, orchestrator)** - REC1 needs to apply `replace`. Until it does, these four fixes do nothing.
  - Format: `"replace": [{"old": <exact text>, "new": <text>}, ...]`.
  - Apply the entries in order to the unit's text before the append. Each `old` must occur exactly once; otherwise
    skip the whole recon and count it as `REC1-REPLACE-MISS`.
  - Every `old` here occurs once in both the pristine decomp and the prepared tree at port 9f865b0a4037, so either
    rule position works.
  - This harness checks the same semantics: rtools try.py on the decomp, gcc_try.sh on the prepared tree.
* **did (shard 4)** - 4 units published, 33 rows:
  - bsmenu_tile3d: 17 forwarders.
  - staging_blk_00420000_od: 2 own-address bodies, byte for byte (0x0042f570 and 0x0042f5a0), plus GCC aliases.
  - speedtree_speedtreert_ob1_variant: 8 forwarders.
  - The other workers are still running.
* **correction** - recon1-005 said shard 4 has 42 units. It has 35.
* **metric** - shard 0: 2 -> 0 once REC1 has `replace`. Shard 4: 91 -> 58.
