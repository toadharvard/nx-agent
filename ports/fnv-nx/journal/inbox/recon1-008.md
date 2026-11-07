### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-007)

* **did** - Shard 4: 89 of 91 rows closed, in 35 units on fnv-nx-build `recon/<unit>`.
  - Every unit passes build_and_match with its bodies, all functions and bytes equal.
  - Every body compiles with the game's AArch64 flags, and every row's name is defined (nm). The exception is a
    few `__fnv_<X>` rows, which the link aliases to the native D0, static or restored definition that the recon
    now emits.
  - Kinds: forwarders to native definitions (most rows); 15 own-address bodies, byte for byte, in recon.json
    `functions`; inline reconstructions; and GCC-only adjustor thunks, deleting destructors and template
    instantiations.
* **decision (recorded)** - 0x00ad7020 (`C::FUN_00ad7020`, home unit staging_blk_00ac0000_od, /Od) is byte-exact
  under MSVC 9 only with /Ob1. The exe inlines a memory-context scope and a setting's GetBool there, as the
  neighbouring /Ob1 bsaudio units do.
  - The body is in the home unit, GCC-only. MSVC keeps reaching 0x00ad7020 through its symbol.
  - `match_ob1.json` on the branch holds the acceptance: the unit with /Ob1 added and the body, 146 / 146 functions,
    15,429 / 15,429 bytes.
  - recon.json `symbols` binds the body's callees (0x00aea150, 0x00ad9f30), the setting (0x011f6d9c) and the
    function itself.
* **not done**
  - `__fnv_00658360` (matrixvt_h_unit): link-side, for S2-bodies. GCC already defines `_ZN13FaceGenMatrixIfEmlERKS0_`
    in the prepared tree and in link/fixed, and fnvlink's idx_lookup_msvc matches it when run alone. Yet addrmap has
    no row for 0x00658360, and the guest binds a stub there.
  - `hk3AxisSweep32CreateBroadPhase` (hkpcpuworldgetclosestpointsjob): the exe has no such function. Its RTTI knows
    only the 16-bit hkp3AxisSweep, and no units.json address names it. Only an unused inline Havok header class
    (ScopedBroadPhaseFactoryOverride) stores it as a function pointer, and only the link's `#vtables` object
    refers to it.
    - No body was invented. A null-returning stub would remove the trap. That is the orchestrator's call.
* **found (for S1 / S2-bodies)**
  - STAND1 drops tagged bodies wrongly in two units:
    - hkmemutil 0x00d2cac0: the signature spans two lines, so the `@fnv` tag is not seen as adjacent;
    - vatsmenu 0x007f4f30: the tag sits on an in-class `_check`.
    The recons restore both bodies GCC-only.
  - Adjustor thunks to a ??_G (0x00c66650, 0x00dda650, and shard 0's eight): the decomp's scaffold vtables declare
    the slots `void()`, so a native call through them passes no flags. The thunks take the exe's (this, flags).
  - blk_00ca0000: the untagged stand-in `FUN_00cb0b10` stores `s+len`. The exe stores a duplicated string pointer +1,
    so CB0StringPtr's native copy keeps wrong semantics.
  - atmosphere: `AtmoNode::operator delete(void*, size_t)` drops the size. The exe passes 0xac. The forwarder
    hard-codes it.
* **tools** - The local publish tool once dropped `replace` from recon.json (shard-0 shadowscenenode and blk_00da0000
  rtm). Both were republished with it within minutes, and it now carries functions, symbols and replace through.
* **metric** - shard 4 rows without a body or binding: 58 -> 2. Accept split for shard 4:
  - (a) 89, one of them under /Ob1 (match_ob1.json);
  - (b) 0.
  No link or boot has run with these recons; the per-unit checks above are all.
