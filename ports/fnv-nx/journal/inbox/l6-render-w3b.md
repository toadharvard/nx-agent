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
| 6 | undetermined vs_bi_alu (b#/i# ALU source in vs) | ps pages say flow-control only; vs pages silent | R4: rows added (b0@mov.s0, i0@mov.s0, vs_2_0+); see the b# note below |
| 7 | undetermined mov_a0_vs2 (mov to a0, vs_2_0+) | mova page requires a0 dst; no page forbids mov | R4: row added (dstreg a0@mov.dst) |
| 8 | undetermined vs_1_0/ps_1_0 | not in any MS page | R4: stays, tag closed-world |
| 9 | undetermined temp_uninit_flow | uninitialised-read rule stated for ps_1_x only | R4 |
| 10 | undetermined rcp_mask | vs page: single-component mask; ps silent | R4 |
| 11 | unreachable-doc if@temp-written-in-branch-only | same as 9 (legacy row) | R4: status legal |
| 12 | unreachable-doc rep@temp-written-in-loop | same as 9 | R4: status legal |

Notes for the lead:
- #5-#7 widen the universe; their legality is a CreateShader validator fact, not recorded as illegal because no source states it. If the owner wants those rejected, a source is needed.
- b# as an ALU source in vs (revision after the lead's review; the earlier R5 "drop the form because uam rejects it" is withdrawn: that is selection by a test result). Decided from the pinned sources before any run: the vs register pages (vs_2_0, vs_2_x, vs_3_0) list b# (Constant Boolean Register, 16, R, 1 read port) with no flow-control note; the sentence "Only usable by a flow control instruction" is a note on the ps register pages (ps-registers-ps-2-0 / ps-2-x) only. wine-9.0 asmparser.c vs_2_reg_allowed / vs_3_reg_allowed list CONSTBOOL as a source type with no instruction restriction; vkd3d d3dbc.c reads CONSTBOOL as an operand of any instruction. No pinned source states the ALU form illegal, so it is legal (R4) and the 3 rows (vs_2_0, vs_2_x, vs_3_0) are in the universe.
  - TRANSLATOR ITEM (the rows fail until it is done): vkd3d defines a b# source as a scalar bool operand (d3dbc.c CONSTBOOL case: VSIR_DATA_BOOL, VSIR_DIMENSION_SCALAR); no source defines the numeric float4 value an ALU op reads from it. mojoshader's GLSL profile emits a bool where a vec4 is needed. A definition (source or owner decision) is needed before the translator can implement it.
- References are pinned and mirrored (render/total/externals.json, read by sm_table.py, which refuses a tree whose cas.py tree hash differs):
  - vkd3d: fnv-nx-build tools/vkd3d commit fd97ca35ae29a3f79b7a3467fe99d1a3b4e94643, tree_sha256 8ea2fb82...82d9; content = libs/vkd3d-shader/d3dbc.c of upstream wine/vkd3d 0afe7d0aad6eabca21c208be609d3a5670e8ab41 (master; released tags 1.5-2.1 lack the named opcode tables sm_xref.py parses).
  - wine: fnv-nx-build tools/wine-sm1 commit 0a52e82db01433f1b0363c52cf68920ba059a1b1, tree_sha256 70046f62...d530; content = dlls/wined3d/shader_sm1.c and dlls/d3dcompiler_43/asmparser.c of upstream wine-9.0 (fd1153552d779e1ac14b0b1c48fbd1cde894eb0a); byte-identical to the cache the table was generated from.
  - MS pages: fnv-nx-build tools/msdocs-sm commit e18183f8c5536262d44677ab77398e320e970cfc, tree_sha256 6bfddc8d...d166; the 172 page texts (learn.microsoft.com rendering of MicrosoftDocs/win32 desktop-src/direct3dhlsl) sm_table.py reads. The parsers read this text form, not the repo markdown.

## Translator item: what Wine 9.0 does for a b# source of an ALU op (case 4: Wine defines no value)
Pinned: fnv-nx-build tools/wine-wined3d (glsl_shader.c, shader.c, shader_sm1.c, wined3d_private.h of wine-9.0), entered in render/total/externals.json (wine-wined3d).
- shader_sm1.c:468 shader_sm1_read_src_param sets `src->reg.data_type = WINED3D_DATA_FLOAT` for every source register, b# included.
- shader.c:737-745 (reg map): CONSTBOOL only checks the index against limits->constant_bool, then sets reg_maps->boolean_constants. No instruction restriction: the form is accepted at CreateVertexShader.
- glsl_shader.c:2820-2822 shader_glsl_get_register_name: CONSTBOOL -> "%s_b[%u]"; glsl_shader.c:2270-2271 declares it `uniform bool %s_b[%u];`.
- glsl_shader.c:3197-3240 shader_glsl_add_src_param_ext: shader_glsl_get_swizzle emits no swizzle (shader_is_scalar, wined3d_private.h:4327 lists CONSTBOOL); shader_glsl_sprintf_cast gets dst type FLOAT (reg.data_type) and src param type FLOAT (default case, size 4): equal types, so NO conversion: the operand string is the bare `vs_b[0]`.
- The ALU handler (e.g. shader_glsl_mov, glsl_shader.c:3914+) emits `vs_r[0] = vs_b[0];`: a bool assigned to a vec4, which GLSL rejects. Wine's own output for this form does not compile; it is the same error uam gives ours.
- Loader: glsl_shader.c:1249-1280 shader_glsl_load_constants_b uploads each b# with glUniform1iv (int 0/1) into the bool uniform, from SetVertexShaderConstantB.
- At draw: shader compile/link status is only logged (shader_glsl_validate_link, glsl_shader.c:620-634: FIXME "Program %u link status invalid" when logging is on; print_glsl_info_log); Wine does not abort or fall back there.
So Wine 9.0 defines no value for a b# read by an ALU op: it accepts the shader at Create*Shader and produces GLSL that fails to compile. By the rule (do not invent a value) the translator is NOT changed; reported to the orchestrator. The 3 rows stay red.

## b# ALU source: reachability census (orchestrator decision, step 1)
Tool: render/shaders/total/bool_census.py (decoder: D3D9 token stream; length field for >= 2.0, sm_table.json operand counts for 1.x; refuses any shader that does not decode exactly to its end token 0xFFFF). Set: fnv-nx-build gamedata/shaders at commit b545961fad52aaf65474038dba302c32f7d37569 (16 .sdp packages, SHA256SUMS verified; byte-identical to gamedata/data/Shaders Shaders.part00 tar), set_sha256 7c097d1dbe0ec36469301690fa84975686d3769be4ee5674c1d261562743eebe. Run at the commit of this note.
Global counts over the WHOLE precompiled set: 15791 shaders (7631 vs: vs_1_1 1808, vs_2_0 5354, vs_3_0 469; 8160 ps: ps_2_1 7604, ps_3_0 556), 343198 vs instructions, 448534 ps instructions, all decoded with 0 refusals.
- b# source operands: vs 0 (FLOW 0, ALU 0), ps 0. Zero b# operands of any kind; vs shaders with flow-control instructions: 0 (ps: 945 flow-control instructions, none with a b#); vs def/defi/defb instructions: 10006, ps 15461 (b# is never read).
- No per-shader list exists to commit (the ALU list is empty).
Status of the proof: INCOMPLETE. The precompiled set is closed and proven free of b# ALU sources, but the exe imports d3dx9_38 D3DXAssembleShader, D3DXAssembleShaderFromFileA, D3DXCompileShader, D3DXCompileShaderFromFileA (platform/win32/imports.tsv:164-175), and the facts that resolve those call sites (reachable or not, and the source they receive) are not in this branch; docs/RENDER.md says they are developer-path only, which is a statement, not a certificate. Owner: L1 lead (session_01KmtZKUn5UJpiHYPtNRT6p9, L1 w18 / L3a facts). Also unresolved here: whether every dynamic-name GetProcAddress site is statically resolved, so any certificate built on this count would belong to a measurement build. The 3 rows are therefore NOT converted to fnvnx_reach_violation yet, and no fact_id was written by hand.

## Step 2 trace: vs `mov r0, b0` in Wine 9.0 (tools/wine-9.0 c8373ee4; glsl_shader.c and shader.c byte-identical to wine-wined3d 9fa10f83)
1. d3d9/device.c:3600 d3d9_device_CreateVertexShader -> d3d9/shader.c:130 vertexshader_init -> wined3d/shader.c:3287 wined3d_shader_create_vs -> shader.c:2501 vertex_shader_init -> shader_set_function (shader.c:2080-2140: shader_get_registers_used at 2094 and version checks at 2099-2135 are the only failure returns; CONSTBOOL is accepted at shader.c:737-745).
2. shader.c:3306 wined3d_shader_init_object -> shader.c:2161 shader_precompile; glsl_shader.c:10679 shader_glsl_precompile compiles ONLY compute shaders. No GLSL is generated for a vertex shader at creation. CreateVertexShader returns D3D_OK (device.c:3612-3621).
3. At draw: glsl_shader.c:10693 shader_glsl_select -> set_glsl_shader_program generates the vs GLSL (b# operand: `vs_b[n]` bool, no cast: glsl_shader.c:2820, 3197-3240), shader_glsl_compile (glsl_shader.c:540, glCompileShader and print_glsl_info_log only), glLinkProgram (glsl_shader.c:10524), shader_glsl_validate_link (10525; glsl_shader.c:620-634: only logs, FIXME "link status invalid").
4. glsl_shader.c:10740-10743 (corrected from 10738-10741): `glUseProgram(program_id)` + checkGLcall. wined3d_gl.h:411 checkGLcall only logs a GL error (ERR) when debug is on. Wine then goes on with the draw.
Result: the failure shows only at draw, as the lead assumed, BUT Wine does not define "the draw emits nothing". Wine calls glUseProgram on a program that failed to link; per the GL spec that call generates GL_INVALID_OPERATION and leaves the previously bound program (or fixed function, program 0) active, so what the draw produces is the GL driver's behaviour, not Wine's. Wine 9.0 has no code that skips the draw, no fallback and no GL-independent value. Reported to the lead before implementing (requirement 1: "if the trace shows something else, report it").
Class (requirement 2): "every SM form Wine compiles to a failing GLSL link" cannot be derived from this one trace; it needs the same trace over every sm_table.json row (7553 rows), i.e. a Wine run or a GLSL generator model. Not derived.

### Which program the GL state keeps across draws (lead's question; wine-9.0 glsl_shader.c)
- The failed entry stays current in wined3d's own state: set_glsl_shader_program sets `ctx_data->glsl_program = entry` on the new-link path (10407) and on a cache hit (10383, early return); nothing resets it after a failed link or a failed glUseProgram (the only NULL assignments are 10241 for "no shaders" and 10786 in the compute path). checkGLcall only logs (context_gl.c:1839-1850).
- On the new-link path wined3d itself calls glUseProgram(program_id) at 10565-10566 ("Set the shader to allow uniform loading on it"): that is the first failing call (INVALID_OPERATION, GL keeps the previous program); it then loads constants with the failed program's uniform locations while another program is current (glGetUniformLocation on a failed program returns -1; locations init at 10526-10531).
- shader_glsl_select (10693): prev_id is read before set_glsl_shader_program (10705), program_id after; `if (prev_id != program_id) glUseProgram(program_id)` (10740-10743), so the second failing call happens on the first draw only.
- A later draw with the SAME program: set_glsl_shader_program hits the cache (10383), ctx_data->glsl_program is unchanged, prev_id == program_id, glUseProgram is SKIPPED as "already current" (10740). Nothing changes the GL state, so the draw runs with the last program that glUseProgram actually accepted.
- A draw with a different valid program B: prev_id (failed id) != B, glUseProgram(B) succeeds. Back to the failed program: prev_id == B != failed, glUseProgram(failed) fails again, GL keeps B. So the GL-bound program is always the last successfully used one (program 0 / fixed function if none), which matches the GL 4.6 §7.3 rule the lead cited, with wined3d believing the failed program is current.

## Reach census over all 7553 rows (lead item 1): render/shaders/total/sm_reach.tsv, sm_reach.json
Generated by sm_reach.py (rule in its docstring): a row is a set of atoms (opcode+comparison/control, predicate, co-issue, slot register type, register index, source modifier, swizzle, write mask, result modifier, relative addressing, dcl word, def/defi/defb register, if/ifc/loop/rep/call nesting depth) keyed by shader model; a shipped shader uses a row when its model is the row's and the pool of its atoms contains every atom the row's FEATURE names (not the incidental operands of the probe program). Pooling makes "used" an over-approximation, so "0" is sound: the form is in no shipped shader. def values are not atoms.
Set: gamedata/shaders b545961f (16 packages, 15791 shaders, set_sha256 7c097d1d...), decoded with 0 refusals. Models present: vs_1_1 1808, vs_2_0 5354, vs_3_0 469, ps_2_x 7604, ps_3_0 556. Rows of every other model (ps_1_x, ps_2_0, ps_2_sw etc., 2672 rows) count 0 by model.
Global result at the parent commit of the one holding the file (see sm_reach.json "commit"): rows 7553, used by >= 1 shipped shader: 1136, used by 0: 6417 (2672 by model, 3745 in shipped models).
Package coverage of the owner's gamedata (fnv-nx-build gamedata/*):
- decoded: shaders (16 .sdp) = byte-identical to data/Shaders (tar of the same 16 files, verified).
- name-scanned, no shader extensions (.vso/.pso/.fx/.sdp): data/Fallout_-_Misc.bsa (xml, dat, txt, ctl, dlodsettings), data/Update.bsa (nif, kf, wav, dds, txt).
- NOT scanned (large content-typed BSAs, each hundreds of MB; branches not fetched): data/Fallout_-_Meshes.bsa, Textures, Textures2, Sound, Voices1, Music. Their BSA content flags and the game's loader declare meshes/textures/audio, but that is not a scan: open item (a header name scan of these needs the first part of each only).
- not bytecode containers for this census: FalloutNV.esm/_lang.esp (records), exe (D3DX call sites: w18's callcensus), redist (d3dx9_31/38 dlls), gog, captures (nxd9 D3D9 streams of runs; could hold run-time created shaders, not decoded here).
The D3DX run-time side stays open until w18 proves it (be1387/be1b2e take an open source pointer).

## Rule addition for L6A: state while a link-failed program is selected (uniform rule)

While a link-failed program is the selected one, the GPU keeps the last successfully bound program and that program's
last uniform values. Constant updates for the failed program are dropped.
Wine 9.0 (tools/wine-9.0 c8373ee4, dlls/wined3d/glsl_shader.c) and GL 4.6:
- 10526-10531: uniform locations are looked up on the failed program and come back -1.
- 1249-1280: shader_glsl_load_constants_b / glUniform* use those locations. GL 4.6 §7.6.1: location -1 is silently ignored.
- 10565-10566: glUseProgram(program_id) on the failed program. GL 4.6 §7.3: INVALID_OPERATION, current program unchanged.
- 10740-10743: shader_glsl_select calls glUseProgram only when prev_id != program_id, so later draws do not retry.
- 10383 / 10407: ctx_data->glsl_program stays at the failed entry.

## Static question from L6A: does the link outcome depend on the (vs, ps) pair?

Answer: yes, in general. It is not provable as a per-shader bit from wined3d alone.
- set_glsl_shader_program (10254-10440) builds a program for key (vs_id, ps_id). vs_id and ps_id come from per-shader
  compile (find_glsl_vertex_shader / find_glsl_fragment_shader).
- For a vs of major < 4 it also generates a third object per program: `shader_glsl_generate_vs3_rasterizer_input_setup(priv,
  vshader, pshader, ...)` (10422-10426, defined 7147-7285). It is compiled with shader_glsl_compile (7283) and attached to the program (10426-10428).
- Its text is a function of both shaders:
  - ps side: ps major (7150), ps->limits->packed_input (7274), ps->input_signature, ps->u.ps.input_reg_map, ps->reg_maps.texcoord (7255-7262).
  - vs side: vs->output_signature, vs->reg_maps.output_registers, vs->limits->packed_output (7188, 7276).
- Varying counts come only from vec4_varyings(ps_major) (1859-1866) and ps packed_input. Both stages declare the same
  `ps_link[in_count]` (1990 for the ps side, 7276 / 2002-2030 for the vs side), so the declaration size is a function of the ps alone.
  The vs side only fills it in setup_vs3_output (6919-7020).
- Index guard: `in_idx >= max_varyings` skips the write with a FIXME (6949-6953). It does not fail.
- Link-time dependencies on the vs: output_signature and reg_maps.output_registers. Entries whose outputs the vs does not write
  are zero-filled (6998-7020), so there is no "input without output" link error.
- What remains pair-dependent is the compile status of the reorder shader.
  - ps major < 3 branch: texcoord write masks depend on ps->reg_maps.texcoord and the vs signature (7222-7262).
  - ps major >= 3 branch: setup_vs3_output (6919-7020).
  - Both are syntactically valid for any signature pair, so no pair-dependent compile error follows from the text.
    That claim relies on the GLSL front end of the GL driver, which is outside the wined3d source. I do not claim it.
- Conclusion: link outcome = f(vs, ps, reorder(vs, ps), driver). Per-shader decomposition is not proven. The farm table must be
  keyed by (vs, ps) pairs. The pairs actually drawn must come from the exe's pair set, which is not statically closed.
  If no pair-level proof exists, I report it as an open class to the lead instead of assuming a per-shader bit.

## Design of the (d) link-failed lookup (against sm_link.tsv, lead decision)

- Key: the pair key (vs GLSL text hash, ps GLSL text hash, reorder text hash). The farm links the full cross product of the distinct
  shipped texts with Mesa. That is a finite superset of every drawable pair, so no per-shader bit is assumed and the drawn-pair set need not be closed.
- Table: sm_link.tsv, one row per key with outcome linked | link-failed. It is generated by the farm, never hand-written, and pinned by hash in externals.json.
- Draw time: the program cache is already per (vs, ps). On a cache miss the translator forms the key and looks it up.
  - Linked: the program is bound normally.
  - Link-failed: the program is marked failed, CreateVertexShader/CreatePixelShader still succeeded, and one cited log line is written per draw
    (wine-9.0 glsl_shader.c shader_glsl_validate_link 620-634). The GPU state follows the uniform rule above.
  - A key missing from the table is a build error. The table is complete by construction because it is the cross product.
- b# rows then expect the failed-link outcome with the citation in `src`. No float(b).
- Implement when sm_link.tsv lands.

## Lemmas L1 and L2 (static, wine-9.0 mirror c8373ee4, dlls/wined3d)

Objects of a D3D9 program: V = vs object, R = reorder object, P = ps object (or F = FFP fragment object when no ps is used).
R exists iff a vs is used and its major is below 4 (glsl_shader.c:10419-10428); for D3D9 that is every vs.
GL 4.6 §7.3 (LinkProgram): a link fails if an attached shader object did not compile.
Nothing in this section was run.

### L1: link succeeds iff V, R, P each compile, under the conditions C1-C3

Every symbol that appears in more than one object, with the source of each declaration:
1. `setup_vs_output(in vec4[N])`.
   - V declares the prototype: 8082-8083, N = vs->limits->packed_output.
   - R defines it: 7187 (ps major < 3) or 7277 (ps major >= 3), N = vs->limits->packed_output, same vs.
   - One source: the vs limits entry (shader.c:553-560, packed_output 12 for vs 1.x-3.0 in the table).
2. FFP varyings `ffp_varying_diffuse`, `ffp_varying_specular`, `ffp_varying_texcoord[8]`, `ffp_varying_fogcoord` (core syntax).
   - V declares out: 8073-8080, iff vs major < 3.
   - R declares out: 7183-7192, iff ps major < 3.
   - P declares in: 7770-7779, iff ps major < 3 or vp_mode != SHADER; diffuse and specular only when read (glsl_is_color_reg_read).
   - F declares in: 9714-9718.
   - Types are identical in every site (vec4, vec4, vec4[8], float).
   - The `flat` qualifier comes from three computations of one state formula:
     - vs args: shader.c:2296-2300;
     - R: glsl_shader.c:10426-10427;
     - ps args: shader.c:3029-3030.
     The formula is emulated_flatshading && SHADEMODE == FLAT.
   - Where V and R both declare a name, the declarations are identical. P's set is a subset of R's set.
3. `ps_link[in_count]`, or the block `shader_in_out { vec4 reg[in_count]; }` when GLSL >= 150.
   - R declares out: 7274-7276 via 2002-2030, iff ps major >= 3.
   - P declares in: 7783-7790 via 1964-1990, iff vp_mode == SHADER and reg_maps->input_registers != 0.
   - Both counts are `min(vec4_varyings(ps major), ps->limits->packed_input)` (7274 and 7785). Both come from the same ps, so the declared size, type and block member list match.
   - vp_mode == SHADER whenever R exists: for ps major >= 3, find_ps_compile_args sets vp_mode = SHADER when use_vs(state) (shader.c:2945-2951), and R is attached only when use_vs (10278-10286, 10419).
   - A vs output the ps does not read is not a link error. R zero-fills unmatched ps inputs (6998-7020) and skips indices >= max_varyings (6949-6953).
4. Uniform names.
   - V only: `pos_fixup` (8085), `clip_planes` (8071), `ffp_point{size,size_min,size_max}` (8061), `vs_c`/`vs_i`/`vs_b` (2261-2271), `vs_sampler*` (2417), `base_vertex_id` (2123, a vertex-id input).
   - R only: `ffp_point{size_min,size_max}` (7168).
   - P only: `ps_c`/`ps_i`/`ps_b`, `ps_sampler*`, `ps_samplerNP2Fixup` (7750), `ffp_fog` (7755), `bumpenv_*` (7797-7802), `ycorrection` (7822), `alpha_test_ref` (7838). F declares its own set (9633-9702), and P and F are alternatives.
   - The prefix comes from shader_glsl_get_prefix (373), so the vs_ and ps_ names are disjoint.
   - The only shared name is `ffp_point` in V and R.
     - V declares it iff point_size && !per_vertex_point_size (8059, 8018).
     - R declares it iff per_vertex_point_size, which is POINTLIST && vs->reg_maps.point_size (10425, 7166).
     - The two conditions are mutually exclusive. The shader.c:2284-2285 formulas show point_size && per_vertex_point_size = R's flag.
5. Built-ins.
   - `invariant gl_Position` is declared in V (2188) and R (7164): same qualifier.
   - gl_Position is written in R (7099) and post-processed in V (8021, fixup_position); gl_PointSize is written in V (8019) or R (7112, 7231), exclusive as in item 4.
6. Attributes `vs_in%u` and bound locations (10454-10463): V only. Fragment outputs `color_out%u`: P only.

Conditions beyond "each object compiles":
- C1 (state coherence). V, R and P are generated from the same state values for flatshading and point size.
  - V and P are looked up with args found at this draw only when their update-mask bit is set; otherwise the ids of the current program are reused (10278-10286, 10345-10351).
  - Proof that every input state of V, R and P sets the mask:
    - shademode: glsl_vertex_pipe_shademode (11884, sets VERTEX) and glsl_fragment_pipe_shademode (12272, sets PIXEL); table entries 12028 and 12393.
    - primitive type POINTLIST: cs.c:1040-1046 invalidates STATE_POINT_ENABLE, which sets VERTEX (12027) and PIXEL (12392).
  - Not read by me: the ffp-settings formula behind F (utils.c:6683, 6858, 6951). F is the FFP-ps partner and must be checked before it is covered.
- C2 (varying components). The total of V's active outputs and R's outputs is at most the context limit.
  - R writes at most 4*10 components (ps 3.0 packed_input = 10, shader.c ps_limits) or 41 for ps < 3. V adds at most one component (`ffp_varying_fogcoord`, 7997-8004).
  - glsl_varyings = GL_MAX_FRAGMENT_INPUT_COMPONENTS - 4 (adapter_gl.c:3141-3146). GL 4.6 requires GL_MAX_FRAGMENT_INPUT_COMPONENTS >= 128, so 41 <= glsl_varyings.
  - Pair-independent for that reason: the bound is the GL minimum, not a measurement.
- C3 (uniform component limits) are per object: max_constantsF is computed from limits and from args (2233-2246), with no partner term.

If C1-C3 hold then no object-crossing declaration can mismatch, so link succeeds iff V, R and P compile. L1 is true under C1-C3.
It is not unconditional: C1 for F is open, and C2 rests on the GL minimum, which is a documented fact of GL 4.6 and not on Mesa.

### L2: compile-argument domain (D3D9 devices only)

A D3D9 device has no GS/HS/DS, so for V: next_shader_type = PIXEL, next_shader_input_count = 0 (shader.c:2288-2294), and interpolation_mode is read only for vs major >= 4 (glsl_shader.c:8087-8090). All three are constants.
Wine itself restricts the swizzle domain to what the shader can see: vs_args_equal compares `(stored->swizzle_map & use_map)` (8609).

V, per vs s. Let p(s) = popcount(s.input_registers & 0xffff), maj = shader_version.major.
| field | read at | domain |
|---|---|---|
| swizzle_map | 2690; WORD cast shader.c:2278 | 2^p(s) |
| clip_enabled | 2233, 8009, 8070 | 2 |
| point_size (per_vertex_point_size is fixed by s.reg_maps.point_size) | 8018, 8059 | 2 |
| fog_src | 7997 | 2 if maj < 3, else 1 |
| flatshading | 8075-8076 | 2 if maj < 3 and the context is core (caps->emulated_flatshading, 11558), else 1 |

|A_V(s)| = 2^p(s) * 2 * 2 * (2 if maj<3 else 1)^2.

P, per ps s. Let n(s) = number of declared samplers (resource_info[i].type != 0), maj and min its version.
| field | read at | domain |
|---|---|---|
| tex_types | via pixelshader_get_resource_type (shader.c:3107-3128), reads every declared sampler | 3^n(s) (values 2D, 3D, CUBE; ERR is never set) |
| color_fixup[i] | 3612 | \|F\|^n(s). F = {identity} plus the complex fixups of the D3D9 formats; identity when can_use_texture_swizzle |
| np2_fixup | 3411, 3614, 7720-7749 | 2^(number of 2D samplers) |
| tex_transform | 5189, 6585; set only for ps 1.0-1.3 (shader.c:2796-2827) | at most 10^(samplers used by texture ops) (count 0..4 times the projected bit) |
| vp_mode | 6787, 6842, 7753, 7787, 7930 | 3 for maj >= 3 (NONE, FF, SHADER), 1 for maj < 3 |
| fog | 7649, 7930; set shader.c:2945-2985 | 4 (OFF, LINEAR, EXP, EXP2) if maj < 3 or vp_mode != SHADER |
| pointsprite | 6794, 6839, 7946 | 2 |
| flatshading | 7774-7776 | 2 if maj < 3 or vp_mode != SHADER |
| alpha_test_func | 7651, 7837 | 8 (3 bits) |
| y_correction | 4048, 7830 | 2 if reg_maps.vpos or usesdsy, else 1 |
| rt_alpha_swizzle | 7615, 7628 | 2^(color outputs written) |
| srgb_correction | 7644, 7809 | 2 |
| dual_source_blend | 7844-7881 | 1 for D3D9 (set only from blend_state->dual_source, shader.c:3043) |
| texcoords_initialized | 6842, 7948 | 1 when full_ffp_varyings (constant mask, shader.c:3017-3018); 2^8 otherwise |
| shadow | not read anywhere in glsl_shader.c | 1 |

Partner coupling of P's args: vp_mode and fog read only use_vs(state), that is whether a vs is present, not which vs. texcoords_initialized reads the partner vs's output_registers (shader.c:3000-3004) only when !full_ffp_varyings. A (vs present) and (no vs) class is therefore enough for P.
The context constants (srgb_write_control, ffp_alpha_test, full_ffp_varyings, the GL limits) are fixed per context. They are the canonical state of the farm run, and the farm states them.

R, per pair. R's text reads only static shader properties plus two state bits, and never P's compile args:
- vs: output_signature, reg_maps.output_registers and its masks, limits->packed_output;
- ps: major, input_signature, input_reg_map, reg_maps.input_registers, reg_maps.texcoord (ps < 3: 7255-7262), limits->packed_input;
- per_vertex_point_size (10425), flatshading (10426-10427).

|R| = |{vs signature class}| * |{ps signature class}| * (1 + [vs.reg_maps.point_size]) * (2 if ps maj < 3 else 1)
- Signature class means the tuple of the fields listed above.
- The flatshading factor is 1 for ps >= 3, because the R text for ps >= 3 does not read it (7274-7285).
- The farm's 337 distinct reorder texts are an instance of this set; the formula is the proof object.

Enumeration for the farm:
- compile set = sum over vs of |A_V(s)| plus sum over ps of the product of the P domains above;
- reorder set = |R| above.
Per-shader numbers (p(s), n(s), signature classes) can be computed from the pinned SDP pack; I can generate that census if the lead wants it.

## Closing C1 and C2, and the domain census (sm_domain.py)

### C1 closed (FFP fragment partner F)
- F's flatshading is the same formula as P's and R's: utils.c:6683-6686 (fragment settings), 6858-6861 and 6951-6954 (vertex settings; they cannot meet R, since R exists only with a vs).
  - `emulated_flatshading && SHADEMODE == FLAT`.
- Same hook as P: glsl_fragment_pipe_shademode (glsl_shader.c:12272 sets PIXEL; table 12393).
- F declares in diffuse/specular/texcoord[WINED3D_MAX_FFP_TEXTURES]/fogcoord unconditionally (9714-9718). R with no ps (ps major 0 < 3) declares the same four as out (7183-7192), with the same types and flat.
- C1 holds for F. No failing case found.

### C2 from the pinned Mesa source
Pin: Mesa 25.2.8 (ENV_PIN / externals.json "sm-linkprobe": Wine 9.0~repack-4build3 + Mesa llvmpipe 25.2.8). Read at tag mesa-25.2.8 from gitlab.freedesktop.org; I did not verify the tag's commit hash against the distro package. L4 can confirm the commit if wanted.
- src/gallium/auxiliary/gallivm/lp_bld_limits.h:100 `caps->max_inputs = 32;` (gallivm_init_shader_caps, used for PIPE_SHADER_FRAGMENT by src/gallium/drivers/llvmpipe/lp_screen.c:124-134). sha256 c7186c2a5346b7fb1fea67db0402744eb8fe20c320cc9a992c3d929c73e92f70.
- src/mesa/state_tracker/st_extensions.c:266-267 `pc->MaxInputComponents = screen->shader_caps[sh].max_inputs * 4;` = 128. sha256 846c964b685467570e466bc351074dd69166a4f205c40b2e7c3e4a8ee8c8a5cd.
- src/mesa/main/get_hash_params.py:428 `MAX_FRAGMENT_INPUT_COMPONENTS` is `Const.Program[MESA_SHADER_FRAGMENT].MaxInputComponents`.
- Wine: glsl_varyings = GL_MAX_FRAGMENT_INPUT_COMPONENTS - 4 = 124 on a core context (adapter_gl.c:3141-3146).
- Demand: 41 (shader.c ps_limits: ps_3_0 packed_input 10 gives 40, plus V's one fog component; ps < 3 gives 41). 41 <= 124. C2 holds for this Mesa, with the constant and file:line above.
- I did not read the Mesa varying-packing linker, so the claim is about the declared limit only.

### Domain census over the shipped pack
Script: render/shaders/total/sm_domain.py (deterministic, numbers only; `--private DIR` writes one row per distinct bytecode for the farm).
Measured at my branch head, shipped set: vs 7631 (distinct bytecodes 443), ps 8160 (distinct 1366); total 15791.
Totals (exact), distinct bytecodes:
- vs compile set: 342,344.
- reorder set |R| = 69,642 (146 vs classes, 258 ps classes including the no-ps class).
- ps compile set without the sampler dimensions: 677,888.
- ps compile set as the full product, per-sampler domain t*2*NFIX:
  - NFIX=1: 9,970,384,972,544 (sm_domain_nfix1.json);
  - NFIX=7: 2.93e26 (sm_domain_nfix7.json).
  Declared-sampler counts of the 1366 distinct ps: 0:57, 1:237, 2:277, 3:242, 4:187, 5:182, 6:88, 7:48, 8:16, 9:4, 10:8, 12:8, 14:8, 16:4.
The product does not fit the budget. The explosion is only the per-sampler dimension (texture type 2D/3D, np2 bit, colour fixup).
Everything else fits: 342,344 + 677,888 + 69,642 ≈ 1.09 million items (about 3 hours on one core at 10 ms).

What is needed to avoid the product (not asserted, needs a proof):
- Per-sampler region: a sampler's (type, np2, fixup) reaches the text only through its declaration (2417), its sampling sites (3411-3420, 3612-3614) and the NP2 fixup uniform (7720-7750).
- One global coupling: `num_consts = (cur + 1) >> 1` where cur counts the np2-flagged 2D samplers (7734-7749). It declares `ps_samplerNP2Fixup[num_consts]`, which is zero-sized when no flagged sampler resolves to 2D.
- Factorisation would be: enumerate each sampler's args independently, plus the np2 pattern class (flagged count and whether it is 0), with a proof that compile status is the conjunction over regions.
I did not prove it. NFIX itself is a context constant (the format-table fixups: complex YUY2, UYVY, YUV, YV12, NV12, P8 = 6 descs, plus scaling fixups, whose set depends on the GL extensions, utils.c:3530-3666); the farm should state it from its run.
