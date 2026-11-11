# L6A DEKO status (lead: Opus, branch claude/fnv-l6a-deko)

Updated 2026-11-11. Trunk read at claude/fnv-nx-clean 6f549875. CLAUDE.md on this branch = trunk 6f549875.

## Priority after the owner correction (6f549875: two backends)
GL renders in the emulator; deko3d (this layer) is the console backend. On the first-frame path L6A has only item 1
(the backend as an admitted product root, so the one build covers it). Items 2, 3, 3b, 2b wait until after the first
frame; GL is not touched. No NRO before dkall.

## Layout decision
The trunk is an unrelated history (shared paths: 20, all machinery). Trunk-bound work of L6A is done on trunk-based worker
branches (claude/fnv-l6a-deko-w<N> from claude/fnv-nx-clean); they carry only the root's files. This branch keeps status,
inbox notes and the dk_* sources of the integration lineage. The two histories are never merged.

## Acceptance of the deko3d frame (owner, CLAUDE.md "Two render backends": OpenGL is the reference)
1. **First acceptance item.** The same nxgpu command stream (the same D3D9 front end, scenes and game frames) is rendered
   by both backends; every deko3d frame is compared with the GL frame produced on the L4 boot machine with the GL
   library. A difference is a deko3d defect until a rule explains it. The check verifies deko3d; it never selects code.
2. The deko3d backend is written by reading the GL backend (render/gl4, nxgl, the renderer pair) as the reference
   implementation of the same nxgpu ABI. Nothing of GL is deleted or disabled.

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | dk product root (target host, LP64 module-10 image per L4 l4-verify-008 @a4b0a846; env.json cross gcc-13; newlib/libnx/deko3d headers from pinned mirrors) ports/fnv-nx/render/backend (agreed with L6: render/d3d9 is L6's root, no nesting; dk_*.c + nxgpu4_check.c + include closure), recipe.py, admitted | w4 session_01KmtZKT5Qhn2Ywp9VAbJkmt -> claude/fnv-l6a-deko-w4 | doing |
| 1a | plugins in the recipe | w4 | trunk e90a7e43 has them; they load only into C++ units (RECIPE.md "Plugins"); the dk root is all C, so 0 plugins apply by the trunk rule |
| 2 | doubles: 12/12 harnesses -> real dk_* files on the L4 deko3d model (FNVNX_EXT_DK=12, verify/bootdk.py), or deleted; dkall gate | — | waits until after the first frame; then blocked by L4 P2-P3 |
| 3 | dk boundary contract: accepted L4 design l4-verify-008; dkabi.tsv (74 rows) moves to render/backend and is republished to fnv-nx-build l6a/dkabi by w4; (67 dk*, 206 sites, 7 libnx; fnv-nx-build l6a/dkabi @071b497) for the L4 model; uam as hermetic stage -> DKSH | — | doing (contract sent to L4) |
| 3b | uam as hermetic stage: DKSH + SM 5.x form table from uam's own encoder source (L4 consumes it for its interpreter and dkcover) | w5 session_01KmtZKRDzqZrLJByWwhKKFu -> claude/fnv-l6a-deko-w5 | paused, partial survey 53bad56f (claude/fnv-l6a-deko-w5): uam needs bison/flex/mako (not in env.json); gm107 emitter 121 emit fns / 149 emitInsn; AST extractor not proven. Resumes after the first frame |
| 2b | 12 harnesses -> LP64 host-test product roots linking the real backend files on module 12 | — | waits until after the first frame; then blocked by L4 P2+P3 |
| 4 | no NRO to the console until dkall green | — | holds; NRO l6a/nro/796aac6c150a withdrawn |
| — | predecessor handoff (session_01KmtZKSVcxvoK4pCGdhtZit) | — | waiting for l6a-deko-handoff-opus.md |

## Global numbers
| what | value | commit |
|---|---|---|
| backend units (nx/dk_*.c + nxgpu4_check.c) | 18 + 1 = 19 | 4335c142 |
| test harnesses with doubles | 12 of 12, 137 product symbols redefined | 4c542643 |
| deko3d boundary | 67 dk* symbols, 206 call sites, 7 libnx symbols | l6a/dkabi 071b497 |
| L4 deko3d model | 0 lines | L4 at 2026-11-11 |
| product roots of L6A admitted on the trunk | 0 | trunk 58a79b3e |
