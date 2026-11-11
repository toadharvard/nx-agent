# L4 VERIFY status (lead: Opus 5.5, branch claude/fnv-l4-verify)

Updated 2026-11-11 22:55Z. Trunk CLAUDE.md d7fac9b50 merged (GL is the reference for deko3d). claude/fnv-l4-verify-w1 (68e37dab7) is never merged. L4 fully in the trunk: claude/fnv-nx-clean 41c9eca merged a4b0a84 (93477f2, 5d106a8 included).

## Tasks

| # | task | worker | status |
|---|---|---|---|
| 1a | ~~No Mesa~~ CANCELLED by the owner's correction (two backends, OpenGL and deko3d; CLAUDE.md 6f5498754). Branch claude/fnv-l4-verify-w1 68e37dab7 is never merged. w1 reassigned: test_bootgpu.py/test_boot_job.py onto the real libnxgl4 (l6-render/nxgl4/13c4573c49d7 @2f0427f5), its GL packages pinned in env.json (no doubles, no unpinned apt) | w1 session_01KmtZKU8dDYDU7RTpbdfEeL, branch claude/fnv-l4-verify-w1b | doing |
| 1b | deko3d boundary model, BESIDE the GL path (GL stays the emulator's renderer) of the boot machine (67 dk* functions, 206 call sites, 7 libnx calls, fnv-nx-build l6a/dkabi @071b497; command buffers; DKSH shaders, uncovered instruction = red) | lead (design: inbox l4-verify-008.md, D1-D5, phases P1-P5), workers after | PAUSED 22:46Z (not on the emulator first-frame path: GL renders in the emulator; spend-only-on-path order); design done, contract agreed with L6A/L1 (dkabi.tsv 74 rows @08252f17, module 12, libdkmodel ABI); P1 blocked by L6A backend product root (L6A); P2 loader next after w1 |
| 2 | /Z7 farms z7 1of4..4of4 (L2's recon rule) | farms of the previous lead: 1of4 session_01KmtZKSuxC7ks3kferbyC6D, 2of4 session_01KmtZKS3a468SDWPQW1Drrm, 3of4 session_01KmtZKU9urUtsHPMAyeK4X9, 4of4 session_01KmtZKTm9tSnKho4mw4Ehyw | done: tree 8f324ec, tree_hash 5dd203b860b5cc52 (canon-recon-1); fnv-nx-build msvc/z7/8f324ecde2a7/{1of4 @dd3e3088 742/749, 2of4 @2c4f2f2d 739/754, 3of4 @276fc311 720/727, 4of4 @2b8ec9cd 748/758}; total 2,949 of 2,988 built, 39 failed (failure .err files on the branches; class of 1of4: source outside the VC9 language, owner L2). Farms archived 22:47Z |
| 3 | Boot run of the next ELF (L3a packs + L5 roots linked by L1): exe entry -> WinMain | lead | blocked by the ELF with module packs (L3a, L1) |
| 5 | bootsock: remove services 27, 28, 29, 32, 33 with L1's new hostapi.h (w12 @6766d18a, sha256 2e7295fc...3f97) | w2 session_01KmtZKUCth9pSFTz7qdhsJo, branch claude/fnv-l4-verify-w2 | doing |
| 6 | deko acceptance check (D6): one nxgpu stream through GL (bootgpu.py, reference) and through the dk model, frames compared; verifies, never selects | - | after P5 (paused with 1b) |
| 4 | dk model for L6A's tests (one shared model, failure injection only as model input) | - | paused with 1b, after the first frame |

## Global numbers (commit 07158f8dad99 unless stated)

- Last boot: ELF 509809b3b809: 1,075,895,420 instructions, 1 thread, 342 host calls, exit 0xC000001D after `init d3dx9_38.dll`; 17/17 loader milestones; exe-entry, WinMain, menu, frame not reached.
- L4 test files 34 (9634457). Tests with doubles: 2 files (test_bootgpu.py, test_boot_job.py), 1 double, 4 symbols (5d106a8); module-attribute replacements 0.
- Previous count line: 1 file with a double of the renderer (test_bootgpu.py fake libnxgl4, used also by test_boot_job.py); goes to 0 with 1a (now: the rewrite onto the real libnxgl4).

## Fleet check (hourly, orchestrator order)

- 22:55Z: z7 2of4 working (cl shard build, 162 files); tc-repro unblocked, building gcc stage1; w1 stopped by the orchestrator, reassigned; w2 doing. LIFTGEN 160438e is run by L3a's own worker w1 (session_01KmtZKSJcRwWUskQJfty9T7) by the L3a lead's word; no L4 farm duplicates it.
- z7 pin under L2 rule canon-recon-1: tree_hash 5dd203b860b5cc52 (A 42a47a07ddaab6b0, B 94094fd8e6e1633f); 1of4 republished @dd3e3088 (742/749). The old pin 118e1d681f01970a is void.
- z7 1of4 failures, 7 of 749, one class: prepared-tree source outside the Visual C++ 2008 language (C++11 decltype x2 in generated global initializers, in-class member initializer C2864, and parse failures C2660/C2065/C2334/C2143 in variant files of teswaterform, hkpcontinuoussimulation, speedtreert, treeengine, blk_00aa0000_od). Owner: L2 (source generator/prepare). Sent to L2.
- Finding (L2, 22:45Z): the cl /Z7 job names objects by flattened path, so src/fallout/ai_baseprocess.cpp and src/fallout/ai/baseprocess.cpp collide on src_fallout_ai_baseprocess.cpp.obj; rule: injective names (u<index>.obj as build_tree.py). The job script is not in L4's tree; after the first frame (off path).
