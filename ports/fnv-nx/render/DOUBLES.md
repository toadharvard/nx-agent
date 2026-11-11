Census of test doubles at commit 060978015eef7078936a730352a3d284e6304d8d (claude/fnv-l6-render), partial: cut short by the lead handoff.

Method: every driver run once (`--no-mutations`) with logging compiler wrappers (host gcc/g++, FNVNX_TC, FNVNX_DK). Per compile, strong defined globals
(nm T D B R C G S; W/V/u excluded; `__x86.get_pc_thunk.*` excluded) were recorded; per link the inputs were resolved to their sources.
A unit is product if it is a build_guest.sh job file (or an identical copy, or a generated .cpp with a build_guest gen basename), a
libfnvnx_shaders source, backend/nx/dk_*.c or nxgpu4_check.c; gl4_*.c/libnxgl4.so are the GL4 class; everything else is test-side.
D = defs(test-side) ∩ (P ∪ B), matched by mangled name or by demangled name without the parameter list (ILP32 vs LP64 mangling differ).
P = d3d9 job list (host -m32 and aarch64), shaders lib, mojoshader/uam, dk_*.c, nxgpu4_check.c, declared nxdk_* handlers. B = libnx.a, libdeko3d.a,
the 12 platform/L1/L5 hooks, nxdk_guest_ptr/on_present. nxgl4_guest_ptr/on_present defined test-side mark GL4-MODEL. No allow-list.

## Render tests (non-L6A)

| test | binary/build cmd (short) | class | test-side files defining product/boundary symbols | doubled symbols | product files left out |
|---|---|---|---|---|---|
| render/total/tests/test_d3d9total.py | - | not measured | - | - | - |
| tests/l6/bridge/test_bridge.py | g++ unresolved=ignore -> t_appunk; g++ unresolved=ignore -> t_bridge | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/bridge/t_appunk.cpp (1); tests/l6/bridge/t_bridge.cpp (2) | 6 | d3d9: 63 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/cmp/test_cmp.py | g++ -> exe_drv; g++ -> exe_rp; g++ -> exe_rpm | CLEAN | - | 0 | - |
| tests/l6/const/test_const.py | g++ unresolved=ignore -> t_const | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/m1/t_env.cpp (17) | 20 | d3d9: 13 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/copy/test_copy.py | g++ unresolved=ignore -> test_copy; gcc -> drv; gcc -> server | DOUBLE-SIBLING +BOUNDARY/GL4 | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/copy/server.c (2); tests/l6/copy/t_rpc.cpp (6) gl4 units: 17 | 11 | d3d9: 38 of 70 left out; dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so); linked with --unresolved-symbols=ignore-all |
| tests/l6/dev/test_dev.py | g++ unresolved=ignore -> test_dev | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/dev/t_factory.cpp (3) | 6 | d3d9: 59 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/devlife/test_devlife.py | g++ unresolved=ignore -> test_devlife | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/devlife/t_env.cpp (17) | 20 | d3d9: 13 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/draw/test_draw.py | g++ unresolved=ignore -> test_draw | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/draw/t_rec.cpp (6) | 9 | d3d9: 34 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/drawgl/test_drawgl.py | g++ unresolved=ignore -> test_drawgl; gcc -> server | DOUBLE-SIBLING +BOUNDARY/GL4 | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/copy/server.c (2); tests/l6/drawgl/t_rpc.cpp (4) gl4 units: 17 | 9 | d3d9: 22 of 70 left out; dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so); linked with --unresolved-symbols=ignore-all |
| tests/l6/env/test_env.py | g++ unresolved=ignore -> host.exe; g++ unresolved=ignore -> test_dev | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/dev/t_factory.cpp (3); tests/l6/env/hostenv.cpp (1) | 7 | d3d9: 57 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/exports/test_exports.py | g++ -> t_exports | DOUBLE-SIBLING +BOUNDARY | tests/l6/exports/t_exports.cpp (4) | 4 | d3d9: 69 of 70 left out |
| tests/l6/ffpdev/test_ffpdev.py | g++ unresolved=ignore -> test_ffpdev; gcc -> server | DOUBLE-SIBLING +BOUNDARY/GL4 | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/copy/server.c (2); tests/l6/ffpdev/t_rpc.cpp (4) gl4 units: 17 | 9 | d3d9: 22 of 70 left out; dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so); linked with --unresolved-symbols=ignore-all |
| tests/l6/front/test_front.py | g++ -> probe; gcc -shared -> front.so; gcc -shared -> k.so | CLEAN | - | 0 | d3d9: 64 of 70 left out |
| tests/l6/gl4/test_gl4.py | gcc -> t_gl4; gcc -shared -> libnxgl4.so; gcc -shared -> preload.so | DOUBLE-BOUNDARY (GL4-MODEL only) | tests/l6/gl4/t_main.c (2); tests/l6/gl4/t_preload.c (2) gl4 units: 17 | 2 | dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so) |
| tests/l6/m1/test_m1.py | g++ unresolved=ignore -> test_m1 | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/m1/t_env.cpp (17) | 20 | d3d9: 13 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/nxcheck/test_nxcheck.py | gcc -> drv | DOUBLE-SIBLING | /tmp/tmphkfmmj80/driver.c (1) | 1 | dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out |
| tests/l6/oom/test_oom.py | g++ unresolved=ignore -> t_oom | DOUBLE-SIBLING +BOUNDARY | /tmp/oom_q2cd3ipj/obj_x.cpp (30); render/d3d9/dev/app_unk_native.cpp (3); tests/l6/oom/t_oom.cpp (14) | 47 | d3d9: 11 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/present/test_present.py | g++ unresolved=ignore -> test_present; gcc -> server | DOUBLE-SIBLING +BOUNDARY/GL4 | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/copy/t_rpc.cpp (3); tests/l6/present/server.c (2); tests/l6/present/t_env.cpp (6) gl4 units: 17 | 14 | d3d9: 8 of 70 left out; dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so); linked with --unresolved-symbols=ignore-all |
| tests/l6/pvw/test_pvw.py | g++ unresolved=ignore -> t_oom; g++ unresolved=ignore -> test_pvw | DOUBLE-SIBLING +BOUNDARY | /tmp/oom_ugqroivn/obj_x.cpp (30); render/d3d9/dev/app_unk_native.cpp (3); tests/l6/oom/t_oom.cpp (14); tests/l6/pvw/t_env.cpp (17) | 60 | d3d9: 7 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/rec/test_rec.py | recorder d3d9.dll + tools/compare nxd9play/d9cap (mingw) | DOUBLE-SIBLING | render/d3d9/rec (d3d9.dll proxy exports, by reading d9rec.def) | 11 (+C++ of d9stream not measured) | all 70 d3d9 job files (Wine's d3d9 replaces them) |
| tests/l6/recjob/test_recjob.py | recorder d3d9.dll + nxd9play (mingw), via test_rec | DOUBLE-SIBLING | render/d3d9/rec (d3d9.dll proxy exports, by reading d9rec.def) | 11 (+C++ of d9stream not measured) | all 70 d3d9 job files (Wine's d3d9 replaces them) |
| tests/l6/reclock/test_reclock.py | recorder d3d9.dll + lockprobe.exe (mingw) | DOUBLE-SIBLING | render/d3d9/rec (d3d9.dll proxy exports, by reading d9rec.def) | 11 (+C++ of d9stream not measured) | all 70 d3d9 job files (Wine's d3d9 replaces them) |
| tests/l6/res/test_res.py | g++ unresolved=ignore -> test_res | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/res/t_env.cpp (10) | 13 | d3d9: 53 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/shcache/test_shcache.py | gcc -> t_sh32; gcc -> t_sh64 | CLEAN | - | 0 | - |
| tests/l6/sm_fix/test_al.py | - | not measured | - | - | - |
| tests/l6/sm_fix/test_layout.py | - | not measured | - | - | - |
| tests/l6/sm_fix/test_ps1x.py | - | not measured | - | - | - |
| tests/l6/sm_fix/test_sink.py | - | not measured | - | - | - |
| tests/l6/sm_fix/test_sm_ops.py | - | not measured | - | - | - |
| tests/l6/sm_link/test_sm_link.py | - | not measured | - | - | - |
| tests/l6/test_sm_total.py | - | not measured | - | - | - |
| tests/l6/test_spec.py | g++ -> p.s | PYTHON-ONLY | - | 0 | - |
| tests/l6/tex/test_tex.py | g++ unresolved=ignore -> test_tex; gcc -> drv | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/tex/t_env.cpp (11) | 14 | d3d9: 54 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/wrap/test_wrap.py | g++ -> exe_base; g++ -> exe_mut_handle; g++ -> exe_mut_input; g++ -> exe_mut_output | CLEAN | - | 0 | - |
| tests/l6/x1/test_x1.py | g++ -> exe_base; g++ -> exe_drv; g++ -> exe_mut_handle; g++ -> exe_mut_input; g++ -> exe_mut_output; g++ -> exe_rp; g++ -> exe_rpm; g++ -> p.s; g++ -> t_exports | DOUBLE-SIBLING +BOUNDARY | render/d3d9/dev/app_unk_native.cpp (3); tests/l6/bridge/t_appunk.cpp (1); tests/l6/bridge/t_bridge.cpp (2); tests/l6/exports/t_exports.cpp (4); tests/l6/x1/t_x1.cpp (4) | 10 | d3d9: 62 of 70 left out; linked with --unresolved-symbols=ignore-all |
| tests/l6/conf/run.py | Wine conformance exes + recorder d3d9.dll (mingw, render/d3d9/rec) | DOUBLE-SIBLING | render/d3d9/rec (d3d9.dll proxy exports, by reading d9rec.def) | 11 (+C++ of d9stream not measured) | all 70 d3d9 job files (Wine's d3d9 replaces them) |

Totals (Render tests (non-L6A)): tests 36; CLEAN 4; DOUBLE-SIBLING 22; DOUBLE-BOUNDARY 1 (of which GL4-MODEL only 1); PYTHON-ONLY 1; not measured 8; tests using GL4 5. Doubled files 26; doubled symbols unique 88, summed per test 326.

## L6A-owned tests (dkcore, dkfmt, dkstate, selftest)

| test | binary/build cmd (short) | class | test-side files defining product/boundary symbols | doubled symbols | product files left out |
|---|---|---|---|---|---|
| tests/l6/dkcore/test_dkcore.py | gcc -> host_base | DOUBLE-SIBLING +BOUNDARY | tests/l6/dkcore/host_main.c (51) | 51 | dk: dk_fmt.c, dk_state.c, nxgpu4_check.c left out |
| tests/l6/dkfmt/test_dkfmt.py | g++ -> tr; gcc -> dkh; gcc -> glh; gcc -> probe | DOUBLE-BOUNDARY (GL4-MODEL only) | - gl4 units: 1 | 0 | d3d9: 69 of 70 left out; dk: dk_core.c, dk_state.c, dk_swap.c, nxgpu4_check.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so) |
| tests/l6/dkstate/test_dkstate.py | gcc -> h | CLEAN | - | 0 | dk: dk_core.c, dk_fmt.c, dk_swap.c, nxgpu4_check.c left out |
| tests/l6/selftest/test_selftest.py | gcc -> h; gcc -shared -> libnxgl4.so | DOUBLE-BOUNDARY (GL4-MODEL only) | tests/l6/selftest/harness.c (2) gl4 units: 17 | 2 | dk: dk_core.c, dk_fmt.c, dk_state.c, dk_swap.c left out; deko3d backend replaced by GL4 (gl4_*.c / libnxgl4.so) |

Totals (L6A-owned tests (dkcore, dkfmt, dkstate, selftest)): tests 4; CLEAN 1; DOUBLE-SIBLING 1; DOUBLE-BOUNDARY 2 (of which GL4-MODEL only 2); PYTHON-ONLY 0; not measured 0; tests using GL4 2. Doubled files 2; doubled symbols unique 53, summed per test 53.

## Path to 0

- render/total/tests/test_d3d9total.py: not measured (handoff cut the run)
- tests/l6/bridge/test_bridge.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/const/test_const.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_present.cpp; render/d3d9/dev/glue_query.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/copy/test_copy.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
  - note tests/l6/copy/test_copy.py: compile-only (not linked) copies of product files defining product symbols: 18 files, not counted
- tests/l6/dev/test_dev.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/res_fvf.cpp; render/d3d9/dev/res_surf.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/devlife/test_devlife.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_present.cpp; render/d3d9/dev/glue_query.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/draw/test_draw.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp; render/d3d9/dev/ffp_prog.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/drawgl/test_drawgl.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
- tests/l6/env/test_env.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/res_fvf.cpp; render/d3d9/dev/res_surf.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/exports/test_exports.py: rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/d3d9_adapter.cpp; render/d3d9/dev/x1_validator.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/ffpdev/test_ffpdev.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
- tests/l6/gl4/test_gl4.py: needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
- tests/l6/m1/test_m1.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_present.cpp; render/d3d9/dev/glue_query.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
  - note tests/l6/m1/test_m1.py: compile-only (not linked) copies of product files defining product symbols: 1 files, not counted
- tests/l6/nxcheck/test_nxcheck.py: rewrite onto the real product files owning the doubled symbols (render/d3d9/backend/nx/dk_core.c)
- tests/l6/oom/test_oom.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/ffp_prog.cpp; render/d3d9/dev/obj.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/present/test_present.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
- tests/l6/pvw/test_pvw.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/ffp_prog.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_present.cpp; render/d3d9/dev/glue_query.cpp; render/); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/rec/test_rec.py: no rewrite onto real files possible (PE proxy over Wine's d3d9, measures the reference); delete from the product test set or move to reference tooling; until then counted as a double (not measured mechanically: mingw not wrapped, exports read from d9rec.def; rc=1)
- tests/l6/recjob/test_recjob.py: no rewrite onto real files possible (PE proxy over Wine's d3d9, measures the reference); delete from the product test set or move to reference tooling; until then counted as a double (not measured mechanically: mingw not wrapped, exports read from d9rec.def; rc=1)
- tests/l6/reclock/test_reclock.py: no rewrite onto real files possible (PE proxy over Wine's d3d9, measures the reference); delete from the product test set or move to reference tooling; until then counted as a double (not measured mechanically: mingw not wrapped, exports read from d9rec.def; rc=1)
- tests/l6/res/test_res.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_query.cpp; render/d3d9/gen/env_table.gen.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/sm_fix/test_al.py: not measured (handoff cut the run)
- tests/l6/sm_fix/test_layout.py: not measured (handoff cut the run)
- tests/l6/sm_fix/test_ps1x.py: not measured (handoff cut the run)
- tests/l6/sm_fix/test_sink.py: not measured (handoff cut the run)
- tests/l6/sm_fix/test_sm_ops.py: not measured (handoff cut the run)
- tests/l6/sm_link/test_sm_link.py: not measured (handoff cut the run)
- tests/l6/test_sm_total.py: not measured (handoff cut the run)
- tests/l6/tex/test_tex.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp; render/d3d9/dev/glue_buf.cpp; render/d3d9/dev/glue_image.cpp; render/d3d9/dev/glue_query.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/x1/test_x1.py: link dev/app_unk_guest.cpp instead of app_unk_native.cpp; rewrite onto the real product files owning the doubled symbols (render/d3d9/dev/app_unk_guest.cpp; render/d3d9/dev/d3d9_adapter.cpp; render/d3d9/dev/x1_validator.cpp; render/d3d9/gen/d9bridge.gen.cpp); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/conf/run.py: no rewrite onto real files possible (PE proxy over Wine's d3d9, measures the reference); delete from the product test set or move to reference tooling; until then counted as a double (not measured mechanically: mingw not wrapped, exports read from d9rec.def; rc=not run)
- tests/l6/dkcore/test_dkcore.py: rewrite onto the real product files owning the doubled symbols (render/d3d9/backend/nx (declared nxdk_*)); needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine)
- tests/l6/dkfmt/test_dkfmt.py: needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)
- tests/l6/selftest/test_selftest.py: needs the L4 machine GPU model (nxgpu/deko3d boundary via the shared L4 boot machine, GL4 leaves the product)

## Not determined mechanically

- conf, rec, recjob, reclock: mingw PE builds were not wrapped; the recorder's 14 exports were read from d9rec.def (11 are product names). Their C++ symbols are not measured.
- tests whose driver failed early (rc != 0 in this run: sm_fix/*, sm_link, test_sm_total, selftest, rec*) may have built fewer binaries than a full run; their rows show what was built.
- tests not in RC when the run was stopped are marked not measured.
- PYTHON-ONLY: no assignment to attributes of imported product Python modules was found (grep of every driver).
