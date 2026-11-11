# x86 inline assembly (stream S8, rule ASM-Pi)

Rule, in tools/rules/s8_asm.py (prepare). Every `__asm` construct in a source or header of the decomp tree is kept
verbatim for cl and replaced for GCC by one empty GNU asm statement:

    #ifdef _MSC_VER
    <pristine logical lines>
    #else
    <same lines, each construct -> __asm__("");   /* fnv-nx ASM: ... lifted (N3 verdict asm) */>
    #endif
    #line <original number of the next line>

No pattern table, no per-site table, no translation to C. include/fnv_asm_helpers.h goes through the same rule as
every other file (no replacement header).

## Grammar (MSVC `__asm`; the module docstring has the full text)
- block: `__asm {` ... matching `}`; nested braces counted, nested `__asm` are body text.
- statement: `__asm instr`, ending at end of logical line, at the next `__asm`, or at `}`. `;` is an asm comment to
  the end of the physical line. Consecutive statements (white space only between, several per line included) are one
  run and give one `__asm__("")`.
- the token is searched in code only (not in comments, strings, character literals).
- span = the logical lines (backslash-newline joins) holding constructs. A span that is a `#define` is emitted twice
  whole (a conditional cannot go inside a replacement list). A construct in another directive, or a directive line
  inside a construct, cannot be wrapped: prepare raises ASMError naming file:line. After the rewrite the GCC view
  (`gcc_view`: `_MSC_VER` undefined) is scanned for `__asm`; a leftover raises too. There is no ASM-LEFT path.

## Why
Inline asm is outside the portable subset Pi. The N3 plugin (toolchain/pi/fnvnx_pi.cc) gives verdict `asm` to a
function whose body, or anything inlined into it (helpers of fnv_asm_helpers.h included), holds a GIMPLE_ASM. Such a
function is never compiled natively: its body is lifted from the exe's x86 bytes. A hand translation could be chosen
as native without proof; the empty asm statement makes that impossible. Both compilers accept the output.

## __rdtsc()
An intrinsic call, not inline asm: call text and the decomp's local prototypes stay. shim/include/fnvnx_asm.h
defines `extern "C" inline unsigned long long __rdtsc(void)` over fnvnx_rdtsc64 (platform monotonic timer; CNTPCT_EL0
on the Switch). A prototype without linkage after it takes C linkage; checked with both include orders of
fnvnx_win32.h against `extern "C"`, `__cdecl` and block-scope prototypes.

## Counts (decomp checkout, src/ + include/)
83 files hold `__asm`; 148 spans: 129 block constructs (8 inside a `#define`, all in include/fnv_asm_helpers.h),
19 statement runs. 0 errors, 0 `__asm` visible to GCC. fnv_asm_helpers.h: 10 helper names (8 macros, 2 inline
functions: fnv_SinCos, fnv_FloatToInt); its GCC branch compiles (g++ -fsyntax-only, aarch64 ILP32). Tests:
tests/s8/run.sh (synthetic forms + totality over the checkout).

## Open
E1 fistp helper (fnv_FloatToInt, 234 uses) and correctly-rounded sincos (fnv_SinCos, 85 uses) could earn native
bodies by a shim lemma; until then their callers are lifted.
