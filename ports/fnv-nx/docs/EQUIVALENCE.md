# Rule equivalence under MSVC 2008 x86 (stream S11)

**Status: interim** (decomp `87eb9373`; neutral-set build 1,899 of 2,988 units at the time of writing). Numbers below
are marked with the run they come from; the tables are completed as the runs finish.

The decomp is byte-matching MSVC 2008 x86-32 code, so the compiler it matches is an oracle for the prepare rules: a
rule that only changes spelling must leave every function compiling to the same code under that compiler. S11 builds
the pristine decomp and prepared trees with MSVC 2008 SP1 / RTM under Wine and compares them per function with the
decomp's own `compare_builds.py` logic (code bytes, padding, every relocation resolved to its original address).
A differing function means the rule changed what the code does under x86, or the rule was misclassified, or MSVC
cannot express what the target does (then the difference is explained, not counted against the rule).

Tool: `tools/equiv/equiv.py` (`tools/equiv/README.md` has the compiler setup, hashes and commands). Tests:
`tests/s11/run.sh`.

## Classification

* **neutral** - claims to change spelling only, on any target. Compared on all units: expected identical.
* **layout** - changes a layout on purpose (for GCC) or swaps in another definition of a type. MSVC class layouts
  (`/d1reportAllClassLayout`) and code are compared before/after; a difference is expected exactly where the layout
  really changes.
* **platform** - replaces an x86/MSVC construct by a target one (sse2neon, portable asm helpers, native renderer) or
  edits code MSVC never compiles. No x86 meaning to compare against.

| Rule | Class | Why | MSVC meaning used in the comparison |
|---|---|---|---|
| `SSE1` `SSE2` `SSE3` `SSE4` | platform | the decomp's copies of MSVC's vector types / intrinsic prototypes give way to sse2neon's | - |
| `DECL1` | neutral | the platform header declares the same CRT / Win32 function | the real MSVC CRT / SDK headers |
| `NEW1` | neutral | `unsigned int` is `size_t` on x86 (same type, same decoration) | as is |
| `UNIT1` | neutral | renames two local types of tesdatahandler (same layout) | as is; decorated names compared up to the rename |
| `KEEP1` | neutral | `__attribute__((used))`: emission only, no code meaning | `FNVNX_KEEP` empty |
| `IFX1` | neutral | static resolution of `__if_exists`; MSVC evaluates the original itself | as is (strongest check) |
| `SAL1` | neutral | attribute-syntax SAL annotations have no code meaning | as is |
| `DECL2` | neutral (claim) | the SDK's definition of the type replaces the decomp's copy | real SDK |
| `EXC1` | neutral (claim) | MSVC 2008 never enforced `throw()`; but it is an optimizer hint there | as is |
| `NEW2` | neutral | `<new>` defines the placement forms identically | real `<new>` |
| `CTOR1` | neutral | explicit constructor call = placement new of the same class at the same address | real `<new>` |
| `LINK1` | neutral | C and C++ linkage of a global variable name the same symbol under Itanium | data names bound by variable name |
| `GOTO1` | neutral | `T x = e;` -> `T x; x = e;` for scalars | as is |
| `STR1` | neutral | `&arr` and `arr` are the same address | as is |
| `CALL1` | neutral | the cast is the conversion the dropped prototype made implicitly | real SDK parameter types |
| `RCAST1` | neutral | `fnvnx_lvalue` returns the temporary itself | `fnvnx_lvalue(x)` = `(x)` |
| `NEW3` | neutral | deallocation functions are implicitly `noexcept` (C++11) | `noexcept` empty |
| `TYPEID1` | neutral | adds `<typeinfo>` only | real `<typeinfo>` |
| `CSX1` | neutral | drops the decomp's Xbox 360 critical-section size / offset asserts (D15, J-0051); asserts emit no code | as is |
| `DUPINT1` | neutral (claim) | keeps one of two integer-only-different prototypes bound to one address; same callee, same bits | as is |
| `RREF1` | neutral | `fnvnx_lvalue(...)` around an argument passed to a non-const reference parameter (every declaration the unit sees) that is not a plain lvalue | the temporary MSVC bound, same lifetime |
| `TPL1` | neutral | `this->` in front of the members of a dependent base that a class template's member functions use by plain name | the members MSVC's (one-phase) lookup found |
| `TPL2` | neutral | declares a namespace's function templates before the class templates that call them (MSVC's late lookup found them) | declarations only |
| `MATH1` | neutral | drops the decomp's copies of MSVC `<math.h>` C++ overloads; the real header defines them | real `<math.h>` |
| `DTOR1` | neutral | declares `template <> NiPointer<T>::~NiPointer();` after a unit's includes for member pointees T it never completes (with DecRefCount): GCC's key-function vtable would instantiate it | MSVC never instantiated it in those units |
| `PTR1` | neutral | `f > 0` / `p > 0` -> `!= 0` and `<= 0` -> `== 0` for a function name or a local pointer | MSVC's unsigned compare of the address gives the same result |
| `INC1` | neutral | a quoted `#include` that names, up to case, a file in the including directory is spelled as that file (`"Actor.h"` -> `"actor.h"`) | the header MSVC opened (directory of the includer first, names without case) |
| `ANON2` | platform | a member with a constructor / destructor / copy assignment in an anonymous struct of an anonymous union becomes raw storage of its size and alignment, its uses `(*(T*)(x.m))` | same bytes and calls; MSVC never ran those functions for a union member either |
| `ELAB1` | neutral | type uses of a file-scope class named like a Win32/CRT function (`struct HeapAlloc`) spelled elaborated: `struct HeapAlloc* p` | same type, same symbols |
| `ENUM1` | neutral | opaque `enum X;` -> `enum X : int;` and every definition of those enums -> `enum X : int {` | int is MSVC's underlying type of every unscoped enum |
| `ANON1` | platform | names an unnamed struct defined in a member function's return type (`struct fnvnx_anon_Float {...}; fnvnx_anon_Float* Float();`) | same layout; MSVC's decorated name of the function changes (`<unnamed-tag>`) |
| `STL1` | platform | includes `fnvnx_msvcstl.h` (MSVC 2008 `<xutility>` internals: `_SCL_SECURE_VALIDATE`, `std::_Checked_base`, `std::_Ptr_cat`, iterator tags) where a unit uses them | MSVC's own header defines them; the gate builds with it |
| `SPEC3` | neutral | qualifies the names a namespace declares in the body of an explicit class specialization written inside it (`fnv_x::T* p`) | the names MSVC's lookup found |
| `LEX1` | neutral | MSVC token spellings: `i64`/`ui64` suffixes -> `LL`/`ULL`, `0xae-0x34` -> `0xae -0x34`, `"C"enum` -> `"C" enum`, `inline __forceinline` -> `__forceinline`, `unsigned long(-1)` -> `((unsigned long)(-1))` | same tokens as MSVC lexed them |
| `STD1` | platform | a decomp `struct std { static float abs(float); }` (the std namespace of the headers) -> `#include <cmath>`: the calls reach the standard `std::abs(float)` | MSVC called the out-of-line copy at 0x00408840 (fabsf) |
| `SPEC2` | neutral | `template <>` on the decomp's member definitions of class template specializations (5 files) | MSVC accepts both spellings |
| `NEW4` | neutral | adds `<new>` where placement new is used without a declaration | real `<new>` |
| `ALLOC1` | platform | drops untagged file-scope replacements of the global operator new/delete; the game's are bound by address | target only |
| `SDK1` | layout | the PC SDK definitions win over the decomp's guarded copies (Xbox 360 layouts) | real SDK |
| `TAIL1` | layout | GCC must not reuse a base's tail padding; MSVC never does | zero-width bit-field, as on the target |
| `GAME1` | neutral | renames a unit's own Win32-named game declaration and its call sites; the address binding is unchanged | same code, other symbol name |
| `SPEC1` | neutral | early `template<>` declarations of member specializations | declarations only |
| `STATIC1` | neutral | internal -> external linkage of image-bound variables | may change MSVC inlining; the gate decides |
| `XINC1` | neutral | caller declarations corrected from the x86 code (S1b) | 62/66 identical, 4 differ by an ecx reload |
| `RET1` | neutral | `return <trailing stmt>` where MSVC's code is identical (S1b) | identical by construction |
| `DBUG1` | neutral | per-site debug fixes (no sites yet) | - |
| `XC1` | platform | dynamic initializers become extern + `__fnvnx_dyninit_<addr>` run by S10 | target only |
| `VA1` | platform | MSVC `char*` va_lists -> real `va_list` | target only |
| `FALL1` | platform | `__builtin_trap()` at fall-off ends of non-void functions | GCC only |
| `FTOL1` | platform | `fnvnx_ftol2(expr)` for arg-less `_ftol2_sse` calls; neutral with `fnvnx_ftol2(x) = (__int64)(x)` under MSVC | S1b: 1,535/1,535 identical |
| `INTL1` | platform | edits only the decomp's non-MSVC branch (`#else` of `_MSC_VER`) | invisible to MSVC |
| `ASM1` `ASM2` `ASM4` | platform | x87 / rdtsc / cpuid asm -> portable helpers (docs/ASM.md has their own checks) | - |
| `NATIVE1` | platform | units leave the build (native renderer, D6) | - |

## Method

Trees: pristine decomp; `equiv.py prepare --rules neutral` (FNVNX_PREPARE_SKIP = every other rule function,
NATIVE1 off; the prepare stats are checked to contain only allowed rules); `neutral,SDK1`; `TAIL1` alone. Every
unit is compiled with its recipe compiler and flags; objects are kept for re-fingerprinting.

What the rules include is given its MSVC meaning (`tools/equiv/msvc/`): `fnvnx_win32.h` = the real `<windows.h>`
(SDK v6.0A) adjusted where the target shim deliberately differs (no A/W name macros, no `min`/`max`, the shim's
looser parameter types and `void *` overloads as dllimport declarations of the same imports, `_ULARGE_INTEGER` with
the forward-declared class key), `fnvnx_crt.h` = MSVC's CRT headers, `fnvnx_new.h` = MSVC's placement forms, and
for prepared trees only `fnvnx_equiv_compat.h` = what `fnvnx_compat.h` declares for DECL1 (intrinsics, the decomp's
sized-delete helper names, C++14 sized deallocation). Both sides get the macro-only `fnvnx_equiv_fi.h`.

Fingerprint: `compare_builds.fingerprint` with the recipe's names (strict) and with extra bindings, used for the
verdict: names the whole recipe binds to one address; the other linkage of a global variable (LINK1); another
decoration of the same import slot; the same decorated name up to MSVC's class-key letter (U/V/T: Itanium does not
encode it); string literals by content instead of by `$SGnnnn` number (MSVC numbers literals with a counter every
included header advances).

Attribution: for each differing (or no longer compiling) unit, rebuild with the rule set minus one rule, and minus
groups (`SDK` = DECL1, DECL2, SDK1, CALL1, CSX1, INTL1; `NEWDEL` = NEW1, NEW2, NEW3, CTOR1) for interacting rules;
a function identical again without R is attributed to R. Differences are also sorted by kind: *retarget* (same code
bytes, a call or data reference reaches another address), *EH* (identical when both sides build with `/EHs-c-`),
*code*.

Control: every pristine unit compiles to COFF (2,987 / 2,988, the `vc8` unit needs VC 2005) and every one of its
68,456 recipe functions is in its object with exactly the recipe size.

## Interim findings

From the neutral-set run (1,831 compiled units, 42,974 functions): 563 functions differ in 308 units, 147 of them
with identical code bytes; 68 units no longer compile under MSVC. Attributed so far (leave-one-out on samples):

* **DECL1 changes callees.** The recipe binds decorated names, not functions: the decomp's own prototypes of Win32 /
  CRT names are often bound to other addresses, and DECL1, by replacing them with the platform declaration, rebinds
  the call by name. `equiv.py bindings` lists 27 such names statically. Real semantic changes:
  `GetCurrentThreadId()` in `falloutaudiomedia.cpp` is the game function 0x00830470 (it calls an
  `ActorTaskManager` method; 14 functions now call kernel32 instead); `Sleep` in `bsspinlock_console` is
  `FUN_008691b0::Lock` (a spin-lock acquire becomes a sleep); `EnterCriticalSection` / `LeaveCriticalSection` in
  `memorymanager.cpp` are the `InterlockedIncrement` / `InterlockedDecrement` import slots; `GetFileSize` is
  `FileSizeInfo::GetFileSize` (0x0065c1f0). Equivalent retargets: `strcpy_s` / `strcat_s` / `_stricmp` /
  `sprintf_s` / `memset` C++ prototypes bound to game wrappers that only forward to the CRT function.
* **EXC1 changes EH code.** Removing `throw()` makes MSVC add EH frames / unwind states (e.g.
  `NiRenderedTexture::Create` gains a frame around `new NiRenderedTexture` that frees the memory if the constructor
  throws). Only exceptional paths differ.
* **NEW2 / CTOR1: placement new through `<new>`** (declared `throw()`) makes MSVC null-check the result; the
  decomp's own placement forms had no exception specification. Only a null pointer behaves differently.
* **DECL2 / SDK1 swap Xbox 360 layouts for PC ones**: the decomp's guarded `_RTL_CRITICAL_SECTION` (28 bytes,
  `OwningThread` at 0x18) and `_FILETIME` (`dwHighDateTime` first) copies are dropped by DECL2 too, not only by SDK1:
  the decomp's own static asserts then fail (`RecursiveLock.h`, `bsshaderaccumulator_types.h`). With CS28 the
  asserts hold and those units compile identical.
* **DECL1 misses `operator new(unsigned, void*)`** (`unsigned` without `int` is not in `PLACEMENT_PARAMS`): the
  prototype is dropped without `<new>` in its place (`tile_ob1_variant_helpers_variant.cpp`, 3 more units).

Not comparable under MSVC (the target accepts what MSVC 2008 cannot express): `std::abs` after DECL1 (libstdc++'s
C headers export into `std`, MSVC's do not; 25+ units), class/union key mismatches, `HANDLE` -> integer
conversions (`-fpermissive`), decomp-local SDK structs that the real SDK also defines (`_MEMORYSTATUS`, `IUnknown`).

## Not covered

The `vc8` unit (VC 2005 SP1 not installed). Platform rules. What only the target can show: GCC layout equals MSVC
layout (TAIL1's purpose) needs a GCC-side comparison; MSVC can only show that the TAIL1 construct is inert (it is,
for every edge case tried: virtual / 8- and 16-aligned / empty / packed / template bases).
