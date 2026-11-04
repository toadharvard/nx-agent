### J-XXXX | 2026-11-04 | stream: S15-scene | status: wip | parent: J-0152

* **did**
  - **Merged S15-threads dd64b55** into the stream branch. It brings the MT1 gate `FNVNX_MT1_ON` (S1b d4c9860) and
    the Havok / animation placement. Conflicts: S15-threads' side for mt1_cull.s1b, compat.h and PERF_budget.json;
    `fnvnx_mt1.h` removed (boot.cpp's `fnvnx_mt1_on()` replaces it); integration's side for S5's
    `--collect-traps` and S15-simd's 11.7 / preset test.
  - **Where the scene-graph update runs** (PERF.md 6.12). On the default path the cells' per-reference downward
    passes run on AI linear thread 1 (`TES::FUN_00453550` -> `UpdateCellGrid` / `TESObjectCELL::FUN_00551890`).
    Threaded `NiUpdateData` defers controllers and particles to main. Main keeps the sky, effect roots, temp
    effects, camera and the queued attaches: 15-35 % [E].
  - **Model correction** (`scene_update` 0.3-1.6 M main + `scene_update_ai` 1.7-2.9 M ai-linear; the scene-graph
    hotspot rows split 25/75). frame_budget.py: negative corrections on a multi-core mask were dropped by the
    water-fill; they are now spread over the mask's cores.
  - **The D21 map** (`tools/perf/scene_deps.py`). x86 reachability with RTTI-typed virtual dispatch. `down` (every
    UpdateDownwardPass override): 2,548 functions, 487 globals written non-atomically. Classes: init-guarded
    function statics (most; pre-warm), per-frame scratch singletons (the real split races), pools and locks,
    cold paths.
  - Two races inherited from the PC code: main's cull against thread 1's pass (S15-threads' event), and AI thread
    0's `RunActorUpdates` (local transforms) against thread 1's pass. Thread 0 waits for thread 1 only at event 7.
* **measured** - plan60 at 1020 MHz (frame_budget.py --render report.json): worst frame 19.39 -> 16.57 ms, main
  12.654 -> 10.823 M. This is a model correction, not code.
* **tests** - tests/s15 test_perf 0 failed, test_mtqemu 0 failed (13 cases), test_scene_deps 0 failed (10).
* **for the orchestrator**
  - With S15-threads' "downward pass done" event, main waits at the cull for thread 1's whole chain (thread 0's
    events 1-4, wind, the Havok step, the cell passes: about 6 M against main's 3-4 M before the cull [E]). The
    split's gain is on that wait. The model has no lever for the race fix yet, so today's 16.57 ms omits the wait.
    I'll add both levers together.
  - Idle cores during the cell passes: thread 0 is busy until `RunActorUpdates`. Main is idle while it waits at the
    event; core 3 is opt-in. The split runs its phase 1 on whoever waits.
  - docs/PERF_budget.json is not regenerated (it needs the boot frameprof). The numbers above are --render only.
* **next** (queue order)
  1. scene_deps.py: classify the written globals by access pattern (init guard / scratch / pool), mechanically.
  2. The MT1 split in `FUN_00551890`: phase 1 the per-node passes in parallel, phase 2 the per-node tails (task 4,
     particle queue, task 6, shadow) in the original order. Gate FNVNX_MT1_ON; byte-identical off.
  3. mtqemu case of the split protocol, then the race checker on the real objects once a frame runs.
  4. Model: the race-fix wait and the split lever.
