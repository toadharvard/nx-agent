### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-010)

* **task** - On the orchestrator's message (taken over from S1-rules): tests/s5/void_defs.py lists 363 candidates on
  link/9f865b0a4037, each a void native definition with a value-returning reference. Each was triaged against the
  exe, and the confirmed ones were fixed by REC1-REPL.
* **triage (exe)** - Every direct `call addr` was found in .text. For each site I checked whether the caller reads
  eax/ax/al before writing it, within 6 instructions or up to a branch or call.
  - 108 confirmed (some caller reads eax).
  - 115 false positives: callers only clobber or ignore eax.
  - 128 false positives: the tool's "value-returning reference" is a data symbol (`?DAT_...@@3HA`, a function address
    the decomp treats as an int), and there is no direct call. This is a void_defs.py flaw; for S5, keep code
    references only.
  - 12 indirect only (no direct call): 7 never set eax and are false positives; 5 set it and went to review.
  - Each of the 113 confirmed or review rows was then read by hand:
    - 87 are real and fixed;
    - 24 are false (below);
    - 2 are unit bugs that work natively by accident: 0x00aa1370 (`jmp _strnicmp`, declared `void()`) and 0x00e4a540
      (2 arguments, declared none). Both are left as they are and reported.
* **hit rate** - void_defs.py: 89 of 363 real (24.5 %), 87 fixed and 2 only by accident correct. Caller-use triage
  alone: 87 of 113 (77 %).
* **fix** - 52 units on fnv-nx-build `recon/<unit>_void`. The suffix keeps these off other streams' `recon/<unit>`
  branches; REC1 applies every recon.json.
  - Each is a GCC-only `replace`: `#if defined(_MSC_VER)` keeps the original, and `#else` gives the declaration and
    definition the real return type and `return <expr>`. Callee declarations are fixed where they too were void.
  - MSVC still compiles the original, so the recipe's void decorated names and the D20 byte match are unchanged:
    try.py gives all bytes equal on every unit. The Itanium names carry no return type, so the link is unchanged.
  - gcc compiles every unit, and objdump shows w0/x0/s0 (or edx:eax as x0) reaching ret, or a tail call to the
    value-returning callee.
  - Float returns (ST0) were checked through the callers' fstp/fcomp.
  - Return types: bool 20+, int/unsigned 25+, pointers 20+, float 4, u64 3, ushort 3, uchar 2.
* **beyond the return type (exe-evidenced, recorded in each fwd.json)**
  - Decomp arities corrected, GCC-only:
    - 0x00d607b0 hkOArchive writeRaw: `(int)` -> `(const void*, int)`. The native code lost the 2nd argument.
    - 0x00665f20: AdvanceState gets its 2nd argument (`ret 8`).
    - 0x0044c000: TSMap::InsertOrReplace takes 4 arguments.
    - 0x00aa1ab0: the path argument.
    - 0x00db7dd0: both arguments.
  - Where the declaration sits in a header a unit replace cannot reach (GarbageCollector, NiObjectNET,
    forenderedmenu decls), the GCC side defines the function with `__asm__("<the native name>")`, so the link sees
    no change.
  - bsspinlock_console: the definition is in a .inl. The unit's `#include` is wrapped, and GCC gets an edited copy of
    the prepared .inl, which will drift if that .inl changes.
  - 0x00aa1420 (NiMemManager Allocate): the GCC side also evaluates the decomp's comma-expression arguments in the
    exe's order (under C++17 the old code read `obj` uninitialized).
* **false positives after reading (24)** - Grouped by cause:
  - eax is a leftover, and the triage "use" is `xor eax, eax` / `xor al, al` / `or eax, -1`: 00450f90, 004b4880,
    0060bef0, 00703c00, 00864980, 00c908c0, 00d1bfa0, 00cb19b0, 00cb1fe0, 004a0c10, 00b601a0, 00d04b00, 00aa4060
    (Deallocate, 1,069 callers, none reads eax), 00608e00.
  - The definition already returns a value; the void name comes from another unit's caller declaration or an
    overload: 009ac9c0, 00a62130, 00be0870, 005b5e40, 00a88970, 006dd6a0, 00a5b690, 00a8f3d0, 0064a4d0.
  - Caller wrong: 00d756a0. hkgpconvexhull.cpp:4243 folds two exe calls into one: `e->d756a0(&v)`, then
    0x00d790a0(result, ...), whose result is the one compared.
* **found (other owners)**
  - Two inner callees are still void natively, so the value is lost below the fixed wrappers:
    - 0x00a5d2f0 (blk_00a40000_o2) should return its tail vcall's bool.
    - 0x008691b0 (unmapped/sleep.cpp) should return 0x0078d200's bool.
  - The link's provider row for 0x00ec8d33 says `_itoa`, but it is CRT `strtoul` (pushes 1, calls strtoxl 0x00ec8ad9).
    The tesscriptcompiler fix calls strtoul directly; other callers bound to that row are wrong.
  - Two objects both define `_ZN15PathingLocation16HasValidTriangleEv`: 0x004ff7e0 (pathinglocation.cpp, void) and
    0x006dd6a0 (navmeshinfo_h_unit, bool).
  - Reader::ForwardToReceiver (recon1-010) is not an arity bug. The decomp's by-value return gives MSVC exactly the
    exe's code: the hidden result pointer is the pushed Receiver*, and `push ebx` is the next call's argument. The
    link already bridges it (abi.tsv `sret-explicit`).
* **metric** - void definitions with a value-returning reference, real ones: 89 -> 2 (the two that work by accident).
  The 274 false positives need no change.
