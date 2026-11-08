### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-010

* **R2's trap list (fnv-nx-build s2d/list/5fd996a4127f status69.tsv): the 8 S1 rows are all fixed, pending the
  next link.**
  - rank 32 `fnv_00b2d2e0::TagEmpty`: rule EMPTY1 (7cbc5a8). The exe's 0x00b09480 builds that arm from "" (0x01011584)
    through 0x0044a4c0, so GCC gets `TagText("")`; MSVC is unchanged.
  - ranks 45 / 46 hkpPhantomDisplayViewer::PhantomAddedCallback / postSimulationCallback: recon/s1_phantomviewer_listeners
    (495ef4c). These are GCC-only forwarders to the exe's secondary-vftable functions, which the unit already defines:
    0x00d66530 through the listener subobject (+40), and 0x00d667b0 (+44, less the shell's 32). Both compile, at
    native offsets 0x28 / 0xc.
  - ranks 60 / 61 hkpWeldingViewer::WorldAddedCallback / WorldRemovedCallback: recon/s1_weldingviewer_listeners
    (52e4a98). Exe 0x010d15f4 slot 1 is 0x00d62780 (the unit's FUN_00d62780); slot 2 is 0x00a89af0, an empty ret 4.
  - ranks 51 / 69 ae60_mix, obbMix: recon/s1_geometryutility_rtmfull_standins (c0700bb), 3 replace pairs.
    - The rtmfull unit's harness stand-ins (FUN_00d5ae60 in two shapes, FUN_00d5b990) become GCC-only declarations
      asm-labelled to __fnv_00d5ae60 / __fnv_00d5b990.
    - Evidence: the sibling unit's recipe binds the same shells there. Every argument is a scalar, pointer or one-int
      struct, so they keep their positions.
  - rank 68 heuristicSampling: recon/s1_gsk_heuristicsampling (3fbdbe9). It is asm-labelled to __fnv_00e62740 (exe
    e4c512: cmp eax,5 / six pushes / call 0x00e62740), which hkcvxcvxdistbyheuristicsampling_rtm_variant.cpp defines.
  - Each recon applies with REC1 0 rejects, and both units compile with no reference to the trap names left.
* **D22-SLOTK (3e9ecce)**: the TESForm family's deleting dtor is at exe slot 4, not slot 0 (S2 vtmetric, 67 rows).
  _d22_classes.py --dtor-slots wrote 157 rows to recon/d22_vtable_dtor slots/d22.json (141 at k = 4). The k-th
  virtual converts, never slot 0, and RAWVT1 shifts only k > K.
* **Census with the main + s5 + rtti lists** (before SLOTK): D22 459 -> 624, D22-CALLONLY 10,930 -> 10,798, RAWVT1 61.
  The run with SLOTK is in progress.
* **START (farms)**: link + boot on the integration head with claude/fnv-s1-rules 7cbc5a8 adopted and every
  recon/* (recon.sh fetches all). Expected: R2's 8 S1 trap rows 8 -> 0, and S2 vtmetric TESWorldSpace /
  ArrowProjectile 67 -> 0.
