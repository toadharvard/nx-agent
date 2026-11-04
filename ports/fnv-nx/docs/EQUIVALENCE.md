# Rule equivalence under MSVC 2008 x86 (stream S11)

Decomp `87eb9373`, prepare rules as of `8fff58b` (J-0052). The decomp is byte-matching MSVC 2008 x86-32 code, so the
compiler it matches is an oracle for the prepare rules: a rule that only changes spelling must leave every function
compiling to the same code under that compiler. S11 builds the pristine decomp and prepared trees with MSVC 2008
SP1 / RTM under Wine and compares them per function with the decomp's own `compare_builds.py` logic (code bytes,
padding, every relocation resolved to its original address). A differing function means the rule changed what the
code does under x86, or the rule was misclassified, or MSVC cannot express what the target does (then the
difference is explained, not counted against the rule).

Tool: `tools/equiv/equiv.py` (`tools/equiv/README.md`: compiler setup, installer hashes, commands). Tests:
`tests/s11/run.sh`.

## Result (neutral set + FTOL1, all 2,988 units)

| | units | functions |
|---|---:|---:|
| compiled on both sides | 2,890 | 59,122 |
| identical | 2,348 | 58,077 |
| differ | 542 | 1,045 |
| - only in exception-handling code (identical when both sides build with `/EHs-c-`) | | 754 |
| - same code bytes, a call or data reference reaches another address (*retarget*) | | 177 |
| - other code | | 114 |
| prepared side does not compile under MSVC 2008 | 97 | |
| pristine does not compile (the `vc8` unit: VC 2005 not installed) | 1 | |

Every pristine unit that compiles has every one of its recipe functions in its object at exactly the recipe size
(68,456 functions): the oracle builds what the decomp claims.

## Verdicts per rule

Leave-one-out attribution (method below). `eh` / `retarget` / `code`: kinds of the differing functions the rule
explains; `no-build`: units that compile under MSVC without the rule but not with it.

| Rule | eh | retarget | code | no-build | Verdict |
|---|---:|---:|---:|---:|---|
| `EXC1` | 562 | | | | **exceptional paths only.** Without `throw()` MSVC adds EH frames / unwind states (318 units, e.g. `NiRenderedTexture::Create` frees the memory when the constructor throws). Every one is identical with `/EHs-c-`. |
| `DECL1` | 4 | 153 | 27 | 65 | **changes callees and math precision.** Retargets: the decomp's C++ prototypes of `strcpy_s` / `strcat_s` / `_stricmp` / `sprintf_s` / `memset` / `memcpy` / `strchr` are bound to game wrappers (0x00406d30 `TaskQueueInterface::CopyStringToBuffer`, 0x00404dc0, 0x00406d00, 0x00403d30, ...) that only forward to the CRT function; DECL1 calls the CRT directly (equivalent). Code: the CRT's `<math.h>` overloads replace the decomp's declarations, so `fabs` / `sqrt` / `floor` of a `float` round through `float` where the matched code stayed in x87 precision, or the other way round (`Calendar::GetGameHoursPassed` passed `floor` a `float` to the game helper 0x00406cc0 bound to `_floor`; it now passes a `double`: hours can differ by one at a boundary); CRT attributes change code after `_invalid_parameter_noinfo` and EH states around CRT calls. No-build: MSVC-only (`std::abs` 29, SDK names, `float` math overloads). **Re-gated at `357bc06`** (J-0093: a dropped C math prototype becomes `fnvnx_cmath/<name>.h`; the gate puts that directory on MSVC's include path). 18 of the 27 code functions are identical now, among them `Calendar::GetGameHoursPassed`; activeeffect (48 functions) is identical too. 3 are not math but CRT bindings: `memset` goes to the CRT instead of the decomp's namespace copy (one EH state store fewer), `free` is called directly instead of through its import slot, and the `_invalid_parameter_noinfo` paths change. 3 more (niobbox 0x00eba680, bhkrigidbody 0x00c8e160, hkpworldviewerbase 0x00ddd040) come from `using ::fabs;`, which DECL1 writes in place of a namespace's `extern "C" double fabs(double);` in a unit that includes `<math.h>` itself. The using-declaration brings in the `float fabs(float)` overload that the namespace declaration hid, so `fabs(f)` rounds through `float`, where the exe stayed in `double`. DECL1 now keeps the namespace's declaration of the C function (DECL1-MATHNS: 33 sites in 24 files; a C++ spelling the unit binds to the CRT becomes `extern "C" double fabs(double);`), and the three are identical. Before/after on the 43 units with such a site changes exactly these 3 functions. The last 3 (shadowlightshader 0x00b79050, robertdavies_random 0x00b43120, tile EvalValue 0x00a09410) are still x87 / register differences, to be attributed by the eq farms. |
| `GAME1` | | | | | **fixes DECL1's rebinding (D13).** No unit differs because of it; `falloutaudiomedia.cpp` (14 functions), `bsspinlock_console`, `memorymanager.cpp` are identical again (they call 0x00830470, `FUN_008691b0::Lock`, the Interlocked import slots as the exe does). |
| `CTOR1` | 31 | 2 | 60 | | **null check only.** `::new ((void*)p) T(args)` calls `<new>`'s placement form (`throw()`): MSVC null-checks the pointer before the constructor, and at `/Od` calls the placement function instead of inlining it. Same behaviour for a non-null `this`. |
| `NEW2` | 55 | | | | **exceptional paths only** (MSVC's placement forms are `throw()`, the decomp's were not). |
| `NEW4` | | | 1 | | **allocator.** `bsmultitargettreadtransfcontroller_ehsc_noehsc_variant.cpp` 0x00a35a80: with `<new>` included, `new[]` reaches the global `operator new[]` instead of the unit's inline MemoryManager copy (on the target ALLOC1 removes that copy anyway; equivalent if the game's global `new[]` forwards to MemoryManager, as ALLOC1 assumes). |
| `STATIC1` | | | 15 | | **reloads only.** An external variable is read again after every call (13 units); the internal static was kept in a register. Same values unless another unit writes the variable during the call. |
| `XINC1` | | | 4 | | as S1b states: 4 functions (bssegmentedtrishape x2, nivbset, hkpprojectilegun) reload `ecx` / pass the argument explicitly; the rest identical. |
| `FTOL1` | | 9 | | | **other helper.** `(__int64)(x)` compiles to a call of `_ftol2` (0x00ec62f6) where the exe calls `_ftol2_sse` (0x00ec62c0): the same truncating conversion. |
| `UNIT1` | | | 1 | | the SpeedTree entry is a deliberate decomp-bug fix (the stack object passed to `std::exception`'s constructor grows from 8 to 12 bytes, frame +4) - a DBUG1-style patch, not a rename. |
| `GOTO1` | | | | 3 | **bug at `8fff58b`** (fixed upstream in `66d2007`): `T a = f(x), b = g(y);` became `T a; a = f(x), b = g(y);`, `b` undeclared (4 sites). |
| `STR1` | | | | 2 | **bug**: a file declares `extern const char str_x[]` in one place and `extern char str_x` in another; `&str_x` where the scalar is in scope became `str_x` (a `char`). |
| `IFX1` | | | | 2 | **platform at two sites**: `__if_not_exists(_mm_mul_ps)` resolved as "exists" (sse2neon declares it on the target), so the decomp's own SSE declarations go, as SSE1/SSE2 would remove them; MSVC without `<xmmintrin.h>` has neither. |
| `CALL1` `DECL2` | | | | 2 + 2 | stand-in limits only (units that include `<windows.h>` themselves, decomp types the full SDK also defines). |
| `CSX1` | | | | | asserts only, no code; but see D15 below. |
| `DUPINT1` `LINK1` `NEW1` `NEW3` `RET1` `SPEC1` `RCAST1` `TYPEID1` `SAL1` | | | | | **identical** in every unit they touch. |
| `KEEP1` | | | | | inert by construction under MSVC (`FNVNX_KEEP` is empty). |
| `DBUG1` | | | | | one site (0x00b01510, `patches/dbug1_crt.s1b`): identical under tests/s1b/msvc_check.py. |
| two or more rules together | 102 | 13 | 6 | 21 | explained only by leaving out a group or a pair of groups: the EH-only ones are EXC1 with NEW2 / CTOR1 or two NEWDEL rules; the retargets are CRT wrappers (DECL1 with DECL2 / CALL1) and `FUN_00ee6de6` (declared with the SDK's global `tagRECT` instead of the namespace's: a decoration the recipe does not bind); code: `GraphicsRenderer::BeginUsingRenderTargetGroup` (D15, below) and 5 functions; no-build: stand-in limits. |

**D15 (CS28 removed, CSX1) is refuted by the matched code.** Of the eight critical-section units, four are identical
(abstractheap_h_unit, oei_io_thread, bsshaderaccumulator_od_variant, aitaskmanager), three differ only in EH code,
and `niconsolerenderer_h_unit.cpp`'s `GraphicsRenderer::BeginUsingRenderTargetGroup` reads `+0x1f4` / `+0x1fc` where
the exe reads `+0x200` / `+0x208`: with the 24-byte PC CRITICAL_SECTION, `NiCriticalSection` shrinks from 128 to 124
bytes (`m_ulThreadOwner` 0x1C -> 0x18, `m_uiLockCount` 0x20 -> 0x1C) and `GraphicsRenderer` (three of them) from
2,432 to 2,420. The byte-matched code needs 4 more bytes behind each critical section of NiCriticalSection (CS28's
`FNVNX_CS28` kept every offset: with it these classes differed in member type names only). BSThread, AbstractHeap and
RecursiveLock (D15's evidence) show no code difference in the units that compile, but `RecursiveLock` also shrinks
(40 -> 36) and `BSShaderAccumulator` (896 -> 888).

**D15 is fixed by CSX2** (J-0073; checked at `3b5ea62`, neutral set + CSX2). Five of the eight units are identical,
now with `niconsolerenderer_h_unit.cpp`: `GraphicsRenderer::BeginUsingRenderTargetGroup` reads `+0x200` / `+0x208`
again. The other three (bsshaderaccumulator, nid3dshader, nidefaultavobjectpalette: two functions each) differ only
in EH code and are identical with `/EHs-c-` (EXC1, as before). Layouts: `GraphicsRenderer` and
`BSShaderAccumulator` (896) are as in pristine. `NiCriticalSection` (128) and `RecursiveLock` keep pristine's size
and member offsets, plus one line, the 4-byte tail member at +24 (`m_kCriticalSection_fnvnx_tail`,
`CriticalSection_fnvnx_tail`).

## First farm gate (eq1-6, all 2,988 units)

All six shards passed on heads with the BIND1 fix (shard 1 `edf9fd1`, 2 / 3 / 6 `49f6666`, 4 `650bf10`, 5
`808c50b`), with the same decomp, rule set and pristine objects. Results agree across heads where the rules are the
same: shard 6 gave the same report at `49f6666` and `afca7d5`. Shard 5 changed only by two STATIC1 functions between
`4f0f481` and `808c50b` (7fc664f, STATIC1 for namespace statics). Shard 4 predates that commit. Units: 2,362 identical,
545 differ, 80 do not compile (prepared side). Functions: 1,067 of 59,567 differ (EH-only 777, retarget 174, code
116). DECL1's 9 code functions are the 9 this document lists above (the local gate agrees); 3 of them are fixed by
DECL1-MATHNS, which is newer than these heads. Per rule, ranked by code functions:

| rule | code | retarget | eh | no-build | owner |
|---|---:|---:|---:|---:|---|
| `CTOR1` | 58 | | 31 | | by design (S1: the placement form's null check, see the rule table) |
| `STATIC1` | 23 | | | | S1 (link semantics: reloads of an external variable) |
| `DECL1` | 9 | 155 | 4 | 36 | S1; retargets: CRT wrappers the link binds by address (S2) |
| `XINC1` | 8 | | | | S1b |
| `PTR1` | 5 | | | | S1 |
| `RET1` | 2 | | | | S1b |
| `MATH1` | 2 | | | 1 | S1 |
| `GOTO1` `NEW4` `UNIT1` | 1 each | | | GOTO1 1 | S1 |
| `EXC1` | | | 646 | | S1 (exceptional paths only) |
| `NEW2` | | 1 | 55 | | S1 |
| `ANON1` `STD1` | | 2 / 1 | | | S1 |
| `ENUM1` | | | | 7 | by design (MSVC 2008 parse limit) |
| `DECL2` `IFX1` `RCAST1` `CALL1` | | | | 2 / 2 / 1 / 1 | stand-in limits, S1 |
| groups (`sdk`, `newdel`), interactions | 6 | 15 | 41 | 29 | |

## Round 8 (S1 rules after `8fff58b`)

The eq farms gate every integration head: `tools/farm/equiv_job.sh` runs on each of six shards, and
`tools/equiv/merge_reports.py` merges the shard reports into the per-rule table. A local preview at `c6658af`
(J-0067; neutral set + FTOL1 + STL1, STD1, ANON1, ANON2, all units) gave 2,349 identical, 548 differing (1,061 of
59,049 functions) and 90 no-build units (97 at `8fff58b`). Against `8fff58b`, 8 functions newly differ and none are
newly identical. 11 units newly fail: 7 are ENUM1 (by design, see below), 1 is ANON2 (`nipoint3interpolator`,
reported to S1), and 3 are not attributed yet (bgsdecalmanager, extradatalist, tesaiform).

**ENUM1 under MSVC 2008: by design.** MSVC 2008 has no opaque enum declarations. It accepts `enum X : int { ... }`
as an extension, but it rejects `enum X : int;` (C2059 `syntax error : ';'`, in the `_decls.h` of inventorymenu x3,
packagedatadialogue x3, tesaiform_o2). These units are counted as not comparable (below), not against ENUM1. The
definitions ENUM1 rewrites are compared wherever the unit compiles.

**INC1, header check.** INC1 has 68 units with a site. For each, the headers MSVC opens (pristine, `/showIncludes`,
names resolved on disk without case) are compared with the headers GCC opens for the target (`-M -MG`, census
flags). With INC1, GCC opens exactly MSVC's `src/` and `include/` headers in all 68 units. Without it, 32 units
differ: MSVC opens 194 headers GCC does not (`mobileobject.h` 24 units, `NiPoint3.h` 17, `actor.h` 11, ...), and GCC
opens 78 that MSVC does not (`include/MobileObject.h` and similar canonical copies). This is what INC1 claims, so the
remaining question is code, which the gate answers.

## Classification

Rules of S1 round 8 (`RREF1` ... `SPEC2`, after `8fff58b`) are classified (orchestrator, J-0058). Their gate is
above (round 8).

* **neutral** - claims to change spelling only, on any target. Compared on all units: expected identical.
* **layout** - changes a layout on purpose (for GCC) or swaps in another definition of a type. MSVC class layouts
  (`/d1reportAllClassLayout`) and code are compared before/after; a difference is expected exactly where the layout
  really changes.
* **platform** - replaces an x86/MSVC construct by a target one (sse2neon, portable asm helpers, native renderer) or
  edits code MSVC never compiles. No x86 meaning to compare against.

| Rule | Class | Why | MSVC meaning used in the comparison |
|---|---|---|---|
| `SSE1` `SSE2` `SSE3` `SSE4` | platform | the decomp's copies of MSVC's vector types / intrinsic prototypes give way to sse2neon's | - |
| `DECL1` | neutral (claim) | the platform header declares the same CRT / Win32 function | the real MSVC CRT / SDK headers |
| `GAME1` | neutral | renames a unit's own Win32-named game declaration and its call sites; the address binding is unchanged | `fnvnx_game_X` bound like the decomp's `X` |
| `NEW1` | neutral | `unsigned int` is `size_t` on x86 (same type, same decoration) | as is |
| `UNIT1` | neutral | renames local types of tesdatahandler (same layout) | as is |
| `KEEP1` | neutral | `__attribute__((used))`: emission only, no code meaning | `FNVNX_KEEP` empty: inert by construction |
| `IFX1` | neutral (claim) | static resolution of `__if_exists`; MSVC evaluates the original itself | as is (strongest check) |
| `SAL1` | neutral | attribute-syntax SAL annotations have no code meaning | as is |
| `CSX1` | neutral (claim) | drops the decomp's Xbox 360 critical-section asserts (D15, J-0051); asserts emit no code | as is |
| `DECL2` | neutral (claim) | the SDK's definition of the type replaces the decomp's copy | real SDK |
| `COM1` | neutral | renames a unit's own copy of IUnknown (bsshader: the decomp's parameter types, its slots); no signature with it | as is |
| `EXC1` | neutral (claim) | MSVC 2008 never enforced `throw()`; but it is an optimizer hint there | as is |
| `NEW2` | neutral | `<new>` defines the placement forms identically | real `<new>` |
| `NEW4` | neutral | adds `<new>` where placement new is used without a declaration | real `<new>` |
| `CTOR1` | neutral (by design: the code differences are the placement form's) | explicit constructor call = placement new of the same class at the same address. `<new>`'s placement `operator new` is `throw()`, so MSVC null-checks the pointer before the constructor, and at `/Od` calls the placement function instead of inlining it (the gate's 58 code + 31 EH functions, e.g. 0x009cff10, 0x0092eb50, 0x007f9840). Standard C++ has no other way to construct in place, and for a non-null `this` (every decomp site constructs into an object it holds) the behaviour is the same | real `<new>` |
| `LINK1` | neutral | C and C++ linkage of a global variable name the same symbol under Itanium | data names bound by variable name |
| `STATIC1` | neutral (claim) | internal -> external linkage of image-bound variables; a namespace static bound by its plain name (`_s_fSpinTime`) gets C linkage in place, an uninitialised scalar / pointer one declared only (the image's variable) | variable bound by the recipe's address |
| `GOTO1` | neutral | `T x = e;` -> `T x; x = e;` for scalars | as is |
| `GOTO2` | neutral (claim) | a forward goto across an initialized class object / reference / aggregate into its scope: `const float& a = f(x);` -> `float a; a = f(x);` for an f returning float by value; `{ ` ... `} ` around the crossed declarations before a label of their block when nothing they declare is named after it; or the label's straight `...; return e;` tail (names not declared in the function) cloned into each crossing goto | the path that jumped never used the crossed objects; scopes end earlier, a cloned tail is the same statements |
| `QUAD1` | neutral | `return q;` -> `return (__m128)q;` where q's class converts to `__m128` and the function's return class is constructible from `__m128` only: MSVC chained both user-defined conversions | the same conversion operator and constructor, the first written out |
| `MSCV1` | neutral | `_MSC_FULL_VER` / `_MSC_VER` in code (not in `#if`) -> the value of the compiler build/units.json gives the unit: sp1 150030729 / 1500, rtm 150021022 / 1500, vc8 140050727 / 1400 | the constant that compiler substituted |
| `STR1` | neutral | `&arr` and `arr` are the same address | as is |
| `CALL1` | neutral | the cast is the conversion the dropped prototype made implicitly | real SDK parameter types |
| `RCAST1` | neutral | `fnvnx_lvalue` returns the temporary itself | `fnvnx_lvalue(x)` = `(x)` |
| `NEW3` | neutral | deallocation functions are implicitly `noexcept` (C++11) | `noexcept` empty |
| `TYPEID1` | neutral | adds `<typeinfo>` only | real `<typeinfo>` |
| `SPEC1` | neutral | early `template<>` declarations of member specializations | declarations only |
| `DUPINT1` | neutral (claim) | keeps one of two integer-only-different prototypes bound to one address | as is |
| `RREF1` | neutral | `fnvnx_lvalue(...)` around an argument passed to a non-const reference parameter (every declaration the unit sees; for an unqualified call in a member function, the declarations of the nearest class that declares the name, RREF1-CLASS) that is not a plain lvalue | the temporary MSVC bound, same lifetime |
| `RREF2` | neutral | `fnvnx_lvalue(T(args))` as the operand of a member operator whose every declaration with a T parameter takes a non-const `T&` (S1-refs) | the temporary MSVC bound, same lifetime |
| `ADDR1` | neutral | `&_mm_f(args)` -> `&fnvnx_lvalue(_mm_f(args))` as the operand of a unary `&` (MSVC's address of a class rvalue, C4238) | the same temporary's address, same lifetime |
| `LANE1` | platform | `&((T*)&(v))`, SSE1's lane array with its address taken (`(&v.m128_f32)[0]`), -> `((T(*)[sizeof(v) / sizeof(T)])&(v))` | MSVC's `&v.m128_f32`: same address and array type; SSE1 is target only |
| `TPL1` | neutral | `this->` in front of the members of a dependent base that a class template's member functions use by plain name (TPL1-NESTED: also a class nested in a class template whose base is another member class of it) | the members MSVC's (one-phase) lookup found |
| `TPL2` | neutral | declares a namespace's function templates before the class templates that call them (MSVC's late lookup found them) | declarations only |
| `TPL3` | neutral | declares `namespace N { template <..> R f(..); }` ahead of the first declaration that calls f qualified (`::N::f(x)`) or with explicit template arguments (`f<T>(..)`) before N defines it; only signatures whose names are all seen there (case B: template parameters and builtins only), no default arguments | declarations only |
| `TYPN1` | neutral | `typename` before `X<..T..>::m` in a template argument list inside a template head that names T, for the library's type members (`iterator_category`, `value_type`, ...): MSVC 2008 already read it as that type | as is |
| `INST1` | neutral | `unsigned int` -> `C<..>::size_type` in an explicit member instantiation, at the positions where C's member declaration (the definition of C in the qualifier's namespace, nearest before the directive) says `size_type`: the member MSVC instantiated | as is |
| `COPY1` | neutral | `X::X(const X& o) : B(o)` -> `B(static_cast<const B&>(o))` when B also has a constructor taking one of its own bases by value: the copy constructor MSVC 2008 picked | as is |
| `DECL3` | neutral | a unit's own `typedef _iobuf FILE;` is dropped and its FILEs spelled `_iobuf`; its `typedef __int64 fpos_t;` and uses become `fnvnx_msvc_fpos_t`: under MSVC FILE is `_iobuf` and fpos_t is `__int64` | as is |
| `MATH1` | neutral | drops the decomp's copies of MSVC `<math.h>` C++ overloads; the real header defines them | real `<math.h>` |
| `DTOR1` | neutral | declares `template <> NiPointer<T>::~NiPointer();` after a unit's includes for member pointees T it never completes (with DecRefCount): GCC's key-function vtable would instantiate it | MSVC never instantiated it in those units |
| `VTBL1` | platform | the listed stand-in polymorphic classes get their full virtual interface in MSVC slot order, slots forwarding to the decomp's functions for them (BSNiAllocator 7 of 10 known, NiStandardAllocator 8 of 10, the FUN_00aa7400 heap's AbstractHeap slots and the NiStream loader map A660A0Map's NiTMapBase slots declared only; StdAllocator's destructor is the plain slot 0 it is in MSVC's vftable; docs/PATCHES.md lists the slots that need the exe) | the native vtable has the slots the game calls; MSVC's vftable of the stand-in grows |
| `ALIGN1` | neutral | `__declspec(align(n)) struct S` -> `struct __declspec(align(n)) S` in front of a class definition or forward declaration; MSVC aligns the type in both positions, GCC only behind the class key | same type alignment and layout as MSVC (S12 f8 ALIGN-IGNORED) |
| `ENUM2` | neutral | an enum with an enumerator literal in [2^31, 2^32) gets MSVC's underlying type written out, `enum E : int {`, and `(int)` on those literals | MSVC's type and values (0x80000000 is INT_MIN there); GCC chose unsigned int (S12 f8 ENUM-UNSIGNED) |
| `BIND1` | neutral | prepared build/units.json: a stand-in member / variable the unit uses but does not bind gets the address the decomp shows it has (MemoryManagerAA3880::Allocate -> 0x00aa3e40 on 0x011f6238, via the unit's TableBaseAA3880 -> 0x00aa47e0) | no source change (a POST hook of s1_compile.py, like S1b's units.json fix, so not in the rule table); the link binds the name as the exe's code does |
| `VA2` | neutral | `va_arg(ap, E)` with an enum E -> `(E)va_arg(ap, int)`: the promoted int the caller passed, read as MSVC read it (GCC traps on the enum form) | same value |
| `VLIT1` | platform | a 1-3 character string literal stored with `=` whose bytes are a defined function's address (a vftable slot 0 read as text) -> the vftable address the unit's only `$SG` binding gives (nitmapbase.cpp `"@K@"` -> 0x0101212c), stored as `&DAT_<addr>` with a C declaration of that image symbol in front of the function and its `_DAT_<addr>` bound in the unit (BIND1 hook), so the link can map it like any hand-stored vptr | MSVC stored that address; the pristine build stored a string |
| `FRIEND1` | neutral | `class X;` in front of the outermost class around a `friend class X;` when X is declared nowhere before (MSVC 2008 made the friend's name visible in the enclosing namespace; GCC does not) | same class, same names |
| `INIT1` | platform | a static object whose dynamic initializer the exe has and the decomp lacks: one `static` NiMainSDM (constructor 0x00a8a5f0) in staging/blk_00a80000_o2.cpp | the unit gets a dynamic initializer MSVC's object of the decomp has not; the functions are unchanged |
| `CSX2` | neutral | 4 bytes behind a generated-layout `_RTL_CRITICAL_SECTION` member whose class asserts it as 28 bytes (NiCriticalSection, RecursiveLock and their holders) | the offsets the pristine MSVC build and the byte-matched code have (J-0067) |
| `STAND1` | platform | removes an untagged definition whose MSVC name the unit binds to the address of a tagged definition in the same unit (a free function keeps its declaration) | the call reaches the tagged function, as in the binary; an optimizing MSVC build may have inlined the stand-in |
| `PTR1` | neutral | `f > 0` / `p > 0` -> `!= 0` and `<= 0` -> `== 0` for a function name or a local pointer | MSVC's unsigned compare of the address gives the same result |
| `INC1` | neutral | a quoted `#include` that names, up to case, a file in the including directory is spelled as that file (`"Actor.h"` -> `"actor.h"`) | the header MSVC opened (directory of the includer first, names without case) |
| `ANON2` | platform | a member with a constructor / destructor / copy assignment in an anonymous struct of an anonymous union becomes raw storage of its size and alignment, its uses `(*(T*)(x.m))` | same bytes and calls; MSVC never ran those functions for a union member either |
| `ANON3` | platform | an anonymous union in a function body with an anonymous struct in it -> `struct fnvnx_anon_uN { union {...}; } fnvnx_anon_vN;` and `decltype(v.m)& m = v.m;` for each member (GCC takes an anonymous struct only inside a named class) | same storage and layout under the same names; MSVC's code may keep the references |
| `ELAB1` | neutral | type uses of a file-scope class named like a Win32/CRT function (`struct HeapAlloc`) spelled elaborated: `struct HeapAlloc* p` | same type, same symbols |
| `ENUM1` | neutral | opaque `enum X;` -> `enum X : int;` and every definition of those enums -> `enum X : int {` | int is MSVC's underlying type of every unscoped enum |
| `ANON1` | platform | names an unnamed struct defined in a member function's return type (`struct fnvnx_anon_Float {...}; fnvnx_anon_Float* Float();`) | same layout; MSVC's decorated name of the function changes (`<unnamed-tag>`) |
| `STL1` | platform | includes `fnvnx_msvcstl.h` (MSVC 2008 `<xutility>` internals: `_SCL_SECURE_VALIDATE`, `std::_Checked_base`, `std::_Ptr_cat`, iterator tags) where a unit uses them; in a unit deriving from `std::_Container_base_aux_alloc_real` also its AUX part (container base, `_Destroy_range`, `stdext::unchecked_uninitialized_copy`, checked vector iterators) and `vector<T>::const_iterator` / `::iterator` spelled `_Vector_const_iterator<T, A>` / `_Vector_iterator<T, A>` | MSVC's own headers define them, and the iterator names are MSVC's names of the same types; the gate builds with `<xutility>`, `<memory>`, `<vector>` |
| `STL2` | platform | in the 7 units that include the real `<string>` / `<vector>` / `<map>`: `std` -> `fnvnx_vc9`, `stdext` -> `fnvnx_vc9::stdext`, `size_t` in the unit's own `namespace std` -> `_Sizet` (unsigned int), and `fnvnx_vc9.h` for the headers. The shim is VC9's string / vector / map (`_Tree`) with the x86 release layout (28 / 24 / 32 bytes, iterators 8; static_asserted, checked against 0x00acb200, 0x00b31270, 0x00abfa90), VC9's growth and string algorithms, `_SECURE_SCL` checks through `_invalid_parameter_noinfo`. Not byte-identical code (GCC compiles the templates), the same layouts and results; libstdc++'s containers (string 24, vector 12) would move every game-struct member after them | MSVC side: the real VC9 headers (the shim is C++11) |
| `SPEC3` | neutral | qualifies the names a namespace declares in the body of an explicit class specialization written inside it (`fnv_x::T* p`) | the names MSVC's lookup found |
| `LEX1` | neutral | MSVC token spellings: `i64`/`ui64` suffixes -> `LL`/`ULL`, `0xae-0x34` -> `0xae -0x34`, `"C"enum` -> `"C" enum`, `inline __forceinline` -> `__forceinline`, `unsigned long(-1)` -> `((unsigned long)(-1))` | same tokens as MSVC lexed them |
| `STD1` | platform | a decomp `struct std { static float abs(float); }` (the std namespace of the headers) -> `#include <cmath>`: the calls reach the standard `std::abs(float)` | MSVC called the out-of-line copy at 0x00408840 (fabsf) |
| `SPEC2` | neutral | `template <>` on the decomp's member definitions of class template specializations (5 files) | MSVC accepts both spellings |
| `XINC1` | neutral (claim) | caller declarations corrected from the x86 code (S1b) | as is |
| `RET1` | neutral | `return <trailing stmt>` where MSVC's code is identical (S1b) | as is |
| `DBUG1` | neutral | per-site decomp-bug fixes (S1b; one site, identical) | as is |
| `FTOL1` | platform | `fnvnx_ftol2(expr)` for arg-less `_ftol2_sse` calls | `fnvnx_ftol2(x)` = `(__int64)(x)` (gated anyway) |
| `SDK1` | layout | the PC SDK definitions win over the decomp's guarded copies (Xbox 360 layouts) | real SDK |
| `TAIL1` | layout | GCC must not reuse a base's tail padding; MSVC never does | zero-width bit-field, as on the target |
| `EBO1` | layout | `char fnvnx_msvc_ebo;` first in a class with exactly two direct bases that are both empty in the unit (and data of its own): MSVC gives only the first empty base a zero size, so the members start at 1. 8 sites (FUN_00c612b0_Class x6, FUN_00c64de0, FUN_00c5da70); the exe allocates 0x14, 0x1c, 0xe0 for them (0x00c61510, 0x00c652c0, 0x00c5e4d0), GCC gave 0x13, 0x18, 0xdf | MSVC's layout of the class itself (not applied on the MSVC side) |
| `INTL1` | platform | edits only the decomp's non-MSVC branch (`#else` of `_MSC_VER`) | invisible to MSVC |
| `ALLOC1` | platform | drops untagged file-scope replacements of the global operator new/delete | target only |
| `XC1` | platform | dynamic initializers become extern + `__fnvnx_dyninit_<addr>` run by S10 | target only |
| `VA1` | platform | MSVC `char*` va_lists -> real `va_list` | target only |
| `FALL1` | platform | `__builtin_trap()` at fall-off ends of non-void functions | GCC only |
| `ASM1` `ASM2` `ASM4` | platform | x87 / rdtsc / cpuid asm -> portable helpers (docs/ASM.md has their own checks) | - |
| `NATIVE1` | platform | units leave the build (native renderer, D6) | - |

## Class layouts (238 units, `/d1reportAllClassLayout`)

| Trees (pristine vs) | classes | differ | what differs |
|---|---:|---:|---|
| TAIL1 alone | 18,269 | 0 | nothing: the zero-width bit-field is inert under MSVC (also for virtual, 8-/16-aligned, empty, packed and template bases in a synthetic test) |
| neutral set (`8fff58b`) | 15,685 | 75 | D15: `NiCriticalSection` 128 -> 124, `RecursiveLock` 40 -> 36, `BSShaderAccumulator` / `AccumWIP` 896 -> 888; the decomp's Xbox 360 copies replaced by the PC SDK's (`_FILETIME` halves swapped, `_ULARGE_INTEGER`, `_WIN32_FIND_DATAA`); 59 SDK structs with 8-byte members shrink because the SDK header lands inside the decomp's `#pragma pack(push, 4)` |
| neutral + SDK1 + CS28 (earlier rules) | 15,225 | 73 | `NiCriticalSection` / `RecursiveLock` member type names only (CS28 kept every offset); the same SDK structs |

The `#pragma pack(push, 4)` placement is a DECL1 / DECL2 property (16 inserted `#include "fnvnx_win32.h"` sit inside
an open `pack(push, 4)`, `include/layout_support.h` among them). On the target the shim's `_LARGE_INTEGER`,
`_ULARGE_INTEGER` and `_MEMORYSTATUSEX` then get 4-byte alignment in those units and 8 elsewhere (sizes unchanged):
the shim should push its own packing.

## Method

Trees: the pristine decomp; `equiv.py prepare --rules neutral,FTOL1` (FNVNX_PREPARE_SKIP = every other rule
function, NATIVE1 off, S1b's units.json renames only with an S1b rule; the prepare stats are checked to contain only
allowed rules); leave-out trees (the same set minus a group or one rule); `TAIL1` alone. Every unit is compiled with
its recipe compiler and flags; objects are kept and fingerprinted again whenever the fingerprint changes. Units
whose include closure did not change between two rule sets reuse the earlier objects (compare_builds' closure test).

What the rules include is given its MSVC meaning (`tools/equiv/msvc/`): `fnvnx_win32.h` = the real `<windows.h>`
(SDK v6.0A) adjusted where the target shim deliberately differs (no A/W name macros, no `min`/`max`, the shim's
looser parameter types and `void *` overloads as dllimport declarations of the same imports, `_ULARGE_INTEGER` with
the forward-declared class key), `fnvnx_crt.h` / `fnvnx_iobuf.h` = MSVC's CRT headers, `fnvnx_new.h` = MSVC's
placement forms, and for prepared trees only `fnvnx_equiv_compat.h` = what `fnvnx_compat.h` declares for DECL1
(intrinsics, the decomp's sized-delete helper names, C++14 sized deallocation) and the shim's own `fnvnx_cmath/`
headers (DECL1's one-function C math declarations, their `_MSC_VER` branch) on an include root of their own. Both sides get the macro-only
`fnvnx_equiv_fi.h` (`FNVNX_KEEP`, `noexcept`, `fnvnx_lvalue`, `FNVNX_MSVC_BASE_END`, `fnvnx_ftol2`).

Fingerprint: `compare_builds.fingerprint` with the recipe's names (strict) and with link-by-address bindings, used
for the verdict: names the whole recipe binds to one address; the other linkage of a global variable, referenced
(LINK1) or defined (STATIC1); another decoration of the same import slot; the same decorated name up to MSVC's
class-key letter (U/V/T: Itanium does not encode it); `fnvnx_game_X` = the decomp's `X` (GAME1); a prepared recipe's
renamed decorations (S1b); string literals by their bytes (MSVC numbers literals with a counter every included
header advances, and a header can move them inside the data section).

Attribution: every differing or no longer compiling unit is built again with the rule set minus one group of rules
(`sdk` = DECL1 DECL2 CALL1 GAME1 DUPINT1; `newdel` = NEW1 NEW2 NEW3 NEW4 CTOR1; `exc` = EXC1; `link` = LINK1
STATIC1; `s1b` = XINC1 RET1 FTOL1; `rest` = UNIT1 IFX1 SAL1 GOTO1 STR1 RCAST1 TYPEID1 SPEC1 CSX1; KEEP1 is inert by
construction), then minus each rule of the groups that explain something, then minus pairs of groups for what no
single group explains. A function identical again without R is attributed to R. Kinds: *eh* (identical when both
sides build with `/EHs-c-`), *retarget* (same code bytes, a relocation reaches another address), *code*.

## Not comparable under MSVC

MSVC 2008 with the real SDK cannot express some of what the target accepts; those units are counted as no-build, not
against a rule: `std::abs` after DECL1 (libstdc++'s C headers export into `std`, MSVC's do not; 29 units), names the
full SDK defines that the target shim does not (`RequestLocation` in winioctl.h, `vc_attributes`, `_MEMORYSTATUS`,
`IUnknown`, `PCH`), MSVC's `float` math overloads colliding with the decomp's own (`fabsf`, `cosf`, `sqrt(float)`),
and `-fpermissive` conversions (`HANDLE` -> integer), and ENUM1's opaque declarations `enum X : int;` (C++11; MSVC
2008 parses the enum base only on a definition: 7 units). The `vc8` unit needs VC 2005 SP1.

What only the target can show: that GCC's layout equals MSVC's (TAIL1's purpose) needs a GCC-side comparison; MSVC
shows that the TAIL1 construct is inert.
