# L4 w7 001: lib/sha256 intake (supersedes the earlier stop note)

Branch claude/fnv-l4-verify-w7 at ef0df80a (+ this note). Base claude/fnv-l4-verify 6608f34 (unrelated to L6 history: a merge is
impossible, "refusing to merge unrelated histories"). Per lead: path-only import `git checkout f12e052f -- ports/fnv-nx/lib/sha256 lib/sha256_tests`
(claude/fnv-l6-render f12e052f). No loader/.

(0) farm/setup_verdict_env.sh + envcheck.py: `envcheck: OK`. fnv-nx-build attached; tools/gcc-ilp32-msvcmodel cloned, HEAD b4057704.
(1) recipe.py check --root lib/sha256: "recipe valid". Cold build (fresh store): green, 1 object, stages run 2 hit 0.
    Warm build (same store): green, stages run 0 hit 2. At ef0df80a.
(2) One definition: objects.json `defined` of the root = [fnvnx_sha256] (one, GLOBAL FUNC, sha256.c.o). No other admitted product root exists on this
    branch. 869b056a's loader (its Makefile, in a separate worktree, host cc): sha256.o and libfnvnx_loader.a each define fnvnx_sha256 once; the
    Makefile compiles ../lib/sha256/sha256.c, no copy in loader/src (loader objects: pe image imports codeptr deldtor trap define none).
    `git grep 0x6a09e667 ports/fnv-nx` (this branch): 1 hit, lib/sha256/sha256.c:37. In the 869b056a worktree: 1 hit, the same file.
(3) Body diff cc188cf9~1:loader/src/sha256.c vs 869b056a:lib/sha256/sha256.c: line 1 comment ("only used for the content probes" ->
    "the content-key hash of fnv-nx") and the include lines (`internal.h` -> `<string.h>` + `fnvnx_sha256.h`) only. f12e052f vs 869b056a under lib/:
    only lib/sha256/build.json differs (+9 semantics flags: -fms-extensions .. -ffp-contract=off); sha256.c is identical.
(4) SHA256SUMS: 3/3 OK. Host ASan/UBSan drivers as the README says: nist ShortMsg+LongMsg ok 133 mismatches 0 (4 FIPS + 65 + 64);
    monte ok 100 mismatches 0. Total 233/233, 0 mismatches, at ef0df80a.
(5) L1 verdict (l1.py verdict, tree 979927ad..., commit ef0df80a): RED, 319.2 s, over budget (budget 300), 26 gates.
    Green: pure, oracle, build, objstub, determinism, argordergate, hostapi (0/0).
    Red:
    - selftests 55/56: uncertified verify/tests/test_gate_hostapi.py (287 s; not related to lib/; not checked against the base).
    - admit 0/451: every tracked file uncertified. The product entry names REQUIRED_PRODUCT, 11 of whose gates are not in this branch's gates.json
      (forms d3d9total r7 namegate abi datamodel order fpexact eh exepin addr32), so admission rule 3 fails for the root and, as the first
      line of the reason shows, for the whole tree. The admit entries (product lib/sha256 target guest; doc lib/sha256_tests) are PROVISIONAL, for L1's decision:
      no `test` class exists, so sha256_tests is class doc only to have exactly one root.
    - linkstrict 1/2: lib/sha256/sha256.c:memcpy (a libc symbol the root does not define: needs the guest runtime at link).
    - n1 n2_data n2_call n2_vcall xc xunit rtti pcrel bodysel abs disp reach reachlink gpa: 0/1 each, uncertified lib/sha256:inputs (no inputs file).
    - imports: lib/sha256:imports.in.json missing; machine: lib/sha256:registry.in.json missing.
Note: admit.json was rewritten by a json dump (indent 1), so its diff shows existing lines reformatted.
