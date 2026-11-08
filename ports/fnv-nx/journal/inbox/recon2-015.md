# recon2-015: VORDER1, same-name virtual overloads in exe slot order (R2)

- Finding: S2 seamcheck VCALL V2 lead MobileObject::Revert @00932f96 (exe slot 346, native 348) is REAL.
  - The decomp's generated header include/BaseProcess.h declares same-name virtual overloads in source order.
  - MSVC lays them out reversed, and the header's `// slot` comments say so (Revert 0x15B above 0x15A).
  - GCC uses declaration order, so the GCC table has every such pair swapped.
  - A caller through an exe-ordered table runs the other overload.
- Size: a head tree (bea5a2ed) scan finds 9 pairs, all in BaseProcess.h:
  - Update
  - AlterActorValue
  - ApplyActorValue
  - ClearReferenceRecords
  - SaveGame
  - LoadGame
  - InitLoadGame
  - Revert
  - GetPrimaryTargetReference
- Fix: prepare rule VORDER1 (tools/rules/r2_vorder.py) sorts adjacent same-name `virtual ... // slot 0xNN` runs into
  ascending slot order.
  - The lines are moved verbatim.
  - The rule is GCC-only, because the prepared tree is GCC-only.
  - Head: 9 runs sorted, 1 file.
- Check: POST VORDER1-LEFT fails prepare if any same-name virtuals in one class still descend in slot order, adjacent
  or not.
  - Head after the rule: 0.
  - FNVNX_VORDER_LAX=1 makes it count only.
  - CLI: `tools/rules/r2_vorder.py <tree>` exits 1 on any.
  - Test: tests/r2/test_vorder.py.
- Measure: on the next link, seamcheck --vcall should move 00932f96 to matched.
- For S1 (D26 class decls), FYI: this is about declaration order inside generated headers, not the D22 dtor rule.
  Nothing to do unless VORDER1-LEFT fires.
