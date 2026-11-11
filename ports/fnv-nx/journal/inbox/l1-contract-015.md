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

## Part 2: the L2 plugins (addr, dispatch, names, sdd, pi) + layout
- Sources imported (the .cc only; their build.sh and test_*.py use the old compile path and are not carried) from claude/fnv-l2-source: order, ftoi, pi, layout
  are NEWER there than on port-nsw-un6t9s (order +242/-72, ftoi, pi +200, layout exists only there); addr, dispatch, names, sdd are identical on both.
  msvcmodel/build.sh also from l2-source (reference compiler lookup).
- l2-source's fnvnx_ftoi has no `enum` argument any more (it accepts only `verbose`), so ftoi args are {} now.
- Plugin set and load order = compile.sh of l2-source: ftoi, order, sdd, pi, dispatch, addr, names, layout (recipe.py PLUGIN_SOURCES, in that order; a native
  root must list exactly these, in this order, with PLUGIN_ARGS; only order has arguments: rtl/msvc/rtl/msvc). `layout` includes names' .cc: new optional plugin
  key "inputs" (tracked files mounted at their repo paths; PLUGIN_INPUTS pins them).
- Native roots must also carry the semantics flags in flags.cxx (recipe.py SEMANTICS_FLAGS, from compile.sh: -mabi=ilp32 -fms-extensions ... -DFNVNX_SDD=1
  -fno-devirtualize); missing = red.
- NOT expressible in the recipe: pi's per-unit `-fplugin-arg-fnvnx_pi-fpfast` (compile.sh derives it per unit from units.json /fp:fast); a recipe has flags per
  language only. Needs a rule from the owner of N3 (per-unit flags in the recipe?).
- Tests (test_plugins.py): all 8 plugins build, load, in order; sections .fnvnx.n3/.sympair/.layout/.names appear; indirect call goes through fnvnx_dispatch.
  Not checked: the behaviour of addr, sdd, and the contents of the sections (L2's own tests need build.sh; they are not run here).
- vtnames (part 3): clang plugin, not a GCC plugin: it is a separate clang run per unit that writes facts files (.vt/.sym/.mem), and its command line comes from
  tools/link/layoutgate.py msvc_flags. Building it needs the clang headers (libclang-18-dev, /usr/lib/llvm-18/include/clang/AST/Mangle.h), which env.json does
  not pin and this container lacks. To be pinned.

## Mirror pin (a8a2ed5)
verify/guest_compiler.json: name gcc-msvcmodel, ref tools/gcc-ilp32-msvcmodel, commit b4057704c0babd012e466cc89181132e02542289,
tree_sha256 b60eafe7a5b0ae3dc679f9bf21e20d3363e3bac2af7379e34127ea5cf8455116, at /ext/gcc, cc/cxx bin/aarch64-linux-gnu-{gcc,g++}.
Provenance in the json: gcc-13.3.0.tar.xz sha256 0845e962..c083, msvcmodel.patch sha256 08c9569a..59d3, src_commit 7ac84bda; dpkg versions stay in env.json.
Fresh checkout of the mirror (no local prefix): guest build, 8 plugins, test_msvcmodel.py all pass with NO changes to the mirror; the 3 empty
dirs / include symlink were not needed (the compiler's own include search list is declared as inputs). Cold 19-21 s (store empty), warm 1.7 s.
test_guest fetches by the pin and is red with the reason when the fetch or tree hash fails; FNVNX_TEST_GCC_PREFIX removed. Oracle entries added.

## Backlog B-D (lead messages of 22:08-22:09Z)
- (A) a8a2ed5: trunk 7de7469 merged, oracle entries for test_plugins/test_guest, verdict green.
- (B) 168006e: env.json + libclang-18-dev, libclang-common-18-dev, libclang1-18 (1:18.1.3-1ubuntu1), libc6-dev-i386 2.39-0ubuntu8.9; envcheck derives them by rule
  (clang API headers + libs, `clang -m32 -M` of stdint/stdlib). No deb sha256 field exists in env.json (versions pinned by apt index).
- (C) bc406ec: gate argordergate (gate_argorder.py): certifies fnvnx_order (not fnvnx_pi) in the rule's plugin list/order; test_argorder.py.
- (D) a626984: unit_flags/unit_facts (fpfast of pi iff the unit's /fp:fast fact in units.json), test_unitflags.py. Needs a units.json in the root (source = root-relative unit path).
- The lead session (01KmtZ...) was inactive when I tried to report (D); heads are in this note. A verdict run was red once only because /tmp/objstub-* filled the disk.
- Open: (E) facts stage / vtnames.

## (E) facts stage / vtnames: NOT started, blocked on inputs (reported, nothing invented)
- env.json now pins clang-18 + libclang-18-dev (B), so the plugin (toolchain/vtnames/fnvnx_vtnames.cc, only on claude/port-nsw-un6t9s) can be built as a hermetic stage.
- The per-unit MSVC-ABI command line must come by rule from units.json; in the L1 tree there is no units.json of a root and no source for the clang
  flags except tools/link/layoutgate.py msvc_flags, which reads private shim headers (shim/include, layoutgate_msvc/*.h) that are not in this tree.
  A facts stage written now would hand-copy those flags. Needs from the orchestrator: where units.json and those headers enter a root (external or tracked), and the rule for the flags.
- The orchestrator session (01KmtZKUSQnbJKPPsvtTQXf7) was inactive, so this could not be asked.
