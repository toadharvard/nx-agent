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
* **decision (orchestrator, approved)** - patch class **MT1** for mt_cull: gated Switch behaviour changes (docs/PATCHES.md "Patch class MT1"). Switch build only, behind bMTRendering, off by default; byte-identical output with bMTRendering=0. Checked by GCC census, mtqemu on the real culling/accumulator objects, the race checker and a frame-equivalence test, instead of the MSVC byte check.
  - **Ask for S1b**: add an "exempt from the MSVC byte check, gated" class to tools/rules/s1b_patches.py and tests/s1b (msvc_check.py skips it; test_patches.py still checks it parses and applies exactly once).
* **also done** - Havok MT linked natively end to end (`5ac6928`, PERF.md 6.9, tools/perf/vtcover.py). V1 B2: every plan includes the built levers. V1 S15-MTQEMU-B1: mtqemu links again (`752dc02`). 60 fps stable is a worst-frame target, target_60 (`cace295`).
* **D21 shared-write class (`94ffe79`, tools/perf/xthread_audit.py, tests/s15/test_xthread.py)**
  - Static audit over the exe: call graph (direct calls, tail jumps, IAT, .rdata function pointers); sides AI = 0x008c7da0 / 0x008c7f50 (the Switch's two-thread path), MAIN = 0x0086e650's calls between the AI start and the join; absolute .data/.bss accesses with lock regions; classes sync-object / both-locked / unprotected-write-write / unprotected-write-read.
  - Result: 1,223 shared words (19 sync, 44 locked, 1,151 unprotected write-write, 9 unprotected write-read) with main's device-reset path (0x004dc360) excluded.
  - Precision limit: both sides reach ~13,400 functions through shared hubs; ~9,000 indirect calls per side are unresolved. Static reachability alone cannot tell never-concurrent code from concurrent code.
  - Real finding: AI thread 1 starts every frame with `FUN_0086fd70(g_osGlobals)`, the interface update (0x007027e0 / 0x00702810 / 0x00702840), concurrently with main's window. The menu globals (HUD tile cache 0x011d96c0, ContainerMenu, StartMenu, the PlayerCharacter pointer 0x011f21f0 via 0x009c6c30) are therefore candidates.
  - Next: dynamic per-thread coverage (hb.c over the game under linhost) once frames boot (S5-boot), which is the precise class mechanism; meanwhile triage the menu path above by hand.
* **V1 fixes**: 45e8724 T1 (`b895af2`: --perf prices each case at its own IPC; V1's test T1 ok) and 2f5880c R1 (report.json's frame_budget rule names perf.budget as the whole-frame gate; R1 ok).
* **Model**: Havok and NPC animation run on the AI linear threads on the default path (`dd64b55`, PERF.md 6.11): as built 33.71 -> 26.90 ms worst at 1020 MHz (a model correction). AI thread 2's critical path 7.20 M; havok_mt (iSimType=4) -> 6.23 M in the plans (`eb6ce1c`). Race fix mt1_cull_sync.s1b (`9bd45e0`), off by default, ~6.4 ms of wait if on.
