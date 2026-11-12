# rules - the source preparation rules, trunk form

`prepare.py` rewrites a decomp checkout so that a second compiler (the pinned GCC, ILP32) accepts it, without changing
what the original compiler (cl) sees. It is a stage tool: its output is a function of the content of its argv inputs.

    python3 -I rules/prepare.py <decomp root> <out dir> --win32-header FILE

- `<decomp root>`: `src/`, `include/`, `build/units.json` (the unit list: source path, symbol table, function addresses),
  `build/`, `third_party/`. `src/` and `include/` are rewritten; `build/` and `third_party/` are copied by content.
- `--win32-header FILE`: the target's Win32 header text (names and prototypes DECL1 / CALL1 / SDK1 / GAME1 rest on).
- Output: the rewritten tree and `prepare_stats.tsv` (edits and files per rule id). Exit 3 names the rule and the
  file when the dual-compile check fails; exit 2 is a bad command line.
- No environment variable, no skip list, no cache or memo, no clock, no dynamic import: the sources name none
  (`tests/test_prepare_pure.py` checks it), and `verify/gate_pure.py` accepts every file.
- Deterministic: sorted directory walks (`core.walk_sorted`, `core.list_ci`), no set order is observable, no hash seed.

## Dual-compile check (mscgate.py)

The text both compilers read must be the same. The only permitted difference is a complete conditional group on
`_MSC_VER` (`ifdef` / `ifndef` / `if defined` / `if !defined`, with exactly one `#else`) whose GCC branch is
non-native: the ASM rule's `__asm__("")` (the function body is lifted) or a declared-only `fnvnx_lifted(` call.
`prepare.py` runs `mscgate.check_rule_edit` after every rule on the token stream (so the failure names the rule) and
`mscgate.check_tree` over the finished tree. A group not of that form counts against the input: the output may hold no
more such groups of a header than the input did (the decomp's own groups pass through untouched; a rule may not add one).

Dropped because they gave GCC text that differs from cl's: STAND7 (template member stand-ins under `#ifdef _MSC_VER`
with no `#else`: a member declared `void f(int)` for cl and a different return type for GCC). Dropped with the legacy
machinery: REC1 / recon / reccert (GCC-only definitions), s1b patches, r2_vorder, the memo.

## Rule table

Neutral for cl, by class: **S** a standard spelling of the same construct (same meaning for cl); **P** drops a
declaration or definition that the platform header supplies with the same meaning; **M** emits an `FNVNX_*` macro or
wrapper whose expansion is per compiler in the shim and the identity (or empty) for cl; **K** only adds a
declaration, include or attribute that has no effect on cl's code; **A** the dual-compile split.

| Id | Rewrites | Founding fact | Neutral |
|---|---|---|---|
| SSE1 | drops a unit's own copy of the vector union / struct types | the platform's intrinsic header declares the type; two definitions clash | P |
| SSE2 | drops own prototypes / definitions of `_mm_*` intrinsics | the same; intrinsics are the platform's | P |
| SSE3 | `v.m128_f32[i]` -> `((float*)&(v))[i]` | the union member names are a cl extension; the vector is 4 floats in memory | S |
| SSE4 | `N::__m128`, `N::_mm_x` -> global names | the copies in N are gone (SSE1/2) | S |
| GAME1 | renames Win32-looking names the unit binds to its own code | a unit's own function named like an SDK function clashes with the SDK declaration | S |
| DECL3 | own `FILE` / `fpos_t` typedefs give way to the platform's | one definition of a library type | P |
| DECL1 | drops own prototypes of CRT / Win32 functions outside classes | the platform header declares them with the target's `size_t` | P |
| NEW1 | `unsigned int` size parameter of `operator new/delete` -> `size_t` | cl's `size_t` is `unsigned int`; the replaceable signature takes `size_t` | S |
| XC1 | per-initializer `extern "C"` functions for `??__E` / `??__F` definitions; copies of an initializer the unit does not own become `extern` declarations | the unit list names the dynamic initializer and atexit function by address | M |
| INC1 | `#include "File.H"` -> the file's real case | cl's file system is case-insensitive | S |
| LEX1 | MSVC token spellings (`__int64`, `__forceinline`, ...) -> standard ones | same token meaning | S |
| STD1 | `struct std { static float abs(float); };` -> `#include <cmath>` | `std` is a namespace in the standard | S |
| STL1, STL2 | include of the compatibility container header; `std` -> compat namespace in units that use the original library's internals | those units program against the original library's private layout | K |
| ANON1 | names an unnamed struct defined in a return type | a type defined in a return type needs a name outside cl | S |
| ENUM1 | opaque enums get `FNVNX_ENUM_INT` / `: int` | the underlying type of a cl enum is `int` | M |
| ENUM2 | enumerator in [2^31, 2^32) gets an explicit `int` underlying type and cast | cl types it `int` (wraps) | S |
| ELAB1 | `struct X` in front of uses of a class named like a platform function | class and function name share a scope | S |
| ANON2, ANON3 | raw storage for members with special functions in anonymous aggregates; local anonymous unions get a named wrapper | the standard forbids them there | M |
| PTR1 | `f > 0` / `p > 0` -> `!= 0`, `<= 0` -> `== 0` for functions and pointers | pointer ordering against 0 is a cl extension; same truth value | S |
| ALLOC1 | drops untagged definitions of the global allocation functions | the platform provides them | P |
| KEEP1 | `FNVNX_KEEP` before every declaration tagged with its address | the link step binds by address; the definition must be emitted | K |
| IFX1 | `__if_exists` / `__if_not_exists` resolved statically | cl evaluates it the same way at compile time | S |
| SAL1 | drops attribute-syntax annotations | they carry no semantics | S |
| DECL2 | drops own copies of SDK / CRT types at file scope | the platform header defines them | P |
| COM1 | renames a unit's own base interface copy | clashes with the SDK's | S |
| EXC1 | drops dynamic exception specifications | never enforced by cl; an error in C++17 | S |
| NEW2 | drops own inline `operator new/delete` that are the standard placement forms | `<new>` defines them identically | P |
| CTOR1 | `p->A::A(args)` -> placement new | both construct an `A` in place | S |
| LINK1 | `extern "C" T x;` -> `extern T x;` at file scope | at global scope both linkages name one variable under cl | S |
| GOTO1, GOTO2 | initializer split / braces around crossed declarations | cl allows a jump past an initialization; the split keeps the order of effects | S |
| QUAD1 | `return q;` -> `return (__m128)q;` | the class converts to the vector; one conversion made explicit | S |
| MSCV1 | `_MSC_VER` in code (not in a directive) -> the unit's value | the unit list gives the compiler build | S |
| SDK1 | skips a generated copy of an SDK type guarded by the SDK guard | the guard is set when the SDK came first | P |
| STR1 | `&x` of `extern char x[]` -> `x` | same address; arrays of unknown bound admit no arithmetic | S |
| INTL1 | drops own `Interlocked*` definitions | the shim declares them like the SDK | P |
| CALL1 | casts pointer arguments of Win32 calls to the parameter type | the SDK declares the parameter types | S |
| RCAST1, RREF1, RREF2, ADDR1, COND1, RSTR1 | `fnvnx_lvalue(...)` / `fnvnx_unrestrict(...)` / `G(false)` around temporaries, restrict locals and bool gates | cl binds a temporary to a non-const reference and ignores `__restrict`; the wrappers are identity on the value | M |
| NEW3 | `noexcept` on replacement `operator delete` | every deallocation function has it | S |
| TYPEID1 | `<typeinfo>` on top of files using `typeid` | cl needs no include | K |
| SPEC3 | namespace qualification in a specialization body | names lookup the same | S |
| VA1, VA2 | `va_list` as `char*` -> a real `va_list`; `va_arg(ap, E)` -> `(E)va_arg(ap, int)` | an enum argument promotes to `int` | S |
| STATIC1 | file statics bound to image addresses get external linkage | the link step binds by name | S |
| DUPINT1 | drops the later of two prototypes that differ only in integer types and bind one address | one entity | P |
| NEW4 | `<new>` on top of files using placement new | cl declared it in its own headers | K |
| TPL1, TPL2, TPL3 | `this->`, forward declarations of function templates | two-phase lookup; cl looks up late | S |
| TYPN1 | `typename` before a dependent type | the same type | S |
| INST1 | `unsigned int` -> `C<..>::size_type` in an explicit instantiation | same type in cl | S |
| COPY1 | casts the copied object to the base in a copy constructor's base initializer | same conversion, explicit | S |
| MATH1 | drops own `<math.h>` overloads at file scope | the platform's `<math.h>` defines them | P |
| TAIL1 | derived members start at the base's `sizeof` (filler `FNVNX_MSVC_BASE_END`) | cl's layout; the filler is empty for cl | M |
| VFPAD1 | vfptr padding before an aligned member | the same | M |
| EBO1, EBO2, EBO3 | empty-base layout (`fnvnx_msvc_ebo`, `FNVNX_EBO2`) | the same | M |
| ALIGN1 | `__declspec(align(n)) struct S` -> `struct __declspec(align(n)) S` | both place the specifier at the class | S |
| FRIEND1 | forward declaration of a class named by a friend declaration | cl made the friend visible | K |
| LANE1 | `&((T*)&(v))` -> pointer-to-array form | the same address | S |
| STD2 | file-scope `R std::f(...)` -> `R fnvnx_std_f(...)` (symbol renamed by the POST hook) | the standard forbids defining in `std` | S |
| NEW5 | `FNVNX_GLOBAL_DELETE` in a class whose only scalar delete is the placement form | the deleting destructor calls the global delete | M |
| NSCALL1 | `N::name(...)` for unqualified calls of a namespace's own CRT prototype | bound only there | S |
| FINL1 | drops `__forceinline` on a body-less declaration of a function the unit does not define | the ordinary call | S |
| VSPLIT1 | `virtual T a(), b();` -> two declarations | same functions, same order | S |
| HARN1 | an untagged free definition no unit references becomes `static` | nothing links to it | S |
| ADDRFN1 | `reinterpret_cast<void*>(0x...)` of a defined code address -> the address symbol | the same address, bound by the link step | M |
| POP2 | call-site rewrite of a used-as-bool member call to the donor / receiver pair named by the unit list (a POST hook, report `pop2_chains`) | both compilers pass the same registers; the member named by address | S |
| ASM | every `__asm` construct -> `#ifdef _MSC_VER` original `#else __asm__("") #endif` | the function body is lifted; cl keeps its own | A |

Rules that read the unit list: XC1, MSCV1, STATIC1, DUPINT1, POP2, ADDRFN1, HARN1, KEEP1.

## Rules keyed on the decomp (stated assumptions)

- GAME1: `IAT_RANGE` (core.py), the import address table range, as rule data.
- STATIC1: image-data address range; ADDRFN1: code address range; both constants in the sources.
- KEEP1, STD2, HARN1, XC1, ADDRFN1: the `// @fnv 0x...` address tag the decomp writes above each definition.
- POP2, STATIC1: the decomp's per-function namespace and `FUN_<address>` spellings.
- Name lists of C library and platform facts (CRT headers, math names, entry-point names).
- ASM raises `ASMError` for a construct it cannot wrap (a failure path: the file is not prepared).

## Tests (run from the checkout root, as `python3 -I rules/tests/test_X.py`)

`test_rules_text` (rule texts), `test_mscgate` (gate, positive and negative), `test_asm_rule`, `test_harn1`,
`test_addrfn1`, `test_pop2`, `test_prepare_pure` (determinism, poisoned environment, path independence, argv contract,
XC1 on a synthetic unit list).
