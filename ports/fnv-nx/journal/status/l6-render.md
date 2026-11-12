# L6 RENDER status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l6-render`. Handoff read: `journal/inbox/l6-render-handoff-opus.md` at ce235cf5. Trunk measured against:
Orchestrator: session_01KmtZKRGpFka9ShREBTYbTx (since 22:48Z; the earlier session_01KmtZKURkbywQNqjAdqyELR is archived).
`claude/fnv-nx-clean` 58a79b3e (0 product roots; render is not in the trunk).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1 | (CANCELLED by owner correction 22:45, CLAUDE.md 6f549875: two backends, OpenGL + deko3d) GL removal; branch claude/fnv-l6-render-w1 89fa6a889 is never merged (owner order) | session_01KmtZKSPYqkKJ24Q6D7aBcE (w1) | claude/fnv-l6-render-w1 | cancelled; w1 reassigned to 2c |
| 1b | OpenGL backend is product and the REFERENCE for deko3d (CLAUDE.md d7fac9b5): render/gl4 (moved unchanged by w2), nxgl, capture/replay_gl, n0/run_gl, gl_link stay; the front end emits one nxgpu stream consumed by both; GL-rendered captures/scenes are kept as reference inputs of the deko-vs-GL frame check. Own host root render/gl4 | L6A lead session_01KmtZKSgdFAv1Sd3kGuQtHk (orchestrator assignment) | (L6A worker) | handed to L6A 22:47Z; L6 w2 does the unchanged move to render/gl4; root needs w1b translator libraries + EGL/GL dev packages pinned in env.json (L1) |
| 2 | render/d3d9 as a guest (trunk verify/ at 08f6ea30 has msvcmodel + plugins; L6A root moves to render/backend) product root: build.json on msvcmodel b4057704 + fnvnx_order/fnvnx_ftoi, admitted, built by recipe.py; build_guest.sh deleted | session_01KmtZKU3QvS8E92qtA8rKes (w2) | claude/fnv-l6-render-w2 | doing: 413db88d recipe/1 root, 70 units (= old set; build_guest.sh deleted), msvcmodel + 8 plugins, 69/70 compile, 2 cold builds bit-identical (151 stages), warm 151/151 hit in 2 s; red: dev/ffp_prog.cpp (FFP ABI, 2e) and include of shim/include (owner L2: it admits shim/include as a product root, zero units) |
| 2c | GL-path translator host root: MojoShader external + our SM1-3 -> GLSL sources, no uam (link null, consumed by L6A's render/gl4 via link.roots) | session_01KmtZKSPYqkKJ24Q6D7aBcE (w1b) | claude/fnv-l6-render-w1b | doing: a5dfbdc5; L1 w16 d91524a3 + w15 559fc150 on the remote, w1b builds the root with the w16 sources key (cold x2 + warm), 02:40Z |
| 2d | uam and sdp2dksh roots (DKSH for deko3d); bison/flex/mako pinned in env.json, generated steps | - | - | after the first frame (L1, L6A); generated files as patch content refused (second path) |
| 2e | Guest FFP programs without uam: front end emits GLSL + content key; DKSH from deko3d's own pack later; no empty DKSH arrays | session_01KmtZKU3QvS8E92qtA8rKes (w2) | claude/fnv-l6-render-w2 | blocked: 028681ff + 63a911bc are local only; w2 session's permission check denies git push (3x), w2 does not route around it; remote head 24f18cdb. Blocker: push permission of the w2 session, owner of the decision: orchestrator (reported 02:45Z) |
| 2a | Recipe stages render needs: host root from external sources (MojoShader, uam); generated step running a built host ELF (FFP GLSL -> DKSH, SDP -> DKSH) | - | - | done at L1 (on remote, not yet trunk): w16 d91524a3 (rule A external sources+patches, tool roots, rule B tool generated step), w15 559fc150 (link.roots, variants) |
| 2b | Markers (stubs/no-op/refusals) in render -> 0. Count = objstub findings (classes 1, 2) on the render root's objects; the "29" of the orchestrator audit is re-measured by the gate, not by text | - | - | next: after 2 gives objects.json |
| 3 | Doubles in tests -> 0. Census (partial) at 06097801: claude/fnv-l6-w-doubles render/DOUBLES.md | - | claude/fnv-l6-w-doubles | next: measure the 8 missing, GL library = boundary; path to 0 = tests link the render root + L4 deko3d model (owner L4) |
| 4 | d3d9total gate + render/total tool root on a trunk-based branch, to MERGER when L1 w16 pins libc6-dev-i386, libclang-common-18-dev (on the path: goal step 1 needs the 13 REQUIRED_PRODUCT gates) | session_01KmtZKUN17roaVoBLBe4QKK (w4) | claude/fnv-l6-render-w4 | doing: cc78b541 verdict green (24 gates, 268 s, 332/332, selftest 47/47 killed) on base a2453fe9; trunk moved to e35b870a (imports, machine gates): conflict in gates.json/test_gates.py, w4 merging the trunk + re-running l1.py (02:45Z); env pins already on the trunk |
| 5 | FLAT: bit 1 = flat colour varyings from D3DRS_SHADEMODE; ps_1_x texcoord fix; shader cache key = all flags except the declared non-key set (FNVNX_SHADER_NONKEY_FLAGS) | session_01KmtZKSg7yny8K34kFfftjv (w3) | claude/fnv-l6-render-w3 77c6837 | done feca6ccf (merged). Acceptance at 97a6aae: front, tex, copy, drawgl 123, gl4, ffpdev, sm_link green; mutants drawgl 34/34, gl4 49/49, ffpdev 24/24, shcache 14/14 |
| 5a | test_sm_total: 7538/7538 ok, but 8 unresolved + 4 conflicts in render/shaders/total/sm_table.json (pre-existing at 1b74946f) | - | - | next |
| 5b | Shader bytecode key is FNV-1a 64 (not injective) in the cache and SHADER_CREATE_D3D: sha256 | - | - | next (deko3d side after first frame, L6A) |
| 6 | L1 assumption #80 (uam, translator, Wine pins) | lead | - | done: answered L1 22:40Z |
| 7 | 5 DROP rows of render/ASSUMPTIONS.md; front/gen_enums.py runs g++ -m32 (compile-only rule) | - | - | next |

## Global numbers

- d3d9total: 332/332 (161 enum values + 171 SM ops) at f8545efa.
- render assumptions: 129 rows (OWNER 13, removed by gate 16, by fact 8, DROP 5, REMOVE without gate 87) at 66e2f971.
- tests with doubles at 06097801 (claude/fnv-l6-w-doubles 86405ed1, partial): render tests 36: 22 double sibling files, 1 boundary double, 1 GL model only, 4 clean, 1 python only, 8 NOT measured (d3d9total, 5 sm_fix, sm_link, test_sm_total; drivers exited rc=2). Doubles in 26 files, 88 distinct symbols, 326 summed over tests. Most frequent: dev/app_unk_native.cpp. L6A tests: 2 files, 53 symbols. Global count is not final: the 8 unmeasured tests are measured next; the real GL library counts as the boundary, not as a double (CLAUDE.md 6f549875).
- render product roots admitted: 0 on the trunk; render/d3d9 recipe/1 root on claude/fnv-l6-render-w2 413db88d: 70 units, 69 compile, determinism green on 69.
