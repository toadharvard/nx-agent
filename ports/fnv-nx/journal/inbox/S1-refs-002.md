### J-XXXX | 2026-11-04 | stream: S1-refs | status: wip | parent: J-0149

* **did** - New rules and rule extensions in tools/rules/s1_compile.py (S1-refs section, REFS_RULES) and prepare.py:
  - STD2 (neutral, new): guardpackage's file-scope `double std::abs(float)` (0x00408840) -> `fnvnx_std_abs`. The
    POST hook rename_std_definitions renames the recipe symbol to `?fnvnx_std_abs@@YANM@Z`, and equiv.py
    POST_RULES lists it. 1 file decomp-wide. guardpackage 2 -> 0.
  - COND1 (neutral, new): `c ? false : x.f(a)` where f returns a bool stand-in class (GateE10) -> `(bool)(...)`.
    1 site decomp-wide. hkgpmesh 1 -> 0.
  - RSTR1 (neutral, new): a restrict local passed to a `const X&` parameter -> `fnvnx_unrestrict(v)`. Under MSVC
    that macro is `(v)`. 1 site decomp-wide. hkpsavecontactpointsutil 1 -> 0.
  - NEW5 (neutral, new), with NEW1 extended to plain `unsigned` size parameters. Fixes the "no suitable operator
    delete" errors: nitexturingproperty 3 -> 0, both hkpkdtreecollidableraycaster units 1 -> 0, blk_00a20000_o2_noehsc
    1 -> 0.
    - NEW5 adds `FNVNX_GLOBAL_DELETE` to a base-less class that has a virtual destructor and only a placement delete
      (3 files). The macro is empty under MSVC.
    - The NEW1 extension edits 57 files / 101 sites; MSVC is unaffected because `unsigned` is `size_t` on x86.
  - TPL1, DTOR1, SPEC1 and RCAST1 extended:
    - TPL1 now handles out-of-class members of a class template defined in a header: bsaudiodefines_h_unit 3 -> 0.
    - DTOR1 now covers include-only header units: middlehighprocess_h_unit 3 -> 0. 8 other header units get the
      declarations and are unchanged.
    - SPEC1 now handles members named through a typedef: nitset_h_unit 1 -> 0.
    - RCAST1 bug fixed: it read a cast spelled inside a comment and wrapped the next line, deleting a constructor
      declaration. nipsysdata_o2_variant 1 -> 0.
* **measured**
  - Single units on t/b6585b87bb73 with the current rules: the 17 units above have 0 errors. Census metric by
    count: 20 units / 67 errors -> 9 / 49.
  - Left: tesanimgroup and niviewerstrings (a worktree agent is on them) and S1-STL's units. The farm census of
    this head is pending.
  - MSVC 2008, run locally (toolchain/msvc9, own Wine prefix): bsshader.cpp compiles on the prepared side
    (COM1 + the `_ReadWriteBarrier` declaration in fnvnx_equiv_compat.h). This closes the queue item; no eq6 report
    at or after 0685791 has landed yet.
  - With STD2, guardpackage also compiles under MSVC. Without it, the prepared side fails with C2039.
* **tests** - tests/s1: 0 failed. tests/s11: ok.
* **note** - The merge of the branch's pre-merge copy of aae5ad1 (ab908dd) is a merge commit that changes no files,
  made so the branch fast-forwards (force pushes are refused).
