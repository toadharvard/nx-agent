# l6-render-w2: render/d3d9 as a product root of the one build

Branch claude/fnv-l6-render-w2. Measured at commit 5f27ad66 (+ this note commit). Measurement base: verify/, toolchain/, farm/, contract/, link/ and CLAUDE.md taken unchanged from origin/claude/fnv-nx-clean a2453fe9 (recipe.py not edited). Environment: farm/setup_verdict_env.sh (pip step of unicorn was very slow, the apt set was complete first); compiler external gcc-msvcmodel b4057704, all 8 plugins in the order recipe.py requires (the task text named 2; recipe.py check demands all 8 and the semantics flags for a native root, so build.json carries them).

## Result
- `recipe.py check --root ports/fnv-nx/render/d3d9`: "recipe valid" (with render/backend and render/shaders/include admitted locally, see blockers).
- Units: 70 = 69 compile + 1 fails. Unit set equals the old build_guest.sh set exactly (diff both ways empty: 58 dev/*.cpp + 4 generated .cpp + 6 front/*.c + 2 ffp/*.c, recipe.units_of vs the script job list). build_guest.sh deleted.
- FAILS (1): dev/ffp_prog.cpp, first error `fatal error: ../gen/ffp_glsl.gen.h: No such file or directory` (ffp_dksh.gen.h likewise). Cause: the gen_glsl.py step is not in the recipe (it needs the uam binary). Blocker: needs recipe stage: host tool run (owner L1) / lead decision on the ffp program ABI (see report to the lead).
- Because the recipe stops at the first red unit, the committed root is red (151 stages run, 0 objects.json). Determinism / warm figures were measured on a scratch copy without dev/ffp_prog.cpp (not committed):
  - cold build 1: green, 69 objects, stages run 151 hit 0, 30 s (j default of the machine).
  - cold build 2 (fresh store): identical, 69 object sha256 equal, plugin .so sha256 equal.
  - warm rebuild (store of build 1): stages run 0 hit 151, 2 s.
- Generators are recipe `generated` steps (python, sandboxed): gen_impl, state_model, res_smgen (inputs sm_table.json, sm_illegal.tsv), gen_bridge, gen_env (input shim/include/d3d9types.h). All outputs exactly listed. Edits needed for the sandbox: res_smgen creates its out dir; envq.py moved into render/d3d9/env (the product generator no longer imports test code; it finds the shim header under /in when run in /tool); dev/draw_core.h includes fnvnx_dk_layout.h through the include dir render/shaders/include.
- Rule applied for the include closure (unitstage assumes GCC reads a file only by lexing it; `#pragma once` identity checks read files by a second path): the root is never listed as its own include dir; every cross-directory quoted include inside the root is relative ("../dev/x.h", "../gen/x.gen.h"), 54 sites + the 3 generator templates. Without this the CC step failed (state_rt.h through /src and /inc/N).

## Moved files (git mv, content unchanged unless stated)
| from | to |
|---|---|
| render/d3d9/backend/nx/** | render/backend/nx/** |
| render/d3d9/backend/nxgpu4.h, nxgpu4_check.{c,h}, nxgpu4_shader.h | render/backend/ |
| render/d3d9/backend/{gl4_*.c, gl4.h, gl4_funcs.h, gl4_xlator.sh, build_gl4.sh, libnxgl4.map, NXGL4_HOST.md} | render/gl4/ (build_gl4.sh: include/source paths of nxgpu4 fixed) |
| render/d3d9/backend/selftest/** | render/gl4/selftest/ |
| render/d3d9/dev/app_unk_native.cpp | tests/l6/native/ |
| render/d3d9/ffp/ffp_{ps,vs}_ref.{c,h} | tests/l6/ffpdev/ref/ |
| render/d3d9/rec/** | render/tools/rec/ |
| render/d3d9/cmp/** | render/tools/cmp/ |
| render/d3d9/gen/d9stream.{cpp,h}, d9wrap_port.h | render/tools/replay/ |
| tests/l6/env/envq.py | render/d3d9/env/envq.py |
Users' paths fixed in tests/l6/*, render/total/gate_d3d9total.py, run_real.sh, build_gl4.sh. Not done: render/backend/build.json was NOT created (L6A admits render/backend); render/gl4 NOT admitted. GL backend: placement per the lead's order (render/gl4, own root later).

## Known breakage left for the owners
- Tests that call render/d3d9/build_guest.sh (copy, draw, ffpdev, m1, pvw, x1: guest symbol audits) have no script any more: they must drive recipe.py (owner L6 tests; part of the "no doubles" rewrite).
- Tests that generate into a temp dir and compile gen/*.cpp there: the generated includes are now "../dev/x.h"; the temp layout needs dev/ and front/ siblings of gen/ (not changed here).
- verify/bootgpu.py and verify/TOOLS.md still name render/d3d9/backend (L1/L4's files, not edited).
- admit.json (revised after the lead's order): I added only `ports/fnv-nx/render/d3d9` (product, guest, cxx_origin native, recipe build.json, the gate list of the L6A backend entry) and two tool roots `ports/fnv-nx/shim` and `ports/fnv-nx/render/shaders/include` (only to make `include` valid; class tool/selftests is a placeholder choice: L1 decides the class of the shim headers). `include` also names ports/fnv-nx/render/backend, which is valid only after L6A admits it; until then `recipe.py check` says "include ... is not a path inside an admitted root". Blocker owner: L6A (admit), L1 (shim class).
- Flags: kept the L6 build_guest flags + the recipe's semantics flags; -mtp=soft / -D__SWITCH__ of the L6A recipe are not in this one (decide with L5/L6A).

## Revision (lead's decisions)
- The admit entries for shim and render/shaders/include are reverted. `include` still names ports/fnv-nx/shim/include, ports/fnv-nx/render/shaders/include and ports/fnv-nx/render/backend: three include dirs outside an admitted root. Owners: L1 (shim), w1b (render/shaders/translator), L6A (render/backend). The root stays red on `recipe.py check` ("include ... is not a path inside an admitted root") until they are admitted.
- Tests left broken, global list at this commit (not run; found by static reference): of 38 test_*.py under tests/l6, 16 use the old paths or generate into a temp dir with the new relative includes (the generated headers now say "../dev/x.h" and need dev/ front/ next to gen/): bridge, copy, dev, devlife, draw, drawgl, env, exports, ffpdev, m1, oom, present, pvw, res, tex, x1. Of them 6 also call the deleted build_guest.sh: copy, draw, ffpdev, m1, pvw, x1 (oom only cites it in a comment, x1 calls it).
- Path-only fixes done for cmp, wrap, recjob, conf/run.py, rec/build.sh (moved tools); not run.
