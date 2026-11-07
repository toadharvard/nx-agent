### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **shard 6** - 140 of 158 rows closed (122 of the 132 trap stubs), in 21 more fnv-nx-build `recon/<unit>` branches.
  R6 now has 43 recon branches in all.
  - Every recon passes the whole-unit MSVC 9 match against the exe and the native aarch64 compile.
  - Bindings were checked by hand against the exe call site, and each is an fnvlink `exact` key.
  - Mechanisms: bindings about 100; bodies at new exe addresses 11; alias bodies 8; inline bodies 37.
    - The 11 new-address bodies: 7 atexit destructors of globals (0x00fda290..0x00fdc3f0), NiFile
      FileRead/FileWrite, _mbsicmp, BSSimpleList AddTail and a ProcessData getter. They close the extern-text names
      bound to them.
    - Each of the 37 inline bodies carries its call sites and containing functions in recon.json.
  - New patterns, for every recon stream:
    - MSVC reads a member named `__vecDelDtor` as `??_E`. GCC does not, so the fix is a native-only forwarder
      (`#ifndef _MSC_VER`) to a helper bound to the exe address.
    - Exe `/O2` code inside an `/Od` unit matches with `#pragma optimize("gty", on)`, except when the original came
      from `/Ob2` inlining.
    - fnvlink cannot parse a global's address used as a template argument (`ContainerBase<&DAT_..>`). Alias bodies
      close those names.
* **open, 18 rows (10 traps)** - table on fnv-nx-build `recon-notes/r6` (HANDOFF.md, shard 6 section).
  - S2-bodies-2, 6 rows:
    - wrong decomp bodies 2, in the BSSimpleListC9A::Remove and ObjectListC8E::AddTail callers;
    - a Ghidra-style name whose address is in the middle of a function 1;
    - code-defmissing 2: STAND1 1, a `??_G` tag on a constructor 1;
    - a vtable placeholder virtual 1 (S2c).
  - S5, 1 row: harness scaffolding.
  - R6, needing fuzz (b), 4 rows:
    - 3 atexit NiPointer releases in the exe's `/Ob2`-inlined form, which does not reproduce under the unit's flags;
    - 1 LTCG custom calling convention (arguments in eax/edi).
  - Trap 0 and referenced nowhere, 7 rows: no action.
* **metric** - shard 6: 158 -> 18 rows without a body (trap stubs 132 -> 10). Shards 5+3: 23 rows, 20 of them
  handed off (recon6-004).
