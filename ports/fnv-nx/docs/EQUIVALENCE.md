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

## Unexplained differences (S11's metric)

`tools/equiv/explain.py` matches every differing function and every no-build unit of the eq1..eq6 shard reports
(`explain.py --git <artifacts clone>`, or the report files) against the explanation classes in
`tools/equiv/explained.tsv` and S1b's by-design table (docs/PATCHES.md). What no class covers is the metric; the
target is 0. A class names the mechanism and where it is documented here; a row matches by kind, attributed rule,
unit, address or first error.

**Declared classes (D24; stream EQ).** `tools/equiv/classes.tsv` lists the explanation classes that are
*declared*: platform (the target platform differs on purpose: CRT, link by address, allocator), compiler (MSVC
2008's code generation of an equivalent spelling, measured) or by-design (a deliberate fix). Each has a reason. A
class not listed is open. explain.py prints `open (D24): N = code functions not in a declared class + no-build
units`, which is the EQ stream's metric (its target is 0). A no-build unit always counts, because nothing in it is
compared. At 2e000047 / 06f45fbb: 65 = 2 (the FINL1 pair) + 63.

At `0685791` (all six shards): 1,068 differing functions and 78 no-build units, of which **32 functions and 28 units
are unexplained**.

| class | functions | units | |
|---|---:|---:|---|
| EH-ONLY | 777 | | identical with `/EHs-c-` on both sides |
| CRT-WRAPPER | 159 | | DECL1 / sdk retargets: the CRT function where the exe calls the game's forwarding wrapper |
| PLACEMENT-NULLCHECK | 58 | | CTOR1 by design |
| EXTERN-RELOAD | 25 | | STATIC1 |
| S1B-BYDESIGN | 12 | | PATCHES.md |
| ANON1-DECORATION, STD1-FABSF, GLOBAL-NEW, DECOMP-BUGFIX | 5 | | rule rows below |
| LIBSTDCXX-STD-ABS, SDK-FULL-NAMES, PERMISSIVE-CONVERSION, MSVC-FLOAT-MATH | | 43 | Not comparable (below) |
| MSVC-OPAQUE-ENUM | | 7 | ENUM1 |
| unexplained | 32 | 28 | |

Code differences that no rule-wide class covers were read one by one (the whole MSVC diff, with `/EHs-c-` on both
sides where EH code mixes in, and the raw bytes where objdump restarts at a `$LN` label inside an instruction); each
row of explained.tsv names its unit and address. Classes: CRT-BINDING, CRT-INTRINSIC (a constant-size memcpy becomes
`rep movsd`), INLINE-DECISION, STL-CHECK-MACRO, PLACEMENT-NULLCHECK, PTR1-UNSIGNED-ZERO, BLOCK-ORDER, X87-SCHEDULE /
SSE-SCHEDULE (commuted multiplies and adds, exact float round trips), and X87-EXCESS-PRECISION: MSVC 2008 keeps a
`float` in an x87 register without rounding on one side only (a GOTO1 split `float x; x = e;` in
hkpcontinuousgsk 0x00db60c0 and hkpcollidecapsuleutil 0x00cd58b0). AArch64 has no excess precision (FLT_EVAL_METHOD
0), so the target rounds where the exe did; the class is limited to rows where both sides' source type is `float`.
After the fixes of this round (GOTO1-CONST, PTR1-DEREF, literal padding) and these rows, the local gate of every
unit with an unexplained difference at `0685791` has 0 unexplained functions; the eq farms confirm at the next head.

Retargets are explained by evidence: shard reports list, per retarget, each relocation that moved as `pristine
binding>prepared binding` (`## Retargets`), and a retarget is explained only when every pair is a `wrapper` row of
explained.tsv (CRT-WRAPPER: the game's forwarding wrappers 0x00403d30 memset, 0x00406d30 / 0x00404d80 strcpy_s,
0x00406d50 strcat_s, 0x00401460 memcpy, 0x00404dc0 _stricmp, and the CRT's own 0x00ec44d0 memcpy / 0x00eca590 tan
bound by a C++ name; CRT-INLINE: the inline C++ overload of MSVC's header that the object defines, `strchr(char*,
int)`, `abs(float)`, placement `operator new`; DECORATION: the same entity under another MSVC decoration). All 168
retargets of the gate's 112 retarget units have such pairs at `c1039e7` (local). Literal digests drop the zero padding
in front of the next symbol: `""` was `00` on one side and `00 00` on the other (blk_00c40000_od_ob1 0x00c44280).

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
| `MT1` | | | | | class `platform`: gated Switch behaviour changes (S15-threads, `patches/mt1_*.s1b`), behind `FNVNX_MT1_ON`; never in a neutral MSVC build and exempt from tests/s1b/msvc_check.py (docs/PATCHES.md, "Gated classes"). |
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

## Stream EQ: prepared-side no-build units (06f45fbb / 2e000047 reports, local gate)

The newest reports had 64 units the prepared side could not compile, so nothing in them was compared. Four
mechanisms close 41 of the 63 local ones (`equiv.py build` of those units on a tree with these rules; the 22 that
remain are listed by first error in journal/inbox/eq-001.md):

* **DECL1-STDABS** (26 units, LIBSTDCXX-STD-ABS). The decomp declares `namespace std { float abs(float); }` itself
  (49 sites) and binds it to the game's out-of-line copy of MSVC's `abs(float)` (0x00408840 -> 0x00408860,
  `(float)fabs((double)x)` through the CRT's fabs). DECL1 drops the declaration for libstdc++'s. It now includes
  `fnvnx_cmath/std_abs.h`, which on the target is `<stdlib.h>`, the header it included before (the target is
  unchanged). Its MSVC branch declares what libstdc++'s `<stdlib.h>` does, std::abs for int, long, float, double and
  long double plus the global using-declaration, without `<math.h>`'s global float overloads. The out-of-line call
  against the inline standard abs is then a CRT-INLINE difference (3 functions locally): same value for every
  input.
* **ENUM1-FWD** (10 units, MSVC-OPAQUE-ENUM). ENUM1 writes an opaque declaration as `enum X FNVNX_ENUM_INT;`.
  fnvnx_compat.h defines the macro as `: int`, and fnvnx_equiv_compat.h defines it empty: MSVC 2008 has no opaque
  declaration, and its forward declaration `enum X;` (the decomp's own) names the same int-based type, also after
  the definition (tessaveloadgame's MEM_CONTEXT).
* **SDK-LEAN** (compile_unit). A prepared unit that the full `<windows.h>` breaks is built again with
  WIN32_LEAN_AND_MEAN, which leaves out winioctl, rpc and ole, closer to the target shim's subset (RequestLocation).
  Head, /EHs-c- and leave-out builds make the same choice.
* **SDK-WINDOWS** (tools/equiv/msvc/windows.h). A prepared unit that includes `<windows.h>` itself gets the
  fnvnx_win32.h stand-in, as on the target, where the shim's windows.h is fnvnx_win32.h. Before, it got the plain
  SDK and missed the shim's looser parameter types. The stand-in reaches the SDK's own header through a generated
  absolute include (gen_dir: fnvnx_equiv_sdk_windows.h). Pristine units get the SDK's header unchanged.

* **SDK-HIDE** (generated `fnvnx_equiv_sdkhide.h` / `fnvnx_equiv_sdkunhide.h`, equiv.py `sdk_only_names`). The
  stand-in reads `<windows.h>` with every SDK function, typedef name, tag and enumerator the target shim never
  names in code renamed to `fnvnx_sdk_<name>`, then undefines the macros. The renaming skips names the CRT's C
  headers use, SDK macro names, C++ keywords and `__` names. The target has no such declaration, so a prepared
  unit cannot rely on one, and the decomp's own types and functions of those names (struct Arc, DragObject,
  typedef PCH, winioctl / winnt copies) no longer collide. This made 4 more units compile (2 identical, 2 with
  retargets only), and 150 random units build identically with and without the list.

CTOR1 was measured too, and nothing changed: MSVC 2008 null-checks the result of every new-expression before the
constructor, even for an allocation function without `throw()` (a tagged placement form: the same `test ecx,ecx`
at /O2, and an out-of-line call at /Od). So no spelling of in-place construction compiles like the explicit call
under MSVC. GCC -O2 emits no check for the standard placement form. PLACEMENT-NULLCHECK is therefore a
compiler-model class: the target code is the explicit call's.

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
| `DECL1` | neutral (claim) | the platform header declares the same CRT / Win32 function; in a namespace that keeps its own C++ copy of a CRT name (bound to a game wrapper), unqualified calls of it become `(name)(...)` so argument-dependent lookup cannot also reach the header's `::name` (DECL1-NSADL: 66 calls in 19 files; tesanimgroup was ambiguous on GCC and MSVC) | the real MSVC CRT / SDK headers |
| `GAME1` | neutral | renames a unit's own Win32-named game declaration and its call sites; the address binding is unchanged | `fnvnx_game_X` bound like the decomp's `X` |
| `NEW1` | neutral | `unsigned int` / plain `unsigned` is `size_t` on x86 (same type, same decoration) | as is |
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
| `VFTBL1` | neutral (claim) | a table the decomp defines (`static const VTable g_vtblAgent = {{(void*)&AgentRelease, ...}};`) for a name units.json binds to an exe RTTI vftable in VFTBL1_SITES (s1_compile.py; tools/rules/check_vftbl1.py checks each site against the exe and lists candidates) becomes a declaration (`extern const VTable g_vtblAgent;`, namespace ones `extern "C" { ... }`); only where the file takes the table's address and nothing else. The code stores the address of the same name, now the image vftable (the link: native vtable address point or D26 minus-one copy) | variable bound by the recipe's address, the vftable at that address |
| `GOTO1` | neutral | `T x = e;` -> `T x; x = e;` for scalars; a const integer literal (`const int n = 4;`, an array bound) stays as written unless a forward goto crosses it, then `static const` (GOTO1-CONST: the split made `n` a VLA bound GCC accepted and MSVC rejected, C2057 in hkpagent1nmachineprocess_..._rtm_variant; now identical) | as is |
| `GOTO2` | neutral (claim) | a forward goto across an initialized class object / reference / aggregate into its scope: `const float& a = f(x);` -> `float a; a = f(x);` for an f returning float by value; `{ ` ... `} ` around the crossed declarations before a label of their block when nothing they declare is named after it; or the label's straight `...; return e;` tail (names not declared in the function) cloned into each crossing goto | the path that jumped never used the crossed objects; scopes end earlier, a cloned tail is the same statements |
| `QUAD1` | neutral | `return q;` -> `return (__m128)q;` where q's class converts to `__m128` and the function's return class is constructible from `__m128` only: MSVC chained both user-defined conversions | the same conversion operator and constructor, the first written out |
| `MSCV1` | neutral | `_MSC_FULL_VER` / `_MSC_VER` in code (not in `#if`) -> the value of the compiler build/units.json gives the unit: sp1 150030729 / 1500, rtm 150021022 / 1500, vc8 140050727 / 1400 | the constant that compiler substituted |
| `STR1` | neutral | `&arr` and `arr` are the same address | as is |
| `CALL1` | neutral | the cast is the conversion the dropped prototype made implicitly | real SDK parameter types |
| `RCAST1` | neutral | `fnvnx_lvalue` returns the temporary itself | `fnvnx_lvalue(x)` = `(x)` |
| `NEW3` | neutral | deallocation functions are implicitly `noexcept` (C++11) | `noexcept` empty |
| `TYPEID1` | neutral | adds `<typeinfo>` only | real `<typeinfo>` |
| `SPEC1` | neutral | early `template<>` declarations of member specializations | declarations only |
| `DUPINT1` | neutral (claim) | keeps one of two integer-only-different prototypes bound to one address; DUPINT1-RET (S1-refs): also when the return types differ, if every mention of the name is a call whose value is discarded and the differing parameters are 32-bit integers | as is |
| `RREF1` | neutral | `fnvnx_lvalue(...)` around an argument passed to a non-const reference parameter (every declaration the unit sees; for an unqualified call in a member function, the declarations of the nearest class that declares the name, RREF1-CLASS) that is not a plain lvalue | the temporary MSVC bound, same lifetime |
| `RREF2` | neutral | `fnvnx_lvalue(T(args))` as the operand of a member operator whose every declaration with a T parameter takes a non-const `T&` (S1-refs) | the temporary MSVC bound, same lifetime |
| `ADDR1` | neutral | `&_mm_f(args)` -> `&fnvnx_lvalue(_mm_f(args))` as the operand of a unary `&` (MSVC's address of a class rvalue, C4238) | the same temporary's address, same lifetime |
| `LANE1` | platform | `&((T*)&(v))`, SSE1's lane array with its address taken (`(&v.m128_f32)[0]`), -> `((T(*)[sizeof(v) / sizeof(T)])&(v))` | MSVC's `&v.m128_f32`: same address and array type; SSE1 is target only |
| `STD2` | neutral | a file-scope definition `R std::f(...)` whose recipe symbol is `?f@std@@...` (guardpackage.cpp's `double std::abs(float)`, 0x00408840) -> `R fnvnx_std_f(...)`; a POST hook renames the recipe symbol to `?fnvnx_std_f@@...` (S1-refs) | same body and signature, only the decorated name differs; callers bind the address; the Fingerprinter takes the renamed definition from the prepared recipe |
| `COND1` | neutral | `c ? false : x.f(a)` (or `? true :`) where f returns a class with `G(bool)` and `operator bool()` and every same-arity declaration of f returns a bool-convertible class -> `c ? G(false) : x.f(a)` (hkgpmesh.cpp, S1-refs) | GCC finds both conversions and refuses; MSVC converted the literal to G (its code tests the result byte directly; a `(bool)` cast on the G operand measured different: `setne`/`test`) |
| `RSTR1` | neutral | a function-local `T* __restrict v` passed by name to a parameter the unit declares `const X&` -> `fnvnx_unrestrict(v)` (hkpsavecontactpointsutil.cpp, S1-refs) | `#define fnvnx_unrestrict(x) (x)` under MSVC (fnvnx_equiv_fi.h): the original text; under GCC the same object as an unqualified lvalue, so the reference binds the local as MSVC's did |
| `NEW5` | neutral | a base-less class with a virtual destructor whose only scalar `operator delete` is the placement form `(void*, void*)` gets `FNVNX_GLOBAL_DELETE` first in its body: under GCC `static void operator delete(void*)` forwarding to `::operator delete`, which MSVC 2008's scalar deleting destructor called (0x00d979a0: a one-argument cdecl call, 31 bytes); GCC otherwise rejects the deleting destructor (S1-refs) | `FNVNX_GLOBAL_DELETE` empty (tools/equiv/msvc/fnvnx_equiv_fi.h): the decomp's class unchanged |
| `NSCALL1` | neutral | an unqualified call, inside namespace N, of N's own C++ prototype of a CRT / Win32 name (`strcpy_s`, `_stricmp`, ... DECL1 keeps it) -> `N::name(`, where the unit's units.json binds that name only in N and no class of the unit declares a member of that name; `<math.h>` names excluded (S1-refs) | the function MSVC called: on MSVC only N::name was declared; on the target a class-type argument brought the shim's ::name in by argument-dependent lookup |
| `FINL1` | neutral | `__forceinline` dropped from a body-less declaration (`;`, file scope or class C) in a .cpp whose unit has no body of that name with the same parameter types and const (`C::name(...) {` or in C's braces) (tile_ob1_variant, three Havok `_rtm_variant` agents, blk_00ae0000_od_ob1; GCC's always_inline: "function body not available" at -O2) (S1-refs) | MSVC cannot inline a body it has not seen: the call was an ordinary call, which the declaration without the keyword gives |
| `IDTOR1` | neutral | `FNVNX_IDTOR1(C)` (GCC: `public: ~C();`, MSVC: nothing) at the end of a class C (.cpp, namespace or class scope, no template, no destructor of its own) whose implicit destructor the unit binds as `??1C@...@@QAE@XZ` to a decompiled address: GCC calls C's D1 by name and the link binds it to the exe's function, instead of an inline implicit destructor built from the placeholder members. extradataobjects.cpp `List_009e03e0` -> 0x0046ffb0 (the exe passes this+0xc, the build destroyed `m_uiX` at +0x10), S13b layout-offset (S1-layout); build_and_match with the exe: 36 / 36 edited units unchanged | MSVC's implicit destructor already is that call; a real declaration would renumber the unit's `$SG` literals |
| `TPL1` | neutral | `this->` in front of the members of a dependent base that a class template's member functions use by plain name | the members MSVC's (one-phase) lookup found |
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
| `VLIT2` | platform | VLIT1 for any unit, by the exe's vftables (tools/rules/_vlit_vftables.py -> private fnv-nx-build recon/vlit_vftables vlit.json, from FNVNX_RECON): a 1-3 character literal stored with `=` (also cast to `void*` / `const char*`) whose bytes are a defined function's address -> `(char*)&DAT_<V>` for the one vftable V with that slot 0 at the store's offset (`(char*)this + N`, else 0 through `this`), the class of the function's other vptr stores breaking a tie (TESObjectARMA 0x00511f60: `" rH"` at +0x68 -> 0x0102a28c, not TESObjectARMO's 0x0102a59c). 47 stores in 33 files; undecided VLIT2-LIST 2 (S1-rules, from S2-bodies) | as VLIT1: the store is the vftable address, as in the binary |
| `FRIEND1` | neutral | `class X;` in front of the outermost class around a `friend class X;` when X is declared nowhere before (MSVC 2008 made the friend's name visible in the enclosing namespace; GCC does not) | same class, same names |
| `INIT1` | platform | a static object whose dynamic initializer the exe has and the decomp lacks: one `static` NiMainSDM (constructor 0x00a8a5f0) in staging/blk_00a80000_o2.cpp | the unit gets a dynamic initializer MSVC's object of the decomp has not; the functions are unchanged |
| `CSX2` | neutral | 4 bytes behind a generated-layout `_RTL_CRITICAL_SECTION` member whose class asserts it as 28 bytes (NiCriticalSection, RecursiveLock and their holders) | the offsets the pristine MSVC build and the byte-matched code have (J-0067) |
| `STAND1` | platform | removes an untagged definition whose MSVC name the unit binds to the address of a tagged definition in the same unit (a free function keeps its declaration) | the call reaches the tagged function, as in the binary; an optimizing MSVC build may have inlined the stand-in |
| `STAND2` | platform | STAND1 for an address another unit's tagged definition provides, only for a non-inline, non-static definition in a unit compiled without /O1, /O2, /Ox, /Ob2 | MSVC never inlined the stand-in there, so its code called the name, which the link took to the other unit's function |
| `STAND3` | platform | in an /Od unit without /Ob1, /Ob2, /O1, /O2, /Ox, removes the body of an untagged in-class definition, or of an out-of-class or free one marked inline, whose MSVC name the unit binds to one address that some unit decompiles (declarations stay) | MSVC inlines nothing there, so the binary calls that address at every site; the call now reaches it too (the link binds the name, tier unit/exact) |
| `STAND4` | platform | STAND1 / STAND2 for an untagged `static` free stand-in at namespace scope (not inline, template or overloaded) whose MSVC name the unit binds to one decompiled address: the definition becomes an extern declaration and the unit's other declarations of the name lose `static` | MSVC decorates a static free function like an extern one and its /Od code called that name, which the recipe binds to the exe function; GCC inlined the stand-in's body; a lone `static __declspec(noinline)` stand-in (any body: `return 0.0f;`, a constant read, a store) converts under the same address gates in a unit of any flags, because MSVC never inlined it (0x00d57b20 and 0x00d57aa0 call 0x00d571d0, 0x00d573e0 and 0x00d57220 through such stand-ins; EQ fuzz native-zero-stub). Other `__declspec` heads (align, naked) and names with no address stay |
| `PHSLOT1` | platform | `virtual void vNNN()` (a placeholder name: v and three hex digits) followed by an empty body `{}` becomes `virtual void vNNN();`; named virtuals and non-void placeholders are left alone | The empty body is no function of the game: the exe's slot holds another function (NiAVObject, magicprojectile.cpp: slot 3 is 0x00acbb70, `xor eax,eax; ret`), so MSVC's `ret` COMDAT for it matches nothing in the exe; declared-only, the slot is a trap that exe_slot_fill binds to the exe slot's function, the same as every other shell's placeholder. Under MSVC the edited class's vtable names the undefined placeholder instead of a local COMDAT, and no other function changes. 1,627 sites in 25 files; on link 0540aff0 only `_ZTV10NiAVObject` had defined vNNN slots (59, all `ret`). EQ difffuzz records 0x00477560, 0x009abe40 |
| `STAND5` | platform | STAND2 for a member function body inside its class (not virtual, not a constructor, destructor or operator, untagged) whose MSVC name, in every spelling the unit's `symbols` hold, binds an address another unit defines and this unit does not: the body becomes its declaration (tools/rules/s2_bodies.py) | MSVC called that name out of line, and the link binds it to the exe function; GCC would inline the local body, which references names no exe function has |
| `STAND6` | platform | STAND2 for an untagged `__declspec(noinline)` out-of-class member or free definition, in a unit of any optimization flags: every spelling of its MSVC name in the unit's `symbols` binds an address another unit defines and this unit does not; not virtual, inline, static or a template. A member's definition goes, a free function keeps its declaration (tools/rules/s2_bodies.py) | MSVC never inlined a noinline function, so its callers called the name, which the recipe binds to the exe function the other unit decompiles; GCC compiled the local stand-in's body instead |
| `ADDRFN1` | platform | `reinterpret_cast<void*>(0x00<a>)` of a .text address some unit defines becomes the address symbol `__fnv_<a>` through a GCC block-scope asm label in a statement expression (tools/rules/s2_bodies.py; BSFile's and the archive's read/write handler pairs) | the exe stores its own function's address there; natively the raw exe address would be stored and called, the link binds `__fnv_<a>` to the native definition of that function |
| `POP2` | neutral (claim) | pop-mismatch chains decided from build/units.json (a POST hook of s2_bodies.py, on the prepared tree): a member call whose reference pushes s words more than its definition pops, directly followed by a call whose reference pushes s words fewer (`D(a.., x..)` used as a bool -> `D(a..).FUN_<R>(x..)`, `D(a.., x..)->R()` -> `D(a..)->R(x..)`, `R(.., D(a.., x..), ..)` -> `R(.., D(a..), x.., ..)`), with the overloads declared in the class bodies MSVC's names place (unit or local header) and their decorated names added to the unit's `symbols`; side-effect-free moved arguments only; a receiver declared with an untagged inline body (a unit-local copy's stand-in) gets the overload declared without a body, so the call binds the exe function (W25: 0x00515340, 0x005807a0); a receiver cast to a class written with namespaces names that class (0x00562660) | MSVC pushes the same words in the same order (S1b's popgen patches of the same shapes compiled identical); the farm's S11 gate checks it |
| `THIS2` | neutral (claim) | a caller in THIS2_SITES (exe evidence: no ecx write and no other call before its call of the callee) that calls a thiscall definition through a free `__stdcall` declaration: a free stdcall caller becomes a member of a shell struct declared on its head line (definition renamed in units.json), a member caller calls `((Shell*)this)->f(..)`; the new reference names go to `symbols` (POST hook) | S1b's XINC1 shapes A-C: same pushes, ecx = the caller's object as before; MSVC may load ecx once more (XINC1's 4 reloads) |
| `HARN1` | platform | an untagged free function definition at namespace scope (not a member, virtual, inline, template, friend or operator) whose identifier no unit's units.json `symbols` or `functions` names becomes `static`, with its namespace-scope prototypes in the file; `extern "C"` on them goes (tools/rules/s2_bodies.py). These are the decomp's byte-matching harnesses (`int stub_caller() { return FUN_x(stub_a(), stub_b()); }`): no exe function is theirs and nothing calls them | platform, not neutral (EQ, measured on 60 of its 203 units: 88 of 2,977 recipe functions are missing from the MSVC objects with HARN1). A harness can be the only reference to a `static` tagged recipe function (`S00c7f4a0_Call` -> `static FUN_00c7f4a0`, actor_o2_variant); once the harness is static and unused, MSVC drops both. On the target KEEP1's `__attribute__((used, noinline))` keeps the tagged function, and GCC drops only the harness and its references to stubs the decomp never defines (trap stubs) |
| `PTR1` | neutral | `f > 0` / `p > 0` -> `!= 0` and `<= 0` -> `== 0` for a function name or a local pointer; not for `*p` of a `T* p` (PTR1-DEREF: `*type <= 0` of an `int* type` is a signed compare, bhkrigidbody 0x00c8cce0's caller 0x00c8dd10 got `je` for `jle`; now identical, with blk_00a20000_o2 0x00a29fe0) | MSVC's unsigned compare of the address gives the same result |
| `INC1` | neutral | a quoted `#include` that names, up to case, a file in the including directory is spelled as that file (`"Actor.h"` -> `"actor.h"`) | the header MSVC opened (directory of the includer first, names without case) |
| `ANON2` | platform | a member with a constructor / destructor / copy assignment in an anonymous struct of an anonymous union becomes raw storage of its size and alignment, its uses `(*(T*)(x.m))` | same bytes and calls; MSVC never ran those functions for a union member either |
| `ANON3` | platform | an anonymous union in a function body with an anonymous struct in it -> `struct fnvnx_anon_uN { union {...}; } fnvnx_anon_vN;` and `decltype(v.m)& m = v.m;` for each member (GCC takes an anonymous struct only inside a named class) | same storage and layout under the same names; MSVC's code may keep the references |
| `ELAB1` | neutral | type uses of a file-scope class named like a Win32/CRT function (`struct HeapAlloc`) spelled elaborated: `struct HeapAlloc* p` | same type, same symbols |
| `ENUM1` | neutral | opaque `enum X;` -> `enum X : int;` and every definition of those enums -> `enum X : int {` | int is MSVC's underlying type of every unscoped enum |
| `ANON1` | platform | names an unnamed struct defined in a member function's return type (`struct fnvnx_anon_Float {...}; fnvnx_anon_Float* Float();`) | same layout; MSVC's decorated name of the function changes (`<unnamed-tag>`) |
| `STL1` | platform | includes `fnvnx_msvcstl.h` (MSVC 2008 `<xutility>` internals: `_SCL_SECURE_VALIDATE`, `std::_Checked_base`, `std::_Ptr_cat`, iterator tags) where a unit uses them; in a unit deriving from `std::_Container_base_aux_alloc_real` also its AUX part (container base, `_Destroy_range`, `stdext::unchecked_uninitialized_copy`, checked vector iterators) and `vector<T>::const_iterator` / `::iterator` spelled `_Vector_const_iterator<T, A>` / `_Vector_iterator<T, A>` | MSVC's own headers define them, and the iterator names are MSVC's names of the same types; the gate builds with `<xutility>`, `<memory>`, `<vector>` |
| `STL2` | platform | in the 7 units that include the real `<string>` / `<vector>` / `<map>`: `std` -> `fnvnx_vc9`, `stdext` -> `fnvnx_vc9::stdext`, `size_t` in the unit's own `namespace std` -> `_Sizet` (unsigned int), and `fnvnx_vc9.h` for the headers. The shim is VC9's string / vector / map (`_Tree`) with the x86 release layout (28 / 24 / 32 bytes, iterators 8; static_asserted, checked against 0x00acb200, 0x00b31270, 0x00abfa90), VC9's growth and string algorithms, `_SECURE_SCL` checks through `_invalid_parameter_noinfo`. Not byte-identical code (GCC compiles the templates), the same layouts and results; libstdc++'s containers (string 24, vector 12) would move every game-struct member after them | MSVC side: the real VC9 headers (the shim is C++11) |
| `STLB1` | platform | tools/rules/s4_stl.py: in a unit whose own top-level `namespace std` copy declares VC9's helper templates and defines none (SpeedTree's indexed geometry), their bodies with VC9's semantics: `_Allocate` (operator new, bad_alloc on overflow), `_Destroy`, `fill`, `swap`, `vector<T>::_Xlen` (length_error through `fnvnx_xlength_error`). A name a recon (REC1) covers or the unit's units.json binds is left to it | MSVC compiled these inline from <xmemory>/<xutility>; the same results, not the same code |
| `STLB2` | platform | tools/rules/s4_stl.py: in a unit's in-unit iostream / `vector<T>` model, the bodies of members it declares and never defines: `ios_base::setstate`, `basic_ios::init` (S4's `fnvnx_basic_ios_init`), `<F>UseFacet` and `widen` (separately), `basic_ifstream::open` (VC9's `basic_filebuf::open` over S4's `fnvnx_fiopen`), `vector<T>`'s constructor and destructor. Left to a recon or a units.json binding where one exists | MSVC: msvcp90 / the real headers; the same semantics |
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
| `VORDER1` | layout | adjacent same-name virtual declarations of the decomp's generated headers (`// slot 0xNN` comments) are sorted into ascending exe slot order: MSVC lays an overload group out in reverse declaration order, GCC in declaration order (BaseProcess.h, 9 pairs; S2 seamcheck VCALL 00932f96). Under MSVC the sorted order reverses the vtable, so a difference is expected exactly there. POST `vorder_post_check` (VORDER1-LEFT) fails prepare on a same-name descent left in a class | MSVC's own (reversed) overload layout (not applied on the MSVC side) |
| `EBO2` | layout | `FNVNX_EBO2` (GCC: `char fnvnx_ebo2[0];`, MSVC: nothing) first in an empty base B (no bases, data or virtuals; used only as a base, `B*`, `B&`, `B::`) whose derived classes all have it as their only base, one of them introducing its own vfptr: MSVC places B after the vfptr (+4), the Itanium ABI at 0 under the vptr; members and sizes are unchanged. effectitemlist.cpp `EffectItemList : BSSimpleList_Standin` (exe passes this+4 to IsEmpty 0x008256d0; pristine unit = exe byte for byte), S13b layout-offset (S1-refs) | MSVC's layout of the class, not applied on the MSVC side |
| `VFPAD1` | layout | `FNVNX_VFPAD(member types)` (GCC: a zero-width bit-field aligned like the most aligned member type; `FNVNX_VFPAD_PACK(N, ...)` under `#pragma pack(N)`, nothing for N <= 4; MSVC: nothing) first in a class with no base, a virtual function and a member aligned to 8 / 16: MSVC pads the class's own vfptr to its alignment, GCC places the first member at 4. LAYOUT gate vfpad rows (hkaabbtree.cpp FUN_00d9c110_cls: m10 at 16 under MSVC, as named); held: a member type nested in the class, an anonymous aggregate (VFPAD-HELD), and VFPAD1_SRC (vfpad-src: offset names agree with the unpadded layout) | MSVC's layout of the class, not applied on the MSVC side |
| `REC1` | neutral | D20: bodies of functions the decomp lacks, from the private fnv-nx-build `recon/<unit>` branches (FNVNX_RECON), appended to their unit with their recipe entries and bindings (POST hook); each is accepted only with build_and_match = all bytes equal against the exe (recon/bscore_memoryheap: MemoryHeap 0x00aa78a0 / 0x00aa7930, 19 / 19 functions, 1,246 / 1,246 bytes). REC1-REPL: a recon's `replace` pairs (exact text, each `old` exactly once or the prepare fails) edit the unit's own code before the append, where a body cannot help (a filler the unit's calls bind to, arguments on the wrong call); under MSVC the unit stays all bytes equal. Nothing without FNVNX_RECON (S1-refs) | new functions; replace edits keep every recipe function's MSVC bytes (build_and_match all equal) REC1-REPL: a recon's `replace` pairs (exact text, each once, applied before the append) are part of the same accepted recon and gated with it; a pair that does not apply fails the prepare |
| `HUNIT1` | neutral | a header unit whose recipe lists class-template members it only references through a null pointer (nitarray_h_unit.cpp: NiTArray<ActorCombatGroup*, ...>::SetAt 0x0096ae90, SetAtGrow 0x00470000, SetSize 0x0096ad30) gets explicit instantiation definitions of exactly those members: GCC at -O2 inlined the references and emitted none (S5-boot: 1,238 trap hits); `used` cannot help because the class is instantiated before the members' definitions (S1-refs) | MSVC emitted those COMDATs already; the instantiations name the same members |
| `XC2` | platform | XC1's form for a hand-written dynamic initializer XC1 does not see (it rewrites generated/ only): nipoint3_console.cpp `gNiPoint3StartupDst = gNiPoint3StartupSrc` (??__E at 0x00a62110, called by InitializeGameSystems) -> `extern` object (bound to its image address 0x011f4600) + `extern "C" __fnvnx_dyninit_00a62110` doing the copy (S1-refs) | as XC1: target only |
| `EBO3` | layout | `FNVNX_EBO2` first in an empty base E (no bases, data or virtuals; used only as a base, `E*`, `E&`, `E::`; never a virtual base) that follows a base with data defined in the same .cpp: MSVC places E after the bases before it (`struct Node : NodeBase0, NodeBase16 {}`: E at +0x10, size 16), the Itanium ABI at 0; members and sizes are unchanged. bgszonetargetlistener.cpp 0x0061ff30 (exe passes node+0x10 to 0x006207a0; pristine unit = exe), S13b layout-offset (S1-layout) | MSVC's layout of the class, not applied on the MSVC side |
| `D22` | neutral | D22 (ARCHITECTURE.md): a listed class's slot-0 placeholder virtual (first declared, no base, no destructor, not defined outside the class or called by name) -> `FNVNX_D22(C, <declaration>)`: GCC `virtual ~C();` (D1/D0 where MSVC's ??_G is, the link's minus-one rule), MSVC the original declaration; a derived class's re-declaration of that placeholder -> `FNVNX_D22_DROP(<declaration>)` (GCC: nothing). The list is exe-derived (slot 0 of the paired exe vftables is a deleting destructor): tools/rules/_d22_classes.py writes d22.json to the private fnv-nx-build recon/d22_vtable_dtor, read from FNVNX_RECON; nothing without it. D22-USED / D22-DERIVED count skipped and derived sites (S1-refs). D26 (b, S1-rules): a call-only placeholder declaration (no base or destructor, two or more virtuals, none defined in the file, first virtual a placeholder name, every derived class of the file defining nothing) takes the same FNVNX_D22 without a list (D22-CALLONLY; tests/s5/callonly_vt.py's set) | the macros expand to the original text under MSVC: objects unchanged |
| `HKRO1` | neutral | a `hkReferencedObject` whose only virtual is its destructor gets `FNVNX_HKRO1` after it: GCC declares Havok's getClassType / calcContentStatistics (exe slots 1 / 2 of every hkReferencedObject-derived vftable) | `FNVNX_HKRO1` is empty under MSVC |
| `VSPLIT1` | neutral | a virtual declaration with several declarators (`virtual void v000(), v004();`) -> one declaration per declarator with the same decl-specifiers, before D22 (EQ G3: FNVNX_D22 took the whole list) | the same declarations in C++; MSVC's objects are unchanged |
| `EMPTY1` | neutral | a use of a declared-only `extern const T NameEmpty;` (T declares `T(const char*)`, the file defines no such object) -> `FNVNX_EMPTY1(T, NameEmpty)` (treeengine's TagEmpty: the exe's 0x00b09480 builds `T("")`) | `FNVNX_EMPTY1(T, name)` is `name` under MSVC |
| `RAWVT1` | neutral | a raw exe slot index k > 0 on `this` (`(*reinterpret_cast<void***>(this))[k]`) in a member of a non-COM class -> `FNVNX_D26_SLOT(k)` (D26: GCC's vtables are minus-one); RAWVT1-MEM: the same for a called slot of the vptr modelled as a data member (`m_ppVirtualMethodTable[k]`, `vtbl[k]`, ...) on `this` or a local `= *(void***)this` (BSDiscBootSystemUtility::Initialize recursed) | `FNVNX_D26_SLOT(k)` is `(k)` under MSVC (fnvnx_equiv_fi.h) |
| `DTORB1` | neutral | a destructor `X::~X` tagged with the address of X's ??_G, whose `??1X` the unit binds to another function A that takes only `this` (X without bases, data members of built-in or pointer type): under GCC the body becomes a call of `__fnv_<A>` with the original body unreachable (`FNVNX_DTORB_BEGIN(A)` ... `FNVNX_DTORB_END`). MSVC's ??_G calls ??1X, which the recipe binds to A; GCC's D0 called X's own empty D1 (fuzz oracle-fault 0x004aa1e0 etc.; 21 sites) (S1-rules) | both macros are empty under MSVC: objects unchanged |
| `STAND7` | neutral | an `inline` out-of-class definition of a class template member in an /Od /Ob1 unit whose one decorated name (`?m@?$C@`) the unit binds to an address another unit defines and the file does not tag: the definition goes under `#ifdef _MSC_VER`, so GCC calls the bound name (treeengine_ob1_variant.cpp `basic_string::assign` 0x0044a580: a frame-reservation stand-in that GCC inlined into a `_Tidy` call; EQ 00b2b910). 6 sites, each adds exactly one undefined reference, its bound instantiation (S1-rules) | MSVC compiles the same text |
| `INTL1` | platform | edits only the decomp's non-MSVC branch (`#else` of `_MSC_VER`) | invisible to MSVC |
| `ALLOC1` | platform | drops untagged file-scope replacements of the global operator new/delete | target only |
| `XC1` | platform | dynamic initializers become extern + `__fnvnx_dyninit_<addr>` run by S10 | target only |
| `VA1` | platform | MSVC `char*` va_lists -> real `va_list` | target only |
| `FALL1` | platform | `__builtin_trap()` at fall-off ends of non-void functions | GCC only |
| `ASM1` `ASM2` `ASM4` | platform | rule ASM-Π: every `__asm` construct stays verbatim for cl and is empty for GCC, whose N3 verdict `asm` makes the function lifted | - |
| `NATIVE1` | platform | units leave the build (native renderer, D6) | - |

## Gate of the rules after `0685791` (local, single units)

Sites: each rule applied by itself to every decomp file; units by include closure (a superset of the real sites).

| rules | units | result |
|---|---:|---|
| GOTO2, QUAD1, MSCV1, RREF1-CLASS, RREF2, ADDR1 (`c1039e7`) | 447 | no differing function and no failing unit beyond the `0685791` gate's (all explained above) |
| TPL3, INST1, TYPN1, COPY1 (S1-STL, `6318d13`) | 1 | blk_00ac0000_od_ob1 compiles (with TPL2's specialization fix): 4 functions differ, identical with `/EHs-c-` (EH-only) |
| STAND2, STAND3 (platform, `6318d13`) | 133 | the neutral set with and without them: the same differing functions and statuses in every unit whose source they change (a removed stand-in only changes which definition the bound name reaches) |
| bsshader units with `_ReadWriteBarrier` | 10 | all compile under the stand-ins; bsshader.cpp identical, the rest EXC1 EH-only |

LANE1 and ANON3 are platform rules that only rewrite what other platform rules (SSE1) or GCC need; the neutral gate
leaves them out.

## Evaluation order: ORDER-OBJECT and ORDER-OPERATOR (for S12)

`tools/equiv/order_check.py` compiles `tests/s11/order/order.cpp` (27 cases, ours) with MSVC 2008 SP1 and RTM in the
decomp's flag sets (`/O2`, `/Od /Oy-`, `/O2 /Ob1`), runs it under Wine, and compares the logged order with the
build's GCC (AArch64 ILP32, compile.sh's semantics flags, the fnvnx_order plugin as compile.sh loads it) under
qemu-aarch64. The MSVC results are recorded in `tests/s11/order/order.msvc`, so `order_check.py <scratch> --gcc`
needs no Wine (S12's machines); `--msvc --record` measures them again; `--strict` fails on a `differs` case.

Measured at the integration head of this note (plugin `args=rtl`):

| case | MSVC 2008 (all 5 configurations unless noted) | GCC build | verdict |
|---|---|---|---|
| `obj()->m(a())`, `(*obj()).m(a())`, `ref().m(a())` | `ao`: arguments right to left, then the object | `oa` | differs |
| `obj()->m(a(), b())`, `ref()(a(), b())` (member operator()) | `bao` | `oba` | differs |
| `obj()->next()->m(a())` | `aon`: the argument before the whole object chain | `ona` | differs |
| `obj()->vm(a())` (virtual) | `oa`: the object first | `oa` | same |
| `obj()->sm(a())` (static member through an object) | `oa` | `oa` | same |
| `(obj()->*pmf())(a())` | `aom` at /O2, `amo` at /Od | `oma` | msvc-varies |
| member `<<`, `>>`, `[]`, `+`, `==` (`ref() OP a()`) | `ao` | `oa` | differs |
| member `=`, `+=` | `ao` | `ao` (C++17: right operand first) | same |
| free `y << a()` | `ao` (operands are arguments: right to left) | `oa` (C++17 fixes `<<` left to right) | differs |
| free `y + a()`, `a() - y`, `y += a()` | `ao`, `oa`, `ao` | same | same |
| built-in `*p() += a()` | `pa` | `ap` (C++17) | differs |
| built-in `*p() = a()`, `arr()[i()] = b()` | `pa` / `abp` at /O2, `ap` / `bpa` at /Od | `ap`, `bpa` | msvc-varies |
| built-in `a() + b()`, `a() << b()`, `a() * b() - c()` | left to right | same | same |
| `x.m(a(), b())` (calibration, ORDER-ARGS) | `ba` | `ba` | same |

So MSVC 2008 treats `this` of a non-virtual member call, and the left operand of a member operator, as the last
argument: evaluated after every argument (it is passed in ecx, after the pushes). A virtual call evaluates the object
first (it needs the vptr). A free operator's operands are ordinary arguments, right to left. `-fstrong-eval-order=some`
or `=none` fix none of the member cases (the plugin keeps `this` first) and `=none` breaks member `=` / `+=`. The
fix belongs in the plugin (S12): a non-virtual member call (also through an operator, also with
`CALL_EXPR_ORDERED_ARGS`) evaluates `this` after its arguments; a free operator call with ordered arguments (`<<`,
`>>`) gets the right-to-left chain too; built-in compound assignment evaluates its left operand first. The
`msvc-varies` shapes are unspecified in the exe as well (the order follows the optimizer), so only the per-site x86
code can decide them. `order_check.py --gcc --strict` is the acceptance test for that change
(`--strict-variant NAME` checks a candidate variant instead of the build).

**S12's switches** (`fnvnx_order object=last|first`, `operator=rtl|cpp17`, S12 branch `8def1d8`). Whenever the plugin
source has them, order_check.py also measures the variants `object-last` and `object-last-rtl`. Cases that differ
from an order every MSVC configuration agrees on:

| GCC variant | differ | cases |
|---|---:|---|
| build (defaults) | 13 | the 5 non-virtual ORDER-OBJECT cases, 7 ORDER-OPERATOR (member `<<` `>>` `[]` `+` `==` `()`, free `<<`), built-in `+=` |
| `object=last` | 6 | `obj-virtual`, member `<<` `>>` `[]`, free `<<`, built-in `+=` |
| `object=last operator=rtl` | 2 | `obj-virtual` (MSVC: object first, `oa`; the variant gives `ao`), built-in `*p() += a()` (MSVC `pa`, GCC `ap`) |

So the build needs `object=last` for non-virtual calls only (a virtual call keeps the object first, as `first` does),
`operator=rtl`, and the left operand first for built-in compound assignment. S12's own `tests/s12/order/evalorder.cpp`
(11 cases) prints the same text in all five MSVC configurations (`order_check.py <scratch> --msvc-stdout
evalorder.cpp evalorder.msvc.expect`); it matches neither of S12's expectations: `member`, `member2`, `retslot`,
`shl`, `shr`, `index` are those of `object=last operator=rtl`, while `virtual` / `virtual2` (`ga:`, `gyx:`: the object
first) are those of the defaults.

**The build's own order** (V1 C1/T1/T2 fixed): the `build` variant is now the plugin with every `ORDER_*` default of
`tools/link/compile.sh`, read from that file (environment overrides as there). `--strict` needs `--gcc`, and a case
that some MSVC configuration did not log (`no-msvc`, `msvc-incomplete`) fails it. At the integration head with S12's
per-shape switches (`object=msvc operator=rtl assign=msvc`): **0 of the 24 agreed cases differ**; the three
`msvc-varies` shapes (`(obj->*pmf())(a())`, `*p() = a()`, `arr()[i()] = b()`) match one MSVC configuration each (`aom`,
`ap`, `bpa`). `object=last` (all member calls) still gets `obj-virtual` wrong: MSVC evaluates a virtual call's object
first.

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
(SDK v6.0A) adjusted where the target shim deliberately differs (no A/W name macros, no `min`/`max`, no object-like
SDK macros the decomp uses as its own names: winerror.h's `ERROR_*`, `small`, `hyper`, `Yield`, `_FILETIME_`, the shim's
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
2008 parses the enum base only on a definition: 7 units, and actor, packagedatadialogue, tessaveloadgame where an
interaction is attributed), decomp structs named like full-SDK functions or enumerators (DragObject, Arc, CacheData)
and the SDK's own gethostname, ANON2's C++11 `alignas(T)` (MSVC 2008 has none; nipoint3interpolator, extradatalist),
the CRT's const-correct `strrchr` overload (animation, -fpermissive on GCC), compat's C++14 sized `operator delete[]`
hiding MSVC's implicit unsized one from an explicit call (tesland_od_variant_decl_variant), and `<intrin.h>` included
inside a namespace, whose extern "C" `_ReadWriteBarrier` MSVC 2008 keeps apart from the global one
(hkadeltacompressedanimation). explained.tsv has a row for each. The `vc8` unit needs VC 2005 SP1.

What only the target can show: that GCC's layout equals MSVC's (TAIL1's purpose) needs a GCC-side comparison; MSVC
shows that the TAIL1 construct is inert.
