# l1-contract-020: no doubles in tests (objstub gate)

Branch claude/fnv-l1-contract-w11, from trunk c9765781.

## What
- recipe.py: `build.json` key `tests` ([{name, units}]). A test is linked from all product units of its root plus its own units, same flags. `units`/`objects`/`defined` of a build result are product-only; `res["tests"]` carries each test.
- doubles.py (pure judge) and gate_objstub.py: red for product-duplicate (a global symbol defined by 2+ product objects of a root, which also catches an undeclared test source), test-double (a test symbol, any bind, defined by any admitted root's product objects), test-omits-unit, test-not-linked. Fields tests_total, tests_with_doubles, double_symbols. With 0 roots the universe is 0 and the gate is green.
- Selftests: verify/tests/test_doubles.py (22 cases), e2e cases in test_recipe.py (60 PASS), oracle.json entry. Docs: VERDICT, RULES (T1), TOOLS, RECIPE.

## Gate numbers (worktree, individual gates; l1.py verdict is blocked: env)
admit 403/403 files, 9 roots; oracle 48/48; objstub green, universe 0 (no product root yet), tests_total 0; pure exit 0. Selftests gate: 45 of 48 pass; the 3 red: test_envcheck and test_gates (blocked: env, /usr/include tree mismatch, same as l1.py verdict) and test_objstub (universe now counts one product-units item per root; expectations updated 4 -> 5, rerun 0 failed).

## Global measurement on trunk c9765781 (whole ports/fnv-nx, no fix)
Criterion (AST): a python file under any tests/ directory that assigns, augment-assigns or deletes an attribute of an imported project module (module name = a .py file of the tree), calls setattr on it, writes sys.modules (subscript, setdefault, pop, update), or imports/uses unittest.mock or monkeypatch.
- Python test files: 47. With doubles: 8. Replaced names: 9.
  - contract/tests: 12 files, 0.
  - link/tests: 2 files, 0.
  - verify/tests: 33 files, 8 with doubles, 9 names: test_bootmachine.py (bootmachine._plat_section_free), test_bootgpu.py and test_boot_job.py (test_bootmachine.GUEST), test_gate_contract.py (gate_build.ensure), test_gates.py (l1._check), test_runtime.py (runtime.DEFAULT_DIRS, runtime.conf_dirs), test_sandbox.py (sandbox.launch, wraps the real one), test_verdict.py (verdict._git).
  - No mock, monkeypatch or sys.modules use anywhere.
- C/C++ tests: 0 test sources (.c/.cc/.cpp) under tests/. 3 fixture files (guest/hostapi.h, guest/fnvnx/sockwire.h, guest/crt0.s) belong to boot-test guests, not product roots; 0 define product-source symbols. C source embedded as python strings (GUEST) is not counted.
- The python doubles are outside the objstub gate (it judges linked objects). They need their own rule; reported, not fixed.

## Not closed as a class
- An undeclared test source cannot be told from product code by name at admit level (tool roots hold host C++ and crt0.s); closed by the product-duplicate rule instead.
- Weak/COMDAT symbols (templates, inline) shared by test and product count as doubles under the stated rule; a content comparison of COMDAT sections would refine it. Not done.
- A strong double already fails in ld; only weak doubles reach the judge.
