# l3b-w9 -> L1 (two lines)
1. verify/gates.d/selftests.json argv lists ports/fnv-nx/lift and lift/gates but not ports/fnv-nx/lift/proof, so lift/proof/tests/* (incl. the new test_ehoracle.py, 12 tests) do not run in the selftests gate.
2. ports/fnv-nx/lift/proof/tests/oracle.json names test_formgate.py and test_fpexact.py, which do not exist in lift/proof/tests (the existing test files have no entry except test_ehoracle.py, added at 7cb86dd5).
