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
