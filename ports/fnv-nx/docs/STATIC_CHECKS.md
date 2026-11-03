# Static checks: MSVC x86-32 vs GCC AArch64 ILP32 (stream S12)

The decomp is C++ that MSVC 2008 compiled into the bytes of `FalloutNV.exe`. We compile the same source with GCC for
AArch64 ILP32. Wherever the language leaves a choice to the implementation, or the program has undefined behaviour
that MSVC happened to compile one way, the two builds can mean different things without any diagnostic. Plain `char`
signedness was one such case (J-0034, now `-fsigned-char`). This document is the systematic hunt for the rest, and
the automated gate that keeps the count from growing.

**Can runtime correctness be checked statically?** Partly, and the part that can is now checked. Every way the two
targets can disagree belongs to a finite set of classes (below). For each class the sites can be found statically,
from the AST, from record layouts, or from the compiler's own flow analysis. Whether a site actually misbehaves
usually depends on runtime values (is this float ever negative, does this flag cross threads), so the gate does three
things. It lists every site; it fixes whole classes where a flag, a header or a mechanical rule makes MSVC's meaning
hold everywhere; and it leaves the value-dependent remainder to per-site review and to the differential testing
outlined at the end.

Already covered elsewhere and not repeated here: the decomp's 3,171 `static_assert(sizeof)` (layout oracle, TAIL1),
S2's ABI verdicts and bridges (calling conventions, aggregate passing), S11's MSVC equivalence of the prepare
rules, S8's x87 `fistp`/`fsincos` emulation.

## The gate: `tools/static/run.sh <prepared tree> <out> [jobs]`

| step | tool | what |
|---|---|---|
| flags | `run.sh` | the flags in `tools/static/flags.sh` must still be the flags of `tools/link/compile.sh` and `tools/census.sh` |
| grep | `run.sh` | text counts: `__try`, `#pragma pack` forms, `long double`, `__declspec(thread)`, TEB access |
| gcc | `gcc_warn.sh` | every unit through the real compiler with the real flags at `-O2` (object discarded), with the target-semantics warning set (`GCC_WARN`). The middle-end warnings (`-Wmaybe-uninitialized`, `-Waggressive-loop-optimizations`, `-Warray-bounds`, `-Wdangling-pointer`, missing return) exist only at `-O2`. |
| gnu | `clang_pass.sh gnu` + `fnvcheck` | clang parses each unit as a GCC/Itanium ILP32 proxy of our build. `fnvcheck` (a libTooling AST checker, `fnvcheck.cpp`) prints one row per suspicious construct and every record layout. clang's flow-sensitive warnings run at `-fsyntax-only`. |
| msvc | `clang_pass.sh msvc` | clang parses each unit as MSVC x86-32 (`i686-pc-windows-msvc`, clang's MSVC record layout engine) and prints every record layout |
| atomic | `clang_pass.sh atomic` | plain stores to every field or global that some unit passes to `Interlocked*` |
| layout | `layout_diff.py` | every record defined in the tree, MSVC layout against GCC layout: size, alignment, every field and bit-field offset, base offsets, with a cause |
| probe | `layout_probe.py` | real `aarch64-linux-gnu-g++ -mabi=ilp32` checks the proxy on every differing record. sizeof and member offsets go through `static_assert`s appended to the unit; bit-field positions come from the store GCC emits for `p->f = ~0`. |
| report | `report.py` | `report.md` (coverage, per-class counts and verdicts, per-site lists), `sites/<class>.tsv`, `counts.tsv`. Exits 1 when a class has more sites than `tools/static/baseline.tsv` allows. |

Results are reused when newer than the unit (`FORCE=1` redoes them, `SKIP="gcc msvc ..."` skips steps). A full run
over the 2,978 units takes about 2 x 1.5 h of CPU on the dev container (GCC -O2 pass, clang gnu pass; the msvc pass is
cheaper). Everything under `<out>` is derived from the decomp: it stays local and is never committed. The tests are
in `tests/s12/run.py <scratch>`: the code generation facts below, every `fnvcheck` check, the layout fixtures, and the
layout steps end to end.

### Why a clang proxy, and how far it can be trusted

clang cannot target AArch64 ILP32 for ELF (J-0002), and GCC has no AST matcher interface. The gnu configuration
(`--target=i686-linux-gnu -malign-double -fshort-wchar -fsigned-char`, `clang_env.sh`) has the same type sizes and
alignments as `aarch64 -mabi=ilp32`: int, long and pointer 4; long long and double 8-aligned in structs; `__m128` 16.
It also uses the same Itanium/generic layout rules: bit-fields never straddle their declared type, named bit-fields
align the record, zero-width bit-fields align the next member (TAIL1 relies on that). The only known difference is
`long double` (12 vs 16), which the decomp does not use. `layout_probe.py` does not trust the proxy: it checks every
differing record against real GCC. Both configurations use clang's own x86 SSE headers instead of sse2neon (same
`__m128` size and alignment). The msvc configuration force-includes `tools/static/prelude_msvc.h` instead of
`fnvnx_compat.h`, so MSVC keywords keep their MSVC meaning.

## Code generation facts (verified, `tests/s12/facts.cpp`)

GCC 13 `aarch64-linux-gnu -mabi=ilp32 -O2` with the D10 flags:

| construct | GCC AArch64 ILP32 | MSVC 2008 x86 |
|---|---|---|
| `f(g(), h())` | `g` first (left to right) | `h` first (pushes right to left) |
| `(unsigned char)x`, `(unsigned)x`, float x | `fcvtzu`: saturates, -1.0 -> 0 | `_ftol2` (int64) then low bits: -1.0 -> 0xFF / 0xFFFFFFFF |
| `(int)x` | `fcvtzs`: saturates, NaN -> 0 | 0x80000000 for NaN / out of range |
| `(long long)ptr` | `sxtw` (sign-extends) | as MSVC, sign-extends |
| `x << n`, 32-bit | `lsl w`: n & 31 | `shl`: n & 31 (same) |
| `x << n`, 64-bit | `lsl x`: n & 63 | `__allshl`: 0 for n >= 64 |
| `p + (unsigned)-1` | wraps at 32 bits (`sub w0, w0, 1`) | wraps |
| `this == 0` | kept (`-fno-delete-null-pointer-checks`) | kept |
| `p + n < p` | folded to `n >> 31` (pointer overflow is UB; `-fwrapv` does not cover it) | compared |
| side-effect-free loop that may not end | deleted (C++ forward progress) | kept |
| volatile read / write | plain `ldr` / `str`; plain stores move across volatile ones | acquire / release (`/volatile:ms`, the x86 default) |
| uninitialized local on one path | path folded away | whatever the stack held |
| address of a dead local returned | `NULL` (isolate-erroneous-paths) | the stale address |
| missing `return` | the path is assumed unreachable | whatever eax held |
| `-ftrivial-auto-var-init=zero` | does not change the above (GCC still optimizes as uninitialized) | - |

## Hazard classes, counts and verdicts

Counts are unique sites in the prepared tree, from the full run (see "Results" below). Verdicts: **benign** (same
meaning, or the original would have crashed), **flag** (a compiler flag gives MSVC's meaning everywhere), **header**
(a change in a shim header), **rule** (a mechanical prepare rule from the site list), **per-site** (a hunk in
`patches/`), **runtime** (platform or compiler support).

The authoritative table with counts is `report.md` of a run; the classes are defined in `tools/static/report.py`
(`CLASSES`). Summary of the reasoning per class:

* **float -> unsigned (F2U)**. **rule**. MSVC converts through `_ftol2` (64-bit truncation, low bits kept); AArch64
  `fcvtzu` saturates, so a negative value becomes 0 instead of wrapping. Colour bytes, `(DWORD)` timers and
  counters are typical. Rule **FTOI1**, keyed by the site list: `(T)x` becomes `(T)(long long)(x)`, and an implicit
  conversion gets the same explicit cast. `fcvtzs x` followed by truncation is exactly `_ftol2` below 2^63
  (verified).
* **float -> signed (F2S)**. **runtime**, mostly benign. The result differs only for NaN or values outside int32:
  0x80000000 against saturation. The default is no change. `FTOI2` (same site list, helper
  `fnvnx_ftol(x)` = `fcvtzs x` plus a range/NaN check giving 0x80000000) is for sites that can see such values.
  Debug builds can use `-fsanitize=float-cast-overflow` with a logging handler to find them.
* **argument order (ORDER-ARGS, ORDER-OBJECT, ORDER-OPERATOR)**. **per-site**. MSVC evaluates call arguments right
  to left, GCC left to right. Rows tagged `stream` (reads, random numbers, iterators in two arguments), `same-callee`
  or `same-object` come first; getters with a visible side-effect-free body are not counted. Fix: hoist the
  arguments into locals in MSVC order. A GCC plugin that gimplifies side-effecting scalar arguments right to left
  would fix the whole class (compiler support, not started). ORDER-BINOP (both operands of a built-in operator) is
  unspecified on both compilers; it is listed, not judged.
* **volatile and memory ordering (VOLATILE-DECL, POLL-VOLATILE, POLL-PLAIN, ATOMIC-*, BARRIER)**. MSVC's volatile
  accesses are acquire/release and x86 is TSO; on AArch64 GCC gives neither.
  * `_ReadWriteBarrier` / `_ReadBarrier` / `_WriteBarrier`: **header**. In `fnvnx_compat.h` they become
    `__atomic_thread_fence(__ATOMIC_SEQ_CST)`, `__ATOMIC_ACQUIRE` and `__ATOMIC_RELEASE` (`dmb ish` / `dmb ishld`)
    instead of an empty asm.
  * volatile accesses: **runtime / compiler support**. Either a GCC plugin turns volatile loads and stores of decomp
    units into `ldar`/`stlr` (`/volatile:ms` emulation), or a rule wraps the listed declarations in
    `fnvnx_msvolatile<T>` (same size, acquire/release accessors).
  * plain stores to `Interlocked*` operands (ATOMIC-PLAIN-STORE), the unlock half of a spin lock: **per-site**, rule
    **ATOM1**: `__atomic_store_n(&f, v, __ATOMIC_RELEASE)`.
  * POLL-PLAIN: **per-site**, `__atomic_load_n(.., __ATOMIC_ACQUIRE)` in the loop condition.
  * Interlocked on packed or misaligned fields (ATOMIC-UNALIGNED): **per-site**. `ldaxr`/`stlxr` fault on
    misalignment where x86 `lock` does not.
* **record layout (LAYOUT-\*)**. Mixed-type bit-fields (MSVC opens a new unit whenever the declared type size
  changes), `#pragma pack` with over-aligned members (`__declspec(align)` and `__m128` beat pack in MSVC, pack caps
  them in GCC, also for an `aligned` attribute on the field), and empty bases after the first (MSVC gives each a byte).
  These are **rule/per-site** where a byte image depends on the layout: files, saves, the data image, or a decomp
  `static_assert`. Records whose only difference is an empty base at another offset are benign. So are records that
  differ only through a member or base type (not root causes).
* **enums (ENUM-BITFIELD, ENUM-UNSIGNED, ENUM-64)**. **rule ENUM1**. MSVC enums are always `int`, so an enum
  bit-field is signed and 3 in 2 bits reads back -1, and `0x80000000` is `INT_MIN`. GCC picks `unsigned int`. Giving
  the listed enums the fixed underlying type `int` (`enum E : int`, with `(int)` casts on enumerators above
  `INT_MAX`) gives MSVC's semantics exactly.
* **varargs (VA-STRUCT, FMT-STRUCT, VA-ADDR-PARAM, W-VA-PROMOTE)**. **per-site**. A struct passed through `...` is
  copied onto the stack on x86, so a one-pointer string object works as `%s`. On AArch64 a struct goes in registers,
  or by reference above 16 bytes. Taking the address of the last named parameter to walk the stack is an x86-only
  idiom. GCC replaces `va_arg(ap, float/char/short/enum)` with a trap.
* **formats (FMT-MSVC, FMT-WIDE, WCHAR-LIB)**. **runtime (S4)**. `sprintf`/`vsprintf`/`sscanf` of game code come
  from newlib (RUNTIME.md), which does not know `%I64d`, `%S`, or 16-bit `wchar_t`. Route them through S4's MSVC
  formatter, as `fprintf`/`printf` already are, and give the wide functions 16-bit implementations.
* **uninitialized reads, missing returns, dangling stack addresses, unsequenced modifications (W-UNINIT, W-RETURN,
  W-STACKADDR, W-UNSEQ)**. **per-site**, plus two flags that make GCC's choice MSVC-like where it would otherwise be
  arbitrary: `-fno-isolate-erroneous-paths-dereference` (return the stale address, not NULL) and
  `-funreachable-traps` (a missing return traps instead of running into the next function). `report.md` counts how
  many W-UNINIT sites are in units the original built with `/Od`. There the stack contents decided what was read,
  so the original value is often recoverable from the exe.
* **loop UB (W-LOOP-UB)**, **pointer overflow**, **forward progress**: **flag**, see below.
* **pointer sign (P2I-SIGNED, P2I64)**. **runtime**. `FalloutNV.exe` is not large-address-aware, so no address at
  or above 2 GiB ever reached this code on Windows. S5 puts the guest heap at 0x40000000 + 2 GiB, which crosses
  0x80000000. Cap the guest heap below 0x80000000. The data image and native code are already below it.
* **TLS**. **runtime**. `__declspec(thread)` variables become `__thread` on TPIDR_EL0. S2 must never rebind a
  native TLS definition to an image address (its weak rebinding of natively defined image variables, J-0008), and
  S4/S5 must make sure no image code reaches them through the TEB TLS index.
* **benign**: 32-bit variable shifts (both mask by 31), integer division (x86 traps where we continue), `this == 0`
  (kept by D10), `long double` (two unused template specializations), float -> int64, mismatched new/delete (one
  heap), GCC ABI notes.

### Flags (proposed addition to D10)

```
-fwrapv-pointer -fno-finite-loops -fno-aggressive-loop-optimizations -fno-isolate-erroneous-paths-dereference
-funreachable-traps
```

The array is `PROPOSED_FLAGS` in `tools/static/flags.sh`; `tests/s12/run.py` checks what each one changes. Each
goes into `tools/link/compile.sh` and `tools/census.sh` (owners S1/S2). Not proposed: `-ftrivial-auto-var-init=zero`
(no semantic effect, verified) and `-fstack-reuse=none`. GCC still deletes stores to a dead local, so a dangling
read does not see the old data anyway; it only makes frames larger.

### Header change (proposed, `shim/include/fnvnx_compat.h`, owner S1)

```c
inline __attribute__((always_inline)) void _ReadWriteBarrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
inline __attribute__((always_inline)) void _ReadBarrier(void) { __atomic_thread_fence(__ATOMIC_ACQUIRE); }
inline __attribute__((always_inline)) void _WriteBarrier(void) { __atomic_thread_fence(__ATOMIC_RELEASE); }
```

### Rules (proposed, owner S1; driven by `sites/<class>.tsv`)

* **FTOI1**: the F2U sites. `(T)x` becomes `(T)(long long)(x)`; an implicit conversion gets the same cast.
* **ENUM1**: the ENUM-BITFIELD / ENUM-UNSIGNED enums get `enum E : int`, with `(int)` casts on enumerators above
  `INT_MAX`.
* **ATOM1**: the ATOMIC-PLAIN-STORE sites become `__atomic_store_n(&lhs, rhs, __ATOMIC_RELEASE)`.
* **ORDER1** (optional): the `stream` / `same-callee` ORDER-ARGS sites get their arguments hoisted into locals,
  last argument first.

## What the gate does not see

* **Values.** Whether a float is ever negative, whether a loop really crosses threads. The gate lists candidates;
  only running the code decides. That is what differential testing (next section) and the debug-build sanitizers
  are for.
* **Units that do not compile** (census: about 240). The clang passes still collect findings up to the first errors;
  GCC's middle-end warnings need a compiled unit.
* **x87 numerics.** Excess precision, the 24-bit precision control that D3D9 sets, and `/fp:fast` (6 units) NaN
  comparisons. These are numerical differences, not changes of meaning. S8 handled the hand-written x87 code.
* **Code outside the tree**: the platform layer, shims and libc behaviour (S4's tests).

## Differential testing (outline, needs the player's exe)

Run an original x86 function from the exe image under Unicorn x86-32 and our AArch64 build of the same function
(found by address) under Unicorn AArch64, with identical inputs and identical memory. Compare the return value and
every memory write.

**Harness pieces that exist**

* S3 `loader/`: maps the exe's sections at their original addresses and relocates; the same mapping code can fill
  both emulators' memory.
* S2 address map (`addrmap.tsv` / `fnvnx_addrmap[]`): original address -> native symbol, kinds (code, data,
  vtable, extern-text). With the ELF symbol table it gives the native entry of every original function.
* S2 `abi.py` / `abi2.py`: the MSVC x86 location of every parameter (ecx / edx / stack, hidden result pointer) and
  the AArch64 location of the same parameter. That is exactly what is needed to place one argument vector in both
  emulators.
* S5 `tests/s5/emu_guest.py`: Unicorn AArch64 runner of the guest NSO, with TLS and a host-call stand-in. It already
  runs the static initialisers of the full link.
* `build/units.json`: address and size of every defined function. The unit's MSVC flags (`/O2` vs `/Od`) tell
  which original behaviours are stack-content dependent.

**Driver (to write)**

1. Load the exe image into a Unicorn x86-32 instance at the original addresses (S3 mapper) and the linked guest
   into a Unicorn AArch64 instance (emu_guest.py), with the same data image at the same addresses in both. The
   guest's data and the original's data are then byte-identical (D1/D3).
2. Per function under test: generate inputs (random scalars and edge values: 0, -1, NaN, +-inf, 2^31, denormals,
   pointers into a scratch arena filled with the same random bytes in both), place them per `abi2.py`, and run
   until return. In x86, push a sentinel return address; in AArch64, set LR to a sentinel. Both run with a step
   budget.
3. Calls out of the function: stub everything at the image boundary identically. Unresolved or imported calls
   return the same canned value in both and log their arguments, which are compared too (call order and argument
   values are compared, so this also catches the argument-order class). Alternatively, let calls into other tested
   functions run on both sides.
4. Compare: return value (eax/edx/ST0 against w0/x0/s0/d0, per abi2's result location), the set of written
   addresses and bytes in the image and the scratch arena (stack excluded), the logged external calls. Floats are
   compared bit-exact first, then with a ULP tolerance for x87 functions (24-bit precision control set in the x86
   FPU control word, as after D3D9 device creation).

**Good first candidates**: leaf, pure, or data-only functions where the static classes predict a difference.

* Every function containing an F2U/F2S site (colour packing, timers, `fnv_FloatToInt` users). Inputs: negative,
  NaN, huge.
* Functions with ORDER-ARGS `stream`/`same-callee` rows. External calls are logged, so a swapped order shows
  without understanding the code.
* Bit-field accessors of the LAYOUT-BITFIELD records, and enum bit-field readers (ENUM-BITFIELD): in-memory
  layouts of the image are compared directly.
* Math leaves: NiPoint3/NiMatrix3/NiQuaternion operations, Havok `hkVector4` helpers (sse2neon against SSE),
  string helpers (`BSString`, `_stricmp` users), hash functions (file-name hashes of BSA archives,
  `NiFixedString` hashing), CRC, compression (zlib is a third-party unit), random number generators.
* Leaf functions are identifiable statically: no call instructions in the x86 body (from the image) and no
  undefined references in the native object (`elfsyms.py`).

Not good candidates: anything that allocates (heap addresses differ), threads, renderer and OS calls. Run those
with the stubs in point 3 only once the leaves pass.

## Results

See `report.md` of the latest run (local). The numbers of the run that defined `baseline.tsv` are in the S12 journal
entry.
