# recon4-026: post-VTFILL residual owners, vtable-slot abi2 bridges

Residual: S2's 4264bfd74f36-vtfill.tsv (50a20177), 8,809 rows. Every row now has a cause and an owner, in
fnv-nx-build reports/vtgate-r4 residual_owners.tsv.

| rows | kind | owner | cause / way to 0 |
|---|---|---|---|
| 4,662 / 3,286 / 367 / 4 | short / shift / trap / pure | S2 (W46) | secondary-base groups |
| 302 | wrong | S2 (W46, class layout) | the native table has a destructor (D1/D0, FUN_00402d50's) where the exe table has a function (TESForm::ClearData's slot): the class declares a virtual dtor the exe table lacks there |
| 124 | wrong | R4 data, S2 VTFILL applies | the native class does not declare the exe's override, so its slot keeps the base function (TESForm CopyFrom/Compare at 67/68: 76). The exe override has a native, so VTFILL writes it: recon/vtbind overrides.tsv. 111 have the same native ABI; 13 go through the slot ABI check |
| 4 | wrong | S2 (W46) | a native stub in the slot (vfree, a _ZThn thunk, msvc entry) |
| 5 + 55 | wrong + trap/shift/null/pure | R2 | no native body |

Native tables longer than the exe's: 0 rows. VTFILL writes every table at the exe's length (S2).

The vtable slot ABI branch tries abi2 (layout bytes) before it traps (ffd6a2a3, Sonnet worker). A slot passing an
aggregate by value now gets `abi2: same` or a generated bridge when its x86 bytes match the definition's. Otherwise
the gap row carries abi2's reason. test_slotabi2 (same, bridge, trap with reason), test_slotfill, test_colliding and
test_layout_equal pass. On R2's 50 link-abi slots this is measured at the next farm link (stat
slot_fill_vslot-abi2-bridge). Layouts come from the probe, which needs one more link round for new types.
