# recon2-007 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-006

* **S2-bodies' sibling-instantiation rows** (c66a7ab4, orchestrator 22:24): S2's bodycause on link 0540aff0d154
  inputs moves 45 names from name-mismatch to no-body/recon (47 on S2's inputs). 35 of them are trap stubs: class-template
  destructors (NiPointer, NiTArray, BSSimpleArray, NiTPointerMap, NiTStringPointerMap, NiTMapBase, NiTObjectArray,
  RefPtr, TaskPtr) and one basic_string::_Grow.
* **metric** (those 35 trap names): 35 -> 3. 32 closed in 26 recons, `recon/sib_*` and `recon/sib_np_*`. Every unit
  rebuilds all functions and bytes equal.
  - 31 bindings to the exe's out-of-line copy for that T, or to the copy MSVC's linker folded for all T (0x0045cec0
    the NiPointer destructor, 0x00ba3d10 the ImageSpace NiTArray destructor). Evidence: the exe's C++ unwind map of
    the owning constructor or destructor (the funclet for that member's state jumps to the destructor, and the
    pristine object's funclet names that exact symbol), the destructor in slot 0 of the RTTI vftable, or the call
    site. None of the names was already mapped in the unit's units.json.
  - 1 D23 (c) inline: NiTArray<NiPointer<NiControllerSequence>> has no out-of-line exe copy (sites 0x00a2eb80 /
    0x00a2ebb0). It is a generic template destructor through a helper defined only for that T, so another T gets no
    silent body.
* **left open: decomp layout class (for S1 / F3)**
  - GrenadeProjectile: NiPointer<NiLight> and NiPointer<ProjectileAuxiliaryData> sit at +0x12c / +0x134 in
    grenadeprojectile_types.h. The exe's Projectile ctor 0x009bbc70 has NiPointers at +0x114 / +0x11c / +0x120 and
    SoundPlaybackHandle at +0x128 / +0x134.
  - NiObjectNET carries NiNode's child array at +0x9c (niobjectnet_decls.h); the exe's ~NiObjectNET never touches it.
  - Binding these would destroy the wrong members silently; they stay traps until the class is fixed.
* **to confirm on the next link**: recon/sib_nictlmgr binds a name only GCC references (UAE decoration, the decomp
  declares the destructor virtual).
* **D23 list**: waiting for full1's newest-head link (0540aff0d154 predates the recons). Dry run published as
  s2d/list/0540aff0d154 (pre-recon; not for counting).
