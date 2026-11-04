### J-XXXX | 2026-11-04 | stream: S1-refs | status: wip | parent: S1-refs-002

* **task** - The orchestrator's metric: units excluded from full1's compile stage. Its figures (61 / 312, 74
  excluded) come from older reports (fa883870, ff0fd855). The newest report, 06857911f5ab-full.md, shows census 24
  failing / 75 errors (the same as farm1) and compile 2940/2978, so 38 excluded:
  - the 24 census failures; 17 of them are fixed by 3047cae2;
  - 9 zlib C units;
  - 5 units that fail only under -O2.
* **did**
  - 37223fd zlib (9 units).
    - Problem: `third_party/zlib-1.2.1/*.c` were missing from the tree, because prepare.py copied only src/,
      include/ and build/. They were also compiled as C++ (K&R definitions; the recipe binds C names such as
      `_adler32`). The census counted them clean because it counts only `: error:` lines, and the compiler's
      message was `cc1plus: fatal error: ... No such file`.
    - Fix: prepare.py copies third_party/. census.sh and link/compile.sh build `*.c` with $CC (gnu99, the same flags
      without the C++-only flags and the plugins) and add the unit's units.json /D defines (only zlib has any). The
      census now counts fatal errors too.
    - Result: census.sh 10/10 clean; compile.sh 9/9 objects with C names.
  - 9096e96 FINL1 (neutral, new): `__forceinline` dropped from a body-less declaration in a .cpp, at file scope or
    in a class C, when the unit has no body of that name with the same parameter types and const qualification.
    - Why: the shim's `__forceinline` is always_inline, and GCC at -O2 refuses the call ("function body not
      available"). MSVC made an ordinary call.
    - The 5 units: tile_ob1_variant, the hkpbvtreestream / hkpspherecapsule / hkpcollectioncollection3
      `_rtm_variant` agents, and blk_00ae0000_od_ob1.
    - Decomp-wide: 72 files / 402 edits. At -O2 those 72 units have 11 errors before and 0 after; none got worse.
      hkgpmesh had 3 more -O2-only errors, also gone.
    - MSVC 2008, local gate on the 4 files with the most edits (37 / 25 / 25 / 21): 3 units are identical with
      and without FINL1. The 4th, hkpcpuworldgetclosestpointsjob_gs_ehsc_nogs_noehsc_variant, does not compile
      under MSVC on either side (C2371 'PBATTERY_REPORTING_SCALE' redefinition, an SDK clash that predates FINL1;
      left for S11).
  - 618cfba, V1's refutations:
    - N2: NEW5 tags the class's own inline operator new(size_t) with `FNVNX_NEW5_TAG`. Under GCC that is a
      defaulted tag parameter, which makes it a placement form with no matching delete, so a throwing constructor
      frees nothing, as on MSVC (C4291). The macro is empty under MSVC.
    - C2/C3: COND1 now requires a non-explicit G(bool), and the operand must be a postfix expression ending in the
      call (`x.f(a)`, not `!x.f(a)`).
    - D2: DUPINT1-RET now requires register returns only (void, integer or pointer).
    - V1's tests/v1/test_s1refs_b.py: 19/19 ok, once V1's PRE block also defines FNVNX_NEW5_TAG.
* **measured** - Metric by local unit checks: 38 -> 7 after a merge. The 7 are S1-STL's units only:
  blk_00ac0000_od_ob1 and its `_std` variant, blk_00b20000 std, speedtree leafinfo / projectedshadow /
  indexedgeometry, tesobject_ob1_nogf. The next full1 report confirms it.
* **S4 hand-off (s4-002.md, 9 std:: names): analysis, no change yet**
  - Placeholders: `ios_base::setstate(int)`, `basic_ios::init(basic_streambufDEB0*, bool)`,
    `basic_ifstream::open(const char*, int, int)` and `CsvUseFacet<char>` (tesobject_ob1_variant's own `namespace
    std` classes) have no binding in the recipe. They are bodyless placeholders: each call site needs decompiling.
  - Bound but dropped: `std::FUN_00ad5770` (0x00ad5770) and `StandardLibrarySingletonObject`'s constructor
    (0x00af30a0) are bound in the recipe, but fnvlink's RUNTIME pattern treats every `_ZSt` / `_ZNSt` name as
    libstdc++'s and never looks it up.
  - Proposal for S2-link: a std:: name that the guest's own C++ runtime (the ILP32 libstdc++.a / libsupc++.a of the
    guest toolchain) does not define goes through the normal lookup. This needs that symbol list as a link input
    (e.g. FNVNX_CXXRT_DEFS, from `nm` in the full/link job).
  - Not the host's LP64 libstdc++: its size_t mangles as `m`, not `j`, so the real STL calls the units' recipes also
    bind (MSVC's inline basic_string members) would be matched by address. That would be wrong: a different layout.
* **hand-off (S11)** - MSVC 2008 cannot build
  src/havok/physics/collide/query/multithreaded/collisionquery/cpu/hkpcpuworldgetclosestpointsjob_gs_ehsc_nogs_noehsc_variant.cpp
  under the gate's rules (`neutral,FTOL1,STL1,STD1,ANON1,ANON2`). Both sides, with and without FINL1, fail with
  `C2371 'PBATTERY_REPORTING_SCALE' : redefinition; different basic types`: the unit's own declaration of that SDK
  name collides with the real SDK v6.0A header that tools/equiv/msvc/fnvnx_win32.h includes. This is like the
  "Not comparable under MSVC" SDK names in docs/EQUIVALENCE.md (RequestLocation, _MEMORYSTATUS ...). The fix is in
  the equiv stand-ins, or a DECL2-style drop of the unit's copy.
