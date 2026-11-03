# Inline assembly (stream S8-asm)

The decomp (commit `87eb9373`) has **286 `__asm` sites in 83 files**: 148 in `include/fnv_asm_helpers.h` (the
project's x87 helper header) and 138 blocks in 82 units under `src/`. MSVC x86 inline assembly cannot be compiled for
AArch64, so `tools/prepare.py` rewrites every site with the rules in `tools/rules/s8_asm.py` into calls of the
portable functions in `shim/include/fnvnx_asm.h`. The decomp is never edited; nothing here copies decomp code.

Goal: behavioural equivalence (same math in the same operation order, same memory reads and writes in the same order
where operands may alias, same x87-defined edge cases), not bit-exactness. Where the x87 result is bit-reproducible
on AArch64 (precision control at 24 bits, see "Precision") it is reproduced bit for bit.

## Rules

| Id | What | Files / edits at `87eb9373` |
|---|---|---|
| ASM1 | `include/fnv_asm_helpers.h` replaced by a header that defines `FNVNX_ASM_FNV_HELPERS` and includes `fnvnx_asm.h`, which then defines the helper names the decomp calls (`fnv_SinCos`, `fnv_FloatToInt`, `fnv_TransformVectors`, ...) with the same signatures / macro arguments. The 91 units including it are unchanged. Edits = `__asm` tokens of the replaced header. | 1 / 148 |
| ASM2 | `__asm { ... }` blocks in `src/`: the body is normalized (one instruction per line, comments dropped, `ptr [x]`, `, `) and matched against `PATTERNS`; a match becomes `{ <call>; /* fnv-nx ASM2 */ }` padded to the block's line count. No match: block left as is, counted in `ASM-LEFT`, warning on stderr with file, line and enclosing `// @fnv` address, the key of a per-site entry in `SITES` (empty: every site matches a pattern). | 82 / 138 |
| ASM3 | `#include "fnvnx_asm.h"` prepended to every unit ASM2 or ASM4 changed. The header uses only compiler builtins and `fnvnx_`-prefixed `static inline` names, so it can go first in any unit. | 180 / 180 |
| ASM4 | MSVC intrinsic `__rdtsc()` (not `__asm`, same instruction): calls -> `fnvnx_rdtsc64()`, the decomp's local prototypes of it that DECL1 left (block scope, `extern "C"` forms) dropped. Without it the calls would need a link-time `__rdtsc` from the runtime. | 128 / 504 |

`ASM-LEFT` = 0: no `__asm` token remains in the prepared tree.

## Catalogue

### `src/` blocks (ASM2)

Grouped by normalized body; `V` is the C++ variable the block names.

| Sites | Body | Translation |
|---:|---|---|
| 136 | `rdtsc` / `mov dword ptr [V], eax` | `fnvnx_asm_store32(&(V), fnvnx_rdtsc32())` |
| 1 | `fld A` / `fistp B` | `fnvnx_asm_fistp(&(B), A)` |
| 1 | `fld A` / `fsincos` / `fstp C` / `fstp S` | `fnvnx_fsincos(A, &(C), &(S))` (first store = ST0 = cos) |

The 136 timestamp sites are Havok monitor-stream timer markers (`TlsGetValue` of the stream, write the timer name,
store the low 32 tick bits) in 80 units: 100 in `src/havok/physics`, 26 in `src/staging`, 5 in `src/havok/animation`,
3 in `src/fallout/ai`, 2 in `src/havok/common`. Their variable names are `ticks` (92), `t` (13), `stamp` (13), `tick`
(7), `value`, `fnv_ticks`, `stamped` (2 each), `t1`, `t2`, `openingTick`, `closingTick`, `time` (1 each); the
variables are 32-bit (`unsigned int` 85, `hkUint32` 20, `unsigned` 18, `DWORD` 7, `hkUint32_S` 3, `TU32` 2,
`UInt32D3` 1). The store helper writes exactly 4 bytes like the `dword ptr` store, whatever the declared type.

The `fistp` site is the engine's own float-to-int helper at `0x00406d90` (`src/fallout_shared/tesland_od_variant.cpp`),
the `fsincos` site its sine/cosine helper at `0x004169a0` (`src/fallout_shared/tesobjectweap.cpp`).

A pattern for `rdtsc` with both halves stored (`[V]` eax, `[V+4]` edx) is also in the table; no `src/` site uses it.

### `include/fnv_asm_helpers.h` (ASM1)

| Helper | Uses (units) | x87 / x86 semantics | Portable version |
|---|---:|---|---|
| `fnv_SinCos(x, s, c)` | 85 (64) | `fsincos` on a float | `fnvnx_sincos`: `sin`/`cos` in double rounded to float, x87 edge cases (below) |
| `fnv_FloatToInt(x)` | 234 (63) | `fld` + `fistp` dword, current rounding mode | `fnvnx_fistp32f`: `rintf` + range check |
| `fnv_TransformVectors(n, src, dst, m)` | 1 (`0x00a74880`) | 3x3 row-major matrix x n vectors, fixed x87 schedule | `fnvnx_transform_vectors` |
| `fnv_TransformPoints(n, src, dst, m, tx, ty, tz)` | 1 (`0x00a74900`) | same + translation | `fnvnx_transform_points` |
| `fnv_AddWeightedPoint(out, p, w)` | 1 (`0x00a855f0`) | `out += p * w` (skinning) | `fnvnx_add_weighted_point` |
| `fnv_ReadTicks64(t)` | 1 (`0x00dd8e00`, hkSystemClock) | `rdtsc`, both halves stored | `fnvnx_asm_store64(&t, fnvnx_rdtsc64())` |
| `fnv_CpuidMaxExtended(r)` | 1 (`0x00a74a10`) | `cpuid` leaf 0x80000000, EAX | emulated: 0x80000008 |
| `fnv_CpuidLegacyExtended(r)` | 1 (`0x00a74a10`) | loads **EDX** = 0x80000001, so the leaf is whatever EAX held; EAX stored | defined as leaf 0x80000001: EAX = 0 |
| `fnv_CpuidFeatures(r)` | 1 (`0x00a74a10`) | `cpuid` leaf 1, EDX | emulated: `FNVNX_CPUID_1_EDX` = FPU, TSC, CX8, CMOV, MMX, FXSR, SSE, SSE2 |
| `fnv_Breakpoint()` | 12 (8) | `int 3` (Havok `HK_BREAKPOINT` after an error report) | `FNVNX_ASM_BREAKPOINT()`, default `__builtin_trap()` |

With the emulated CPUID, `0x00a74a10` grades the CPU as level 5 (MMX + CMOV + SSE), as on any SSE-class PC.

## Translation notes

**x87 stack schedules.** The three fixed loops were decoded instruction by instruction (stack contents after each
`fld`/`fxch`/`faddp`/`fstp`). Results:

* vectors: `dst[r] = (s0*m[3r] + s1*m[3r+1]) + s2*m[3r+2]`. Memory order: row 0 and row 1 products are loaded, then
  `dst[0]` is stored, then `s0` and `m[6..8]` are loaded **again** for row 2, then `dst[1]` is stored, then `dst[2]`.
  An in-place call therefore computes row 2 from the new `dst[0]`; the C++ keeps that order (pointers not `restrict`).
* points: `dst[r] = ((s0*m[3r] + s1*m[3r+1]) + s2*m[3r+2]) + t[r]`; every load precedes the first store.
* weighted point: `out[k] = p[k]*w + out[k]`; `out[0]` is stored before `out[2]` is loaded.
* `n` is tested for zero first; the loop runs `n` times (`dec`/`jne`).

**fistp.** Rounds in the current mode (`rint`, which follows FPCR.RMode on AArch64; the runtime maps `_controlfp`'s RC
onto it). NaN, infinities and values outside the destination range store the integer indefinite (`0x80000000`,
`0x8000`, `0x8000000000000000`), the x87 masked-invalid response; AArch64 `fcvtzs` would saturate, hence the explicit
check. Store width follows the destination type (word / dword / qword) like MSVC's operand-size inference.

**fsincos.** In range (`|x| < 2^63`): cos in ST0, sin in ST1. NaN: both results are the (quieted) NaN. ±inf: invalid,
both the default NaN (`0xffc00000`). Finite `|x| >= 2^63`: C2 set and nothing pushed, so the first `fstp` stores `x`
and the second underflows the register stack and stores the default NaN. All four cases are reproduced.

**rdtsc.** AArch64: `CNTVCT_EL0` (19.2 MHz on the Switch, readable from EL0; libnx `armGetSystemTick()` reads the same
register), with a compiler memory barrier so the read stays where the `__asm` block was. Only the rate differs from a
PC TSC: tick consumers take differences, and Havok's hkSystemClock (`0x00dd8e20`) calibrates its tick rate against
`QueryPerformanceCounter`/`QueryPerformanceFrequency`. The low 32 bits wrap every 224 s instead of every 1-2 s.

## Precision

* **x87 precision control.** D3D9 sets the x87 precision control to 24 bits on device creation unless the game
  passes `D3DCREATE_FPU_PRESERVE`; then every x87 add/mul rounds to float, and the three loops above give the same bits
  as float arithmetic in the decoded order. To keep that on AArch64 the products go through `fnvnx_x87_mul` (an empty
  asm register barrier) so GCC cannot fuse them into FMA (`-std=gnu++17` defaults to `-ffp-contract=fast`, and an
  `optimize("fp-contract=off")` attribute is lost on inlining - measured). If the game ran with 53/64-bit precision,
  the original keeps extended intermediates and differs from float math in the last bits (large ulp counts only on
  results that cancel to near zero).
* **fsincos** is computed in extended precision with a 66-bit pi; `sin`/`cos` in double reduce exactly. Rounded to
  float they agree bit for bit over [-100, 100] rad (800,001 samples); they differ for `|x|` in the thousands of
  radians and above, where the x87 reduction is the inaccurate one. `FNVNX_ASM_FAST_TRIG` switches to `sinf`/`cosf`
  (cheaper on the A57, within 1 ulp).
* **fistp** is exact (integer result) in every rounding mode.

## Verification

* `tests/s8/run.sh`: host build and run of `tests/s8/test_asm.cpp`, which checks every helper against x87-free
  reference math written from the documented x87 semantics (long double, all four rounding modes, edge cases,
  in-place aliasing, store widths); plus an AArch64 ILP32 compile of the test and of a unit-like snippet.
* Not committed (it transliterates the decomp's instruction sequences): a host x86-64 harness that runs the original
  x87 sequences on the real FPU and compares bit patterns. Result: fistp 0 mismatches (4 modes x 200,000 values incl.
  NaN/inf/out of range); fsincos 0 mismatches on [-100, 100] and on all edge cases; the three loops 0 mismatches
  (including in place) at precision control 24 bits.
* Parse: the 265 units that contain `__asm`, include the helper header or call `__rdtsc`, compiled with the census
  flags (`-fsyntax-only`, AArch64 ILP32) on trees prepared without and with the S8 rules: clean 14 -> 50, errors
  2,722 -> 1,797, no unit with more errors, no error located in `fnvnx_asm.h` or at a rewritten site. The x87 /
  `__asm` errors (839 before: `fld`, `rdtsc`, `fsincos`, ... not declared, `expected ... before` on asm bodies) are
  gone; what remains in those units belongs to other streams.
