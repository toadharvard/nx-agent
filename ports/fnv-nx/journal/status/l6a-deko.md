# L6A DEKO status (lead: Opus, branch claude/fnv-l6a-deko)

Updated 2026-11-11. Trunk read at claude/fnv-nx-clean 08f6ea30. CLAUDE.md on this branch = trunk 08f6ea30.

## Layout decision
The trunk is an unrelated history (shared paths: 20, all machinery). Trunk-bound work of L6A is done on trunk-based worker
branches (claude/fnv-l6a-deko-w<N> from claude/fnv-nx-clean); they carry only the root's files. This branch keeps status,
inbox notes and the dk_* sources of the integration lineage. The two histories are never merged.

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | dk product root ports/fnv-nx/render/d3d9/backend (dk_*.c + nxgpu4_check.c + include closure), recipe.py, msvcmodel b4057704, admitted | w4 session_01KmtZKT5Qhn2Ywp9VAbJkmt -> claude/fnv-l6a-deko-w4 | doing |
| 1a | plugins in the recipe | w4 | trunk e90a7e43 has them; they load only into C++ units (RECIPE.md "Plugins"); the dk root is all C, so 0 plugins apply by the trunk rule |
| 2 | doubles: 12/12 harnesses -> real dk_* files on the L4 deko3d model, or deleted; dkall gate | — | blocked by L4 deko3d model (0 lines), L4 |
| 3 | dk boundary contract (67 dk*, 206 sites, 7 libnx; fnv-nx-build l6a/dkabi @071b497) for the L4 model; uam as hermetic stage -> DKSH | — | doing (contract sent to L4) |
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
