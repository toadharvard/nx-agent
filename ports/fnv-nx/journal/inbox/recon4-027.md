# recon4-027: EQ Boot/Hot rows from S2-link (588863): vftable getters, the TESObjectREFR ctor, SpawnedSub, __readfsdword

Input: EQ's bh_A / bh_C verdicts (fnv-nx-build reports/eq-base 588863/), link 5fd996a4.

- **13 "exe data table" rows are MSVC vftable getters.** The exe's static initialisers fill Havok class-registry
  records {hkClass, create, finish, vftable} with what they return, so the value is the class's identity. In the port
  that identity is `__fnv_<V>`, which aliases.ld already defines as the native vtable paired with V. It is not the
  image address: image vftables hold x86 code pointers.
  - 4 rows (00da0780 00dfc400 00dfe5e0 00dfe9b0) already return the paired vtable. They are harness artifacts: the
    unit images do not map a native vtable to the vftable it stands for.
  - 9 rows return a decomp stand-in table that no object of the class carries: 00dfd800 00dff380 00da07d0 00db7c40
    00dfba10 00dfbe20 00e01490 00e01c60 00d2c8a0.
  - Fix: cf9230b6 (Sonnet worker, reviewed) and d4715be1. exevft.vtable_getter detects the getter in the exe; the
    link writes `<stand-in _ZTV> = <paired _ZTV> + (apN - apZ);` to aliases.ld (out/vtget.tsv, stat vtget_alias,
    FNVNX_VTGET=0 turns it off). The ld script assignment overriding a weak COMDAT vtable is verified.
  - Detection: 13 of 13 EQ rows; 142 getters exe-wide. Simulated on 5fd's objects: alias 9, already paired 4.
  - Tests: test_vtget (bytes, selection, end-to-end resolve, apply and trial link), plus test_slotfill,
    test_slotabi2, test_colliding and test_layout_equal: all pass.
  - Not yet measured on a farm link. The EQ replay needs a link with d4715be1 and the harness's native-vtable mapping.
- **00551180 (boot):** the decomp writes ctor 0055a2f0 as a unit-local stand-in (`fnv_0055a2f0::FUN_0055a2f0`, local
  TESForm and TESChildCell bases). It stores a 3-word table where the exe stores _ZTV13TESObjectREFR (0x0102f55c), so
  FUN_00575690 calls slot 63, reads 0 and branches to 0. The stand-in has two address points that differ from
  TESObjectREFR's, so no link alias can map it. It is a declaration fix, with R2 (a recon of the ctor as
  TESObjectREFR's).
- **00e98f20 SpawnedSub ODR:** R2 recon/spawnedsub_e98f20 (b6b36e93) closes that unit. The other SpawnedSub units
  go to S2-bodies' COMDAT ODR pass (S1-rules' call: a source rename would unbind MSVC-named members).
- **__readfsdword (00a5b5a0):** not a link defect. The guest ELF binds it (5fd: `bl __readfsdword` at 30ad7450). EQ's
  unit images lacked the platform library: EQ aa52d47c, 6 reproducers gone.
