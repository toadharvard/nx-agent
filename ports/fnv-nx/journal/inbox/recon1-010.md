### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-009)

* **did** - Shard 8: all 80 rows are closed, in 21 units on fnv-nx-build `recon/<unit>`.
  - Every unit passes build_and_match with its bodies, all functions and bytes equal.
  - Every body compiles with the game's AArch64 flags.
  - nm check per row:
    - 65 names are defined in the unit's object.
    - 10 are no longer referenced on the GCC side: recon `replace` edits make the unit's invented stand-ins
      forward to native definitions.
    - 3 are other units' names for 0x00462d40, which bind by address once that own-address body exists.
    - 2 are `__fnv_` aliases of the native D0 / restored definition.
  - Kinds: forwarders (most rows); 1 own-address body, byte for byte (0x00462d40, the _splitpath_s wrapper);
    inline reconstructions; GCC-only adjustor thunks, deleting destructors and template instantiations; and
    4 replace units.
* **replace (unit bugs fixed through REC1-REPL)** - each one GCC-only; MSVC is unchanged:
  - hkserializeutil: the stand-ins for 0x00d48a90 and 0x00d48760 forward to their native definitions. That drops
    5 invented Reader/Format members.
  - hkdataobjectnative: the two invented `FUN_00dbb260` fillers forward to `__fnv_00dbb260`. That drops DbbExtern,
    warnNativeC4D0 and allocateNativeC4D0.
  - treeengine: `~TagText` is the exe's `_Tidy(true, 0)` 0x0044a730 on the temporary, which drops TagTidy.
  - blk_00d20000 iso: GCC devirtualized `[vptr+0x58]` (0x00d2c9b8) to a declared-only placeholder. The object
    pointer is laundered, so the call stays virtual.
* **found (other owners)**
  - blk_00b20000_od: the native FUN_00b35f00 is declared `void`, but the exe returns the allocated pointer.
    shard 8's ShortVec::allocate uses the unit's own allocate model instead.
  - STAND1 also removed the pristine 0x0046ffd0 (blk_00460000_od): the tag sits above a NiTMallocInterface
    specialization. The body is restored GCC-only. This is the third STAND1 case, after hkmemutil and vatsmenu.
  - hkserializeutil: `S00d48d30_Reader::ForwardToReceiver()` is declared with no arguments. The exe passes a
    result pointer and ebx = options (0x00d48e05).
* **metric** - shard 8 rows without a body or binding: 80 -> 0. Accept split: (a) 80, (b) 0.
  No link or boot has run with these recons; the per-unit checks above are all.
