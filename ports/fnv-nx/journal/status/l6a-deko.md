# L6A DEKO status (lead: Opus, branch claude/fnv-l6a-deko)

Updated 2026-11-11. Trunk read at claude/fnv-nx-clean 08f6ea30. CLAUDE.md on this branch = trunk 08f6ea30.

## Layout decision
The trunk is an unrelated history (shared paths: 20, all machinery). Trunk-bound work of L6A is done on trunk-based worker
branches (claude/fnv-l6a-deko-w<N> from claude/fnv-nx-clean); they carry only the root's files. This branch keeps status,
inbox notes and the dk_* sources of the integration lineage. The two histories are never merged.

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | dk product root (target host, LP64 module-10 image per L4 l4-verify-008 @a4b0a846; env.json cross gcc-13; newlib/libnx/deko3d headers from pinned mirrors) ports/fnv-nx/render/backend (agreed with L6: render/d3d9 is L6's root, no nesting; dk_*.c + nxgpu4_check.c + include closure), recipe.py, admitted | w4 session_01KmtZKT5Qhn2Ywp9VAbJkmt -> claude/fnv-l6a-deko-w4 | doing |
| 1a | plugins in the recipe | w4 | trunk e90a7e43 has them; they load only into C++ units (RECIPE.md "Plugins"); the dk root is all C, so 0 plugins apply by the trunk rule |
| 2 | doubles: 12/12 harnesses -> real dk_* files on the L4 deko3d model (FNVNX_EXT_DK=12, verify/bootdk.py), or deleted; dkall gate | — | blocked by L4 model phases P2-P3 (loader, bootdk.py), L4 |
| 3 | dk boundary contract: accepted L4 design l4-verify-008; dkabi.tsv (74 rows) moves to render/backend and is republished to fnv-nx-build l6a/dkabi by w4; (67 dk*, 206 sites, 7 libnx; fnv-nx-build l6a/dkabi @071b497) for the L4 model; uam as hermetic stage -> DKSH | — | doing (contract sent to L4) |
| 3b | uam as hermetic stage: DKSH + SM 5.x form table from uam's own encoder source (L4 consumes it for its interpreter and dkcover) | w5 session_01KmtZKRDzqZrLJByWwhKKFu -> claude/fnv-l6a-deko-w5 | doing (survey); stage key waits on L1 answer for tool-binary stages |
| 2b | 12 harnesses -> LP64 host-test product roots linking the real backend files on module 12 | — | blocked by L4 P2 loader + P3 object model, L4 |
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
