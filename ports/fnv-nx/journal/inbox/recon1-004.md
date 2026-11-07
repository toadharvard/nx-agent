### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-003)

* **did** - The 25 extern-text and code-defmissing names of shard 0 are closed (authoritative list s2d/list at
  bf0b0ebf). Units: tesobject, blk_00400000_od, blk_004e0000_od, and four Havok units (hkpworld, broadphaseborder,
  vehicleviewer, swepttransformdisplayviewer). All are on fnv-nx-build `recon/<unit>`. Every unit passes
  build_and_match with all bytes equal and compiles for AArch64.
  - extern-text: 5 exe addresses get bodies of their own, in recon.json `functions`, byte for byte:
    - 0x0050f9a0 (22 bytes, a setter at this+0x1c);
    - 0x00404d80 (strcpy_s wrapper) and 0x00404da0 (vftable store + call);
    - 0x004e44d0 (float sin) and 0x004f6e60 (270 bytes, list lookup + GetPrevPath recursion).
    The callers' other GCC names (8 for 0x0050f9a0) bind by address once the address is defined.
  - extern-text 0x0044aa30 / 0x0044ab60 are not function starts: they lie inside ResizeBuffer 0x0044aa10.
    - The list's `name-addr` evidence for them is wrong (for S2-bodies). FnvString's AssignSubstring is the exe's
      assign 0x0044a580, which calls _Grow 0x0044a8d0 (size, false) and _Eos 0x0044a880.
    - Both names forward there. The bodies sit in the home unit tesobject, with a local FnvString declaration.
  - code-defmissing: 8 of the 9 addresses are MSVC adjustor thunks (`sub ecx, n; jmp ??_G`). GCC never emits them,
    because the override is declared only. Each is a GCC-only `__fnv_<X>(this, flags)` that adjusts `this` and
    calls the unit's target.
  - The ninth, 0x00d649c0, is a real ??_G, missing because the key function (the virtual dtor) was never defined.
    It now gets an out-of-line dtor for GCC only, so D0/D1 and the vtable are emitted.
* **for review (orchestrator / S2c-vt)**
  - The thunks have the exe's ABI (this, flags). The scaffold vtable slots they fill (A1_/A2_,
    hkBroadPhasePrimary/Secondary) declare `void f()`, so a native call through those slots passes an undefined
    flags value.
  - `__fnv_00d649c0` is expected from the link's ??_G -> D0 alias. This has not been checked by a link.
  - The 0x00d649c0 D0's operator delete calls the heap's ReleaseBlock at Itanium slot offset 12; the exe uses
    offset 8. The unit's existing operator delete does the same, and this is correct only if that heap object has a
    native Itanium vtable.
  - tesobjectcell declares `RegionAnswer::FUN_004f6e60()` with no arguments, but the exe function takes three
    (key, bool, bool) and pops 0xc. The caller passes garbage; fix the declaration on the tesobjectcell side.
* **blockers** - unchanged from recon1-003: `visitLevel(int)` and `ReleaseBoundB5E` are unit bugs (a filler
  FUN_00da71c0, and the decomp's RemoveBound shape). No body can make them correct. The fixes are named there.
* **metric** - shard 0 names (authoritative list, bf0b0ebf) without a body or binding: 27 -> 2.
  - Accept split: (a) 70 bodies, plus 9 bound by address (pending REC1 symbols persistence, recon1-002); (b) 0.
  - All checks are local: per-unit build_and_match and an AArch64 compile. No link or boot with the recons has run
    here. The first link with FNVNX_RECON pointing at these branches is the farms' / V1's check.
