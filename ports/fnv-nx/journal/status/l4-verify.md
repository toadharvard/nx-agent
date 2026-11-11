# L4 VERIFY status (lead: Opus 5.5, branch claude/fnv-l4-verify)

Updated 2026-11-11 22:55Z. Code head 5d106a8 (+ trunk 58a79b3); merge of claude/fnv-l4-verify @46373a2 requested from MERGER session_01KmtZKSxPHjVrmy1iFKiW3g.

## Tasks

| # | task | worker | status |
|---|---|---|---|
| 1a | No Mesa: delete llvmpipe/GL from the boot machine (boot.py GPU_ENV/--gpu/--frames, bootgpu.py, boot_job.sh --gpu-branch/GPU_PKGS, bootinputs render-branch/pair, TOOLS.md, test_bootgpu.py and the fake libnxgl4 in test_boot_job.py) | w1 session_01KmtZKU8dDYDU7RTpbdfEeL, branch claude/fnv-l4-verify-w1 | doing |
| 1b | deko3d boundary model of the boot machine (67 dk* functions, 206 call sites, 7 libnx calls, fnv-nx-build l6a/dkabi @071b497; command buffers; DKSH shaders, uncovered instruction = red) | lead (design: inbox l4-verify-008.md, D1-D5, phases P1-P5), workers after | design done; P1 blocked by L6A backend product root (L6A); P2 next |
| 2 | /Z7 farms z7 1of4..4of4 (L2's recon rule) | farms of the previous lead: 1of4 session_01KmtZKSuxC7ks3kferbyC6D, 2of4 session_01KmtZKS3a468SDWPQW1Drrm, 3of4 session_01KmtZKU9urUtsHPMAyeK4X9, 4of4 session_01KmtZKTm9tSnKho4mw4Ehyw | 1of4 done (msvc/z7/8f324ecde2a7/1of4 @a8ff7fd5: 742 built, 7 failed); 2of4..4of4 doing (prepare with L2's 503 recon branches) |
| 3 | Boot run of the next ELF (L3a packs + L5 roots linked by L1): exe entry -> WinMain | lead | blocked by the ELF with module packs (L3a, L1) |
| 5 | bootsock: remove services 27, 28, 29, 32, 33 with L1's new hostapi.h | - | blocked by L1 w12 header merge (L1) |
| 4 | dk model for L6A's tests (one shared model, failure injection only as model input) | - | after 1b |

## Global numbers (commit 07158f8dad99 unless stated)

- Last boot: ELF 509809b3b809: 1,075,895,420 instructions, 1 thread, 342 host calls, exit 0xC000001D after `init d3dx9_38.dll`; 17/17 loader milestones; exe-entry, WinMain, menu, frame not reached.
- L4 test files 34 (9634457). Tests with doubles: 2 files (test_bootgpu.py, test_boot_job.py), 1 double, 4 symbols (5d106a8); module-attribute replacements 0.
- Previous count line: 1 file with a double of the renderer (test_bootgpu.py fake libnxgl4, used also by test_boot_job.py); goes to 0 with 1a.
