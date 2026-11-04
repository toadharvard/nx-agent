### J-XXXX | 2026-11-04 | stream: S1-fuzzA | status: wip | parent: S1-fuzzA-002

* **measured** - I re-fuzzed 82 of the open native-null-read functions, in 43 units, with the current tools.
  - Setup, per unit: a one-unit prepare with this branch's rules, compile.sh of link 0bd144 (no `-fcheck-new`), and
    a subset link with the current fnvlink. Then 40 iterations of 8 s, and `causes._load_campaign` over the
    results.
  - Result: 43 functions are still native-null-read and 1 is native-trap. 38 left these two causes:
    - clean: 20;
    - native-undefined: 13;
    - vptr-unpaired: 6;
    - the rest are spread over small causes: vcall-garbage 7, bool-repr 5, call-order 4, native-extra-calls 4,
      after-native-undefined 4, oracle-vcall 3, local-static 3. A function can have several.
  - Expected metric once the farms re-fuzz: about 475 -> 386. That is null-read 305 -> 216 and native-trap 170
    unchanged. The figure counts:
    - the 38 above;
    - the 9 STAND3 functions;
    - the 8 `-fcheck-new` functions;
    - 4 functions already clean on both sides of S1-fuzzA-002's A/B;
    - the 30 functions whose records no longer reproduce.
    - It assumes the farms link with a fnvlink that has S2d `8c2cfc2`.
* **found** - About 40 of the open functions called a conversion operator through a named stub:
  `NiPointer<T>::operator T*`, `BSStringT<char>::operator const char*` and decomp-local handles.
  - At link 0bd144, `sigs.parse_msvc` read `??B...` as special, so those names stayed unresolved.
  - S2d `8c2cfc2` fixed that. On a subset link with the current fnvlink, magiclight binds
    `_ZNK9NiPointerI10NiAVObjectEcvPS0_Ev` -> `__fnv_00559450` (unit/exact), and 0081c240 no longer reproduces.
  - Of these 17 units, 12 functions are now clean.
  - No published link has `8c2cfc2` yet: link-head c0d6c71516f6, 06857911f5ab and defb010a2a1b all lack it. So the
    farm caches keep these until the link farm reaches a newer head.
  - Decomp-local conversions whose MSVC name the unit does not bind stay unresolved, e.g. actor.cpp
    `fnv_00567050::NiFixedStr::operator const char*`.
* **for S13** - S2c's thisadj (`73d8499`) gives the 13 secondary-base overrides of S1-fuzzA-002 an address-map entry
  that names GCC's `_ZThn<d>_` thunk. The fuzzer can enter those targets through that thunk with the x86 `this`.
* **next** - The 43 still-null-read functions, by unit, once the farms' caches reflect STAND2, STAND3, `-fcheck-new`
  and a link with `8c2cfc2`.
