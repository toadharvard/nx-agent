### J-XXXX | 2026-11-04 | stream: S6n-native-render | status: done | parent: J-0147

* **did** - The V1 finding of J-0147 is fixed: nx_render survives malformed `.nxd9` / `.nxcap` input.
  - `render/native/capture/d3d9_exec.cpp` and `replay.cpp` now check every size against its record in 64 bits and
    against fixed limits before they allocate, copy or call the backend. A file that breaks a rule ends in exit 1 with
    a message that names the record. Ids out of a table's range map to 0. The limits are in RENDER_NATIVE.md 8.4.
  - The GL backend's readback check `x + w > width` could wrap in 32 bits. It is now done in 64 bits.
  - New: `make -C tests/s6n nx_render_robust`. It builds an ASan/UBSan nx_render (vptr check off, because the front
    is built with -fno-rtti), then runs `tests/s6n/test_nx_render_bounds.py` and V1's `tests/v1/test_nx_render_robust.py`.
* **measured**
  - Before the fix, on the ASan build: V1's 13 cases gave 11 crashes. They were the 4 GB allocations (D1, D3, D4,
    D6), a segfault on a wrapped VBU (D2), heap overflows (C1, C3), a wild SUBM offset (C2) and out-of-bounds table
    indexes (C4, C5).
  - After the fix: all 13 V1 cases end in exit 0 or 1. The 35 new bounds cases pass. A mutation run of 300 per
    seed (t.nxd9 and n0.nxcap, 600 runs) gave 0 crashes.
  - No regressions: the regular nx_render checks pass (43, 0 failed), and so do the d3d9_state checks (527, 0
    failed), front (53, 0 failed), harness (29, 0 failed) and all 19 golden scenes.
* **for V1** - Please re-verify: `make -C tests/s6n nx_render_robust ITERS=1000`.
