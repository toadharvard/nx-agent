### J-XXXX | 2026-11-04 | stream: S15-threads | status: wip | parent: J-0148

* **did**
  - **J-0148 merge finished** (`8d8bec5`): S12's inline Interlocked + ORDER_REFCOUNT=weak kept as they were; d909c82's FNVNX_INTERLOCKED_INLINE / fnvnx_ool_* dropped; S15's tests kept; 42e9fa5 (frame-budget hotspots[]/queue[], --render report.json, ilk-cost B9) applied. ilk_cost.c became ilk_cost.cpp: in C, a gnu_inline `inline` body is an external definition next to sync.cpp's. litmus.cpp LOCK_ITERS follows LITMUS_ROUNDS again.
  - **barrier_relax in the as-built scenario** (`c580683`): S12's refcount rule is compile.sh's default. `placed` = placement + the built levers; hotspots/queue are after them; `built_levers` in fnvnx-frame-budget/1.
  - **Switch ini defaults** (`b345828`, platform/win32/ini.cpp port_default): for Fallout.ini / Fallout_default.ini, a missing key reads as the port's value; the file's value wins; `[fnvnx] bPortDefaults=0` is the off switch. Table: bUseThreadedMorpher / ParticleSystem / TempEffects = 1 (the exe's defaults are 0, the PC default ini's are 1).
  - **SortPackets reentrant** (`ca005bb`): the static `hist` is now the caller's (V1); tests/s6n test_sort_two_threads crashes with the static table and passes with the local one.
  - **bMTRendering is unsafe on the PC code** (`3b9ff17`, PERF.md 6.8): every accumulator is built with one batch renderer. The worker indexes a NULL `m_apkRenderers[1]` (AccumulateRenderPasses 0x00b63f90) and shares renderer 0 with main. The current-accumulator globals DAT_011f95ec / DAT_011f4748 are shared. **mt_cull needs port code** (two-renderer accumulators, per-thread current accumulator, fade-node routing), not an ini switch.
  - Names from the owner's exe (setting_names.py): 0x011c710c bMTRendering:Display, 0x011c3ea4 iNumHWThreads:General, 0x011dfb68 bUseThreadedAI, 0x011cc2e8 iSimType:HAVOK (default 1).
  - **Havok MT is iSimType=4** (`9b9f280`, PERF.md 6.9): bhkWorld::StepSimulation 0x00c6ae70 has the stepMultithreaded path; FUN_00c661d0 maps 0/1/4 -> discrete/continuous/multithreaded, 3 -> continuous. The pool is created in the unreconstructed EnsureObjectsCreated. Not enabled.
  - **Threaded effects unverified**: no reader of the three settings is reconstructed. Queued controller updates return to main after the 0x008c7990 join. The hand-off has plain cross-thread fields. Not counted.
  - Merged the integration branch (`ea81529`). tests/s15 test_perf had a Slerp proxy check hard-coding 130 instructions per call; S15-simd's 88 broke it on integration too. It is fixed here (reads the model).
* **next** (queue order)
  1. Havok: build/link check of the MT step and EnsureObjectsCreated in the port; mtqemu with iSimType=4.
  2. threaded_anim: an mtqemu case of the AI linear hand-off (aiCurrent / m_uiTaskFlags / packed queue lock).
  3. mt_cull port code (PERF.md 6.8: two-renderer accumulators, per-thread current accumulator, fade-node routing).
  4. Render two-thread recording with the render machine (SortPackets is reentrant now).
* **measured** - Metric (frame_budget.py --render report.json, placed mid at 1020 MHz): 36.4 -> 35.15 ms.
* **tests** - tests/s15 test_mtqemu (13 cases) and test_perf: 0 failed. tests/s12: 0 failures. tests/s1: 0 failed. tests/s4: 71 tests, 0 failed. tests/s6n: 0 failed.
* **note**
  - tests/s12's fnvcheck MSVC configuration needs `libc6-dev-i386 lib32stdc++-13-dev`. Without them FMT-MSVC / FMT-CLASS / WCHAR-LIB / OUTARG-UNINIT and the ALIGN report fail (5 failures, on the integration branch too).
  - Do not install g++-multilib: it removes g++-aarch64-linux-gnu.
  - The render report.json (e02c871) predates S6n's F2 (1b0665e) and bind work (6d305cd). The render machine's next report moves renderer_main.
