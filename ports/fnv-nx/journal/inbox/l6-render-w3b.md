# l6-render W3b: test_sm_total 8 unresolved + 4 conflicts -> 0 / 0

Branch claude/fnv-l6-render-w3b (from origin/claude/fnv-l6-render f8c9936d). The expected result (test_sm_total.py) is untouched.
The generator (sm_table.py, sm_legacy.py, sm_universe*.py) now records each of the 12 as a `resolved` entry (rule + evidence) in
sm_table.json instead of leaving it in `undetermined` / `conflicts`. A resolved item that makes a form illegal moves it from the universe
to sm_illegal.tsv; one that makes a form legal adds universe rows that sm_run must translate.

Rules (each closes a class, not an item):
- R1 model-set conflict: when MS's version table is a subset of vkd3d d3dbc.c's explicit range and wine's decoder (unconstrained = silence) accepts it, the legal set is vkd3d's range. A conflict stays open when that does not hold.
- R2 operand count: where the MS syntax line is version-generic and both decoders (wine shader_sm1.c, vkd3d d3dbc.c) fix an exact count per version, the decoders' per-version count is the legal form; the other count is illegal.
- R3 same-page prose over a table summary cell (as already done for o#).
- R4 closed world: the universe holds every form no source states illegal; whether CreateShader rejects such a form is a validator fact, listed in sm_illegal.tsv only when a source states it.

| # | row | why open | resolution |
|---|-----|----------|------------|
| 1 | conflict ps:47 defb (ps_2_0) | MS table: ps_2_x, ps_3_0; vkd3d {2,0}-; wine unconstrained | R1: legal in ps_2_0 too (universe unchanged, was the union) |
| 2 | conflict ps:70 texreg2gb (ps_1_1) | MS table: ps_1_2, ps_1_3; vkd3d {1,0}-{1,3}; wine 1.0-1.3 | R1: legal in ps_1_1 |
| 3 | conflict vs:sgn@vs_3_0 | MS sgn page: one syntax line, 3 sources; wine {SGN,1,2,(3,0)}; vkd3d {SGN,1,1,{3,0}} (3 sources for 2_0-2_1) | R2: vs_3_0 sgn has 1 source; the 3-source form (was a universe row) is now in sm_illegal.tsv |
| 4 | conflict vs_3_0 v[a0.x] | MS table cell 'a0/aL'; same page prose: indexed 'using aL'; wine silent | R3: illegal (sm_illegal.tsv); v[aL+n] unchanged |
| 5 | undetermined vs_a0_src (a0 as ALU source) | MS: a0 R/W in the register tables; no page says it is a legal source; wine allows | R4: rows added (srcreg a0@mov.s0, a0@add.s1) |
| 6 | undetermined vs_bi_alu (b#/i# ALU source in vs) | ps pages say flow-control only; vs pages silent | R4: rows added (b0@mov.s0, i0@mov.s0, vs_2_0+) |
| 7 | undetermined mov_a0_vs2 (mov to a0, vs_2_0+) | mova page requires a0 dst; no page forbids mov | R4: row added (dstreg a0@mov.dst) |
| 8 | undetermined vs_1_0/ps_1_0 | not in any MS page | R4: stays, tag closed-world |
| 9 | undetermined temp_uninit_flow | uninitialised-read rule stated for ps_1_x only | R4 |
| 10 | undetermined rcp_mask | vs page: single-component mask; ps silent | R4 |
| 11 | unreachable-doc if@temp-written-in-branch-only | same as 9 (legacy row) | R4: status legal |
| 12 | unreachable-doc rep@temp-written-in-loop | same as 9 | R4: status legal |

Notes for the lead:
- #5-#7 widen the universe; their legality is a CreateShader validator fact, not recorded as illegal because no source states it. If the owner wants those rejected, a source is needed.
- #7a (new, from the run): b# as an ALU source in vs is NOT in the universe (rule R5: no source defines the float4 value of a b# read; the translator emits a bool-to-vec4 assignment that uam rejects, 3 rows measured). i# and a0 as sources translate and are in. This needs a source or an owner decision; reported, not patched.
- References are pinned and mirrored (render/total/externals.json, read by sm_table.py, which refuses a tree whose cas.py tree hash differs):
  - vkd3d: fnv-nx-build tools/vkd3d commit fd97ca35ae29a3f79b7a3467fe99d1a3b4e94643, tree_sha256 8ea2fb82...82d9; content = libs/vkd3d-shader/d3dbc.c of upstream wine/vkd3d 0afe7d0aad6eabca21c208be609d3a5670e8ab41 (master; released tags 1.5-2.1 lack the named opcode tables sm_xref.py parses).
  - wine: fnv-nx-build tools/wine-sm1 commit 0a52e82db01433f1b0363c52cf68920ba059a1b1, tree_sha256 70046f62...d530; content = dlls/wined3d/shader_sm1.c and dlls/d3dcompiler_43/asmparser.c of upstream wine-9.0 (fd1153552d779e1ac14b0b1c48fbd1cde894eb0a); byte-identical to the cache the table was generated from.
  - The MS pages (ms/i) are the third source; they are the learn.microsoft.com D3D9 asm reference text cache and are not pinned by this task (no mirror exists; open item for the lead).
