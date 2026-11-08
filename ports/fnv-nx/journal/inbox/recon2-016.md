# recon2-016: -fno-lifetime-dse in the game flags; D22 widening dropped (R2)

- Change: the game flags now carry -fno-lifetime-dse. It is in tools/link/compile.sh, tools/static/flags.sh,
  tools/census.sh, layout.SEMANTICS, toolchain/game/build.sh and test_vslots.
  - Why: EQ farm baseline 588863, 00d979f0. Decomp code calls a destructor explicitly and reuses the object
    (`this->SpawnedBase::~SpawnedBase(); m_a = 0;`). MSVC keeps the destructor's vptr store, but GCC's default
    -flifetime-dse drops it, so the native object kept the derived vptr.
  - Size: about 254 explicit `X::~X()` calls in 177 units of the head tree.
  - A per-function `optimize("no-lifetime-dse")` attribute does not work, so this is a flag.
  - Proof: hkpkdtreecollidableraycaster.cpp compiled with the head flags plus the new flag. ResetFields now stores
    the vptr (`ldr vtable; add #8; str [this]`). With the head flags alone it stores only the fields.
  - Test: tests/r2/test_nolifetimedse.py. It checks that the flag is in all five lists. It also checks that the
    store is there with the flag and missing without it.
  - Static gate: flags.sh BUILD_FLAGS still equals compile.sh, and census.sh has every flag.
- D22 widening (607ce60e / 56ee671c, never active, because no rows were published): dropped in the merge.
  - Integration's _d22_numbered (S1, EQ G1) now covers the letter+zeros placeholders: `r0, r1` and `s_000, s_001`.
  - D22-INLINE (S2-bodies) now covers the case of a deriving class that defines members.
  - At the current head, Ref_00932b70, N92Procedure/N92Actor and CAC300Owner are all converted without R2's change.
- For S1 and S2: one more semantics flag (-fno-lifetime-dse) next to W41's -fno-sized-deallocation. It shares W41's
  full recompile.
