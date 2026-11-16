# recon2-005 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-004

* **metric**: shard 1 48 (correction: +2, see withdrawals); shard 7 85 -> 31 (54 closed).
* **shard 7 published** (fnv-nx-build, every unit all functions and bytes equal): recon/bsmenu_font (36 rows: bytes
  + bindings + 4 inline), recon/r8_idvspline (8: bindings + 2 inline), recon/r8_bstexmgr (3: 2 bindings + StubCmpXchg
  inline), recon/r8_magicball (1), recon/r8_ragdoll_q (3, bound to 0x00cb26e0 from recon/havok_hkquaternion),
  recon/r8_ce0000_cf8690 (2, bytes), recon/r8_ca0000_wa (WorkArray destructors, trap = 0 rows).
* **shard 1**: parser839's eraseTree body withdrawn and bound to the byte-matched 0x00acf0b0; parent / firstNode /
  lastNode became (c) inline (orchestrator: accepted).
* **withdrawn as no-ops** (`fnvnx-recon-pending/1`, not counted): recon/fallout_shared_tesrace,
  recon/staging_blk_00640000_od_006592f0, recon/r8_pathhandler, recon/r8_facegenmgr (15 rows). units.json at 87eb9373
  already maps every one of those names to the address, and REC1 adds symbols with setdefault, so nothing changes.
  - The traps come from link shape pairing (S2-bodies). Example: <va> is `?FUN_00559450@@YIHPAH@Z` (fastcall,
    one pointer) while 14 references are thiscall `??B` smart-pointer conversions. Both take the pointer in ecx (x86)
    and in x0 (AArch64).
  - Rule added to the R2 workers' brief: no binding for a name units.json already maps.
* **decisions to confirm**
  - StubCmpXchg and the 4 compare-exchange wrappers in font.cpp are D23 (c): the exe's only out-of-line wrapper,
    0x0043b460, takes (dst, comparand, exchange), and the decomp calls them (dst, exchange, comparand). A binding
    would swap the two values (the lock would spin), so each body reproduces the exe Lock's import call instead.
  - The thread-id bindings (0x0040fc90) and 0x00553300 / 0x00553320 follow precedent (10 units already bind there).
    The decomp declares those home functions void (garbagecollector.cpp `void QueryCurrentThread(bool)`) while the
    exe returns a value in eax. That return-value class belongs to EQ/F1 at the home units.
  - recon/bsmenu_font places 0x00401460 (CopyMemoryBlock, a memcpy wrapper, 25 bytes byte-equal). S4 lists it as
    "needs body" and 40 units map `_memcpy` there. A later recon placing it is skipped (REC1-DUP).
* **shard 7 open (31), by class**: vptr calls the decomp makes direct (MapKeyToHashIndex, MapIsKeysEqual,
  S00e340e0_vnum, dispatch); decomp placeholders and shell aliases of placed functions (S00e340e0_read,
  continueRayTraversal, ext_init / ext_free / ext_merge / ext_show); a dead duplicate (LBCase0 / 00 / 000);
  hand-stored image vftables (AgentFlush, AgentRelease, D26 (c)); link shape (Date::__vecDelDtor, FUN_00ecdf44 =
  __iob_func, plus the 13 withdrawn rows); a decomp shape bug (FUN_00aa13f0, mid-function, which should be the sized
  delete 0x00aa1460); addr-host thunks (__fnv_00d66200, __fnv_00d66220); __fnv_00b12890 (Vector3D operator* in
  idvvector.h: an MSVC by-value result vs GCC name pairing, to check on the native object).
