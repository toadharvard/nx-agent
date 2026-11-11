# L6 RENDER status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l6-render`. Handoff read: `journal/inbox/l6-render-handoff-opus.md` at ce235cf5. Trunk measured against:
`claude/fnv-nx-clean` 58a79b3e (0 product roots; render is not in the trunk).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1 | One backend (deko3d): desktop GL (backend gl4_*, libnxgl4, native/gl), Mesa/llvmpipe/EGL out of product and tests | session_01KmtZKSPYqkKJ24Q6D7aBcE (w1) | claude/fnv-l6-render-w1 | doing |
| 2 | render/d3d9 as a guest product root: build.json on msvcmodel b4057704 + fnvnx_order/fnvnx_ftoi, admitted, built by recipe.py; build_guest.sh deleted | session_01KmtZKU3QvS8E92qtA8rKes (w2) | claude/fnv-l6-render-w2 | doing (measured on L1's w4 recipe) |
| 2a | Recipe stages render needs: host root from external sources (MojoShader, uam); generated step running a built host ELF (FFP GLSL -> DKSH, SDP -> DKSH) | - | - | blocked by recipe extension, owner L1 (asked 22:40Z) |
| 2b | Markers (stubs/no-op/refusals) in render -> 0. Count = objstub findings (classes 1, 2) on the render root's objects; the "29" of the orchestrator audit is re-measured by the gate, not by text | - | - | next: after 2 gives objects.json |
| 3 | Doubles in tests -> 0. Census (partial) at 06097801: claude/fnv-l6-w-doubles render/DOUBLES.md | - | claude/fnv-l6-w-doubles | next: recount after 1 (GL tests deleted); path to 0 = tests link the render root + L4 deko3d model (owner L4) |
| 4 | d3d9total gate into trunk (with render/total tool root) | - | - | blocked by L1: env.json packages libc6-dev-i386, libclang-common-18-dev |
| 5 | FLAT (flat shading of translated programs): claude/fnv-l6-w-int 19c96209, not accepted | - | - | next after 1 (its acceptance tests were GL) |
| 6 | L1 assumption #80 (uam, translator, Wine pins) | lead | - | done: answered L1 22:40Z |
| 7 | 5 DROP rows of render/ASSUMPTIONS.md; front/gen_enums.py runs g++ -m32 (compile-only rule) | - | - | next |

## Global numbers

- d3d9total: 332/332 (161 enum values + 171 SM ops) at f8545efa.
- render assumptions: 129 rows (OWNER 13, removed by gate 16, by fact 8, DROP 5, REMOVE without gate 87) at 66e2f971.
- tests with doubles: census partial at 06097801 (not a global number yet).
- render product roots admitted: 0 (trunk 58a79b3e).
