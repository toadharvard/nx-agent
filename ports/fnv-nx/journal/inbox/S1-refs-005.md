### J-XXXX | 2026-11-04 | stream: S1-refs | status: wip | parent: S1-refs-004

D21 classes from S5-boot's collect-all list (reports/s5-traps/06857911f5ab.tsv) and S13b's classes.tsv (callee-bodies).
Each is given with its size before and after, and the one mechanism that closes it.

* **Class A: `FUN_<addr>(...)` varargs declarations** - 167 distinct addresses, in 5 files (166 in
  src/fallout/misc/main.cpp).
  - 165 have a native definition. Their varargs call sites go through S2c's vacall bridges, which take the
    callee's fixed signature from the call sites' argument types.
  - The 2 without a definition are both provided by S4 on the integration branch:
    - 0x00406d50 is in platform/crt/msvc_names.c as strcat_s, under the C name and the varargs C++ name
      `_Z12FUN_00406d50z`;
    - 0x00ecc071 is `_wcsicmp`, as `fnvnx_wcsicmp16`.
  - Open: 0 for S1. S5's boot predates S4's forwarders.
* **Class B: an `@fnv` body the link has no native for** - measured on link/defb010a2a1b (14:41, before the S1-refs
  merges) over every `@fnv` tag of the decomp:
  - 71 tags in `.inl` / `.h` and 11 in non-unit .cpp files have a recipe owner but no native. Of the latter, 4 of
    those checked are this-adjustor thunks (`@@W..@AE`), the link's thisadj class.
  - 1,056 tags inside units have the same gap.
  - The causes measured so far:
    - an owner unit that failed to compile at that commit. bsaudiodefines_h_unit, middlehighprocess_h_unit,
      nitset_h_unit, nitexturingproperty, niintegersextradata_ob1_variant and others are fixed by
      TPL1 / DTOR1 / SPEC1 / NEW5 / RREF2;
    - a class-template member that GCC never emits (HUNIT1, 81803c0);
    - a hand-written dynamic initializer that XC1 does not see (XC2, 1e626f4).
  - In progress: compile every owner unit (201) with the current rules (a full prepare) and match each recipe
    function to the object's native definitions with fnvlink's own lookup. The result is the class size after
    the merged rules, and the residue by cause.
* **Class C: constructors declared and defined nowhere** (5 names)
  - GlobalObject_0045cfe0 / GlobalObject2_0045cfe0 / GlobalObjectO2_0045cfe0: the setting constructor 0x0045cfe0,
    which no unit defines.
    - The generated owner classes number 106. 101 are bound and defined; 5 are bound but have no definition:
      0x0045cfe0 with 400 globals, and 0x00ec3289 / 0x00ec3a75 (CRT range, 8 globals, S4).
    - D20 reconstruction: fnv-nx-build recon/generated_global_object_initializers (2ddf974). build_and_match:
      6,286 / 6,286 functions, 163,256 / 163,256 bytes. REC1 applies it.
  - std::StandardLibrarySingletonObject(): bound (0x00af30a0), but fnvlink's RUNTIME pattern drops every
    `_ZNSt` name. This is the std:: proposal passed to S2d (S1-refs-003).
  - NiPointer<QueuedFile>(QueuedFile*): bstempeffectsimpledecal(.cpp, _ob1_variant) defines
    `NiPointer<QueuedFile> BSTempEffectSimpleDecal::ms_pPreloadDecalDataFile;`. The recipe binds it to image data
    0x011d6c40 and has no `??__E` for it: the exe zero-initializes it statically. GCC constructs it at startup
    through a constructor nothing defines.
    - Class: an object the recipe binds to an image data address, with no `??__E`, whose definition GCC
      initializes dynamically. Its size is not measured yet.
    - Mechanism: the definition becomes a declaration (the data image holds the object), as XC1 does for the
      generated units.
  - Overall: the link's unresolved constructor / destructor references are 948 distinct names (136 constructors,
    804 destructors; link/0bd144db1464 refs.tsv). The largest subclass is D1 of a class whose unit binds `??_G`:
    MSVC's `delete` calls the scalar deleting destructor, GCC's calls D1. Proposed to S2 as a bridge to
    `??_G(this, 0)` (S1-refs-004).
* **Class D: one class declared twice with different virtual orders** (S5's NiTMap_S vs fnv_00486a00, exe vftable
  0x0101ca78)
  - MSVC gives a virtual destructor one slot; GCC gives it two (D1, D0). A declaration with a placeholder at slot 0
    (`virtual void v0();`) and one with a virtual destructor therefore disagree on every later slot under GCC.
  - Size: 9,733 decomp classes start with a placeholder virtual (`v0`, `Unk00`, ...) and declare no virtual
    destructor. Only 23 of them have a vftable binding in their unit, which would prove what slot 0 is.
  - A class-wide fix is a vtable ABI decision: GCC vtables in MSVC's shape, one deleting-destructor slot, for
    example a rule that models every virtual destructor as MSVC's `??_G(flags)` slot. It changes every polymorphic
    class, so it is for the orchestrator / S2c-vt. S1-refs has not made this change.
* **callee-bodies (S13b classes.tsv: 747 worst / 883 functions)** - the sub-classes measured (S1-refs-004):
  - conversion operators the recipe binds to 0x00559450: fixed in the link by S2d's 8c2cfc2. 159 of the 456
    native-undefined rows resolve with the current link code;
  - template / header-unit members never emitted (HUNIT1);
  - D1 vs `??_G` (S2 proposal);
  - vtable-slot names (S2b);
  - per-unit placeholders.
  - The next fuzz on a current link re-measures it.
