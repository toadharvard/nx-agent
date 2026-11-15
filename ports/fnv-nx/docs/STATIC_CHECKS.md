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
hold everywhere; and it leaves the value-dependent remainder to per-site review and to S13's differential testing,
against which it is cross-checked (section "Cross-check with S13").

Already covered elsewhere and not repeated here: the decomp's 3,171 `static_assert(sizeof)` (layout oracle, TAIL1),
S2's ABI verdicts and bridges (calling conventions, aggregate passing), S11's MSVC equivalence of the prepare
rules, S8's x87 `fistp`/`fsincos` emulation.

## The gate: `tools/static/run.sh <prepared tree> <out> [jobs]`

| step | tool | what |
|---|---|---|
| flags | `run.sh` | `BUILD_FLAGS` of `tools/static/flags.sh` must be exactly the flag set of `tools/link/compile.sh` (D10 semantics flags, `-fPIC` of D16, `-Werror=return-type -Wno-narrowing`; both directions), and `tools/census.sh` must have all of them but `-fPIC` |
| grep | `run.sh` | text counts: `__try`, `#pragma pack` forms, `long double`, `__declspec(thread)`, TEB access |
| gcc | `gcc_warn.sh` | every unit through the real compiler with the real flags at `-O2` (object discarded), with the target-semantics warning set (`GCC_WARN`). The middle-end warnings (`-Wmaybe-uninitialized`, `-Waggressive-loop-optimizations`, `-Warray-bounds`, `-Wdangling-pointer`, missing return) exist only at `-O2`. |
| gnu | `clang_pass.sh gnu` + `fnvcheck` | clang parses each unit as a GCC/Itanium ILP32 proxy of our build. `fnvcheck` (a libTooling AST checker, `fnvcheck.cpp`) prints one row per suspicious construct and every record layout. clang's flow-sensitive warnings run at `-fsyntax-only`. |
| msvc | `clang_pass.sh msvc` | clang parses each unit as MSVC x86-32 (`i686-pc-windows-msvc`, clang's MSVC record layout engine) and prints every record layout |
| atomic | `clang_pass.sh atomic` | plain stores to every field or global that some unit passes to `Interlocked*` |
| layout | `layout_diff.py` | every record defined in the tree, MSVC layout against GCC layout: size, alignment, every field and bit-field offset, base offsets, with a cause |
| probe | `layout_probe.py` | real `aarch64-linux-gnu-g++ -mabi=ilp32` checks the proxy on every differing record. sizeof and member offsets go through `static_assert`s appended to the unit; bit-field positions come from the store GCC emits for `p->f = ~0`. |
| report | `report.py` | `report.md` (coverage, per-class counts, verdicts and what closes a class, per-site lists), `sites/<class>.tsv`, `counts.tsv`. GCC's `-Werror=return-type` errors (the build's own) count as W-RETURN sites. Exits 1 when a class has more sites than `tools/static/baseline.tsv` allows. |
| cross-check | `crosscheck.py` | not part of `run.sh`: `causes` dumps S13's divergences with S13's own final cause; `matrix` attributes every site to the function around it (`// @fnv` tag, brace-matched body) and joins with S13's fuzzed functions, both directions (section "Cross-check with S13"); `targets` writes S13 campaign lists for chosen classes |

Results are reused when newer than the unit (`FORCE=1` redoes them, `SKIP="gcc msvc ..."` skips steps). Every
compiler and clang process runs under `nice -n 19` (`NICE`). `UNITS=<list>` runs the passes on a stratified sample
and `report.md` says so; `SHIM=<dir>` gates a tree prepared at an older commit with that commit's shim
(`git archive <commit> ports/fnv-nx/shim ports/fnv-nx/platform/crt`; the shim includes `platform/crt`). With one job
on the loaded 4-CPU dev container (load 7), the 462-unit sample took 11 min for GCC and about 25 min for clang gnu, so
a full run of the 2,978 units is about 1.2 h + 2.7 h at one job. Everything under `<out>` is derived from the decomp:
it stays local and is never committed. The tests are in `tests/s12/run.py <scratch>`: the flag set against
compile.sh and census.sh, the code generation facts below, every `fnvcheck` check, the layout fixtures, the layout
steps and report.py end to end, and crosscheck.py's function attribution.

The whole-tree run belongs on the farm: `tools/farm/static_job.sh <commit> <k> <N> <report.md>` prepares the tree
at a commit and runs every step on shard k of N (crc32 of the unit path, as compile_job.sh). Its report has counts and
sites as `file line CLASS` only, and the shards' `.tsv` files merge with `sort -u`.

fnvcheck reports a site only when both its expansion and its spelling location are in the tree and in no excluded
directory (`--exclude`, `CLANG_EXCLUDE` of clang_env.sh): the shim, sse2neon, the clang headers standing in for
sse2neon, and the generated headers under `<out>/clang`. An intrinsic macro or inline code of those, expanded in a
unit, is not the unit's code. Before this, a scratch directory inside the root (the tests' default `tests/s12/out`)
put the shim copy's `_rotl`/`_rotr` shifts into the findings. On real runs the out dir lies outside the tree, and the
rows were identical before and after on the 5 sample units with the most rows (1,589 rows).

**The gate compiles what the build compiles.** On the f8 sample (below) the GCC pass failed on exactly the units
S2c's `compile.sh` run of the same tree failed on (462 of 462 agree: 436 compile, 26 do not).

### Why a clang proxy, and how far it can be trusted

clang cannot target AArch64 ILP32 for ELF (J-0002), and GCC has no AST matcher interface. The gnu configuration
(`--target=i686-linux-gnu -malign-double -fshort-wchar` plus the D10 semantics flags clang knows, `clang_env.sh`)
has the same type sizes and
alignments as `aarch64 -mabi=ilp32`: int, long and pointer 4; long long and double 8-aligned in structs; `__m128` 16.
It also uses the same Itanium/generic layout rules: bit-fields never straddle their declared type, named bit-fields
align the record, zero-width bit-fields align the next member (TAIL1 relies on that). The only known difference is
`long double` (12 vs 16), which the decomp does not use. `layout_probe.py` does not trust the proxy: it checks every
differing record against real GCC. Both configurations use clang's own x86 SSE headers instead of sse2neon (same
`__m128` size and alignment). The msvc configuration force-includes `tools/static/prelude_msvc.h` instead of
`fnvnx_compat.h`, so MSVC keywords keep their MSVC meaning. Neither configuration parses every unit cleanly (f8 sample:
276 gnu and 269 msvc units with errors, 98 of the gnu ones inside the tree); findings are collected up to the errors,
and records the msvc configuration dropped members from show up as LAYOUT-PARSE, not as differences.

## Code generation facts (verified, `tests/s12/facts.cpp`)

GCC 13 `aarch64-linux-gnu -mabi=ilp32 -O2` with the build flags (`BUILD_FLAGS`, i.e. compile.sh):

| construct | GCC AArch64 ILP32 | MSVC 2008 x86 |
|---|---|---|
| plain `char` load, `c < 0` | `ldrsb`, sign bit (`-fsigned-char`) | signed (same) |
| `x + 1 > x` | compared with INT_MAX (`-fwrapv`) | compared (same) |
| `a * b + c`, float | `fmul` + `fadd`, never `fmadd` (`-ffp-contract=off`; 0 fused ops in the 436 f8 objects of the sample) | x87/SSE mul + add (same rounding steps) |
| `*i = 1; *f = 0; return *i;` | reloaded (`-fno-strict-aliasing`) | reloaded |
| a global | through its own GOT slot (`-fPIC`, D16) | absolute address |
| `!b`, `b ? x : y` for a bool byte | `b ^ 1`, `tst x0, 1`: takes the byte as 0/1 | `test al, al`: any non-zero byte is true |
| `DAT[i]`, `int` or `unsigned` index | `ldr w0, [x1, w0, sxtw]`: zero-extended base + sign-extended 32-bit offset, in 64 bits | base + offset mod 2^32 |
| `*(int*)(intval + 8)` | `uxtw` then +8 in 64 bits | mod 2^32 |
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
| missing `return` | the path is treated as unreachable | whatever eax held |
| `-ftrivial-auto-var-init=zero` | does not change the above (GCC still optimizes as uninitialized) | - |

## Hazard classes, counts and verdicts

Counts are unique sites of a run (see "Status on f8" below for the current ones). Verdicts: **benign** (same
meaning, or the original would have crashed), **flag** (a compiler flag gives MSVC's meaning everywhere), **header**
(a change in a shim header), **rule** (a mechanical prepare rule from the site list), **per-site** (a hunk in
`patches/`), **runtime** (platform or compiler support).

The authoritative table with counts is `report.md` of a run; the classes are defined in `tools/static/report.py`
(`CLASSES`). Summary of the reasoning per class:

* **float -> integer (F2S, F2U, F2S64, F2U64)**. **compiler support: the fnvnx_ftoi plugin (FTOI2)**. What MSVC
  2008 SP1 compiles for `(T)x`, x float or double, read from its output (the same at /Od and /O2; a test file
  through cl.exe, and every conversion of the 2,987 pristine objects):

  | T | MSVC | NaN / out of range on an SSE2 PC | pristine objects |
  |---|---|---|---:|
  | int, long, short, signed char (also implicit, `i += f`) | `call __ftol2_sse` = `cvttsd2si` | 0x80000000, then its low bits | 1,253 calls |
  | unsigned short, unsigned char | inline `fistp DWORD` (truncating control word) | 0x80000000, low bits: 0 | 445 |
  | unsigned int, unsigned long | inline `fistp QWORD`, low dword | 0x8000000000000000: 0 | 267 |
  | __int64, unsigned __int64 | `call __ftol2` (x87 QWORD) | 0x8000000000000000 | 10 |

  So unsigned int is an int64 truncation (-1.0 -> 0xFFFFFFFF, 3e9 -> 3000000000) while unsigned short / char are
  int32 ones, and FTOI1's `(T)(long long)x` was right only in range. AArch64 `fcvtzs` / `fcvtzu` saturate (NaN -> 0,
  too large -> the maximum, negative -> 0 for unsigned): 240 of the 810 conversions of tests/s12 `ftoi/` differ.
  A source rule cannot reach all of them: implicit conversions, compound assignments (`i += f`, no cast node in
  clang, so the F2S/F2U counts miss them too), macros, templates. `toolchain/ftoi/fnvnx_ftoi.cc` is a GCC plugin
  that rewrites every GIMPLE `lhs = (T) x` (after the CFG is built) into MSVC's computation for T: int32 path for
  every target up to 32 bits but unsigned int (`ok = x > -2^31-1 && x < 2^31`, else 0x80000000 - S1b's
  `fnvnx_ftol2_sse` verbatim), int64 path for unsigned int and both 64-bit types (`x >= -2^63 && x < 2^63`, else
  INT64_MIN), then the low bits for T. Tested: tests/s12 `ftoi/` runs plain casts compiled with the plugin under
  qemu-aarch64 against the host executing `cvttsd2si` / `fisttp` (the instructions MSVC uses) - 810 of 810 equal;
  in ILP32 with the build's flags on f8 units (font.cpp: 44 conversions including S13's 0x00a1b450,
  blk_00a60000_o2: 8, animation.cpp: 1). Adoption (S1, compile.sh) is one flag; see "Fix proposals".
* **argument order (ORDER-ARGS, ORDER-OBJECT, ORDER-OPERATOR)**. **per-site**. MSVC evaluates call arguments right
  to left, GCC left to right. Rows tagged `stream` (reads, random numbers, iterators in two arguments), `same-callee`
  or `same-object` come first; getters with a visible side-effect-free body are not counted. Fix: hoist the
  arguments into locals in MSVC order. **Closed by the fnvnx_order plugin (`args=rtl`, S12-ordering,
  [ORDERING.md](ORDERING.md)):** every call's scalar arguments are evaluated right to left; tests/s12/order args.cpp
  matches GCC x86-32 on 9 call shapes. ORDER-OBJECT and ORDER-OPERATOR are closed by `object=last` and `operator=rtl` (S11 confirms). ORDER-BINOP (both operands of a built-in operator) is
  unspecified on both compilers; it is listed, not judged.
* **volatile and memory ordering (VOLATILE-DECL, POLL-VOLATILE, POLL-PLAIN, ATOMIC-*, BARRIER)**. MSVC's volatile
  accesses are acquire/release and x86 is TSO; on AArch64 GCC gives neither. **Closed by S12-ordering**
  ([ORDERING.md](ORDERING.md)): the fnvnx_order plugin (volatile = ldar/stlr, Interlocked operands ordered), the
  barrier header (dmb ish / ishld / ishst), Interlocked* with a trailing dmb ish and a split lock for misaligned
  operands. ATOMIC-PLAIN-STORE and POLL-PLAIN are closed by the whole-program key list (compile.sh's default
  ORDER_KEYS, ORDERING.md "Whole-program keys"). The bullets below are the analysis that led there.
  * `_ReadWriteBarrier` / `_ReadBarrier` / `_WriteBarrier`: **header**. In `fnvnx_compat.h` they become
    `__atomic_thread_fence(__ATOMIC_SEQ_CST)`, `__ATOMIC_ACQUIRE` and `__ATOMIC_RELEASE` (`dmb ish` / `dmb ishld`)
    instead of an empty asm. Still open at 0c630cf and at the branch head: the shim keeps the empty asm, and the 436 f8
    objects of the sample contain no `dmb` at all.
  * volatile accesses: **runtime / compiler support**. Either a GCC plugin turns volatile loads and stores of decomp
    units into `ldar`/`stlr` (`/volatile:ms` emulation), or a rule wraps the listed declarations in
    `fnvnx_msvolatile<T>` (same size, acquire/release accessors).
  * plain stores to `Interlocked*` operands (ATOMIC-PLAIN-STORE), the unlock half of a spin lock: was per-site (rule
    ATOM1); **closed by the plugin with the whole-program key list**, which reaches every unit (ATOM1 not needed).
  * POLL-PLAIN: was per-site; **closed by the plugin**: a wait-only loop's polled fields are ordered (ldar in the
    loop) and, through the key list, every store to them in any unit is a release.
  * Interlocked on packed or misaligned fields (ATOMIC-UNALIGNED): **per-site**. `ldaxr`/`stlxr` fault on
    misalignment where x86 `lock` does not.
* **record layout (LAYOUT-\*)**. Mixed-type bit-fields (MSVC opens a new unit whenever the declared type size
  changes), `#pragma pack` with over-aligned members (`__declspec(align)` and `__m128` beat pack in MSVC, pack caps
  them in GCC, also for an `aligned` attribute on the field), and empty bases after the first (MSVC gives each a byte).
  These are **rule/per-site** where a byte image depends on the layout: files, saves, the data image, or a decomp
  `static_assert`. Records whose only difference is an empty base at another offset are benign. So are records that
  differ only through a member or base type (not root causes).
  * **`__declspec(align(n))` before the class key (ALIGN-IGNORED, LAYOUT-ALIGN-IGNORED)**. **rule ALIGN1** (new on
    f8). `__declspec(align(16)) struct Vec { float x, y, z, w; };`: MSVC aligns the type, while GCC drops an
    attribute in that position ("attribute ignored in declaration of 'struct Vec'", `-Wattributes`, which the gate
    used to ignore). Vec is then 4-aligned, and a record holding it shrinks and moves its members (in the sample: a
    48-byte record is 36 bytes under GCC, a 240-byte one is 224 with every member after the first moved). The probe
    confirms GCC's layout, and tests/s12 `layouts.cpp` shows the fix: after the class key
    (`struct __declspec(align(16)) Vec`) GCC honours it. 22 root declarations and 21 affected records in the sample, all Havok / staging. The tree has 199
    `__declspec(align(n)) struct|class|union` spellings in 115 files; the ones without a declarator are the roots.
  * **LAYOUT-PARSE**: the two clang configurations see different members (the MSVC configuration drops `__m128`
    unions after a parse error). A proxy artefact, not a difference: the probe checks the gnu side against GCC.
* **enums (ENUM-BITFIELD, ENUM-UNSIGNED, ENUM-64)**. **rule ENUM2** (S1). MSVC enums are always `int`, so an enum
  bit-field is signed and 3 in 2 bits reads back -1, and `0x80000000` is `INT_MIN`. GCC picks `unsigned int`. Giving
  the listed enums the fixed underlying type `int` (`enum E : int`, with `(int)` casts on enumerators above
  `INT_MAX`) gives MSVC's semantics exactly. S1's ENUM1 (after 0c630cf, f87f63d) writes `: int` only for enums the
  decomp declares opaque, and leaves out on purpose every enum with an enumerator outside int, which is exactly
  ENUM-UNSIGNED. ENUM2 extends it to the gate's site lists.
* **varargs (VA-STRUCT, FMT-STRUCT, VA-ADDR-PARAM, W-VA-PROMOTE)**. **per-site**. A struct passed through `...` is
  copied onto the stack on x86, so a one-pointer string object works as `%s`. On AArch64 a struct goes in registers,
  or by reference above 16 bytes. Taking the address of the last named parameter to walk the stack is an x86-only
  idiom. GCC replaces `va_arg(ap, float/char/short/enum)` with a trap.
* **formats (FMT-MSVC, FMT-WIDE, WCHAR-LIB)**. **runtime (S4)**. `sprintf`/`vsprintf`/`sscanf` of game code come
  from newlib (RUNTIME.md), which does not know `%I64d`, `%S`, or 16-bit `wchar_t`. Route them through S4's MSVC
  formatter, as `fprintf`/`printf` already are, and give the wide functions 16-bit implementations.
* **uninitialized reads, missing returns, dangling stack addresses, unsequenced modifications (W-UNINIT, W-RETURN,
  W-STACKADDR, W-UNSEQ)**. **per-site**. `report.md` counts how many W-UNINIT sites are in units the original built
  with `/Od`. There the stack contents decided what was read, so the original value is often recoverable from the
  exe. `-fno-isolate-erroneous-paths-dereference` is no longer proposed: it would return a dead local's stale
  address instead of NULL (W-STACKADDR, 1 site in the sample), but the same GCC pass also turns a division by a
  provable zero into a trap (facts: `divzero`), which is what x86 does there (#DE); without it AArch64's `udiv`
  returns 0 and the game runs on.
  * W-RETURN is **closed** by S1's FALL1 (`__builtin_trap()` at the end of every non-void function without a final
    return) and the build's `-Werror=return-type`. 91 warnings in the old run, 3 on the 249 units both runs compiled,
    0 on f8. What remains is where FALL1's trap is reachable and MSVC returned eax: GCC keeps only 11 `brk` in the
    323 FALL1-marked units of f8 that compile (it removes the other traps as dead code), 8 of them in units with no
    other trap source (HasValidTriangle, FUN_00bb62b0, FUN_00af28f0, RegisterActorValue, AddAtFirstFreeSlot,
    UploadQuads, PopRecord, FUN_00cf6f10). These were S1b's RET1 candidates. One is not FALL1: UploadQuads
    0x00e723a0 traps in the middle, at `byteCount / stride` where its inlined `GetStride()` returns 0 for a geometry
    with no streams (`count > 0 ? strides[0] : 0`); GCC's isolate-erroneous-paths pass made that division a `brk`.
    The x86 code divides by zero on the same path and raises #DE, so both crash there: benign, nothing to fix.
* **loop UB (W-LOOP-UB)**, **pointer overflow**, **forward progress**: **flag**, see below.
* **pointer sign (P2I-SIGNED, P2I64)**. **runtime**. `FalloutNV.exe` is not large-address-aware, so no address at
  or above 2 GiB ever reached this code on Windows. S5 puts the guest heap at 0x40000000 + 2 GiB, which crosses
  0x80000000. Cap the guest heap below 0x80000000. The data image and native code are already below it.
* **TLS**. **runtime**. `__declspec(thread)` variables become `__thread` on TPIDR_EL0. S2 must never rebind a
  native TLS definition to an image address (its weak rebinding of natively defined image variables, J-0008), and
  S4/S5 must make sure no image code reaches them through the TEB TLS index.
* **bool bytes (BOOL-PUN)**. **runtime**, rule where needed. Memory of another type read as `bool`
  (`*(bool*)((char*)this + 0x104)`, `*((bool*)this + 0xe2)`, the decomp's raw-offset style). MSVC tests the whole byte
  (`test al, al`) and keeps its value in integer contexts; GCC takes the byte as 0/1 (`!b` is `b ^ 1`, `b ? x : y` tests bit 0;
  facts). Same result while the byte holds a real bool, which is what MSVC wrote into the image and into saves. It
  matters only where the byte is really a flag byte or counter typed `bool` by the decomp. Fix: rule **BOOL1** (S1,
  from the site list, only on reads, only where the byte can hold other values): `(*(unsigned char*)(p) != 0)`.
* **float bits (FLOAT-BITS)**. **per-site**. A float seen as an integer: a pointer pun (`*(unsigned*)&f`), the integer
  member of a float/integer union, `_mm_movemask_ps` / `_mm_castps_si128` of an arithmetic result (a compare mask
  is the same on both). A generated NaN is 0xFFC00000 on x86 (x87 and SSE "real indefinite", sign set) and 0x7FC00000
  on AArch64 (FPCR.DN gives the same). Float arithmetic and compares cannot tell them apart; only these integer views
  can. No flag exists. Review the sites that test the sign bit or compare with an integer NaN pattern.
* **pointer difference (PTRDIFF-EXACT)**. **benign**. `p - q` with a non-power-of-two element size: both compilers
  divide with an exact-division multiply that is right only when the byte difference is a multiple of the size. For
  pointers into one array they agree; for unrelated pointers (fuzzed garbage) they give different garbage.
* **out-parameters (OUTARG-UNINIT)**. **benign** for the port, a **harness** class for S13. An uninitialized local
  whose first use passes its address to a call (`T x; f(&x); use(x);`, a buffer by decay, `x.Init()`, a non-const
  reference parameter). The callee writes it. S13 runs callees as stubs that do not write through pointer arguments
  (DIFFFUZZ.md, Limits), so a divergence of such a function that reads `x` is the harness's. GCC warns only when it
  inlines the callee. The 4 `-Wmaybe-uninitialized` warnings of the old run in the 249 units both runs compiled were
  of this kind (racesexmenu.cpp `ilow` through `int&` out-parameters), and they are gone on f8 with and without `-fPIC`
  (checked): the source change since then, not D16, removed them.
* **ILP32 addressing**. **benign**, no class (S13 ilp32-pointer). GCC forms every address as zero-extended base plus
  sign-extended 32-bit offset, computed in 64 bits (`ldr w0, [x1, w0, sxtw]`, also for an `unsigned` index; facts).
  That equals x86's address mod 2^32 whenever the true address is in [0, 2^32). Every valid x86 access is: the exe is
  not large-address-aware, so base and result are below 2 GiB. A difference needs a wild pointer or index whose x86
  address would have been at or above 2 GiB, which faults on Windows too. All 38 S13 ilp32-pointer records are of this
  kind: 36 negative offsets whose x86 address is at or above 0x80000000, and 2 reads through a stored 0xffffffff
  "pointer". Objects S5 places above 2 GiB stay correct: base + offset stays below 4 GiB. The sign of such an
  address is a different matter (P2I-SIGNED).
* **benign**: 32-bit variable shifts (both mask by 31), integer division (x86 traps where we continue; S13's two
  division divergences are x86 dividing by zero), `this == 0` (kept by D10), `long double` (two unused template
  specializations), float -> int64, mismatched new/delete (one heap), GCC ABI notes, `__debugbreak()` in Havok error
  paths (int3 in the original as well).

### Flags (proposed addition to D10)

```
-fwrapv-pointer -fno-finite-loops -fno-aggressive-loop-optimizations
```

None of them is in compile.sh yet (0c630cf and the branch head). The array is `PROPOSED_FLAGS` in
`tools/static/flags.sh`; `tests/s12/run.py` checks what each one changes. Each goes into `tools/link/compile.sh` and
`tools/census.sh` (owners S1/S2). No longer proposed: `-funreachable-traps`. FALL1 traps at the end of every
non-void function without a return, `-Werror=return-type` rejects any other missing return, and the shim's
`__assume` is a no-op, so no `__builtin_unreachable` is left for it to turn into a trap. Not proposed:
`-ftrivial-auto-var-init=zero` (no semantic effect, verified) and `-fstack-reuse=none`. GCC still deletes stores to
a dead local, so a dangling read does not see the old data anyway; it only makes frames larger.

### Header change (proposed, `shim/include/fnvnx_compat.h`, owner S1)

```c
inline __attribute__((always_inline)) void _ReadWriteBarrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
inline __attribute__((always_inline)) void _ReadBarrier(void) { __atomic_thread_fence(__ATOMIC_ACQUIRE); }
inline __attribute__((always_inline)) void _WriteBarrier(void) { __atomic_thread_fence(__ATOMIC_RELEASE); }
```

### Rules (proposed, owner S1; driven by `sites/<class>.tsv`)

* **ALIGN1** (new, real layout differences): `__declspec(align(n)) struct|class|union X {` becomes
  `struct|class|union __declspec(align(n)) X {`, for declarations without a declarator (exactly the ones GCC
  reports as "attribute ignored in declaration of"). Same meaning under MSVC; GCC then aligns the type (tests/s12
  `layouts.cpp`: `UsesPre` 20 bytes against MSVC's 32, `UsesPost` 32).
* (FTOI1 / FTOI2 as source rules: superseded by the fnvnx_ftoi plugin, which covers every conversion, see the
  float -> integer class.)
* **ENUM2** (S1's ENUM1 id is taken by the opaque-enum rule): the ENUM-BITFIELD / ENUM-UNSIGNED enums get
  `enum E : int`, with `(int)` casts on enumerators above `INT_MAX`.
* **ATOM1** (not needed: the fnvnx_order key list orders these stores in every unit): the ATOMIC-PLAIN-STORE sites
  would become `__atomic_store_n(&lhs, rhs, __ATOMIC_RELEASE)` (3 in the sample, all on one refcount field).
* **BOOL1** (only where the byte can hold other values): BOOL-PUN reads become `(*(unsigned char*)(p) != 0)`.
* **ORDER1** (optional): the `stream` / `same-callee` ORDER-ARGS sites get their arguments hoisted into locals,
  last argument first.

## Status on f8 (tree of 0c630cf, S2c's fresh8), stratified sample

Sample: every 10th unit of `build/units.json` (298) plus every unit with an S13 f10 divergence (184), 462 units
(20 in both). It holds 721 of the 1,523 functions S13 fuzzed, 248 of them divergent. The tree was gated read-only with
the shim and `platform/crt` of 0c630cf (`SHIM`). GCC compiled 436 units and clang gnu parsed 461 (98 with errors
inside the tree). The probe ran 461 checks: 446 ok, 15 unverifiable, no mismatch.

"Earlier" is the gate's only previous run (2026-11-03, before FALL1/STATIC1/GAME1/RET1): the restart cut it at 1,344
GCC units and 554 clang gnu units, with no msvc pass, so it has no layout classes. "Same units" compares it with f8 on
the units both runs covered (249 GCC, 106 clang), so the numbers measure the tree and gate changes, not the
coverage. The classes added in this round have no earlier count.

| class | earlier run | same units: earlier -> f8 | f8 sample | status |
|---|---:|---:|---:|---|
| W-RETURN | 91 | 3 -> 0 | 0 | closed: FALL1 + `-Werror=return-type`; 8 reachable traps tree-wide go to RET1 |
| THIS-NULL | 286 | 208 -> 208 | 400 | closed: `-fno-delete-null-pointer-checks` (fact: compare kept) |
| (char signedness, signed overflow, aliasing, FP contraction) | - | - | - | closed: `-fsigned-char` `-fwrapv` `-fno-strict-aliasing` `-ffp-contract=off` (facts; 0 fused ops in 436 f8 objects) |
| W-UNINIT | 8 | 4 -> 0 | 3 | closed for scalars: fnvnx_order `uninit=zero` reads an unwritten local as 0 (ORDERING.md); 11 -> 0 warnings on shard 3's sites |
| W-ARRAY-BOUNDS | 194 | 96 -> 91 | 125 | open (per-site) |
| W-LOOP-UB | 1 | 1 -> 2 | 2 | open: `-fno-aggressive-loop-optimizations` not adopted |
| F2U | 135 | 75 -> 75 | 152 | open: the fnvnx_ftoi plugin closes it once compile.sh loads it; never fuzzed |
| F2S | 792 | 432 -> 432 | 536 | open: NaN / out of int32 give 0x80000000 on x86 (S13 0x00a1b450); the fnvnx_ftoi plugin closes it once compile.sh loads it |
| ORDER-ARGS / -OBJECT / -OPERATOR | 1,018 / 578 / 53 | 441 / 270 / 24, unchanged | 897 / 490 / 47 | ORDER-ARGS closed: fnvnx_order args=rtl; -OBJECT / -OPERATOR closed: object=last, operator=rtl (ORDERING.md; S11 confirms) |
| ORDER-BINOP / -ASSIGN | 1,046 / 17 | 470 / 4, unchanged | 831 / 51 | listed (unspecified on both) |
| VOLATILE-DECL / BARRIER / POLL-* | 146 / 15 / 5 | 41 / 5 / 3, unchanged | 928 / 197 / 5 | closed: fnvnx_order + barrier header (ORDERING.md); POLL-PLAIN: plain-poll rule + whole-program keys |
| ATOMIC-USE / ATOMIC-PLAIN-STORE | 148 / - | 42 / 0 -> 1 | 165 / 3 | ATOMIC-USE closed (fully ordered Interlocked, in-unit operands and forwarders); PLAIN-STORE closed: compile.sh's whole-program ORDER_KEYS (default) |
| ENUM-UNSIGNED | 14 | 5 -> 5 | 6 | open: S1's ENUM1 excludes these by design (ENUM2) |
| P2I-SIGNED | 16 | 5 -> 5 | 12 | runtime (S5 heap below 2 GiB) |
| SHIFT64-VAR | 2 | - | 10 | open (per-site) |
| W-NONPOD-VARARGS / VA-STRUCT / VA-ADDR-PARAM | 451 / 2 / 12 | 84 / - / 4, unchanged | 231 / 2 / 7 | open (per-site) |
| W-CAST-FN | 58 | 33 -> 32 | 78 | review |
| TLS | 73 | 30 -> 30 | 206 | closed: S2 tls binding + fs:[0x2c][0] hook share one block per thread |
| ALIGN-IGNORED (new) | - | - | 22 | **open, real**: ALIGN1 |
| LAYOUT-ALIGN-IGNORED (new) | - | - | 21 | **open, real**: ALIGN1 |
| LAYOUT-BITFIELD / -EMPTY-BASE | - | - | 2 / 3 | open (per-site where a byte image depends on it) |
| LAYOUT-EMPTY-BASE-OFFSET / -MEMBER-TYPE / -BASE-TYPE / -PARSE | - | - | 94 / 147 / 17 / 17 | benign / derived / proxy |
| BOOL-PUN (new) | - | - | 66 | runtime; BOOL1 where a flag byte is typed bool |
| FLOAT-BITS (new) | - | - | 156 | per-site review (sign-bit tests) |
| PTRDIFF-EXACT (new) | - | - | 19 | benign |
| OUTARG-UNINIT (new) | - | - | 2,364 | benign for the port; S13 harness class |
| DIV-VAR / SHIFT32-VAR | 58 / 57 | 22 / 20, unchanged | 87 / 178 | benign |

The old gate also counted 4 narrowing *errors* that the build never sees: it re-enabled `-Wnarrowing`, which
compile.sh turns off. With BUILD_FLAGS equal to compile.sh's they are gone, and the GCC pass fails on exactly the
units the build fails on.

## Cross-check with S13 (f10 campaigns: leaf_f10, rules_f10, bridge_f10)

Ground truth: S13's 312 saved divergences (248 functions, 184 units) with S13's own final causes
(`crosscheck.py causes`, which reruns `tools/difffuzz/report.reclassify`; the counts equal `report_f10.md`). Each gate
site is attributed to the function whose `// @fnv` tag and brace-matched body contain it (6,387 of the sample's
8,825 sites; the rest are declarations, layouts and headers).

**By S13 cause** (divergent functions in the sample; a hit means a predicting class has a site in the function):

| S13 cause | functions | predicting classes | hit | miss | verdict |
|---|---:|---|---:|---:|---|
| unclassified | 105 | - | - | - | classes present: OUTARG-UNINIT 17, ORDER-ARGS 10, ORDER-BINOP 8, F2S 4 |
| ilp32-pointer | 38 | (none needed) | 0 | 38 | benign: all 38 need an x86 address at or above 2 GiB (36 negative offsets, 2 a 0xffffffff "pointer"); GCC's `zext(base) + sext(off32)` is exact for every valid access (facts) |
| call-flow | 34 | ORDER-* | 9 | 25 | 3 records are consistent with a swap at the flagged statement (00603370 ORDER-ARGS, 00713f00 ORDER-OBJECT, 007b7b40 ORDER-BINOP), 2 are naming (`rand` vs `fnvnx_rand`); 8 have OUTARG-UNINIT |
| bool-repr | 29 | BOOL-PUN, PTRDIFF-EXACT | 3 | 26 | soft: in the ones read, bool globals, references and stub results the fuzzer filled with bytes other than 0/1 |
| nan-bits | 25 | FLOAT-BITS | 1 | 24 | soft: generated NaNs (0/0, inf*0) in plain float/SSE arithmetic; S13 compares bytes, the program sees them only through FLOAT-BITS; 12 have OUTARG-UNINIT (garbage out-parameters) |
| uninit-arm / -both / -x86 | 12 / 7 / 6 | W-UNINIT, OUTARG-UNINIT | 4 / 5 / 2 | 8 / 2 / 4 | W-UNINIT hits 0; OUTARG-UNINIT 11 of 25: a stub left the local unwritten |
| x86-custom-cc | 11 | (none) | 0 | 11 | binary-level (MSVC whole-program conventions): not visible in source; S13's `classify.entry_register_reads` is the static check |
| local-static, split-global, vptr-store | 4, 2, 1 | (none) | - | - | S2 link classes (S13's `check_statics.py` / `check_rebind.py`) |
| signedness / signedness? | 2 / 4 | F2U, F2S, ENUM-*, SHIFT64, P2I-SIGNED, W-TYPE-LIMITS | 0 | 6 | 4 have OUTARG-UNINIT; one "signedness?" is a NaN sign in a call argument |
| division | 2 | DIV-VAR | 2 | 0 | benign: x86 divides by zero |
| falloff? | 2 | W-RETURN | 0 | 2 | harness: x86 call count 0 is the ASM2 `fsincos` helper and a static-local guard, not a fall-off |
| float, x87-precision | 1, 1 | - | - | - | x87 numerics (out of scope) |

**By class** (fuzzed functions in the sample that hold a site of the class; base rate 248 of 721 = 34%; among
functions that call something 45%, leaf functions 16%):

| class | sites | fuzzed with it | diverged | with a predicted cause | reading |
|---|---:|---:|---:|---|---|
| OUTARG-UNINIT | 2,364 | 72 | 55 (76%) | uninit 11 | a real signal, for the harness: 76% against 45% for calling functions without it |
| ORDER-ARGS | 897 | 28 | 19 (68%) | call-flow 5 | confirm with traces |
| ORDER-BINOP | 831 | 17 | 11 | call-flow 4 | - |
| ORDER-OBJECT | 490 | 11 | 7 | call-flow 1 | - |
| BOOL-PUN | 66 | 8 | 5 | bool-repr 2 | - |
| DIV-VAR | 87 | 5 | 4 | division 2 | - |
| FLOAT-BITS | 156 | 4 | 4 | nan-bits 1 | - |
| F2S | 536 | 6 | 4 | 0 | no divergence with a float->int cause: S13 saw no NaN or out-of-range conversion |
| F2U | 152 | 0 | - | - | **never fuzzed**: no verdict possible |
| VOLATILE-DECL, ATOMIC-*, TLS, LAYOUT-*, ALIGN-* | 928, 168, 206, 301, 22 | 0-1 | - | - | outside what single-function fuzzing can see (threads, layouts) |
| W-NONPOD-VARARGS | 231 | 1 | 0 | - | possibly noise; no fuzz evidence either way |

What follows:
* Every named S13 cause now has a static class or a proof, except x86-custom-cc (binary-level) and the x87 numerics.
  ilp32-pointer and division are benign by proof. bool-repr and nan-bits are soft; BOOL-PUN and FLOAT-BITS mark the
  sites where the program itself can see the difference. The 105 unclassified functions have no cause to match.
* The largest single factor is the harness: 55 of the 248 divergent functions (22%) hold an OUTARG-UNINIT local (76%
  of such functions diverge, against 45% of calling functions without one), and 11 of the 25 uninit-* functions show
  the mechanism directly. S13 should make stubs write out-parameters identically on both machines (or zero them), or
  discount those divergences.
* The classes with many sites and no fuzz hits (F2U, the memory-ordering and layout classes) are not noise. Fuzzing
  never reached them. `crosscheck.py targets` lists the functions with F2U, F2S, BOOL-PUN, FLOAT-BITS and SHIFT64-VAR
  sites as an S13 campaign (375 in the sample).
* The by-class rates are correlations on a few functions per class (S13 fuzzed only 1,523 of the image's functions).
  The three ORDER candidates need `difffuzz.py repro --trace` before they count as confirmed.

## Fix proposals per owner

| owner | fix | classes | evidence |
|---|---|---|---|
| S1 (rule) | **ALIGN1**: `__declspec(align(n))` after the class key when no declarator follows | ALIGN-IGNORED, LAYOUT-ALIGN-IGNORED | real layout differences confirmed by GCC (probe, layouts.cpp); 22 roots in the sample |
| S1 (rule) | **ENUM2**: `enum E : int` for the ENUM-BITFIELD / ENUM-UNSIGNED lists, `(int)` casts on enumerators above INT_MAX | ENUM-* | ENUM1 leaves these out by design |
| S1 (compile.sh) | **FTOI2**: load the fnvnx_ftoi plugin - `-fplugin="$(bash "$HERE/toolchain/ftoi/build.sh" "$OUT/.ftoi")"` in FLAGS (build hosts: `gcc-13-plugin-dev-aarch64-linux-gnu`; compile_job.sh's flags hash should include `toolchain/ftoi/fnvnx_ftoi.cc`). The gate follows on its own (gcc_warn.sh loads the plugin when compile.sh names toolchain/ftoi; static_job.sh installs the headers) | F2S, F2U, F2S64, F2U64 (every float -> integer conversion; 1,975 in the MSVC objects) | tests/s12 ftoi 810/810 against x86; S13 0x00a1b450 |
| S12-ordering (done) | fnvnx_order plugin, barrier header, Interlocked* (supersedes ATOM1 and the barrier proposal) | VOLATILE-DECL, POLL-VOLATILE, BARRIER, ORDER-ARGS, ATOMIC-* | tests/s12/order; ORDERING.md |
| S1/S2 (flags) | `-fwrapv-pointer -fno-finite-loops -fno-aggressive-loop-optimizations` | pointer overflow, forward progress, W-LOOP-UB | facts (`fixed:` lines) |
| S1b (patch) | **RET1** for the 8 reachable FALL1 traps (HasValidTriangle and 7 more, list above) | W-RETURN residue | `brk` kept in the f8 objects |
| S1 (rule, conditional) | **BOOL1** where a BOOL-PUN byte is a flag byte or counter typed `bool` | BOOL-PUN | 2 bool-repr hits; soft otherwise |
| S13 (harness) | stubs write out-parameters identically (or zero them); fuzz `crosscheck.py targets`; trace the 3 ORDER candidates; unclassified x86-only faults concentrate in /O2 units (27 of 41 records, while /O2 units hold 388 of the 1,523 fuzzed functions) | OUTARG-UNINIT, F2U, ORDER-* | matrix above |
| S5 (runtime) | guest heap below 0x80000000 | P2I-SIGNED | unchanged |

## What the gate does not see

* **Values.** Whether a float is ever negative, whether a loop really crosses threads. The gate lists candidates;
  only running the code decides. That is what differential testing (next section) and the debug-build sanitizers
  are for.
* **Units that do not compile** (230 of 2,978 at 0c630cf, S2c's f8 compile). The clang passes still collect findings up to the first errors;
  GCC's middle-end warnings need a compiled unit.
* **x87 numerics.** Excess precision, the 24-bit precision control that D3D9 sets, and `/fp:fast` (6 units) NaN
  comparisons. These are numerical differences, not changes of meaning. S8 handled the hand-written x87 code.
* **Code outside the tree**: the platform layer, shims and libc behaviour (S4's tests).

## Differential testing

S13 built it (`docs/DIFFFUZZ.md`, `tools/difffuzz/`): an MSVC object and our AArch64 object of the same function run
under Unicorn with identical inputs and memory, compared by return value, writes, calls and call arguments. The
section "Cross-check with S13" above joins its verdicts with the gate's sites. `crosscheck.py targets` turns the
classes fuzzing has not reached into its campaign lists.

## Results

`report.md` of a run (local) is the authoritative table. No `baseline.tsv` is committed yet: a baseline needs a full
run, and a sample's counts would not be a valid ceiling for one.
