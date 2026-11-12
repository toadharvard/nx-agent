# l1-contract-w17-001: gate `datamodel` (class A) written; `fpexact` (class C, native part) blocked on a decision

Branch `claude/fnv-l1-contract-w17`.

## Gate datamodel (verify/gate_datamodel.py, in gates.json, tests/test_datamodel.py, TOOLS.md, VERDICT.md row A)
Rule. Universe through `gate_build.quantify`: per admitted guest product root, one item `<root>:compiler` plus one per unit of the build; no guest root: universe 0, green.
Everything is read from the build's own outputs: objects.json `stage_keys`, then the stage documents in `GATE_CACHE/store/stage-docs` whose stage is `cc:<unit>`.
- compiler item: build green, `recipe.guest_errors` (the root's compiler is the pinned external: name, repo, ref, commit, tree_sha256, at, drivers), and every CC document mounts the pin's `tree_sha256` at the pin's `at` and starts with a driver inside it.
- unit item: `-mabi=ilp32` in the CC argv; for a C++ unit of a `cxx_origin: native` root also every flag of `recipe.SEMANTICS_FLAGS` and, for each plugin of the new constant `recipe.GUEST_CXX_PLUGINS` (fnvnx_ftoi, fnvnx_order; the one shared list, a subset of PLUGIN_SOURCES), the declared input `/plug/<name>` and the argv flag `-fplugin=/plug/<name>/<name>.so`.
- A lifted root's C++ needs no plugin (RECIPE.md "Gate rule": the lifter emits the order itself). The pin declares no data-model flags, so the flag set is the recipe's own constant; C and assembler units are checked for `-mabi=ilp32` only.
Tests (tests/test_datamodel.py, real pinned msvcmodel external, no doubles): no guest root green; stock g++ root red; wrong commit of the external red; unit missing a plugin red (pure check on a CC document); unit without `-mabi=ilp32` red; no CC document red; good root green 3/3 (compiler + 2 units).

## Gate fpexact: not written, decision needed (sent to the lead)
The native criterion "no float-to-int call other than the plugin's emitted ones" has no decidable definition from the build outputs: `fnvnx_ftoi` emits no calls (inline range check + bounded fcvtzs), writes no per-unit result (only a `verbose` inform, which PLUGIN_ARGS does not set), and leaves integers wider than 64 bits unrewritten (a libgcc `__fixdfti` stays possible). Options sent: (a) load-only criterion, (b) plugin writes a per-unit count, (c) plugin errors on any FIX_TRUNC_EXPR it did not rewrite (recommended). The rcp/rsqrt table (L1) and the lifted part (L3a) are not covered by any gate yet. REQUIRED_PRODUCT still names `fpexact`, so no product root can be admitted until it exists.

## Numbers (commit of the verdict run: 05400e1, before the test_gates.py fix in the next commit)
Verdict at c5b20de: 24 gates, 24 green, 191.2 s (the earlier red, selftests test_gates.py, was the missing `datamodel` entry; fixed). Tests: test_datamodel.py 9/9, test_gates.py 0 failed.
