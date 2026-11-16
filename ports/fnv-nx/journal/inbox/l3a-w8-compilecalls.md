# l3a-w8: census of compiler, assembler and linker calls (commit 3f875c252, int3 tree)

## Method
Mechanical: every git-tracked `.py`, `.sh`, `.json`, `Makefile` under ports/fnv-nx/ was scanned for string literals naming a tool (g++, gcc, cc, clang,
as, ld, ar, nm, objcopy, objdump, cpp, with the aarch64-* / x86_64-* prefixes), `cc('g..')`, `$(CC)` and `$(CXX)`, outside comment lines.
Result: 372 matching lines in 97 files. Totals: files 97, sites 372.

## Caveat (over-count)
The match is on names, not on executed calls. It also hits name lists in json and specs (render/d3d9/spec/d3d9_spec.json 19, verify/env.json 22,
the declared compiler in build.json files), mnemonics that look like tool names (`ld`, `cc`, `as` as instruction or opcode names), and non-compile
tools (nm, ar). 372 is an upper bound, not the number of invocations.

## Classes
invocation = the file runs a compiler, assembler or linker itself. name-only = the tool name is data (a declared compiler, a list, a mnemonic).
machinery = the prepared build (verify/recipe.py and its stages), which is the one allowed caller.

## Per-file counts and owner (all 97 files)
- L1 machinery (verify/, contract/, link/): recipe.py 19, unitstage.py 7, envcheck.py 7, toolflags_policy.py 12, objstub.py 5, gate_objstub.py 3,
  guest_flags.py 3, toolflags.py 2, producers.py 2, icount.py 1, bootgpu.py 1, env.json 22, guest_compiler.json 1, samples/platform.build.json 1;
  verify/tests: test_toolflags 61, test_objstub 12, test_linkstrict 10, test_guest 8, test_plugins 8, test_link 6, test_recipe 5, test_bootmachine 4,
  test_envcheck 4, test_guest_flags 4, test_producers 3, test_sandbox 2, test_coffnorm 2, 11 files with 1 each; contract/ 5 tools (bind, l2sections 2,
  pcrel_abs, proto 2, verdict) and 4 tests (test_bind 2, test_gate_reach 3, test_l2sections 2, test_proto 3); link/ (a64order, ldcheck 4, unitpairs)
  and link/tests (test_gate_abs 3, test_gate_addr 5, test_gate_order 4, test_odr 1, test_order_cont 2, realobj 1).
  Not classified by me: L1 owns the split between invocation and name-only.
- L5 platform: platform/manifest/{env_const_export 1, fontgate 2, import_escape 4, platform_faces 2}, platform/build.json, hostabi/build.json, shim/build.json (1 each).
- L6 render: render/d3d9/front/gen_enums.py 1, render/total/gate_d3d9total.py 1, render/shaders/translator/build.json 1, render/d3d9/spec/d3d9_spec.json 19.
- L2 toolchain/rules: rules/_msvcsig.py 2, toolchain/msvcmodel/{build.sh 1, test_msvcmodel.py 1}, toolchain/tests/{plugbuild.py 2, test_cc1.py 1,
  test_exitgen_defdisp.py 1, test_ftoi.py 1}.
- L3b w3 (left alone): lift/tools/tests/{instest.py 9, instest_extra.py 1, sehcodes.py 4, citest.py 5}, lift/test/x87test.py 2.
- L3a (this file's classification, checked line by line):
  - invocation (host or stock compiler run by the file):
    test_descclass.py 5 and test/descclasstest.py 5 (duplicate pair), test/defdisptest.py 6, tools/tests/test_ranges.py 3,
    tools/tests/test_lock_only.py 1, tools/tests/fmathtest.py 2, test/rothost.py 1, test/bthost.py 1, test/rcptest.py 1, tools/rcpgen.py 1,
    proof/backends.py 1.
  - name-only: gates/gate_order.py 1 (instruction mnemonic tables), gates/tests/test_gate_lift.py 2 (declared compiler names in a recipe fixture),
    gates/tests/test_gate_order.py 1, proof/tests/test_rtroot.py 1 (compares the recipe's declared compiler), proof/tests/test_shape.py 1 (opcode bytes),
    tools/ehflow.py 8 (value-kind tags), pack/build.json 1 and rt/build.json 1 (declared compiler path).
  - machinery-adjacent: tools/liftgen.py 2: `resolved_flags(driver='aarch64-linux-gnu-g++')` asks recipe.semantics_for for the flag list; it names the stock
    driver as an argument but runs no compiler. Its callers (test_ranges, test_lock_only, instest) are the invocations.

## L3a rules and order (from the lead)
1. test_descclass.py and descclasstest.py, defdisptest.py: onto admitted roots, then drop the duplicate.
2. test_ranges.py and test_lock_only.py: read the objects of a recipe-built admitted root.
3. x86-64 host programs on the reference CPU (rcptest, rothost, bthost, fmathtest, rcpgen): host-variant recipe roots for the reference host, with the
   compiler pinned in env.json for that host. Waits for L1 to confirm that variant exists.
4. proof/backends.py: sanitizer-variant host root (UBSan variant of RECIPE.md).
rcpgen.py rewrites a tracked region of rt/rcptab.c: that becomes a declared output of its root, rcptab.c includes it, the tracked region is deleted.

## Removed so far
lift/tools/tests/test_exitbridge.py, test_disp.py, test_imp.py, test_msvcrt.py (3f875c252).
