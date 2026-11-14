# l3b-w3: lift/ test doubles census (static text read, no runs)

Head: claude/fnv-l3a-lift-int3 62b8a2c39 (w3b merge c3c0e1b0d).
Method: `git ls-files` plus text reads of every test file under lift/; the product symbol sets are the definitions in lift/rt (unit set of lift/rt/build.json) and lift/pack.
The symbol column is a text census (a definition found by pattern in the test text, embedded C included); it names candidates, and the per-binary symbol check of the rule (nm of the test binary) comes
with the rewrite. Counts are over the whole set.

## The set
- 50 python files in lift test dirs (lift/*/tests/*.py, lift/test/*.py); 34 are tests/test_*.py (fnvlift 3, gates 5, proof 11, tools 15) and 1 more test (lift/test/import_reads_no_rt_test.py,
  lift/test/modlift_closure_test.py are outside the gate naming); the remaining files are helpers of those tests.
- selftests universe under lift: DIRs `lift`, `lift/proof`, `lift/gates`: 14 test files (proof 11 + gates 3 of 5 where the gate lists them; lift/tools/tests and lift/fnvlift/tests are NOT selftests DIRs, so
  their 18 test files are neither run nor judged by oracle today).

## Doubles, by kind
1. Runs code under Unicorn (import unicorn): 11 files.
   - test_*.py (4): tools/tests/test_disp.py, test_exitbridge.py, test_imp.py, test_msvcrt.py
   - helpers/harness (7): tools/tests/instest.py, instest_extra.py, mmxtest.py, citest.py, sehcodes.py, x87dyn.py; test/x87test.py
2. A stand-in image class `Fake` (hand-written pemod image: secs/bytes/u32): 3 files: test_disp.py, test_imp.py, test_msvcrt.py.
3. Test text defines symbols that a lift product root also defines: tools/tests/sehcodes.py defines fnvl_dispatch (also defined by rt/rt.c) and fnvl_os_unhandled (the host hook that rt/seh.cc and unhandled.cc
   declare): 2 symbols in 1 file. test_disp/test_imp/test_msvcrt/citest/instest_extra/rcptest only declare product symbols (declarations are not doubles); they define their own test_main, t_* entries.
4. Stand-in of an external service: gates/tests/test_gate_lift.py (a git repo standing in for fnv-nx-build): 1 file.
5. Fake gate outputs written by the test: test/modlift_closure_test.py (fake gate_pcsrc.json, pcsrc_s4.json): 1 file.

Union: 13 files carry a double (11 Unicorn files, test_gate_lift.py, modlift_closure_test.py). Per-kind sums 11 + 3 + 1 + 1 + 1 = 17 sites in those 13 files.
The earlier baseline (w2g-rewrite f056fb4b: fakeext, test_formgate, test_fpexact) is already off the tree (removed in 21ae905a); its 3 files do not appear.

## Proposed split with L3a w6
- w3 (L3b side): the instruction/flag oracle group under tools/tests: instest.py, instest_extra.py, mmxtest.py, citest.py, x87dyn.py, test/x87test.py (kind 1, 6 files); sehcodes.py (kind 1 and 3); test_gate_lift.py (kind 4); modlift_closure_test.py (kind 5).
- w6 (L3a side, owner of rt, dispatcher, imports and exit bridge): test_disp.py, test_exitbridge.py, test_imp.py, test_msvcrt.py (kinds 1 and 2).
Open for the lead: the L4 boot machine's shared model is the replacement for the Unicorn harness; its entry point for running lifted objects is not yet on this tree, so the replacement of kind 1 waits for it, while kinds 3, 4, 5
and the deletion of a harness a gate already covers (named per file) can go first.
