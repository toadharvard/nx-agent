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

## Warning for the next merger (w17 history)
The branch contains a `-s ours` merge of the old head (cdc0041 is an ancestor), so git counts the ftoi rule as merged although its tree is NOT there. Never re-merge `w17-ftoi-tmp` or cdc0041. The rule (c) is delivered as the patch `journal/inbox/l1-contract-w17-ftoi-rule-c.patch` and is applied by L2 w1 on its fnvnx_ftoi.cc.

## Host-root gates (item 1 of the lead's order)
1. objstub plugin per root compiler: objstub.plugin_so already builds the terminator plugin once per distinct root compiler (key: compiler version, machine, plugin dir). What was missing is the plugin headers of the distro x86-64 compiler of a host root (`gcc-13-plugin-dev`). Rule: envcheck derives them (every file of `gcc -print-file-name=plugin` is read by the verdict, so its package is in env.json: `gcc-13-plugin-dev 13.3.0-6ubuntu2~24.04.1`, snapshot regenerated) and `envcheck check` refuses an admitted root whose distro compiler's plugin headers are in no listed package. Tests: test_envcheck (root-compiler rule, mismatch case), test_objstub (x86-64 plugin builds and sees a noreturn declaration; plugin key differs from the aarch64 compiler's).
2. linkstrict rule (d), host boundary: an undefined name of an object of a `"target": "host"` root is certified iff it is exported (`.dynsym`, defined GLOBAL/WEAK) by a shared object that dpkg lists for libc6, libgcc-s1, libstdc++6 (versions pinned by env.json); `gate_linkstrict.host_exports`, cached by file content. Guest roots have no such set. Stated limits: it is a cached read of the installed packages (not a sandbox stage), names only (no symbol versions), and the `link.roots` key of the lead's wording does not exist in recipe.py yet, so cross-root definitions go through the existing rule (b) (every product root's objects). Tests: test_linkstrict 60/60 (judge-level cases and two gate-level cases on a host-target toy root).
