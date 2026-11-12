# l1-contract-w17-001: gate `datamodel` (class A) written; `fpexact` (class C, native part) blocked on a decision

Branch `claude/fnv-l1-contract-w17`.

## Gate datamodel (verify/gate_datamodel.py, in gates.json, tests/test_datamodel.py, TOOLS.md, VERDICT.md row A)
Rule. Universe through `gate_build.quantify`: per admitted guest product root, one item `<root>:compiler` plus one per unit of the build; no guest root: universe 0, green.
Everything is read from the build's own outputs: objects.json `stage_keys`, then the stage documents in `GATE_CACHE/store/stage-docs` whose stage is `cc:<unit>`.
- compiler item: build green, `recipe.guest_errors` (the root's compiler is the pinned external: name, repo, ref, commit, tree_sha256, at, drivers), and every CC document mounts the pin's `tree_sha256` at the pin's `at` and starts with a driver inside it.
- unit item: `-mabi=ilp32` in the CC argv; for a C++ unit of a `cxx_origin: native` root also every flag of `recipe.SEMANTICS_FLAGS` and, for each plugin of the new constant `recipe.GUEST_CXX_PLUGINS` (fnvnx_ftoi, fnvnx_order; the one shared list, a subset of PLUGIN_SOURCES), the declared input `/plug/<name>` and the argv flag `-fplugin=/plug/<name>/<name>.so`.
- A lifted root's C++ needs no plugin (RECIPE.md "Gate rule": the lifter emits the order itself). The pin declares no data-model flags, so the flag set is the recipe's own constant; C and assembler units are checked for `-mabi=ilp32` only.
Tests (tests/test_datamodel.py, real pinned msvcmodel external, no doubles): no guest root green; stock g++ root red; wrong commit of the external red; unit missing a plugin red (pure check on a CC document); unit without `-mabi=ilp32` red; no CC document red; good root green 3/3 (compiler + 2 units).

## For L3b (fpexact, native part): the argument, per the lead's decision (c)
`fnvnx_ftoi` (toolchain/ftoi/fnvnx_ftoi.cc) now rewrites every FIX_TRUNC_EXPR of a function or reports an `error_at` naming the source and target types, for every shape it does not cover: a vector or other non-integer target, a floating source wider than 64 bits, an integer target wider than 64 bits (the enum case already was an error). The pass is registered unconditionally at `plugin_init` and runs on every function after the CFG; a failed init or an error fails the CC stage.
Therefore: if a unit's CC stage document holds the declared input `/plug/fnvnx_ftoi` and `-fplugin=/plug/fnvnx_ftoi/fnvnx_ftoi.so` (the `datamodel` gate already checks exactly this for every native C++ unit, `gate_datamodel.unit_why`) and the stage succeeded, no unbounded float-to-int conversion written in that unit's source reaches its object. The criterion needs no object scan and no name list.
Stated limits, not covered by this argument (for L3b to take or to assign):
- FIX_TRUNC_EXPR that GCC creates after the pass (the pass runs once, right after the CFG; later folding such as a rounding builtin turned into a conversion). Not shown absent by the plugin. Needs either a second check pass late in the pipeline (L2, plugin) or an object-level rule.
- Library conversions (lrint, lround, ...) are calls, not conversions; their semantics are the library's.
- Constant-folded conversions (static initialisers) are compile-time values, not run-time.
- Lifted code (L3a) and the rcp/rsqrt table (L1 reference-CPU table) have no gate yet.
Test: tests/test_plugins.py (40/40): double -> __int128 fails the build with an FTOI error naming both types; the ordinary conversions of the same plugin still give the bounded form (0x80000000 case).
Not done: "flags" in guest_compiler.json (pin file is the lead's/w4's; the gate reads recipe.SEMANTICS_FLAGS).

## Numbers (commit of the verdict run: 05400e1, before the test_gates.py fix in the next commit)
Verdict at the head below (after the ftoi change): 24 gates, 24 green, 236.5 s. Tests: test_datamodel.py 9/9, test_plugins.py 40/40, test_gates.py 0 failed.
