### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: done | parent: J-0149

* **did** - Fixed two V1 findings (claude/fnv-v1 b1e1d6b).
  - `front/front.cpp` SortPackets: the histogram is now a per-call stack table (8 KiB) instead of a function-level
    static, so two threads can sort their own frames at once. This unblocks two-thread recording.
  - `host/nxgpu.c` NxOp_BindUbo: the bind cache is now `uboAddr/uboSize[2][16]`, indexed by the real slot. A slot
    of 16 or above is ignored, since deko3d has 16 per stage.
* **measured** - V1's test_sort_radix.py: S1-S9, T0 and T1 all ok (T1 used to segfault).
  - V1's test_nxgpu_ubo_cache.py with its harness's cache arrays widened to [2][16]: U1-U4 all ok. The old code
    still fails U3.
  - s6n tests: 527/53/29 checks, 0 failed. 19 scenes match.
* **for V1** - The UBO harness declares `uboAddr[2][4]` itself. Against the fixed case it then writes out of bounds,
  so it reports U3 (and garbage on U2) even though the code is correct. Please declare the arrays from nxgpu.c's
  sizes, or as [2][16].
  - The nx_render bounds finding was fixed in 2686fc6 on
    claude/port-nsw-render (after 44adf6c); see render-012.
