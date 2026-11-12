# VTSYM remeasure (render/d3d9 with the fixed fnvnx_sdd plugin)

Commits: origin/claude/fnv-l6-render 9de5bdc3 plus the plugin source of origin/claude/fnv-l2-source-w4-sdd 726ab210.
How the plugin got in: the two branches have a merge base (a232df97) but the sdd branch carries 2030 other file changes (it is a trunk-era tree), so `git merge --no-commit` conflicted all over. I did not merge. In a scratch worktree I replaced only `ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc` (the plugin source declared in render/d3d9/build.json) with 726ab210's copy; the plugin stage is a declared input, so the keys changed. Nothing else differs from 9de5bdc3. Local uncommitted admit entries for shim/include, render/shaders/include, render/backend.

Build (recipe, cold then warm): green, 70 objects; cold 155 stages run, 0 hit; warm 0 run, 155 hit.
objstub: green 1784/1784, 0 findings, 70 objects.

## (1) undefined `*.fnvnx_ap*` over all 70 objects
Was 48. Now 22 occurrences in 15 objects, 19 distinct names (`_ZTV<C>.fnvnx_ap8`). Every one of the 19 is defined in some other object of the 70 (undefined-not-defined: 0).
(I did not re-run the old build, so whether the earlier 48 counted occurrences or distinct names is not known to me; 22 occurrences / 19 distinct is the measurement now.)

## (2) defined `*.fnvnx_ap*`
48 distinct names, 131 definitions over the 70 objects. For each definition (readelf -sW, -SW, -gW): same section, same COMDAT group and same binding as the `_ZTV<C>` of its class. Violations: 0 of 131. Example: `_ZTV13NxDevLifeBase.fnvnx_ap8` is WEAK in `.rodata._ZTV13NxDevLifeBase`, group `.group`, the vtable's own section and binding, in every object that defines it.

## (3) undefined closure of the 70 objects (distinct names, defined in none of them): 35
(694 distinct undefined, 1671 distinct defined globals.)
- Boundary, nxgpu ABI (L6A / L4 shared GPU model): nxd3d_gpu_call, nxd3d_gpu_free, nxd3d_gpu_alloc, nxd3d_dc_create, nxd3d_dc_release (5)
- Platform / kernel hooks (L4 shared model, L5): nxd3d_perf_counter, nxd3d_os_cursor_pos, nxd3d_window_client, nx_env_profile (4)
- Guest runtime of the exe image (L3 / loader): fnvnx_dispatch, fnvnx_guest_invoke, fnvnx_import_key, fnvnx_wine_fixme (4)
- C++ runtime (fnvnx-a64 libstdc++/libgcc): _Unwind_Resume, __gxx_personality_v0, __cxa_atexit, __cxa_pure_virtual, __dso_handle, __aarch64_ldadd4_relax, operator delete (_ZdlPv), _ZTVN10__cxxabiv117__class_type_infoE, ...120__si_class_type_infoE, ...121__vmi_class_type_infoE (10)
- libc / libm (fnvnx-a64 newlib): malloc, free, memcmp, memcpy, memmove, memset, strcmp, cos, cosf, expf, powf, sqrtf (12)
No `_ZTV*` of an interface is open any more; nx_ffp_ps_program / nx_ffp_vs_program are defined (ffp_prog.cpp is in the 70).
Total 5+4+4+10+12 = 35.
