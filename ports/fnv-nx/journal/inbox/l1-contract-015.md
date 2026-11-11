# l1-contract-015: our compiler (g++-13 + fnvnx_order + fnvnx_ftoi) in the recipe

Added to the one build mechanism (verify/recipe.py, unitstage.py); no second path:
- Sources on the trunk: `toolchain/order/fnvnx_order.cc`, `toolchain/ftoi/fnvnx_ftoi.cc` (from claude/port-nsw-un6t9s, sources only; its build.sh and
  compile.sh are not carried). New admit root `ports/fnv-nx/toolchain` (tool).
- Stage `plugin:<name>`: hermetic, host `x86_64-linux-gnu-g++-13` against the plugin headers of the target g++-13, key = content + compiler + flags.
  The `.so` is in GATE_CACHE/store and a declared input `/plug/<name>` of every C++ CC stage (so it is in its key); `-fplugin=` goes only there.
- build.json: `"plugin_cxx"` and `"plugins": [{name, source, args}]` (RECIPE.md "Plugins"). Fixed product values (recipe.py PLUGIN_ARGS):
  order args=rtl object=msvc operator=rtl assign=msvc; ftoi enum=error. `-fplugin*` in `flags` is refused.
- Gate rule: admit.json entry of a root with C++ units declares `cxx_origin`: "native" -> both plugins, exact sources and args, else red;
  `{"lifted_by": TOOL.py, "pack": EXTERNAL}` -> no plugins needed, consistent only if the root has no tracked C++ unit and every C++ unit is an output of a
  step of `lifted_by`. No declaration = red.
- env.json needed no change: x86_64 g++-13, libc6-dev and gcc-13-plugin-dev-aarch64 are already pinned; envcheck OK.
Assumptions: `lifted_by` really is the lifter (declaration reviewed in admit.json); the plugin links no library (-nostdlib, -fno-exceptions);
the old `ORDER_MODE` is gone from the plugin (always TSO), so build.json has no mode.
Not covered: the other plugins of compile.sh (sdd, pi, dispatch, addr, names) are not part of this task and are not in the recipe.

## CC stage mounts no foreign external (item 7)
Rule (recipe.py compile, unitstage.Build.cc_drop): the CC stage (-fpreprocessed, input unit.ii) declares as inputs only the compiler (its external, if it
is one), the plugin `.so` files and unit.ii. Every other external is not mounted there, so the object cannot depend on it by construction. PP is unchanged
(all externals stay inputs). Tests (test_plugins.py): a new commit of an unrelated external re-runs PP, CC runs 0 stages, objects equal; the CC stage
documents hold no `/ext/<name>`; an `asm(".incbin \"/ext/pack/pack.txt\"")` in a C++ unit fails in the CC stage.
Cases where CC legitimately reads more: none found. `.s` units already see only their own file (assemble stage). Inline-asm `.incbin`/`.include` of an
external path in C++/C/.S text was readable in CC before and is now a loud CC error; it was never in the PP closure, so it was untracked by the key anyway.
If a root needs such data in an object, it must come through the preprocessor (a header the PP stage reads) - not decided here.

## Part 1 of the msvcmodel extension (A, B, C, D for order/ftoi)
- A: `toolchain/msvcmodel/*` imported from claude/port-nsw-un6t9s (build.sh, patch, test_msvcmodel.cpp/py).
- B/C: admit.json product roots declare `"target": "guest"|"host"` (required, checked by recipe.check, so by `build`). `verify/guest_compiler.json` pins the
  external compiler of guest roots (name, repo, ref, commit, tree_sha256, at, driver paths); a guest root must use exactly it for C and C++. The file is NOT yet
  in the tree: it needs the commit and tree_sha256 of fnv-nx-build `tools/gcc-ilp32-msvcmodel` (L4). Until then no guest root is valid.
- External compiler mechanism extended (the "tree copy" assumed a self-contained prefix): the assembler, linker and header directories the compiler itself
  names outside its tree (the distribution cross binutils/headers of env.json) become declared inputs of the tool runtime.
- D (order, ftoi): plugin stage reads the plugin headers of the external compiler. Tests: tests/test_guest.py (rule: 8 cases; the compiler cases need
  FNVNX_TEST_GCC_PREFIX = a prefix built by toolchain/msvcmodel/build.sh and print SKIP without it).
Verified with a real prefix (toolchain/msvcmodel/build.sh, GCC 13.3.0 + patch, 1.1 GB, packed as a local external): test_guest.py all passed, 0 skipped:
guest root builds; plugins built against the external's headers, .so equal in two cold stores; warm run 0 stages; size_t mangles `j`; ldar/stlr and _ftol
on it; test_msvcmodel.py passes on it. Times (dominated by ingesting the 1.1 GB tree): cold 107 s, warm 9.7 s.
Open: without FNVNX_TEST_GCC_PREFIX the compiler cases print SKIP and the file exits 0 (the selftests gate has no skip logic, so this is a silent pass).
When guest_compiler.json lands, the test should fetch the pinned external instead and fail without it.
