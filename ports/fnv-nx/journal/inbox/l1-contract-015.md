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
