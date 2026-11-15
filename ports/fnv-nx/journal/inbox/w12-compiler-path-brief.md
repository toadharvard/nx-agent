# Brief for w15: compiler-path conversions of the tests (from w12, L1 item h)

Branch to work on: claude/fnv-l1-contract-w12L2 (after the cand2 merge it holds) or its successor; you have the verdict env.

## Rule
- A test builds only through `verify/recipe.py` (a host-profile root, or a recipe-built program). It never calls a compiler driver itself.
- An execve under the sandbox of any compiler driver that is not under the pinned external/host-pin path is red. Gate: `gate_tests` (execve gate): derived from the sandbox's execve trace of the test run; a compiler mounted outside recipe stages is untraced, i.e. does not exist.
- Check with the gate's selftests: a test that execs an unpinned driver is red; one that builds via the recipe is green.

## Sites (static, per file; commit a1aa3932e)
A site = a driver literal (gcc, g++, cc, clang, cpp, aarch64-*-gcc ...) that reaches the argv of subprocess.run/Popen/call/check_call/check_output or os.exec*/spawn*, directly or through a local name of the same file (assignment fixpoint, ast). Names in recipe/JSON strings are not sites. Lower bound: a compiler name carried through another module (helper imported from realobj etc.) is not seen; after converting, re-run the script (below) and grep the helpers.
```
commit a1aa3932e ; test files 188 ; files with sites 22 ; sites 42
1 contract/tests/test_gate_reach.py [346]
3 contract/tests/test_l2sections.py [70, 77, 81]
1 contract/tests/test_objfacts_dir.py [19]
1 lift/gates/tests/test_gate_order.py [348]
1 lift/proof/tests/test_gate_fpexact.py [38]
1 toolchain/tests/plugbuild.py [48]
1 toolchain/tests/test_ldbl.py [26]
1 toolchain/tests/test_stamp.py [35]
1 verify/tests/test_doubles.py [33]
2 verify/tests/test_gate_hostapi.py [67, 70]
1 verify/tests/test_guest.py [186]
4 verify/tests/test_link.py [261, 185, 284, 239]
1 verify/tests/test_linkstrict.py [41]
4 verify/tests/test_objstub.py [125, 68, 206, 340]
1 verify/tests/test_objstub_assume.py [35]
1 verify/tests/test_objstub_rules.py [34]
4 verify/tests/test_objstub_x64.py [120, 124, 30, 146]
5 verify/tests/test_recipe.py [229, 87, 346, 206, 357]
1 verify/tests/test_runtime.py [229]
1 verify/tests/test_sandbox.py [137]
1 verify/tests/test_sdcheck.py [160]
5 verify/tests/test_unitstage.py [76, 112, 284, 296, 85]
```
Also on the list: lift/proof/tests/test_gate_fpexact.py (L3b w4, 1c445868e) is the entry above; cherry-pick 1c445868e first and run it, failures go to w4 (session_01KmtZKSmVSGEpAznLZh6oF7).

Script (re-run for the global number after your commit): /home/user/nx-agent/ports/fnv-nx/journal/inbox/w12-compiler-sites.py

## Report
Per file: sites before/after, the recipe root or program that replaced the call; global site count after, with the commit; tests that failed and why. One message to L1 when done.
